import subprocess
from unittest.mock import patch, MagicMock, call
import pytest
import shlex
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

    def test_submit_workflow_success(self, mock_config):
        """Test the successful submission of a workflow."""
        submitter = WorkflowSubmitter(config=mock_config)
        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")

            submitter.submit_workflow(case_path, pueue_group="test_group")

            # Define expected commands based on the security-hardened implementation
            remote_path = "/remote/base/dir/case_001"
            scp_command = [
                "scp",
                "-r",
                case_path,
                "test_user@test_host:/remote/base/dir",
            ]
            remote_command = f"cd {shlex.quote(remote_path)} && python sim.py"
            ssh_command = [
                "ssh",
                "test_user@test_host",
                "pueue",
                "add",
                "--group",
                "test_group",
                "--",
                remote_command,
            ]

            # Check if subprocess.run was called correctly
            calls = [
                call(
                    scp_command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300,
                ),
                call(
                    ssh_command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                ),
            ]
            mock_run.assert_has_calls(calls, any_order=False)

    def test_submit_workflow_scp_failure(self, mock_config):
        """Test that workflow submission fails if scp command fails."""
        submitter = WorkflowSubmitter(config=mock_config)
        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd="scp", stderr="SCP failed"
            )

            with pytest.raises(WorkflowSubmissionError) as excinfo:
                submitter.submit_workflow(case_path)

            assert "Failed to copy case" in str(excinfo.value)
            assert "SCP failed" in str(excinfo.value)
            mock_run.assert_called_once()

    def test_submit_workflow_ssh_failure(self, mock_config):
        """Test that workflow submission fails if ssh command fails."""
        submitter = WorkflowSubmitter(config=mock_config)
        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_run:
            # Mock a successful scp followed by a failed ssh
            mock_run.side_effect = [
                MagicMock(returncode=0),  # Successful scp
                subprocess.CalledProcessError(
                    returncode=1, cmd="ssh", stderr="SSH failed"
                ),
            ]

            with pytest.raises(WorkflowSubmissionError) as excinfo:
                submitter.submit_workflow(case_path)

            assert "Failed to submit job" in str(excinfo.value)
            assert "SSH failed" in str(excinfo.value)
            assert mock_run.call_count == 2

    def test_submit_workflow_scp_timeout(self, mock_config):
        """Test that workflow submission fails if scp command times out."""
        submitter = WorkflowSubmitter(config=mock_config)
        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="scp", timeout=300)

            with pytest.raises(WorkflowSubmissionError) as excinfo:
                submitter.submit_workflow(case_path)

            assert "Timeout during scp" in str(excinfo.value)
            mock_run.assert_called_once()

    def test_submit_workflow_ssh_timeout(self, mock_config):
        """Test that workflow submission fails if ssh command times out."""
        submitter = WorkflowSubmitter(config=mock_config)
        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_run:
            # Mock a successful scp followed by a timed-out ssh
            mock_run.side_effect = [
                MagicMock(returncode=0),  # Successful scp
                subprocess.TimeoutExpired(cmd="ssh", timeout=60),
            ]

            with pytest.raises(WorkflowSubmissionError) as excinfo:
                submitter.submit_workflow(case_path)

            assert "Timeout during ssh submission" in str(excinfo.value)
            assert mock_run.call_count == 2
