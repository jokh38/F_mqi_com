import subprocess
from pathlib import Path
import yaml
from typing import Dict, Any


class WorkflowSubmitter:
    """
    Handles the submission of QA cases to the remote HPC.

    This class is responsible for transferring case files via scp and
    submitting simulation jobs to the Pueue daemon on the remote server.
    """

    def __init__(self, config_path: str):
        """
        Initializes the WorkflowSubmitter with HPC configuration.

        Args:
            config_path: The path to the YAML configuration file.
        """
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
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
            subprocess.CalledProcessError: If the scp or ssh command fails.
        """
        case_name = Path(case_path).name
        remote_path = f"{self.hpc_config['remote_base_dir']}/{case_name}"
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
            subprocess.run(scp_command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            error_message = (
                f"Failed to copy case '{case_name}' to HPC. SCP stderr: {e.stderr}"
            )
            raise Exception(error_message) from e

        # 2. Submit job to Pueue via ssh
        # The command to be executed on the remote machine.
        remote_command = f"cd {remote_path} && python interpreter.py"

        # The pueue command that wraps the remote command.
        pueue_command_str = (
            f"pueue add --group {pueue_group} -- '{remote_command}'"
        )

        ssh_command = ["ssh", f"{user}@{host}", pueue_command_str]
        try:
            subprocess.run(ssh_command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            error_message = (
                f"Failed to submit job for case '{case_name}' to Pueue. "
                f"SSH stderr: {e.stderr}"
            )
            raise Exception(error_message) from e
