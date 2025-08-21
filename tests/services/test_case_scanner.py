import time
from pathlib import Path
from unittest.mock import patch, Mock

import pytest
from watchdog.events import DirCreatedEvent, FileCreatedEvent

from src.services.case_scanner import StableDirectoryEventHandler

# Use a short delay for tests to run quickly
TEST_STABILITY_DELAY = 0.1


@pytest.fixture
def temp_watch_dir(tmp_path: Path) -> Path:
    """Creates a temporary directory to be watched."""
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    return watch_dir


@pytest.fixture
def mock_db_manager() -> Mock:
    """Provides a mocked DatabaseManager instance."""
    return Mock()


@pytest.fixture
def stable_event_handler(mock_db_manager: Mock) -> StableDirectoryEventHandler:
    """Provides an instance of StableDirectoryEventHandler with a short delay."""
    return StableDirectoryEventHandler(
        db_manager=mock_db_manager,
        pueue_group="test_gpu",
        stability_delay=TEST_STABILITY_DELAY,
    )


def test_handler_processes_stable_directory(
    stable_event_handler: StableDirectoryEventHandler,
    mock_db_manager: Mock,
    temp_watch_dir: Path,
):
    """
    Tests that the handler calls add_case after a directory is created and
    remains stable for the duration of the delay.
    """
    # Arrange
    new_dir_path = temp_watch_dir / "new_case_001"
    new_dir_path.mkdir()
    event = DirCreatedEvent(str(new_dir_path))

    # Act
    stable_event_handler.on_any_event(event)

    # Assert: Should not be called immediately
    mock_db_manager.add_case.assert_not_called()
    mock_db_manager.get_case_by_path.assert_not_called()

    # Wait for the stability delay to pass
    time.sleep(TEST_STABILITY_DELAY + 0.05)

    # Assert: Should be called now
    mock_db_manager.get_case_by_path.assert_called_once_with(str(new_dir_path))
    mock_db_manager.add_case.assert_called_once_with(str(new_dir_path), "test_gpu")


def test_handler_ignores_top_level_file_creation(
    stable_event_handler: StableDirectoryEventHandler,
    mock_db_manager: Mock,
    temp_watch_dir: Path,
):
    """
    Tests that the handler ignores file creation events that are not in a
    subdirectory it is tracking.
    """
    # Arrange
    new_file_path = temp_watch_dir / "a_file.txt"
    new_file_path.touch()
    event = FileCreatedEvent(str(new_file_path))

    # Act
    stable_event_handler.on_any_event(event)
    time.sleep(TEST_STABILITY_DELAY + 0.05)

    # Assert
    mock_db_manager.add_case.assert_not_called()


def test_handler_resets_timer_on_internal_modification(
    stable_event_handler: StableDirectoryEventHandler,
    mock_db_manager: Mock,
    temp_watch_dir: Path,
):
    """
    Tests that file activity inside a newly created directory resets the
    stability timer.
    """
    # Arrange
    new_dir_path = temp_watch_dir / "new_case_002"
    new_dir_path.mkdir()
    dir_event = DirCreatedEvent(str(new_dir_path))

    # Act
    # 1. A new directory is created, starting the timer.
    stable_event_handler.on_any_event(dir_event)

    # 2. Wait for half the delay, then create a file inside it.
    time.sleep(TEST_STABILITY_DELAY / 2)
    internal_file_path = new_dir_path / "internal.txt"
    internal_file_path.touch()
    file_event = FileCreatedEvent(str(internal_file_path))
    stable_event_handler.on_any_event(file_event) # This should reset the timer

    # 3. Wait for the original timer to expire.
    time.sleep(TEST_STABILITY_DELAY / 2 + 0.05)

    # Assert: add_case should NOT have been called yet, because the timer was reset.
    mock_db_manager.add_case.assert_not_called()

    # 4. Wait for the new timer to expire.
    time.sleep(TEST_STABILITY_DELAY / 2)

    # Assert: add_case should have been called now.
    mock_db_manager.add_case.assert_called_once_with(str(new_dir_path), "test_gpu")


def test_handler_handles_db_error_gracefully(
    stable_event_handler: StableDirectoryEventHandler,
    mock_db_manager: Mock,
    temp_watch_dir: Path,
):
    """
    Tests that the handler logs an error if the DB call fails, but does not crash.
    """
    # Arrange
    mock_db_manager.get_case_by_path.return_value = None
    mock_db_manager.add_case.side_effect = Exception("DB connection failed")
    new_dir_path = temp_watch_dir / "new_case_003"
    new_dir_path.mkdir()
    event = DirCreatedEvent(str(new_dir_path))

    # Act & Assert
    # Patch the logger within the 'case_scanner' module to check its calls
    with patch("src.services.case_scanner.logger") as mock_logger:
        stable_event_handler.on_any_event(event)
        time.sleep(TEST_STABILITY_DELAY + 0.05)

        # Check that the DB call was attempted
        mock_db_manager.add_case.assert_called_once()
        # Check that the error was logged
        mock_logger.error.assert_called_once()
        log_message = mock_logger.error.call_args.args[0]
        assert "Failed to add case" in log_message
        assert "DB connection failed" in log_message
