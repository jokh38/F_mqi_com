import subprocess
from unittest.mock import patch, MagicMock, call
import pytest
import shlex
import json
from src.services.workflow_submitter import (
    WorkflowSubmitter,
    WorkflowSubmissionError,
)


@pytest.fixture
def mock_config():
    """Fixture to provide a mock configuration for tests."""
    return {
        "hpc": {
            "host": "test_host",
            "user": "test_user",
            "remote_base_dir": "/remote/base/dir",
            "remote_command": "python sim.py",
        }
    }


class TestWorkflowSubmitter:
    """Test suite for the WorkflowSubmitter class."""

    def test_initialization(self, mock_config):
        """Test that WorkflowSubmitter initializes correctly."""
        submitter = WorkflowSubmitter(config=mock_config)
        assert submitter.hpc_config == mock_config["hpc"]

    def test_submit_workflow_success_returns_task_id(self, mock_config):
        """Test the successful submission of a workflow returns the parsed task ID."""
        submitter = WorkflowSubmitter(config=mock_config)
        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_run:
            # Simulate a successful scp and an ssh command that returns a task ID
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="Success", stderr=""),
                MagicMock(returncode=0, stdout="New task added (id: 123).", stderr=""),
            ]

            task_id = submitter.submit_workflow(case_path, pueue_group="test_group")

            assert task_id == 123
            assert mock_run.call_count == 2

    def test_submit_workflow_scp_failure(self, mock_config):
        """Test that workflow submission fails if scp command fails."""
        submitter = WorkflowSubmitter(config=mock_config)
        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd="scp", stderr="SCP failed"
            )

            with pytest.raises(WorkflowSubmissionError, match="Failed to copy case"):
                submitter.submit_workflow(case_path)
            mock_run.assert_called_once()

    def test_submit_workflow_ssh_failure(self, mock_config):
        """Test that workflow submission fails if ssh command fails."""
        submitter = WorkflowSubmitter(config=mock_config)
        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),
                subprocess.CalledProcessError(returncode=1, cmd="ssh", stderr="SSH failed"),
            ]

            with pytest.raises(WorkflowSubmissionError, match="Failed to submit job"):
                submitter.submit_workflow(case_path)
            assert mock_run.call_count == 2


class TestGetWorkflowStatus:
    """Test suite for the get_workflow_status method."""

    @pytest.fixture
    def submitter(self, mock_config):
        return WorkflowSubmitter(config=mock_config)

    def mock_pueue_status(self, tasks_dict):
        """Helper to create a mock subprocess result with a given tasks dictionary."""
        json_output = json.dumps({"tasks": tasks_dict})
        return MagicMock(returncode=0, stdout=json_output, stderr="")

    def test_get_status_success(self, submitter):
        """Test status is 'success' for a 'Done' task."""
        with patch("subprocess.run") as mock_run:
            # A 'Done' task must also have a 'success' result to be considered successful
            mock_run.return_value = self.mock_pueue_status(
                {"101": {"status": "Done", "result": "success"}}
            )
            status = submitter.get_workflow_status(101)
            assert status == "success"

    def test_get_status_failure(self, submitter):
        """Test status is 'failure' for a 'Failed' task."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = self.mock_pueue_status({"102": {"status": "Failed"}})
            status = submitter.get_workflow_status(102)
            assert status == "failure"

    def test_get_status_running(self, submitter):
        """Test status is 'running' for a 'Running' task."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = self.mock_pueue_status({"103": {"status": "Running"}})
            status = submitter.get_workflow_status(103)
            assert status == "running"

    def test_get_status_queued_is_running(self, submitter):
        """Test status is 'running' for a 'Queued' task."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = self.mock_pueue_status({"104": {"status": "Queued"}})
            status = submitter.get_workflow_status(104)
            assert status == "running"

    def test_get_status_not_found(self, submitter):
        """Test status is 'not_found' when the task ID is not in the output."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = self.mock_pueue_status({"200": {"status": "Done"}})
            status = submitter.get_workflow_status(105)
            assert status == "not_found"

    def test_get_status_ssh_failure_is_unreachable(self, submitter):
        """Test status is 'unreachable' if the ssh command fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "ssh")
            status = submitter.get_workflow_status(106)
            assert status == "unreachable"

    def test_get_status_timeout_is_unreachable(self, submitter):
        """Test status is 'unreachable' if the ssh command times out."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("ssh", timeout=60)
            status = submitter.get_workflow_status(107)
            assert status == "unreachable"

    def test_get_status_json_error_is_failure(self, submitter):
        """Test status is 'failure' if the output is not valid JSON."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
            status = submitter.get_workflow_status(107)
            assert status == "failure"

    def test_get_status_done_with_failure_result(self, submitter):
        """Test status is 'failure' for a 'Done' task with a 'failure' result."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = self.mock_pueue_status(
                {"108": {"status": "Done", "result": "failure"}}
            )
            status = submitter.get_workflow_status(108)
            assert status == "failure"
