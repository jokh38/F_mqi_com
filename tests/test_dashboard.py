"""
Tests for the dashboard module.
"""

import unittest
from unittest.mock import patch, MagicMock
from src.dashboard import display_dashboard


class TestDashboard(unittest.TestCase):
    """Test suite for the dashboard display."""

    @patch("src.dashboard.time.sleep")
    @patch("src.dashboard.Live")
    @patch("src.dashboard.Console")
    def test_display_dashboard_runs_without_error(
        self, mock_console, mock_live, mock_sleep
    ):
        """
        Tests that display_dashboard can be called without raising an exception.
        """
        # Arrange: Mock the console and live display to avoid actual screen output
        mock_console_instance = MagicMock()
        mock_console.return_value = mock_console_instance

        mock_live_instance = MagicMock()
        mock_live.return_value.__enter__.return_value = mock_live_instance

        # Act & Assert: The main check is that this call does not raise an error
        try:
            display_dashboard()
        except Exception as e:
            self.fail(f"display_dashboard() raised an unexpected exception: {e}")

        # Assert: Verify that our mocked objects were called
        mock_console_instance.print.assert_called()
        mock_live.assert_called_once()
        mock_sleep.assert_called_once_with(5)
