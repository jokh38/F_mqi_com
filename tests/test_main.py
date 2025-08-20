import pytest
from unittest.mock import patch
import logging
from logging.handlers import RotatingFileHandler
import os

# We need to be able to import from src
from src.main import setup_logging, main

LOG_FILE = "communicator_local.log"


@pytest.fixture(autouse=True)
def cleanup_files():
    """Fixture to clean up log files before and after tests."""
    # Ensure logging is reset and file is gone before each test
    logging.shutdown()
    logging.getLogger().handlers = []
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)

    yield

    # Clean up after the test
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)


def test_setup_logging_creates_file_and_configures_logger():
    """
    Tests if the setup_logging function creates the log file and
    configures the root logger with a RotatingFileHandler.
    """
    # Act
    setup_logging()

    # Assert
    # Check that the logger is configured
    logger = logging.getLogger()
    assert logger.level == logging.DEBUG
    assert any(isinstance(h, RotatingFileHandler) for h in logger.handlers)

    # Check that the file was created
    assert os.path.exists(LOG_FILE)

    # Check if the initial configuration message was logged
    with open(LOG_FILE, "r") as f:
        log_content = f.read()
        assert "Logger has been configured." in log_content


@patch("src.main.DatabaseManager")
def test_main_function_initializes_db_and_logs(MockDatabaseManager):
    """
    Tests the main function's core logic by mocking the DatabaseManager.
    Verifies that the DB is initialized and that logs are written.
    """
    # Arrange: Setup the mock for the DatabaseManager instance
    setup_logging()  # Ensure file logging is active for this test
    mock_db_instance = MockDatabaseManager.return_value

    # Act: Run the main function
    main()

    # Assert
    # Verify that DatabaseManager was instantiated
    MockDatabaseManager.assert_called_once()

    # Verify that the init_db method was called on the instance
    mock_db_instance.init_db.assert_called_once()

    # Verify that the log file was created and contains expected messages
    assert os.path.exists(LOG_FILE)
    with open(LOG_FILE, "r") as f:
        log_content = f.read()
        assert "MQI Communicator application starting..." in log_content
        assert "Database manager initialized successfully." in log_content
        assert "Application setup complete." in log_content
        assert "MQI Communicator application shutting down." in log_content


@patch("src.main.DatabaseManager")
def test_main_function_handles_exception(MockDatabaseManager):
    """
    Tests that the main function catches and logs exceptions.
    """
    # Arrange: Configure the mock to raise an exception
    setup_logging()  # Ensure file logging is active for this test
    error_message = "Test DB connection failed"
    MockDatabaseManager.side_effect = Exception(error_message)

    # Act: Run the main function
    main()

    # Assert
    # Verify that logging recorded the error
    assert os.path.exists(LOG_FILE)
    with open(LOG_FILE, "r") as f:
        log_content = f.read()
        assert "An unhandled exception occurred" in log_content
        assert error_message in log_content
