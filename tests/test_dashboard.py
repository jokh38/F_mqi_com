"""
Tests for the dashboard module.
"""

import unittest
from unittest.mock import patch, MagicMock, mock_open

import yaml
from rich.layout import Layout

from src.dashboard import display_dashboard

# Sample data that mimics the database output
MOCK_CASE_DATA = [
    {
        "case_id": 1,
        "case_path": "/path/to/case_001",
        "status": "running",
        "progress": 50,
        "pueue_group": "gpu_a",
        "pueue_task_id": 101,
        "submitted_at": "2023-10-27T10:00:00",
        "status_updated_at": "2023-10-27T10:05:00",
    }
]

MOCK_RESOURCE_DATA = [
    {"pueue_group": "gpu_a", "status": "assigned", "assigned_case_id": 1},
    {"pueue_group": "gpu_b", "status": "available", "assigned_case_id": None},
]

MOCK_CONFIG = {"database": {"path": "dummy/path/to/db.sqlite"}}
MOCK_CONFIG_YAML = yaml.dump(MOCK_CONFIG)


@patch("src.dashboard.time.sleep")
@patch("src.dashboard.Live")
@patch("src.dashboard.Console")
@patch("src.dashboard.DatabaseManager")
@patch("builtins.open", new_callable=mock_open, read_data=MOCK_CONFIG_YAML)
@patch("pathlib.Path.exists", return_value=True)
def test_display_dashboard_live_update(
    mock_exists: MagicMock,
    mock_open_file: MagicMock,
    mock_db_manager_cls: MagicMock,
    mock_console_cls: MagicMock,
    mock_live_cls: MagicMock,
    mock_sleep: MagicMock,
):
    """
    Tests that the dashboard correctly initializes, fetches data,
    and updates the live display in a loop.
    """
    # Arrange
    # Mock the console and live display
    mock_console = MagicMock()
    mock_console_cls.return_value = mock_console
    mock_live_context = MagicMock()
    mock_live_cls.return_value.__enter__.return_value = mock_live_context

    # Mock the DatabaseManager instance and its cursor methods
    mock_db_instance = MagicMock()
    mock_db_manager_cls.return_value = mock_db_instance

    # The execute method returns an object that has a fetchall method.
    # We mock this chain: execute() -> returns mock_cursor -> mock_cursor.fetchall() -> returns data
    mock_cursor = MagicMock()
    mock_db_instance.cursor.execute.return_value = mock_cursor
    # Configure fetchall to return the different data sets on subsequent calls
    mock_cursor.fetchall.side_effect = [
        MOCK_CASE_DATA,
        MOCK_RESOURCE_DATA,
    ]

    # To stop the infinite loop, we make time.sleep raise an exception
    # after the first call.
    mock_sleep.side_effect = KeyboardInterrupt("Stopping test loop")

    # Act
    display_dashboard()

    # Assert
    # 1. Config and DB initialization
    mock_open_file.assert_called_once_with("config/config.yaml", "r")
    mock_exists.assert_called_once()
    mock_db_manager_cls.assert_called_once_with(db_path=MOCK_CONFIG["database"]["path"])

    # 2. Live display was set up
    mock_live_cls.assert_called_once()

    # 3. Data was fetched from the database
    assert mock_db_instance.cursor.execute.call_count == 2
    mock_db_instance.cursor.execute.assert_any_call(
        "SELECT * FROM cases ORDER BY case_id DESC"
    )
    mock_db_instance.cursor.execute.assert_any_call(
        "SELECT * FROM gpu_resources ORDER BY pueue_group"
    )
    assert mock_cursor.fetchall.call_count == 2


    # 4. Live display was updated with a Layout
    args, kwargs = mock_live_context.update.call_args
    assert len(args) == 1
    assert isinstance(
        args[0], Layout
    ), f"Live display should be updated with a Layout, not {type(args[0])}"

    # 5. Loop ran once before being interrupted
    mock_sleep.assert_called_once_with(2)

    # 6. DB connection was closed
    mock_db_instance.close.assert_called_once()


@patch("src.dashboard.time.sleep")
@patch("src.dashboard.Live")
@patch("src.dashboard.Console")
@patch("builtins.open", new_callable=mock_open, read_data=MOCK_CONFIG_YAML)
@patch("pathlib.Path.exists", return_value=False)  # DB does NOT exist
def test_display_dashboard_handles_no_db_file(
    mock_exists: MagicMock,
    mock_open_file: MagicMock,
    mock_console_cls: MagicMock,
    mock_live_cls: MagicMock,
    mock_sleep: MagicMock,
):
    """
    Tests that the dashboard shows a warning and an empty table
    if the database file does not exist.
    """
    # Arrange
    mock_console = MagicMock()
    mock_console_cls.return_value = mock_console
    mock_live_context = MagicMock()
    mock_live_cls.return_value.__enter__.return_value = mock_live_context

    # Act
    display_dashboard()

    # Assert
    # 1. Checks for config and that the DB path does not exist
    mock_open_file.assert_called_once_with("config/config.yaml", "r")
    mock_exists.assert_called_once()

    # 2. Prints a warning message
    mock_console.print.assert_any_call(
        "[bold yellow]Database file not found at 'dummy/path/to/db.sqlite'.[/bold yellow]"
    )

    # 3. Shows an empty dashboard for a few seconds
    mock_live_cls.assert_called_once()
    args, kwargs = mock_live_cls.call_args
    assert isinstance(args[0], Layout)
    mock_sleep.assert_called_once_with(5)
