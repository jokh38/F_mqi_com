import pytest
from unittest.mock import patch
import logging
from logging.handlers import RotatingFileHandler
import os

from src.main import setup_logging, main, KSTFormatter

# Use a temporary log file for testing
TEST_LOG_FILE = "test_communicator.log"


@pytest.fixture
def mock_config():
    """Provides a default mock config for tests."""
    return {
        "logging": {"path": TEST_LOG_FILE},
        "database": {"path": "test.db"},
        "scanner": {"watch_path": "test_cases"},
        "pueue": {"default_group": "test_group"},
        "hpc": {
            "host": "test_host",
            "user": "test_user",
            "remote_base_dir": "/remote/test",
            "remote_command": "python test_sim.py",
        },
        "main_loop": {"sleep_interval_seconds": 0.1},
    }


@pytest.fixture(autouse=True)
def cleanup_files():
    """Fixture to clean up log files before and after tests."""
    logging.shutdown()
    # Clear handlers from root logger to prevent interference between tests
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    if os.path.exists(TEST_LOG_FILE):
        os.remove(TEST_LOG_FILE)
    yield
    if os.path.exists(TEST_LOG_FILE):
        os.remove(TEST_LOG_FILE)


def test_setup_logging_configures_handlers_and_formatter(mock_config):
    """
    Tests if setup_logging configures root logger with KST formatter,
    file handler, and stream handler.
    """
    # Act
    setup_logging(mock_config)

    # Assert
    logger = logging.getLogger()
    assert logger.level == logging.INFO

    # Check that the correct handlers were added, without making assumptions
    # about pytest's own logging handlers.
    has_file_handler = any(isinstance(h, RotatingFileHandler) for h in logger.handlers)
    has_stream_handler = any(
        isinstance(h, logging.StreamHandler) for h in logger.handlers
    )
    assert has_file_handler, "RotatingFileHandler was not added."
    assert has_stream_handler, "StreamHandler was not added."

    file_handler = next(
        (h for h in logger.handlers if isinstance(h, RotatingFileHandler)), None
    )
    assert file_handler is not None
    assert os.path.basename(file_handler.baseFilename) == TEST_LOG_FILE
    assert isinstance(file_handler.formatter, KSTFormatter)

    assert os.path.exists(TEST_LOG_FILE)
    with open(TEST_LOG_FILE, "r") as f:
        log_content = f.read()
        assert "Logger has been configured" in log_content


@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("builtins.open")
@patch("src.main.yaml.safe_load")
def test_main_initializes_services_and_runs_loop(
    mock_safe_load,
    mock_open,
    MockDatabaseManager,
    MockCaseScanner,
    MockWorkflowSubmitter,
    mock_sleep,
    mock_config,
):
    """
    Tests that main initializes all services and runs the main loop.
    """
    # Arrange
    mock_safe_load.return_value = mock_config
    mock_db = MockDatabaseManager.return_value
    mock_scanner = MockCaseScanner.return_value
    mock_submitter = MockWorkflowSubmitter.return_value

    test_case = {
        "case_id": 1,
        "case_path": "/path/to/case",
        "pueue_group": "test_group",
    }
    mock_db.get_cases_by_status.side_effect = [[test_case], []]

    # Act
    main()

    # Assert
    mock_safe_load.assert_called_once()
    MockDatabaseManager.assert_called_once_with(config=mock_config)
    mock_db.init_db.assert_called_once()
    MockCaseScanner.assert_called_once_with(
        watch_path="test_cases", db_manager=mock_db, pueue_group="test_group"
    )
    mock_scanner.start.assert_called_once()
    MockWorkflowSubmitter.assert_called_once_with(config=mock_config)

    mock_db.get_cases_by_status.assert_called_with("submitted")
    mock_submitter.submit_workflow.assert_called_once_with(
        case_path="/path/to/case", pueue_group="test_group"
    )
    mock_scanner.stop.assert_called_once()
    mock_db.close.assert_called_once()


@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("builtins.open")
@patch("src.main.yaml.safe_load")
def test_main_handles_keyboard_interrupt(
    mock_safe_load,
    mock_open,
    MockDatabaseManager,
    MockCaseScanner,
    MockWorkflowSubmitter,
    mock_sleep,
    mock_config,
):
    """
    Tests that main catches KeyboardInterrupt and shuts down gracefully.
    """
    mock_safe_load.return_value = mock_config
    mock_db = MockDatabaseManager.return_value
    mock_scanner = MockCaseScanner.return_value
    mock_scanner.observer.is_alive.return_value = True

    mock_db.get_cases_by_status.side_effect = KeyboardInterrupt

    main()

    mock_scanner.stop.assert_called_once()
    mock_db.close.assert_called_once()


@patch("src.main.logging.critical")
@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("builtins.open")
@patch("src.main.yaml.safe_load")
def test_main_handles_critical_exception(
    mock_safe_load,
    mock_open,
    MockDatabaseManager,
    MockCaseScanner,
    MockWorkflowSubmitter,
    mock_sleep,
    mock_log_critical,
    mock_config,
):
    """Tests that a critical error in initialization is logged."""
    mock_safe_load.side_effect = Exception("Failed to load config")

    main()

    mock_log_critical.assert_called_once()
    assert "Failed to load config" in mock_log_critical.call_args[0][0]


@patch("src.main.logging.error")
@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("builtins.open")
@patch("src.main.yaml.safe_load")
def test_main_loop_handles_submission_failure(
    mock_safe_load,
    mock_open,
    MockDatabaseManager,
    MockCaseScanner,
    MockWorkflowSubmitter,
    mock_sleep,
    mock_log_error,
    mock_config,
):
    """
    Tests that the main loop catches an exception during workflow submission,
    logs the error, and marks the case as 'failed'.
    """
    # Arrange
    mock_safe_load.return_value = mock_config
    mock_db = MockDatabaseManager.return_value
    mock_submitter = MockWorkflowSubmitter.return_value

    # Simulate a submitted case being found, then no more cases
    test_case = {
        "case_id": 1,
        "case_path": "/path/to/failed_case",
        "pueue_group": "test_group",
    }
    mock_db.get_cases_by_status.side_effect = [[test_case], []]

    # Simulate an error during the submission process
    submission_error = Exception("SSH connection failed")
    mock_submitter.submit_workflow.side_effect = submission_error

    # Act
    main()

    # Assert
    # Check that the workflow submission was attempted
    mock_submitter.submit_workflow.assert_called_once_with(
        case_path="/path/to/failed_case", pueue_group="test_group"
    )

    # Check that the error was logged correctly
    mock_log_error.assert_called_once()
    log_args, log_kwargs = mock_log_error.call_args
    assert "Failed to process case ID: 1" in log_args[0]
    assert log_kwargs["exc_info"] is True

    # Crucially, check that the case status was updated to 'failed'
    mock_db.update_case_completion.assert_called_once_with(1, status="failed")
