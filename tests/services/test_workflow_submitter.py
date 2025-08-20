import subprocess
from unittest.mock import patch, MagicMock, call
import pytest
from src.services.workflow_submitter import WorkflowSubmitter


@pytest.fixture
def mock_config():
    """Fixture to provide a mock configuration for tests."""
    return {
        "hpc": {
            "host": "test_host",
            "user": "test_user",
            "remote_base_dir": "/remote/base/dir",
        }
    }


@patch("src.services.workflow_submitter.yaml.safe_load")
@patch("src.services.workflow_submitter.open")
class TestWorkflowSubmitter:
    """Test suite for the WorkflowSubmitter class."""

    def test_initialization(self, mock_open, mock_safe_load, mock_config):
        """Test that WorkflowSubmitter initializes correctly."""
        mock_safe_load.return_value = mock_config

        submitter = WorkflowSubmitter(config_path="dummy/path/config.yaml")

        mock_open.assert_called_once_with("dummy/path/config.yaml", "r")
        mock_safe_load.assert_called_once()

        assert submitter.hpc_config == mock_config["hpc"]

    def test_submit_workflow_success(self, mock_open, mock_safe_load, mock_config):
        """Test the successful submission of a workflow."""
        mock_safe_load.return_value = mock_config
        submitter = WorkflowSubmitter(config_path="dummy/path/config.yaml")

        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = MagicMock(
                returncode=0, stdout="Success", stderr=""
            )

            submitter.submit_workflow(case_path, pueue_group="test_group")

            # Define expected commands
            remote_case_path = "/remote/base/dir/case_001"
            scp_command = [
                "scp",
                "-r",
                case_path,
                "test_user@test_host:/remote/base/dir",
            ]

            pueue_command_str = (
                f"pueue add --group test_group -- "
                f"'cd {remote_case_path} && python interpreter.py'"
            )
            ssh_command = ["ssh", "test_user@test_host", pueue_command_str]

            # Check if subprocess.run was called correctly
            calls = [
                call(scp_command, check=True, capture_output=True, text=True),
                call(ssh_command, check=True, capture_output=True, text=True),
            ]
            mock_subprocess.assert_has_calls(calls, any_order=False)

    def test_submit_workflow_scp_failure(self, mock_open, mock_safe_load, mock_config):
        """Test that workflow submission fails if scp command fails."""
        mock_safe_load.return_value = mock_config
        submitter = WorkflowSubmitter(config_path="dummy/path/config.yaml")

        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_subprocess:
            # Mock a failed scp command by raising CalledProcessError
            mock_subprocess.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd="scp", stderr="SCP failed"
            )

            with pytest.raises(Exception) as excinfo:
                submitter.submit_workflow(case_path)

            assert "Failed to copy case" in str(excinfo.value)
            assert "SCP failed" in str(excinfo.value)
            mock_subprocess.assert_called_once()

    def test_submit_workflow_ssh_failure(self, mock_open, mock_safe_load, mock_config):
        """Test that workflow submission fails if ssh command fails."""
        mock_safe_load.return_value = mock_config
        submitter = WorkflowSubmitter(config_path="dummy/path/config.yaml")

        case_path = "/local/path/case_001"

        with patch("subprocess.run") as mock_subprocess:
            # Mock a successful scp followed by a failed ssh
            mock_subprocess.side_effect = [
                MagicMock(returncode=0),  # Successful scp
                subprocess.CalledProcessError(
                    returncode=1, cmd="ssh", stderr="SSH failed"
                ),
            ]

            with pytest.raises(Exception) as excinfo:
                submitter.submit_workflow(case_path)

            assert "Failed to submit job" in str(excinfo.value)
            assert "SSH failed" in str(excinfo.value)
            assert mock_subprocess.call_count == 2
