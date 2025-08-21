import pytest
from unittest.mock import patch, call
import logging
from logging.handlers import RotatingFileHandler
import os

# We need to be able to import from src
from src.main import setup_logging, main, KSTFormatter

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


def test_setup_logging_configures_handlers_and_formatter():
    """
    Tests if setup_logging configures root logger with KST formatter,
    file handler, and stream handler.
    """
    # Arrange
    # Pytest's logging capture can interfere, so we reset handlers
    logging.getLogger().handlers = []

    # Act
    setup_logging()

    # Assert
    logger = logging.getLogger()
    assert logger.level == logging.INFO
    assert len(logger.handlers) == 2  # RotatingFileHandler and StreamHandler

    # Check handler types
    assert any(isinstance(h, RotatingFileHandler) for h in logger.handlers)
    assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)

    # Check formatter type
    for handler in logger.handlers:
        assert isinstance(handler.formatter, KSTFormatter)

    # Check that the file was created and contains the initial message
    assert os.path.exists(LOG_FILE)
    with open(LOG_FILE, "r") as f:
        log_content = f.read()
        assert "Logger has been configured with KST timezone." in log_content


@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("src.main.yaml.safe_load")
def test_main_initializes_services_and_runs_loop_once(
    mock_safe_load,
    MockDatabaseManager,
    MockCaseScanner,
    MockWorkflowSubmitter,
    mock_sleep,
):
    """
    Tests that main initializes all services and runs the main loop once.
    """
    # Arrange
    setup_logging()
    mock_config = {
        "database": {"path": "dummy.db"},
        "scanner": {"watch_path": "dummy_path"},
        "pueue": {"default_group": "test_group"},
        "hpc": {},
    }
    mock_safe_load.return_value = mock_config

    mock_db_instance = MockDatabaseManager.return_value
    mock_scanner_instance = MockCaseScanner.return_value
    mock_submitter_instance = MockWorkflowSubmitter.return_value

    # Mock a case to be processed
    test_case = {
        "case_id": 1,
        "case_path": "/path/to/case",
        "pueue_group": "test_group",
    }
    # First call to get_cases returns one case, second call returns none to exit loop
    mock_db_instance.get_cases_by_status.side_effect = [[test_case], []]

    # Act
    main()

    # Assert
    # Initialization
    MockDatabaseManager.assert_called_once_with(config=mock_config)
    mock_db_instance.init_db.assert_called_once()
    MockCaseScanner.assert_called_once_with(
        watch_path="dummy_path",
        db_manager=mock_db_instance,
        pueue_group="test_group",
    )
    mock_scanner_instance.start.assert_called_once()
    MockWorkflowSubmitter.assert_called_once_with(config=mock_config)

    # Main loop logic
    mock_db_instance.get_cases_by_status.assert_called_with("submitted")
    mock_db_instance.update_case_status.assert_has_calls(
        [
            call(1, status="running", progress=10),
            call(1, status="running", progress=30),
        ]
    )
    mock_submitter_instance.submit_workflow.assert_called_once_with(
        case_path="/path/to/case", pueue_group="test_group"
    )

    # Shutdown
    mock_scanner_instance.stop.assert_called_once()
    mock_db_instance.close.assert_called_once()


@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("src.main.yaml.safe_load")
def test_main_handles_keyboard_interrupt_gracefully(
    mock_safe_load,
    MockDatabaseManager,
    MockCaseScanner,
    MockWorkflowSubmitter,
    mock_sleep,
):
    """
    Tests that main catches KeyboardInterrupt and shuts down gracefully.
    """
    # Arrange
    setup_logging()
    mock_safe_load.return_value = {
        "database": {},
        "scanner": {},
        "pueue": {},
        "hpc": {},
    }
    mock_db_instance = MockDatabaseManager.return_value
    mock_scanner_instance = MockCaseScanner.return_value
    mock_scanner_instance.observer.is_alive.return_value = True

    # Make the loop raise KeyboardInterrupt
    mock_db_instance.get_cases_by_status.side_effect = KeyboardInterrupt

    # Act
    main()

    # Assert
    mock_scanner_instance.stop.assert_called_once()
    mock_db_instance.close.assert_called_once()


@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("src.main.yaml.safe_load")
def test_main_handles_exception_in_loop(
    mock_safe_load,
    MockDatabaseManager,
    MockCaseScanner,
    MockWorkflowSubmitter,
    mock_sleep,
):
    """
    Tests that the main loop catches exceptions, logs them,
    and marks the case as 'failed'.
    """
    # Arrange
    setup_logging()
    mock_safe_load.return_value = {
        "database": {},
        "scanner": {},
        "pueue": {},
        "hpc": {},
    }
    mock_db_instance = MockDatabaseManager.return_value
    mock_submitter_instance = MockWorkflowSubmitter.return_value

    # Mock a case and a failure during submission
    test_case = {
        "case_id": 1,
        "case_path": "/path/to/fail",
        "pueue_group": "test_group",
    }
    mock_db_instance.get_cases_by_status.side_effect = [[test_case], []]
    mock_submitter_instance.submit_workflow.side_effect = Exception("Workflow failed")

    # Act
    main()

    # Assert
    # Check that update_case_completion was called with 'failed' status
    mock_db_instance.update_case_completion.assert_called_once_with(1, status="failed")

    # Check that the error was logged
    with open(LOG_FILE, "r") as f:
        log_content = f.read()
        assert "Failed to process case ID: 1" in log_content
        assert "Workflow failed" in log_content
