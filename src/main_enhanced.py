import logging
import sys
import time
import yaml
import os
import subprocess
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from src.common.db_manager import DatabaseManager
from src.services.case_scanner import CaseScanner
from src.services.workflow_submitter import WorkflowSubmitter
from src.services.dynamic_gpu_manager import DynamicGpuManager
from src.services.parallel_processor import ParallelCaseProcessor
from src.services.priority_scheduler import PriorityScheduler, PriorityConfig
from src.services.main_loop_logic import (
    recover_stuck_submitting_cases,
    manage_running_cases,
    manage_zombie_resources,
    process_new_submitted_cases,
    process_new_submitted_cases_parallel,
    process_new_submitted_cases_with_optimization,
)

# Define the path to the configuration file
CONFIG_PATH = "config/config.yaml"

# Define Korea Standard Time (KST)
KST = timezone(timedelta(hours=9))


class KSTFormatter(logging.Formatter):
    """A logging formatter that uses KST for timestamps."""

    def format_time(
        self, record: logging.LogRecord, datefmt: Optional[str] = None
    ) -> str:
        dt = datetime.fromtimestamp(record.created, KST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


def setup_logging(config: Dict[str, Any]) -> None:
    """Sets up file-based, timezone-aware logging for the application."""
    log_config = config.get("logging", {})
    log_path = log_config.get("path", "communicator_fallback.log")

    log_formatter = KSTFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    log_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5)
    log_handler.setFormatter(log_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)  # Set to INFO for production
    root_logger.addHandler(log_handler)

    # Add a console handler for immediate feedback
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    logging.info(f"Logger has been configured. Logging to: {log_path}")


