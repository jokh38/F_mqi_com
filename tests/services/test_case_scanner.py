import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from watchdog.events import DirCreatedEvent

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
