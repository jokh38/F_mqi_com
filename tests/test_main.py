import pytest
from unittest.mock import patch, MagicMock, call
import logging
from datetime import datetime, timezone

from src.main import main

@pytest.fixture(autouse=True)
def mute_logging():
    """Fixture to mute logging output during tests."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)

@pytest.fixture
def mock_config():
    """Provides a default mock config for tests, reflecting the new structure."""
    return {
        "logging": {"path": "test.log"},
        "database": {"path": "test.db"},
        "scanner": {"watch_path": "test_cases"},
        "pueue": {"groups": ["gpu_a", "gpu_b"]},  # Use 'groups' instead of 'default_group'
        "hpc": {
            "host": "test_host",
            "user": "test_user",
            "remote_base_dir": "/remote/test",
        },
        "main_loop": {"sleep_interval_seconds": 0.01}, # Use a very short sleep
    }

# --- Mocks Fixture ---
@pytest.fixture
def mock_dependencies(mock_config):
    """A single fixture to manage all patched dependencies."""
    with patch("src.main.time.sleep") as mock_sleep, \
         patch("src.main.WorkflowSubmitter") as MockWorkflowSubmitter, \
         patch("src.main.CaseScanner") as MockCaseScanner, \
         patch("src.main.DatabaseManager") as MockDatabaseManager, \
         patch("builtins.open") as mock_open, \
         patch("src.main.yaml.safe_load") as mock_safe_load:

        mock_safe_load.return_value = mock_config

        # Make mocks accessible
        mocks = {
            "sleep": mock_sleep,
            "WorkflowSubmitter": MockWorkflowSubmitter,
            "CaseScanner": MockCaseScanner,
            "DatabaseManager": MockDatabaseManager,
            "open": mock_open,
            "safe_load": mock_safe_load,
            "db": MockDatabaseManager.return_value,
            "scanner": MockCaseScanner.return_value,
            "submitter": MockWorkflowSubmitter.return_value
        }

        # Default behavior for a clean shutdown
        mocks["scanner"].observer.is_alive.return_value = True
        mocks["config"] = mock_config

        yield mocks

# --- Tests for RUNNING cases (largely unchanged) ---

def test_main_loop_handles_running_case_success(mock_dependencies):
    """Tests a 'running' case that completes successfully."""
    mocks = mock_dependencies
    now = datetime.now(timezone.utc).isoformat()
    running_case = {"case_id": 1, "pueue_task_id": 101, "status_updated_at": now}
    # Loop 1: No stuck, one running, no submitted. Loop 2: Clean exit.
    mocks["db"].get_cases_by_status.side_effect = [
        [],  # For stuck cases
        [running_case],  # For running cases timeout check
        [running_case],  # For running cases status check
        [],  # For submitted cases
        SystemExit,
    ]
    mocks["submitter"].get_workflow_status.return_value = "success"

    with pytest.raises(SystemExit):
        main(mocks["config"])

    mocks["submitter"].get_workflow_status.assert_called_once_with(101)
    mocks["db"].update_case_completion.assert_called_once_with(1, status="completed")
    mocks["scanner"].stop.assert_called_once()

def test_main_loop_handles_running_case_failure(mock_dependencies):
    """Tests a 'running' case that fails."""
    mocks = mock_dependencies
    now = datetime.now(timezone.utc).isoformat()
    running_case = {"case_id": 2, "pueue_task_id": 102, "status_updated_at": now}
    # Loop 1: No stuck, one running, no submitted. Loop 2: Clean exit.
    mocks["db"].get_cases_by_status.side_effect = [
        [],  # For stuck cases
        [running_case],  # For running cases timeout check
        [running_case],  # For running cases status check
        [],  # For submitted cases
        SystemExit,
    ]
    mocks["submitter"].get_workflow_status.return_value = "failure"

    with pytest.raises(SystemExit):
        main(mocks["config"])

    mocks["submitter"].get_workflow_status.assert_called_once_with(102)
    mocks["db"].update_case_completion.assert_called_once_with(2, status="failed")


def test_main_loop_times_out_case_before_status_check(mock_dependencies):
    """
    Tests that a case is timed out based on its own timestamp, even if the
    HPC was previously unreachable. The timeout check should happen before
    the remote status check.
    """
    mocks = mock_dependencies
    # Set a very short timeout for this test
    mocks["config"]["main_loop"]["running_case_timeout_hours"] = 0.01  # 36 seconds
    from datetime import timedelta
    # Create a timestamp that is definitely older than the timeout
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    timed_out_case = {"case_id": 3, "pueue_task_id": 103, "status_updated_at": old_timestamp}

    # First loop, check for stuck cases, then check for running cases
    mocks["db"].get_cases_by_status.side_effect = [
        [],  # No stuck cases
        [timed_out_case],  # One running case that should be timed out
        SystemExit, # Exit after one loop
    ]

    with pytest.raises(SystemExit):
        main(mocks["config"])

    # CRITICAL: Verify that we never even tried to check the remote status
    mocks["submitter"].get_workflow_status.assert_not_called()

    # Verify that the case was marked as failed due to timeout
    mocks["db"].update_case_completion.assert_called_once_with(3, status="failed")
    mocks["db"].release_gpu_resource.assert_called_once_with(3)


# --- Tests for SUBMITTED cases (rewritten for dynamic allocation) ---

def test_main_loop_submits_case_with_available_gpu(mock_dependencies):
    """
    Tests the new dynamic submission process:
    1. A submitted case is found.
    2. An available GPU is found and locked.
    3. The case is submitted to that GPU's group.
    """
    mocks = mock_dependencies
    submitted_case = {"case_id": 4, "case_path": "/path/new"}
    # Loop 1: No stuck, no running, one submitted. Loop 2: Clean exit.
    mocks["db"].get_cases_by_status.side_effect = [
        [], [], [submitted_case], SystemExit
    ]

    # Simulate that no resource is currently assigned
    mocks["db"].get_gpu_resource_by_case_id.return_value = None

    # Simulate a successful GPU lock
    mocks["db"].find_and_lock_any_available_gpu.return_value = "gpu_b"
    mocks["submitter"].submit_workflow.return_value = 201

    with pytest.raises(SystemExit):
        main(mocks["config"])

    # Verify the dynamic allocation logic
    mocks["db"].find_and_lock_any_available_gpu.assert_called_once_with(4)
    mocks["db"].update_case_pueue_group.assert_called_once_with(4, "gpu_b")

    # Verify the submission
    mocks["submitter"].submit_workflow.assert_called_once_with(
        case_id=4, case_path="/path/new", pueue_group="gpu_b"
    )
    mocks["db"].update_case_pueue_task_id.assert_called_once_with(4, 201)

    # Verify status updates
    assert call(4, status="submitting", progress=10) in mocks["db"].update_case_status.call_args_list
    assert call(4, status="running", progress=30) in mocks["db"].update_case_status.call_args_list

def test_main_loop_defers_submission_when_no_gpu_available(mock_dependencies):
    """
    Tests that if no GPU is available, the case is not submitted and the system
    waits for the next cycle.
    """
    mocks = mock_dependencies
    submitted_case = {"case_id": 5, "case_path": "/path/wait"}
    # Loop 1: No stuck, no running, one submitted. Loop 2: Clean exit.
    mocks["db"].get_cases_by_status.side_effect = [
        [], [], [submitted_case], SystemExit
    ]

    # Simulate that no resource is currently assigned
    mocks["db"].get_gpu_resource_by_case_id.return_value = None

    # Simulate NO available GPU
    mocks["db"].find_and_lock_any_available_gpu.return_value = None

    with pytest.raises(SystemExit):
        main(mocks["config"])

    # Verify we checked for a GPU
    mocks["db"].find_and_lock_any_available_gpu.assert_called_once_with(5)

    # CRITICAL: Verify no further action was taken
    mocks["db"].update_case_pueue_group.assert_not_called()
    mocks["submitter"].submit_workflow.assert_not_called()
    mocks["db"].update_case_status.assert_not_called()

def test_main_loop_handles_submission_id_failure(mock_dependencies):
    """
    Tests that if a workflow is submitted but parsing the ID fails,
    the case is marked as 'failed' and the GPU is released.
    """
    mocks = mock_dependencies
    submitted_case = {"case_id": 6, "case_path": "/path/id_fail"}
    # Loop 1: No stuck, no running, one submitted. Loop 2: Clean exit.
    mocks["db"].get_cases_by_status.side_effect = [
        [], [], [submitted_case], SystemExit
    ]

    # Simulate that no resource is currently assigned
    mocks["db"].get_gpu_resource_by_case_id.return_value = None

    # Simulate a successful lock but a failed submission (no ID returned)
    mocks["db"].find_and_lock_any_available_gpu.return_value = "gpu_a"
    mocks["submitter"].submit_workflow.return_value = None

    with pytest.raises(SystemExit):
        main(mocks["config"])

    # Verify lock and submission attempt
    mocks["db"].find_and_lock_any_available_gpu.assert_called_once_with(6)
    mocks["submitter"].submit_workflow.assert_called_once_with(
        case_id=6, case_path="/path/id_fail", pueue_group="gpu_a"
    )

    # Verify failure handling
    mocks["db"].update_case_completion.assert_called_once_with(6, status="failed")
    mocks["db"].release_gpu_resource.assert_called_once_with(6)
