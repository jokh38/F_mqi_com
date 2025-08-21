import subprocess
import shlex
import re
import json
from pathlib import Path
from typing import Dict, Any, Optional, Literal


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
        self.user = self.hpc_config["user"]
        self.host = self.hpc_config["host"]
        self.ssh_cmd = self.hpc_config.get("ssh_command", "ssh")
        self.pueue_cmd = self.hpc_config.get("pueue_command", "pueue")

    def _parse_pueue_add_output(self, output: str) -> Optional[int]:
        """
        Parses the output of `pueue add` to find the task ID.
        Example output: "New task added (id: 2)."
        """
        match = re.search(r"\(id: (\d+)\)", output)
        if match:
            return int(match.group(1))
        return None

    def submit_workflow(self, case_path: str, pueue_group: str = "default") -> Optional[int]:
        """
        Submits a case to the HPC for simulation and returns the Pueue task ID.

        This method performs two main actions:
        1. Copies the entire case directory to the remote HPC using scp.
        2. Submits a job to the Pueue daemon to run the simulation script.

        Args:
            case_path: The local path to the case directory.
            pueue_group: The Pueue group to assign the job to.

        Returns:
            The integer ID of the submitted pueue task, or None if parsing fails.

        Raises:
            WorkflowSubmissionError: If the scp or ssh command fails.
        """
        case_name = Path(case_path).name
        # Sanitize case_name to prevent directory traversal
        safe_case_name = Path(case_name).name
        remote_path = f"{self.hpc_config['remote_base_dir']}/{safe_case_name}"

        # 1. Transfer files using scp
        scp_cmd = self.hpc_config.get("scp_command", "scp")
        scp_command = [
            scp_cmd,
            "-r",
            case_path,
            f"{self.user}@{self.host}:{self.hpc_config['remote_base_dir']}",
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
        # The command for pueue must be a single string for `pueue add`
        remote_command = f"cd {shlex.quote(remote_path)} && {base_remote_command}"

        ssh_command = [
            self.ssh_cmd,
            f"{self.user}@{self.host}",
            self.pueue_cmd,
            "add",
            "--group",
            pueue_group,
            "--",
            remote_command,
        ]
        try:
            result = subprocess.run(
                ssh_command, check=True, capture_output=True, text=True, timeout=60
            )
            return self._parse_pueue_add_output(result.stdout)
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

    def get_workflow_status(
        self, task_id: int
    ) -> Literal["success", "failure", "running", "not_found"]:
        """
        Checks the status of a specific workflow task in Pueue.

        Args:
            task_id: The ID of the task to check.

        Returns:
            A string representing the task status: 'success', 'failure',
            'running', or 'not_found'.
        """
        ssh_command = [
            self.ssh_cmd,
            f"{self.user}@{self.host}",
            self.pueue_cmd,
            "status",
            "--json",
        ]
        try:
            result = subprocess.run(
                ssh_command, check=True, capture_output=True, text=True, timeout=60
            )
            status_data = json.loads(result.stdout)
            tasks = status_data.get("tasks", {})

            if str(task_id) not in tasks:
                return "not_found"

            task_info = tasks[str(task_id)]
            status = task_info["status"]

            if status == "Done":
                return "success"
            elif status in ["Failed", "Killing"]:
                return "failure"
            else:
                return "running"  # Includes 'Running', 'Queued', 'Paused', etc.

        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
            # If the command fails or parsing fails, we cannot determine the status.
            # This could be a transient network issue, so we treat it as 'running'
            # to allow for retries, but log the issue.
            # A more robust solution might have a separate 'unknown' state.
            # For now, this prevents a job from being marked 'failed' prematurely.
            # A proper logger should be injected here, but for now, print.
            print(f"Warning: Could not determine status for task {task_id}: {e}")
            return "running"
        except subprocess.TimeoutExpired:
            print(f"Warning: Timeout while checking status for task {task_id}")
            return "running"
