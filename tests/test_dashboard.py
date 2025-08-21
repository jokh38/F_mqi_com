"""
Tests for the dashboard module.
"""

from unittest.mock import patch, MagicMock
from rich.table import Table

from src.dashboard import display_dashboard


@patch("src.dashboard.time.sleep")
@patch("src.dashboard.Live")
@patch("src.dashboard.Console")
def test_display_dashboard_creates_and_renders_table(
    mock_console_cls: MagicMock, mock_live_cls: MagicMock, mock_sleep: MagicMock
):
    """
    Tests that display_dashboard:
    1. Runs without raising an exception (pytest handles this automatically).
    2. Creates a rich Table with the correct columns.
    3. Passes the table to a Live display context.
    """
    # Arrange
    mock_console = MagicMock()
    mock_console_cls.return_value = mock_console

    # The Live context manager needs to be mocked
    mock_live_context = MagicMock()
    mock_live_cls.return_value.__enter__.return_value = mock_live_context

    # Act
    display_dashboard()

    # Assert
    # 1. Check that a Console and Live display were instantiated.
    mock_console_cls.assert_called_once()
    mock_live_cls.assert_called_once()

    # 2. Find the Table object that was passed to Live.
    # mock_live_cls.call_args gives the args Live() was called with.
    args, kwargs = mock_live_cls.call_args
    assert len(args) > 0, "Live() was not called with a positional argument"
    rendered_table = args[0]

    # 3. Verify the table is a rich Table and has the correct columns.
    assert isinstance(
        rendered_table, Table
    ), f"Expected a Table object, but got {type(rendered_table)}"

    expected_columns = [
        "Case ID",
        "Case Path",
        "Status",
        "Progress",
        "Pueue Group",
    ]
    actual_columns = [col.header for col in rendered_table.columns]
    assert actual_columns == expected_columns, (
        f"Table columns are incorrect. Expected {expected_columns}, "
        f"but got {actual_columns}"
    )

    # 4. Verify the Live context was updated.
    mock_live_context.update.assert_called_once_with(rendered_table)

    # 5. Verify the sleep was called, indicating the loop ran.
    mock_sleep.assert_called_once_with(5)
