import logging
from logging.handlers import RotatingFileHandler
from src.common.db_manager import DatabaseManager


def setup_logging():
    """Sets up file-based logging for the application."""
    log_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    log_handler = RotatingFileHandler(
        "communicator_local.log", maxBytes=5 * 1024 * 1024, backupCount=5
    )
    log_handler.setFormatter(log_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(log_handler)
    logging.info("Logger has been configured.")


def main():
    """
    Main function for the MQI Communicator application.
    """
    logging.info("MQI Communicator application starting...")

    try:
        # Initialize the database manager
        db_manager = DatabaseManager()
        db_manager.init_db()
        logging.info("Database manager initialized successfully.")

        # In a real application, the main loop would start here.
        # For now, we just log and exit.
        logging.info("Application setup complete. Starting main loop (placeholder)...")
        # while True:
        #     # Main application logic would go here
        #     time.sleep(10)

    except Exception as e:
        logging.error(f"An unhandled exception occurred: {e}", exc_info=True)

    logging.info("MQI Communicator application shutting down.")


if __name__ == "__main__":
    setup_logging()
    main()
