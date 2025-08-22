"""
Tests for the dashboard module.
"""

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
    # (initial load: cases, resources, then refresh: cases, resources)
    mock_cursor.fetchall.side_effect = [
        MOCK_CASE_DATA,
        MOCK_RESOURCE_DATA,
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
    # Check that config file was opened (path may be absolute)
    assert any("config.yaml" in str(call) for call in mock_open_file.call_args_list)
    mock_exists.assert_called_once()
    # Check that DatabaseManager was called with a path containing the expected database file
    call_args = mock_db_manager_cls.call_args
    assert "db.sqlite" in call_args.kwargs["db_path"]

    # 2. Live display was set up
    mock_live_cls.assert_called_once()

    # 3. Data was fetched from the database (initial load + one refresh)
    assert mock_db_instance.cursor.execute.call_count == 4
    mock_db_instance.cursor.execute.assert_any_call(
        "SELECT * FROM cases ORDER BY case_id DESC"
    )
    mock_db_instance.cursor.execute.assert_any_call(
        "SELECT * FROM gpu_resources ORDER BY pueue_group"
    )
    assert mock_cursor.fetchall.call_count == 4

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
    # Check that config file was opened (path may be absolute)
    assert any("config.yaml" in str(call) for call in mock_open_file.call_args_list)
    mock_exists.assert_called_once()

    # 2. Prints a warning message
    # Check that a warning message was printed (path may be absolute)
    print_calls = [str(call) for call in mock_console.print.call_args_list]
    assert any("Database file not found" in call for call in print_calls)

    # 3. Does not create Live display (returns early when DB doesn't exist)
    mock_live_cls.assert_not_called()
    # Does not sleep (returns early)
    mock_sleep.assert_not_called()
