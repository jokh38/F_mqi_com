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
from src.services.main_loop_logic import (
    recover_stuck_submitting_cases,
    manage_running_cases,
    manage_zombie_resources,
    process_new_submitted_cases,
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


def main(config: Dict[str, Any]) -> None:
    """
    Main function for the MQI Communicator application.
    This function initializes all components and runs the main loop.
    """
    case_scanner = None
    db_manager = None
    dashboard_process = None

    try:
        logging.info("MQI Communicator application starting...")

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

        # 4. Continue Component Initialization
        main_loop_config = config.get("main_loop", {})
        watch_path = config.get("scanner", {}).get("watch_path", "new_cases")
        sleep_interval = main_loop_config.get("sleep_interval_seconds", 10)
        running_case_timeout_hours = main_loop_config.get(
            "running_case_timeout_hours", 24
        )
        timeout_delta = timedelta(hours=running_case_timeout_hours)

        # Ensure the watch path exists before starting the scanner
        os.makedirs(watch_path, exist_ok=True)
        logging.info(f"Ensured watch directory exists: {watch_path}")

        workflow_submitter = WorkflowSubmitter(config=config)
        logging.info("WorkflowSubmitter initialized.")

        case_scanner = CaseScanner(
            watch_path=watch_path, db_manager=db_manager, config=config
        )
        logging.info("CaseScanner initialized.")

        # 5. Start Background Services
        case_scanner.start()
        logging.info(f"CaseScanner started, watching '{watch_path}'.")

        # 6. Main Application Loop
        logging.info("Starting main application loop...")
        while True:
            try:
                # The core logic is now refactored into separate, testable functions.
                recover_stuck_submitting_cases(db_manager, workflow_submitter)
                manage_running_cases(db_manager, workflow_submitter, timeout_delta, KST)
                manage_zombie_resources(db_manager, workflow_submitter)
                process_new_submitted_cases(db_manager, workflow_submitter)

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
        logging.info("MQI Communicator application has shut down.")


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

    main(initial_config)
