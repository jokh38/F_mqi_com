import pytest
from unittest.mock import patch, MagicMock
import logging

from src.main import main

@pytest.fixture
def mock_config():
    """Provides a default mock config for tests."""
    return {
        "logging": {"path": "test.log"},
        "database": {"path": "test.db"},
        "scanner": {"watch_path": "test_cases"},
        "pueue": {"default_group": "test_group"},
        "hpc": {
            "host": "test_host",
            "user": "test_user",
            "remote_base_dir": "/remote/test",
        },
        "main_loop": {"sleep_interval_seconds": 0.1},
    }

@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("builtins.open")
@patch("src.main.yaml.safe_load")
def test_main_loop_handles_running_case_success(
    mock_safe_load, mock_open, MockDatabaseManager, MockCaseScanner, MockWorkflowSubmitter, mock_sleep, mock_config
):
    """Tests a 'running' case that completes successfully."""
    mock_safe_load.return_value = mock_config
    mock_db = MockDatabaseManager.return_value
    mock_submitter = MockWorkflowSubmitter.return_value
    running_case = {"case_id": 1, "pueue_task_id": 101}
    mock_db.get_cases_by_status.side_effect = [[running_case], [], KeyboardInterrupt]
    mock_submitter.get_workflow_status.return_value = "success"
    MockCaseScanner.return_value.observer.is_alive.return_value = True

    main()

    mock_submitter.get_workflow_status.assert_called_once_with(101)
    mock_db.update_case_completion.assert_called_once_with(1, status="completed")
    MockCaseScanner.return_value.stop.assert_called_once()


@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("builtins.open")
@patch("src.main.yaml.safe_load")
def test_main_loop_handles_running_case_failure(
    mock_safe_load, mock_open, MockDatabaseManager, MockCaseScanner, MockWorkflowSubmitter, mock_sleep, mock_config
):
    """Tests a 'running' case that fails."""
    mock_safe_load.return_value = mock_config
    mock_db = MockDatabaseManager.return_value
    mock_submitter = MockWorkflowSubmitter.return_value
    running_case = {"case_id": 2, "pueue_task_id": 102}
    mock_db.get_cases_by_status.side_effect = [[running_case], [], KeyboardInterrupt]
    mock_submitter.get_workflow_status.return_value = "failure"
    MockCaseScanner.return_value.observer.is_alive.return_value = True

    main()

    mock_submitter.get_workflow_status.assert_called_once_with(102)
    mock_db.update_case_completion.assert_called_once_with(2, status="failed")


@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("builtins.open")
@patch("src.main.yaml.safe_load")
def test_main_loop_handles_running_case_not_found(
    mock_safe_load, mock_open, MockDatabaseManager, MockCaseScanner, MockWorkflowSubmitter, mock_sleep, mock_config
):
    """Tests a 'running' case that is not found in Pueue."""
    mock_safe_load.return_value = mock_config
    mock_db = MockDatabaseManager.return_value
    mock_submitter = MockWorkflowSubmitter.return_value
    running_case = {"case_id": 3, "pueue_task_id": 103}
    mock_db.get_cases_by_status.side_effect = [[running_case], [], KeyboardInterrupt]
    mock_submitter.get_workflow_status.return_value = "not_found"
    MockCaseScanner.return_value.observer.is_alive.return_value = True

    main()

    mock_submitter.get_workflow_status.assert_called_once_with(103)
    mock_db.update_case_completion.assert_called_once_with(3, status="failed")


@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("builtins.open")
@patch("src.main.yaml.safe_load")
def test_main_loop_handles_submitted_case_success(
    mock_safe_load, mock_open, MockDatabaseManager, MockCaseScanner, MockWorkflowSubmitter, mock_sleep, mock_config
):
    """Tests the submission of a new case."""
    mock_safe_load.return_value = mock_config
    mock_db = MockDatabaseManager.return_value
    mock_submitter = MockWorkflowSubmitter.return_value
    submitted_case = {"case_id": 4, "case_path": "/path/new", "pueue_group": "default"}
    mock_db.get_cases_by_status.side_effect = [[], [submitted_case], KeyboardInterrupt]
    mock_submitter.submit_workflow.return_value = 201
    MockCaseScanner.return_value.observer.is_alive.return_value = True

    main()

    mock_submitter.submit_workflow.assert_called_once_with(case_path="/path/new", pueue_group="default")
    mock_db.update_case_pueue_task_id.assert_called_once_with(4, 201)
    mock_db.update_case_status.assert_called_with(4, status="running", progress=30)


@patch("src.main.time.sleep")
@patch("src.main.WorkflowSubmitter")
@patch("src.main.CaseScanner")
@patch("src.main.DatabaseManager")
@patch("builtins.open")
@patch("src.main.yaml.safe_load")
def test_main_loop_handles_submission_and_id_failure(
    mock_safe_load, mock_open, MockDatabaseManager, MockCaseScanner, MockWorkflowSubmitter, mock_sleep, mock_config
):
    """Tests when workflow submission returns no task ID."""
    mock_safe_load.return_value = mock_config
    mock_db = MockDatabaseManager.return_value
    mock_submitter = MockWorkflowSubmitter.return_value
    submitted_case = {"case_id": 5, "case_path": "/path/fail", "pueue_group": "default"}
    mock_db.get_cases_by_status.side_effect = [[], [submitted_case], KeyboardInterrupt]
    mock_submitter.submit_workflow.return_value = None
    MockCaseScanner.return_value.observer.is_alive.return_value = True

    main()

    mock_submitter.submit_workflow.assert_called_once_with(case_path="/path/fail", pueue_group="default")
    mock_db.update_case_completion.assert_called_once_with(5, status="failed")
