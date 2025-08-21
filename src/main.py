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

        # 3. Initialize Components
        db_manager = DatabaseManager(config=config)
        db_manager.init_db()
        logging.info("DatabaseManager initialized.")

        pueue_group = config.get("pueue", {}).get("default_group", "default")
        watch_path = config.get("scanner", {}).get("watch_path", "new_cases")
        sleep_interval = config.get("main_loop", {}).get("sleep_interval_seconds", 10)

        workflow_submitter = WorkflowSubmitter(config=config)
        logging.info("WorkflowSubmitter initialized.")

        case_scanner = CaseScanner(
            watch_path=watch_path, db_manager=db_manager, pueue_group=pueue_group
        )
        logging.info("CaseScanner initialized.")

        # 4. Start Background Services
        case_scanner.start()
        logging.info(f"CaseScanner started, watching '{watch_path}'.")

        # 5. Main Application Loop
        logging.info("Starting main application loop...")
        while True:
            submitted_cases = db_manager.get_cases_by_status("submitted")

            if not submitted_cases:
                time.sleep(sleep_interval)
                continue

            for case in submitted_cases:
                case_id = case["case_id"]
                case_path = case["case_path"]
                logging.info(
                    f"Processing submitted case ID: {case_id}, Path: {case_path}"
                )

                try:
                    db_manager.update_case_status(
                        case_id, status="running", progress=10
                    )
                    logging.info(f"Case ID: {case_id} status updated to 'running'.")

                    workflow_submitter.submit_workflow(
                        case_path=case_path, pueue_group=case["pueue_group"]
                    )
                    logging.info(
                        f"Case ID: {case_id} successfully submitted to workflow."
                    )

                    db_manager.update_case_status(
                        case_id, status="running", progress=30
                    )

                except Exception as e:
                    logging.error(
                        f"Failed to process case ID: {case_id}. Error: {e}",
                        exc_info=True,
                    )
                    db_manager.update_case_completion(case_id, status="failed")

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
