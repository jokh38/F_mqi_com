"""
Test module for enhanced dashboard features including filtering, search, and export
functionality.
"""

import unittest
import tempfile
import os
import json
import csv
from datetime import datetime
from pathlib import Path

from src.dashboard import (
    DashboardFilter,
    filter_cases,
    search_cases,
    export_to_csv,
    export_to_json,
    format_dashboard_snapshot,
    get_utilization_statistics,
    export_utilization_statistics,
)


class TestDashboardFilter(unittest.TestCase):
    """Test cases for DashboardFilter class."""

    def test_dashboard_filter_initialization_with_defaults(self):
        """Test DashboardFilter initialization with default values."""
        filter_obj = DashboardFilter()

        self.assertIsNone(filter_obj.status_filter)
        self.assertIsNone(filter_obj.gpu_group_filter)
        self.assertIsNone(filter_obj.date_from)
        self.assertIsNone(filter_obj.date_to)
        self.assertEqual(filter_obj.search_term, "")

    def test_dashboard_filter_initialization_with_custom_values(self):
        """Test DashboardFilter initialization with custom values."""
        date_from = datetime(2025, 1, 1)
        date_to = datetime(2025, 1, 31)

        filter_obj = DashboardFilter(
            status_filter="running",
            gpu_group_filter="gpu0",
            date_from=date_from,
            date_to=date_to,
            search_term="test_case",
        )

        self.assertEqual(filter_obj.status_filter, "running")
        self.assertEqual(filter_obj.gpu_group_filter, "gpu0")
        self.assertEqual(filter_obj.date_from, date_from)
        self.assertEqual(filter_obj.date_to, date_to)
        self.assertEqual(filter_obj.search_term, "test_case")


class TestCaseFiltering(unittest.TestCase):
    """Test cases for case filtering functionality."""

    def setUp(self):
        """Set up test cases data."""
        self.test_cases = [
            {
                "case_id": 1,
                "case_path": "/path/to/case1",
                "status": "running",
                "pueue_group": "gpu0",
                "submitted_at": "2025-01-15 10:30:00",
                "progress": 50,
            },
            {
                "case_id": 2,
                "case_path": "/path/to/case2",
                "status": "completed",
                "pueue_group": "gpu1",
                "submitted_at": "2025-01-16 14:20:00",
                "progress": 100,
            },
            {
                "case_id": 3,
                "case_path": "/path/to/test_case3",
                "status": "failed",
                "pueue_group": "gpu0",
                "submitted_at": "2025-01-17 09:15:00",
                "progress": 25,
            },
        ]

    def test_filter_cases_by_status(self):
        """Test filtering cases by status."""
        filter_obj = DashboardFilter(status_filter="running")
        filtered = filter_cases(self.test_cases, filter_obj)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["case_id"], 1)
        self.assertEqual(filtered[0]["status"], "running")

    def test_filter_cases_by_gpu_group(self):
        """Test filtering cases by GPU group."""
        filter_obj = DashboardFilter(gpu_group_filter="gpu0")
        filtered = filter_cases(self.test_cases, filter_obj)

        self.assertEqual(len(filtered), 2)
        self.assertIn(filtered[0]["case_id"], [1, 3])
        self.assertIn(filtered[1]["case_id"], [1, 3])

    def test_filter_cases_by_date_range(self):
        """Test filtering cases by date range."""
        date_from = datetime(2025, 1, 16)
        date_to = datetime(2025, 1, 17, 23, 59, 59)

        filter_obj = DashboardFilter(date_from=date_from, date_to=date_to)
        filtered = filter_cases(self.test_cases, filter_obj)

        self.assertEqual(len(filtered), 2)
        self.assertIn(filtered[0]["case_id"], [2, 3])
        self.assertIn(filtered[1]["case_id"], [2, 3])

    def test_search_cases_by_path(self):
        """Test searching cases by path."""
        filter_obj = DashboardFilter(search_term="test_case")
        filtered = search_cases(self.test_cases, filter_obj)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["case_id"], 3)
        self.assertIn("test_case", filtered[0]["case_path"])

    def test_search_cases_by_id(self):
        """Test searching cases by ID."""
        filter_obj = DashboardFilter(search_term="2")
        filtered = search_cases(self.test_cases, filter_obj)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["case_id"], 2)


