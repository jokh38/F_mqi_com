import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from watchdog.events import DirCreatedEvent, FileCreatedEvent

from src.services.case_scanner import CaseScanner, DirectoryCreatedEventHandler


@pytest.fixture
def temp_watch_dir(tmp_path: Path) -> Path:
    """Creates a temporary directory to be watched."""
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    return watch_dir


@patch("src.common.db_manager.DatabaseManager")
def test_directory_creation_event_handler(MockDatabaseManager, temp_watch_dir: Path):
    """
    Tests that the event handler calls add_case with the correct arguments
    when a new directory is created.
    """
    # Arrange
    mock_db_instance = MockDatabaseManager.return_value
    test_pueue_group = "test_gpu"
    handler = DirectoryCreatedEventHandler(
        db_manager=mock_db_instance, pueue_group=test_pueue_group
    )

    new_dir_path = temp_watch_dir / "new_case_001"
    new_dir_path.mkdir()

    # Act
    # Simulate the event that watchdog would generate for a directory creation
    event = DirCreatedEvent(str(new_dir_path))
    handler.on_created(event)

    # Assert
    # Check that add_case was called once with the correct path and pueue_group
    mock_db_instance.add_case.assert_called_once_with(
        str(new_dir_path), test_pueue_group
    )


@patch("src.common.db_manager.DatabaseManager")
def test_directory_creation_event_handler_ignores_files(
    MockDatabaseManager, temp_watch_dir: Path
):
    """
    Tests that the event handler ignores file creation events.
    """
    # Arrange
    mock_db_instance = MockDatabaseManager.return_value
    handler = DirectoryCreatedEventHandler(
        db_manager=mock_db_instance, pueue_group="test_gpu"
    )

    new_file_path = temp_watch_dir / "a_file.txt"
    new_file_path.touch()

    # Act
    event = FileCreatedEvent(str(new_file_path))
    handler.on_created(event)

    # Assert
    mock_db_instance.add_case.assert_not_called()


@patch("src.services.case_scanner.logger.error")
@patch("src.common.db_manager.DatabaseManager")
def test_directory_creation_event_handler_handles_db_error(
    MockDatabaseManager, mock_logger_error, temp_watch_dir: Path
):
    """
    Tests that the event handler logs an error if adding a case to the
    database fails, and does not crash.
    """
    # Arrange
    mock_db_instance = MockDatabaseManager.return_value
    mock_db_instance.add_case.side_effect = Exception("DB connection failed")
    handler = DirectoryCreatedEventHandler(
        db_manager=mock_db_instance, pueue_group="test_gpu"
    )

    new_dir_path = temp_watch_dir / "new_case_003"
    new_dir_path.mkdir()

    # Act
    event = DirCreatedEvent(str(new_dir_path))
    # We expect this to log an error but not raise an exception
    handler.on_created(event)

    # Assert
    mock_db_instance.add_case.assert_called_once()
    mock_logger_error.assert_called_once()
    # Check that the log message contains the expected info
    log_message = mock_logger_error.call_args.args[0]
    assert "Failed to add case" in log_message
    assert str(new_dir_path) in log_message
    assert "DB connection failed" in log_message

    # Verify that exc_info=True was passed to the logger to get the full traceback
    assert mock_logger_error.call_args.kwargs["exc_info"] is True


@patch("src.common.db_manager.DatabaseManager")
def test_case_scanner_integration(MockDatabaseManager, temp_watch_dir: Path):
    """
    Tests the CaseScanner class integration with the event handler.
    """
    # Arrange
    mock_db_instance = MockDatabaseManager.return_value
    test_pueue_group = "integration_test_gpu"

    scanner = CaseScanner(
        watch_path=str(temp_watch_dir),
        db_manager=mock_db_instance,
        pueue_group=test_pueue_group,
    )

    # Act
    # Run the scanner in a background thread. The scanner's start() method
    # has an infinite loop, so we can't run it in the main thread.
    scanner_thread = threading.Thread(target=scanner.start, daemon=True)
    scanner_thread.start()

    # Give the observer a moment to start up
    time.sleep(0.5)

    # Create a new directory in the watched folder
    new_case_path = temp_watch_dir / "case_002"
    new_case_path.mkdir()

    # Give the event handler a moment to process the event
    time.sleep(0.5)

    # Stop the scanner
    scanner.stop()
    scanner_thread.join(timeout=1)

    # Assert
    mock_db_instance.add_case.assert_called_once_with(
        str(new_case_path), test_pueue_group
    )