def main_enhanced(config: Dict[str, Any]) -> None:
    """
    Enhanced main function with parallel processing and dynamic GPU management.
    This function initializes all components and runs the optimized main loop.
    """
    case_scanner = None
    db_manager = None
    dashboard_process = None
    gpu_manager = None
    parallel_processor = None
    priority_scheduler = None

    try:
        logging.info("MQI Communicator Enhanced application starting...")

        # 1. Initialize Components & DB
        db_manager = DatabaseManager(config=config)
        db_manager.init_db()
        logging.info("DatabaseManager initialized.")

        # 2. Start dashboard if configured to do so
        dashboard_config = config.get("dashboard", {})
        if dashboard_config.get("auto_start", False):
            try:
                # Launch dashboard as a separate process
                dashboard_process = subprocess.Popen([
                    sys.executable, "-m", "src.dashboard"
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                logging.info("Dashboard started as separate process.")
            except Exception as e:
                logging.warning(f"Failed to start dashboard: {e}")

        # 3. Initialize GPU Resources from Config
        pueue_config = config.get("pueue", {})
        pueue_groups = pueue_config.get("groups", [])
        if not pueue_groups:
            raise ValueError("Config error: 'pueue.groups' must be a non-empty list.")

        for group in pueue_groups:
            db_manager.ensure_gpu_resource_exists(group)
            logging.info(f"Ensured GPU resource for group '{group}' exists.")

        # 4. Initialize Dynamic GPU Manager
        try:
            gpu_manager = DynamicGpuManager(config=config, db_manager=db_manager)
            logging.info("DynamicGpuManager initialized for optimal resource allocation.")
            
            # Initial GPU resource discovery
            gpu_manager.refresh_gpu_resources()
        except Exception as e:
            logging.warning(f"Failed to initialize DynamicGpuManager: {e}. Using static configuration.")

        # 5. Initialize Priority Scheduler
        priority_config = main_loop_config.get("priority_scheduling", {})
        if priority_config.get("enabled", False):
            try:
                scheduler_config = PriorityConfig(
                    algorithm=priority_config.get("algorithm", "weighted_fair"),
                    aging_factor=priority_config.get("aging_factor", 0.1),
                    starvation_threshold_hours=priority_config.get("starvation_threshold_hours", 24)
                )
                priority_scheduler = PriorityScheduler(
                    db_manager=db_manager,
                    config=scheduler_config
                )
                logging.info(f"Priority scheduler enabled with algorithm: {scheduler_config.algorithm}")
            except Exception as e:
                logging.warning(f"Failed to initialize priority scheduler: {e}. Using FIFO ordering.")
                priority_scheduler = None

        # 6. Continue Component Initialization
        main_loop_config = config.get("main_loop", {})
        watch_path = config.get("scanner", {}).get("watch_path", "new_cases")
        sleep_interval = main_loop_config.get("sleep_interval_seconds", 10)
        running_case_timeout_hours = main_loop_config.get(
            "running_case_timeout_hours", 24
        )
        timeout_delta = timedelta(hours=running_case_timeout_hours)

        # 7. Initialize Parallel Processing (if enabled)
        parallel_config = main_loop_config.get("parallel_processing", {})
        if parallel_config.get("enabled", False):
            try:
                parallel_processor = ParallelCaseProcessor(
                    db_manager=db_manager,
                    workflow_submitter=None,  # Will be set after WorkflowSubmitter creation
                    gpu_manager=gpu_manager,
                    priority_scheduler=priority_scheduler,
                    max_workers=parallel_config.get("max_workers", 4),
                    batch_size=parallel_config.get("batch_size", 10),
                    processing_timeout=parallel_config.get("processing_timeout", 300.0)
                )
                logging.info(
                    f"Parallel processing enabled with {parallel_processor.max_workers} workers, "
                    f"batch size {parallel_processor.batch_size}"
                )
            except Exception as e:
                logging.warning(f"Failed to initialize parallel processor: {e}. Using sequential processing.")
                parallel_processor = None

        # Ensure the watch path exists before starting the scanner
        os.makedirs(watch_path, exist_ok=True)
        logging.info(f"Ensured watch directory exists: {watch_path}")

        workflow_submitter = WorkflowSubmitter(config=config)
        logging.info("WorkflowSubmitter initialized.")

        # Set workflow_submitter for parallel processor if it exists
        if parallel_processor:
            parallel_processor.workflow_submitter = workflow_submitter

        case_scanner = CaseScanner(
            watch_path=watch_path, db_manager=db_manager, config=config
        )
        logging.info("CaseScanner initialized.")

        # 7. Start Background Services
        case_scanner.start()
        logging.info(f"CaseScanner started, watching '{watch_path}'.")

        # 8. Enhanced Main Application Loop
        logging.info("Starting enhanced main application loop with parallel processing and dynamic GPU management...")
        loop_iteration = 0
        gpu_refresh_interval = 50  # Refresh GPU resources every 50 iterations (default ~8.3 minutes)
        
        while True:
            try:
                loop_iteration += 1
                
                # Periodically refresh GPU resources for optimal allocation
                if gpu_manager and loop_iteration % gpu_refresh_interval == 0:
                    try:
                        gpu_manager.refresh_gpu_resources()
                        logging.info("GPU resources refreshed for optimal allocation")
                    except Exception as e:
                        logging.warning(f"GPU resource refresh failed: {e}")

                # The core logic with enhanced parallel processing
                recover_stuck_submitting_cases(db_manager, workflow_submitter)
                manage_running_cases(db_manager, workflow_submitter, timeout_delta, KST)
                manage_zombie_resources(db_manager, workflow_submitter)
                
                # Use parallel processing if available, otherwise fall back to sequential
                if parallel_processor:
                    try:
                        cases_processed = process_new_submitted_cases_parallel(
                            db_manager, workflow_submitter, parallel_processor
                        )
                        
                        # Log performance metrics periodically
                        if loop_iteration % 10 == 0 and cases_processed:
                            metrics = parallel_processor.get_performance_summary()
                            logging.info(
                                f"Parallel processing metrics: {metrics['total_cases_processed']} cases, "
                                f"{metrics['success_rate_percent']}% success rate, "
                                f"{metrics['average_processing_time_seconds']}s avg time"
                            )
                            
                            # Log priority scheduling metrics if available
                            if priority_scheduler and loop_iteration % 20 == 0:  # Less frequent for priority metrics
                                priority_stats = priority_scheduler.get_priority_statistics()
                                logging.info(
                                    f"Priority scheduling metrics: {priority_stats['total_cases_scheduled']} cases scheduled, "
                                    f"{priority_stats['starvation_prevented']} starvation prevented, "
                                    f"algorithm: {priority_stats['algorithm']}"
                                )
                    except Exception as e:
                        logging.error(f"Parallel processing error: {e}. Falling back to sequential.")
                        process_new_submitted_cases_with_optimization(db_manager, workflow_submitter, gpu_manager)
                else:
                    # Use optimized sequential processing with dynamic GPU management
                    process_new_submitted_cases_with_optimization(db_manager, workflow_submitter, gpu_manager)

            except Exception as e:
                # Catch exceptions in the main loop itself to prevent crashing
                logging.error(
                    f"An unexpected error occurred in the main loop: {e}", exc_info=True
                )

            time.sleep(sleep_interval)

    except KeyboardInterrupt:
        logging.info("Shutdown signal received (KeyboardInterrupt).")
    except Exception as e:
        logging.critical(
            f"A critical unhandled exception occurred in main: {e}", exc_info=True
        )
    finally:
        logging.info("Initiating graceful shutdown...")
        
        # Log final performance metrics if parallel processing was used
        if parallel_processor:
            try:
                final_metrics = parallel_processor.get_performance_summary()
                logging.info(f"Final parallel processing metrics: {final_metrics}")
            except Exception as e:
                logging.warning(f"Failed to log final metrics: {e}")
        
        if case_scanner and case_scanner.observer.is_alive():
            case_scanner.stop()
            logging.info("CaseScanner stopped.")
        if db_manager:
            db_manager.close()
            logging.info("Database connection closed.")
        if dashboard_process:
            try:
                dashboard_process.terminate()
                dashboard_process.wait(timeout=5)
                logging.info("Dashboard process terminated.")
            except subprocess.TimeoutExpired:
                dashboard_process.kill()
                logging.warning("Dashboard process killed after timeout.")
            except Exception as e:
                logging.error(f"Error terminating dashboard process: {e}")
        logging.info("MQI Communicator Enhanced application has shut down.")


if __name__ == "__main__":
    # Load config just for logging setup before the main function
    try:
        with open(CONFIG_PATH, "r") as f:
            initial_config = yaml.safe_load(f)
        setup_logging(initial_config)
    except FileNotFoundError:
        print(
            f"ERROR: Configuration file not found at '{CONFIG_PATH}'. "
            f"The application cannot start."
        )
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to load or parse '{CONFIG_PATH}'. Error: {e}")
        sys.exit(1)

    main_enhanced(initial_config)