class TestDashboardExport(unittest.TestCase):
    """Test cases for dashboard export functionality."""

    def setUp(self):
        """Set up test data."""
        self.test_cases = [
            {
                "case_id": 1,
                "case_path": "/path/to/case1",
                "status": "running",
                "pueue_group": "gpu0",
                "submitted_at": "2025-01-15 10:30:00",
                "progress": 50,
                "pueue_task_id": 101,
                "status_updated_at": "2025-01-15 11:30:00",
            },
            {
                "case_id": 2,
                "case_path": "/path/to/case2",
                "status": "completed",
                "pueue_group": "gpu1",
                "submitted_at": "2025-01-16 14:20:00",
                "progress": 100,
                "pueue_task_id": 102,
                "status_updated_at": "2025-01-16 16:45:00",
            },
        ]

        self.test_resources = [
            {"pueue_group": "gpu0", "status": "assigned", "assigned_case_id": 1},
            {"pueue_group": "gpu1", "status": "available", "assigned_case_id": None},
        ]

    def test_export_to_csv_creates_valid_file(self):
        """Test CSV export creates a valid file with correct content."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as temp_file:
            temp_path = temp_file.name

        try:
            export_to_csv(self.test_cases, temp_path)

            # Verify file exists
            self.assertTrue(Path(temp_path).exists())

            # Verify content
            with open(temp_path, "r", newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                rows = list(reader)

                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["case_id"], "1")
                self.assertEqual(rows[0]["status"], "running")
                self.assertEqual(rows[1]["case_id"], "2")
                self.assertEqual(rows[1]["status"], "completed")

        finally:
            if Path(temp_path).exists():
                os.unlink(temp_path)

    def test_export_to_json_creates_valid_file(self):
        """Test JSON export creates a valid file with correct content."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as temp_file:
            temp_path = temp_file.name

        try:
            export_to_json(self.test_cases, self.test_resources, temp_path)

            # Verify file exists
            self.assertTrue(Path(temp_path).exists())

            # Verify content
            with open(temp_path, "r") as jsonfile:
                data = json.load(jsonfile)

                self.assertIn("cases", data)
                self.assertIn("resources", data)
                self.assertIn("exported_at", data)

                self.assertEqual(len(data["cases"]), 2)
                self.assertEqual(len(data["resources"]), 2)

                self.assertEqual(data["cases"][0]["case_id"], 1)
                self.assertEqual(data["cases"][0]["status"], "running")

        finally:
            if Path(temp_path).exists():
                os.unlink(temp_path)

    def test_format_dashboard_snapshot_returns_formatted_text(self):
        """Test dashboard snapshot formatting."""
        snapshot = format_dashboard_snapshot(self.test_cases, self.test_resources)

        self.assertIsInstance(snapshot, str)
        self.assertIn("MQI Communicator Dashboard Snapshot", snapshot)
        self.assertIn("Case Summary", snapshot)
        self.assertIn("Resource Summary", snapshot)
        self.assertIn("case1", snapshot)
        self.assertIn("gpu0", snapshot)


class TestUtilizationStatistics(unittest.TestCase):
    """Test cases for utilization statistics functionality."""

    def setUp(self):
        """Set up test data."""
        self.test_cases = [
            {"case_id": 1, "status": "running", "progress": 50},
            {"case_id": 2, "status": "completed", "progress": 100},
            {"case_id": 3, "status": "failed", "progress": 25},
        ]

        self.test_resources = [
            {"pueue_group": "gpu0", "status": "assigned"},
            {"pueue_group": "gpu1", "status": "available"},
        ]

    def test_get_utilization_statistics_with_data(self):
        """Test utilization statistics calculation with data."""
        stats = get_utilization_statistics(self.test_cases, self.test_resources)

        self.assertEqual(stats["total_cases"], 3)
        self.assertEqual(stats["average_progress"], 58.33)  # (50+100+25)/3 = 58.33
        self.assertEqual(stats["completion_rate"], 33.33)  # 1/3 = 33.33

        # Status distribution
        expected_status = {"running": 1, "completed": 1, "failed": 1}
        self.assertEqual(stats["status_distribution"], expected_status)

        # Resource utilization
        expected_resources = {
            "gpu0": {"available": 0, "assigned": 1},
            "gpu1": {"available": 1, "assigned": 0},
        }
        self.assertEqual(stats["resource_utilization"], expected_resources)

        # Check timestamp
        self.assertIn("generated_at", stats)

    def test_get_utilization_statistics_with_empty_data(self):
        """Test utilization statistics with no cases."""
        stats = get_utilization_statistics([], [])

        self.assertEqual(stats["total_cases"], 0)
        self.assertEqual(stats["average_progress"], 0)
        self.assertEqual(stats["completion_rate"], 0)
        self.assertEqual(stats["status_distribution"], {})
        self.assertEqual(stats["resource_utilization"], {})

    def test_export_utilization_statistics_creates_file(self):
        """Test statistics export creates a valid file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as temp_file:
            temp_path = temp_file.name

        try:
            export_utilization_statistics(
                self.test_cases, self.test_resources, temp_path
            )

            # Verify file exists and has valid content
            self.assertTrue(Path(temp_path).exists())

            with open(temp_path, "r") as jsonfile:
                stats = json.load(jsonfile)

                self.assertEqual(stats["total_cases"], 3)
                self.assertIn("generated_at", stats)
                self.assertIn("status_distribution", stats)
                self.assertIn("resource_utilization", stats)

        finally:
            if Path(temp_path).exists():
                os.unlink(temp_path)


if __name__ == "__main__":
    unittest.main()
