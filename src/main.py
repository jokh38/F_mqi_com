import logging
import time
import yaml
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta

from src.common.db_manager import DatabaseManager
from src.services.case_scanner import CaseScanner
from src.services.workflow_submitter import WorkflowSubmitter

# Define Korea Standard Time (KST)
KST = timezone(timedelta(hours=9))


class KSTFormatter(logging.Formatter):
    """A logging formatter that uses KST for timestamps."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, KST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


def setup_logging():
    """Sets up file-based, timezone-aware logging for the application."""
    log_formatter = KSTFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    log_handler = RotatingFileHandler(
        "communicator_local.log", maxBytes=5 * 1024 * 1024, backupCount=5
    )
    log_handler.setFormatter(log_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)  # Set to INFO for production
    root_logger.addHandler(log_handler)

    # Add a console handler for immediate feedback
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    logging.info("Logger has been configured with KST timezone.")


def main():
    """
    Main function for the MQI Communicator application.
    This function initializes all components and runs the main loop.
    """
    logging.info("MQI Communicator application starting...")

    try:
        # 1. Load Configuration
        with open("config/config.yaml", "r") as f:
            config = yaml.safe_load(f)
        logging.info("Configuration loaded successfully.")

        # 2. Initialize Components
        db_manager = DatabaseManager(config=config)
        db_manager.init_db()
        logging.info("DatabaseManager initialized.")

        # For now, we assume a single pueue group from config.
        # This could be expanded later.
        pueue_group = config.get("pueue", {}).get("default_group", "default")
        watch_path = config.get("scanner", {}).get("watch_path", "new_cases")

        workflow_submitter = WorkflowSubmitter(config=config)
        logging.info("WorkflowSubmitter initialized.")

        case_scanner = CaseScanner(
            watch_path=watch_path, db_manager=db_manager, pueue_group=pueue_group
        )
        logging.info("CaseScanner initialized.")

        # 3. Start Background Services
        case_scanner.start()
        logging.info(f"CaseScanner started, watching '{watch_path}'.")

        # 4. Main Application Loop
        logging.info("Starting main application loop...")
        while True:
            # Find new cases waiting for submission
            submitted_cases = db_manager.get_cases_by_status("submitted")

            if not submitted_cases:
                time.sleep(10)  # Wait if no new cases
                continue

            for case in submitted_cases:
                case_id = case["case_id"]
                case_path = case["case_path"]
                logging.info(
                    f"Processing submitted case ID: {case_id}, Path: {case_path}"
                )

                try:
                    # Update status to 'running' before submission
                    db_manager.update_case_status(
                        case_id, status="running", progress=10
                    )
                    logging.info(f"Case ID: {case_id} status updated to 'running'.")

                    # Submit the workflow
                    workflow_submitter.submit_workflow(
                        case_path=case_path, pueue_group=case["pueue_group"]
                    )
                    logging.info(
                        f"Case ID: {case_id} successfully submitted to workflow."
                    )

                    # Update progress after submission
                    db_manager.update_case_status(
                        case_id, status="running", progress=30
                    )

                except Exception as e:
                    logging.error(
                        f"Failed to process case ID: {case_id}. Error: {e}",
                        exc_info=True,
                    )
                    # Mark case as 'failed'
                    db_manager.update_case_completion(case_id, status="failed")

            time.sleep(10)  # Wait before polling again

    except KeyboardInterrupt:
        logging.info("Shutdown signal received (KeyboardInterrupt).")
    except Exception as e:
        logging.critical(
            f"A critical unhandled exception occurred in main: {e}", exc_info=True
        )
    finally:
        # 5. Graceful Shutdown
        logging.info("Initiating graceful shutdown...")
        if "case_scanner" in locals() and case_scanner.observer.is_alive():
            case_scanner.stop()
            logging.info("CaseScanner stopped.")
        if "db_manager" in locals():
            db_manager.close()
            logging.info("Database connection closed.")
        logging.info("MQI Communicator application has shut down.")


if __name__ == "__main__":
    setup_logging()
    main()
