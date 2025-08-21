import time
from pathlib import Path
from unittest.mock import patch, Mock, call
from typing import Generator

import pytest
from watchdog.events import DirCreatedEvent, FileCreatedEvent

from src.services.case_scanner import StableDirectoryEventHandler, CaseScanner

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
    mock = Mock()
    mock.get_case_by_path.return_value = None
    return mock


@pytest.fixture
def stable_event_handler(mock_db_manager: Mock) -> StableDirectoryEventHandler:
    """Provides an instance of StableDirectoryEventHandler."""
    # No teardown needed as we will mock the Timer class itself
    return StableDirectoryEventHandler(
        db_manager=mock_db_manager,
        stability_delay=TEST_STABILITY_DELAY,
    )


@patch("src.services.case_scanner.threading.Timer")
def test_handler_starts_timer_on_new_directory(
    MockTimer,
    stable_event_handler: StableDirectoryEventHandler,
    temp_watch_dir: Path,
):
    """Tests that the handler starts a timer when a directory is created."""
    new_dir_path = temp_watch_dir / "new_case_001"
    event = DirCreatedEvent(str(new_dir_path))

    stable_event_handler.on_any_event(event)

    MockTimer.assert_called_once_with(
        TEST_STABILITY_DELAY,
        stable_event_handler._process_directory,
        args=[str(new_dir_path)],
    )
    mock_timer_instance = MockTimer.return_value
    mock_timer_instance.start.assert_called_once()


@patch("src.services.case_scanner.threading.Timer")
def test_handler_calls_add_case_when_timer_expires(
    MockTimer,
    stable_event_handler: StableDirectoryEventHandler,
    mock_db_manager: Mock,
    temp_watch_dir: Path,
):
    """
    Tests that the handler calls add_case by simulating a timer's callback.
    """
    new_dir_path = str(temp_watch_dir / "new_case_001")
    event = DirCreatedEvent(new_dir_path)

    stable_event_handler.on_any_event(event)

    # Extract the callback and its arguments from the mock Timer
    timer_args = MockTimer.call_args.args
    callback_func = timer_args[1]
    callback_args = timer_args[2]

    # Simulate the timer expiring by executing the callback
    callback_func(*callback_args)

    mock_db_manager.get_case_by_path.assert_called_once_with(new_dir_path)
    mock_db_manager.add_case.assert_called_once_with(new_dir_path)


@patch("src.services.case_scanner.threading.Timer")
def test_handler_resets_timer_on_internal_modification(
    MockTimer,
    stable_event_handler: StableDirectoryEventHandler,
    mock_db_manager: Mock,
    temp_watch_dir: Path,
):
    """
    Tests that file activity inside a directory resets the stability timer.
    """
    new_dir_path = temp_watch_dir / "new_case_002"
    dir_event = DirCreatedEvent(str(new_dir_path))

    # 1. A new directory is created, starting the first timer.
    stable_event_handler.on_any_event(dir_event)
    first_timer_instance = MockTimer.return_value

    # 2. A file is created inside, which should cancel the first timer and start a new one.
    internal_file_path = new_dir_path / "internal.txt"
    file_event = FileCreatedEvent(str(internal_file_path))
    stable_event_handler.on_any_event(file_event)

    # Assert that the first timer was cancelled
    first_timer_instance.cancel.assert_called_once()
    # Assert that a new timer was started (total of 2 calls to the constructor)
    assert MockTimer.call_count == 2

    # Extract the callback from the second timer call
    timer_args = MockTimer.call_args.args
    callback_func = timer_args[1]
    callback_args = timer_args[2]

    # Simulate the second timer expiring
    callback_func(*callback_args)

    mock_db_manager.add_case.assert_called_once_with(str(new_dir_path))


@patch("src.services.case_scanner.Observer")
def test_case_scanner_integration(MockObserver, mock_db_manager: Mock, temp_watch_dir: Path):
    """
    Tests the CaseScanner class integration with the observer.
    """
    # Arrange
    scanner = CaseScanner(watch_path=str(temp_watch_dir), db_manager=mock_db_manager)
    mock_observer_instance = MockObserver.return_value

    # Act
    scanner.start()
    scanner.stop()

    # Assert
    mock_observer_instance.schedule.assert_called_once()
    mock_observer_instance.start.assert_called_once()
    mock_observer_instance.stop.assert_called_once()
    mock_observer_instance.join.assert_called_once()
