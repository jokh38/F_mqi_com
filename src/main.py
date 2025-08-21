import logging
import time
import yaml
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from src.common.db_manager import DatabaseManager
from src.services.case_scanner import CaseScanner
from src.services.workflow_submitter import WorkflowSubmitter

# Define the path to the configuration file
CONFIG_PATH = "config/config.yaml"

# Define Korea Standard Time (KST)
KST = timezone(timedelta(hours=9))


class KSTFormatter(logging.Formatter):
    """A logging formatter that uses KST for timestamps."""

    def formatTime(
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


def main() -> None:
    """
    Main function for the MQI Communicator application.
    This function initializes all components and runs the main loop.
    """
    case_scanner = None
    db_manager = None

    try:
        # 1. Load Configuration
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)

        # 2. Setup Logging (as early as possible)
        setup_logging(config)
        logging.info("MQI Communicator application starting...")
        logging.info("Configuration loaded successfully.")

        # 3. Initialize Components & DB
        db_manager = DatabaseManager(config=config)
        db_manager.init_db()
        logging.info("DatabaseManager initialized.")

        # 4. Initialize GPU Resources from Config
        pueue_config = config.get("pueue", {})
        pueue_groups = pueue_config.get("groups", [])
        if not pueue_groups:
            raise ValueError("Configuration error: 'pueue.groups' must be a non-empty list.")

        for group in pueue_groups:
            db_manager.ensure_gpu_resource_exists(group)
            logging.info(f"Ensured GPU resource for group '{group}' exists.")

        # 5. Continue Component Initialization
        watch_path = config.get("scanner", {}).get("watch_path", "new_cases")
        sleep_interval = config.get("main_loop", {}).get("sleep_interval_seconds", 10)

        workflow_submitter = WorkflowSubmitter(config=config)
        logging.info("WorkflowSubmitter initialized.")

        case_scanner = CaseScanner(watch_path=watch_path, db_manager=db_manager)
        logging.info("CaseScanner initialized.")

        # 4. Start Background Services
        case_scanner.start()
        logging.info(f"CaseScanner started, watching '{watch_path}'.")

        # 5. Main Application Loop
        logging.info("Starting main application loop...")
        while True:
            try:
                # --- Part 1: Check status of RUNNING cases ---
                running_cases = db_manager.get_cases_by_status("running")
                if running_cases:
                    logging.info(f"Found {len(running_cases)} running case(s) to check.")
                for case in running_cases:
                    case_id = case["case_id"]
                    task_id = case["pueue_task_id"]

                    if task_id is None:
                        logging.warning(
                            f"Case ID {case_id} is 'running' but has no pueue_task_id. Marking as failed."
                        )
                        db_manager.update_case_completion(case_id, status="failed")
                        db_manager.release_gpu_resource(case_id)
                        logging.info(f"Released GPU resource for failed case ID: {case_id}.")
                        continue

                    status = workflow_submitter.get_workflow_status(task_id)
                    logging.info(f"Case ID {case_id} (Task {task_id}) has remote status: '{status}'.")

                    if status in ("success", "failure", "not_found"):
                        final_status = "completed" if status == "success" else "failed"
                        db_manager.update_case_completion(case_id, status=final_status)
                        db_manager.release_gpu_resource(case_id)
                        if final_status == "completed":
                            logging.info(f"Case ID {case_id} successfully completed. GPU resource released.")
                        else:
                            logging.error(
                                f"Case ID {case_id} failed (status: {status}). GPU resource released."
                            )
                    # If status is 'running', do nothing and check again next cycle.

                # --- Part 2: Process new SUBMITTED cases with dynamic resource allocation ---
                submitted_cases = db_manager.get_cases_by_status("submitted")
                if submitted_cases:
                    logging.info(
                        f"Found {len(submitted_cases)} submitted case(s) to process."
                    )

                    # Iterate through submitted cases and try to assign them to available GPUs
                    for case_to_process in submitted_cases:
                        case_id = case_to_process["case_id"]
                        case_path = case_to_process["case_path"]

                        # Try to lock ANY available GPU resource for this case
                        locked_pueue_group = db_manager.find_and_lock_any_available_gpu(
                            case_id
                        )

                        if locked_pueue_group:
                            # If we found a GPU, process the case
                            logging.info(
                                f"GPU resource '{locked_pueue_group}' locked for case ID: {case_id}"
                            )
                            try:
                                # Assign the locked group to the case
                                db_manager.update_case_pueue_group(
                                    case_id, locked_pueue_group
                                )

                                # Mark as 'submitting' to prevent re-processing
                                db_manager.update_case_status(
                                    case_id, status="submitting", progress=10
                                )

                                pueue_task_id = workflow_submitter.submit_workflow(
                                    case_path=case_path, pueue_group=locked_pueue_group
                                )

                                if pueue_task_id is not None:
                                    db_manager.update_case_pueue_task_id(
                                        case_id, pueue_task_id
                                    )
                                    db_manager.update_case_status(
                                        case_id, status="running", progress=30
                                    )
                                    logging.info(
                                        f"Case ID: {case_id} successfully submitted to '{locked_pueue_group}' as Task ID: {pueue_task_id}."
                                    )
                                else:
                                    # Handle case where submission succeeded but ID parsing failed
                                    logging.error(
                                        f"Failed to get Pueue Task ID for case ID: {case_id}."
                                    )
                                    db_manager.update_case_completion(
                                        case_id, status="failed"
                                    )
                                    db_manager.release_gpu_resource(
                                        case_id
                                    )  # Release lock on failure
                                    logging.info(
                                        f"Released GPU resource for failed case ID: {case_id}."
                                    )

                            except Exception as e:
                                logging.error(
                                    f"Failed to process case ID: {case_id}. Error: {e}",
                                    exc_info=True,
                                )
                                db_manager.update_case_completion(
                                    case_id, status="failed"
                                )
                                db_manager.release_gpu_resource(
                                    case_id
                                )  # Release lock on failure
                                logging.info(
                                    f"Released GPU resource for failed case ID: {case_id}."
                                )
                        else:
                            # If we failed to get a lock, it means no GPUs are available.
                            # We can stop trying for this cycle.
                            logging.info(
                                "No more available GPU resources at this time. Will retry in the next cycle."
                            )
                            break  # Exit the for loop

            except Exception as e:
                # Catch exceptions in the main loop itself to prevent crashing
                logging.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)

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
        logging.info("MQI Communicator application has shut down.")


if __name__ == "__main__":
    # Load config just for logging setup before the main function
    try:
        with open(CONFIG_PATH, "r") as f:
            initial_config = yaml.safe_load(f)
        setup_logging(initial_config)
    except FileNotFoundError:
        print(f"ERROR: {CONFIG_PATH} not found. Cannot configure logging.")
    except Exception as e:
        print(f"ERROR: Failed to configure logging from config.yaml: {e}")

    main()
