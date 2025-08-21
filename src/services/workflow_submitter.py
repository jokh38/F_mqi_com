import subprocess
import shlex
from pathlib import Path
from typing import Dict, Any


class WorkflowSubmissionError(Exception):
    """Custom exception for errors during workflow submission."""

    pass


class WorkflowSubmitter:
    """
    Handles the submission of QA cases to the remote HPC.

    This class is responsible for transferring case files via scp and
    submitting simulation jobs to the Pueue daemon on the remote server.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initializes the WorkflowSubmitter with HPC configuration.

        Args:
            config: A dictionary containing the HPC configuration.
                    Expected keys: 'hpc'
        """
        self.hpc_config: Dict[str, Any] = config["hpc"]

    def submit_workflow(self, case_path: str, pueue_group: str = "default") -> None:
        """
        Submits a case to the HPC for simulation.

        This method performs two main actions:
        1. Copies the entire case directory to the remote HPC using scp.
        2. Submits a job to the Pueue daemon to run the simulation script.

        Args:
            case_path: The local path to the case directory.
            pueue_group: The Pueue group to assign the job to.

        Raises:
            WorkflowSubmissionError: If the scp or ssh command fails.
        """
        case_name = Path(case_path).name
        # Sanitize case_name to prevent directory traversal
        safe_case_name = Path(case_name).name
        remote_path = f"{self.hpc_config['remote_base_dir']}/{safe_case_name}"
        user = self.hpc_config["user"]
        host = self.hpc_config["host"]

        # 1. Transfer files using scp
        scp_command = [
            "scp",
            "-r",
            case_path,
            f"{user}@{host}:{self.hpc_config['remote_base_dir']}",
        ]
        try:
            subprocess.run(
                scp_command, check=True, capture_output=True, text=True, timeout=300
            )
        except subprocess.CalledProcessError as e:
            error_message = (
                f"Failed to copy case '{safe_case_name}' to HPC. SCP stderr: {e.stderr}"
            )
            raise WorkflowSubmissionError(error_message) from e
        except subprocess.TimeoutExpired as e:
            error_message = f"Timeout during scp of case '{safe_case_name}'."
            raise WorkflowSubmissionError(error_message) from e

        # 2. Submit job to Pueue via ssh
        base_remote_command = self.hpc_config.get(
            "remote_command", "python interpreter.py && python moquisim.py"
        )
        remote_command = f"cd {shlex.quote(remote_path)} && {base_remote_command}"

        ssh_command = [
            "ssh",
            f"{user}@{host}",
            "pueue",
            "add",
            "--group",
            pueue_group,
            "--",
            remote_command,
        ]
        try:
            subprocess.run(
                ssh_command, check=True, capture_output=True, text=True, timeout=60
            )
        except subprocess.CalledProcessError as e:
            error_message = (
                f"Failed to submit job for case '{safe_case_name}' to Pueue. "
                f"SSH stderr: {e.stderr}"
            )
            raise WorkflowSubmissionError(error_message) from e
        except subprocess.TimeoutExpired as e:
            error_message = (
                f"Timeout during ssh submission for case '{safe_case_name}'."
            )
            raise WorkflowSubmissionError(error_message) from e
