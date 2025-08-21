import logging
import sys
import time
import yaml
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from src.common.db_manager import DatabaseManager
from src.services.case_scanner import CaseScanner
from src.services.workflow_submitter import WorkflowSubmitter

# Define the path to the configuration file
CONFIG_PATH = "config/config.yaml"

# Define Korea Standard Time (KST)
KST = timezone(timedelta(hours=9))


class KSTFormatter(logging.Formatter):
    """A logging formatter that uses KST for timestamps."""

    def formatTime(
        self, record: logging.LogRecord, datefmt: Optional[str] = None
    ) -> str:
        dt = datetime.fromtimestamp(record.created, KST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


def setup_logging(config: Dict[str, Any]) -> None:
    """Sets up file-based, timezone-aware logging for the application."""
    log_config = config.get("logging", {})
    log_path = log_config.get("path", "communicator_fallback.log")

    log_formatter = KSTFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    log_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5)
    log_handler.setFormatter(log_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)  # Set to INFO for production
    root_logger.addHandler(log_handler)

    # Add a console handler for immediate feedback
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    logging.info(f"Logger has been configured. Logging to: {log_path}")


def main(config: Dict[str, Any]) -> None:
    """
    Main function for the MQI Communicator application.
    This function initializes all components and runs the main loop.
    """
    case_scanner = None
    db_manager = None

    try:
        logging.info("MQI Communicator application starting...")

        # 1. Initialize Components & DB
        db_manager = DatabaseManager(config=config)
        db_manager.init_db()
        logging.info("DatabaseManager initialized.")

        # 4. Initialize GPU Resources from Config
        pueue_config = config.get("pueue", {})
        pueue_groups = pueue_config.get("groups", [])
        if not pueue_groups:
            raise ValueError("Config error: 'pueue.groups' must be a non-empty list.")

        for group in pueue_groups:
            db_manager.ensure_gpu_resource_exists(group)
            logging.info(f"Ensured GPU resource for group '{group}' exists.")

        # 5. Continue Component Initialization
        main_loop_config = config.get("main_loop", {})
        watch_path = config.get("scanner", {}).get("watch_path", "new_cases")
        sleep_interval = main_loop_config.get("sleep_interval_seconds", 10)
        running_case_timeout_hours = main_loop_config.get("running_case_timeout_hours", 24)
        timeout_delta = timedelta(hours=running_case_timeout_hours)

        # Ensure the watch path exists before starting the scanner
        os.makedirs(watch_path, exist_ok=True)
        logging.info(f"Ensured watch directory exists: {watch_path}")

        workflow_submitter = WorkflowSubmitter(config=config)
        logging.info("WorkflowSubmitter initialized.")

        case_scanner = CaseScanner(
            watch_path=watch_path, db_manager=db_manager, config=config
        )
        logging.info("CaseScanner initialized.")

        # 4. Start Background Services
        case_scanner.start()
        logging.info(f"CaseScanner started, watching '{watch_path}'.")

        # 5. Main Application Loop
        logging.info("Starting main application loop...")
        while True:
            try:
                # --- Part 0: Recover cases stuck in 'submitting' state ---
                stuck_submitting_cases = db_manager.get_cases_by_status("submitting")
                if stuck_submitting_cases:
                    logging.warning(
                        f"Found {len(stuck_submitting_cases)} stuck cases. "
                        "Attempting recovery..."
                    )
                    for case in stuck_submitting_cases:
                        case_id = case["case_id"]
                        label = f"mqic_case_{case_id}"
                        logging.info(
                            f"Checking remote task with label '{label}' for case {case_id}."
                        )

                        # Check if a task with the corresponding label exists on the HPC
                        (
                            status,
                            remote_task,
                        ) = workflow_submitter.find_task_by_label(label)

                        if status == "found":
                            # --- Recovery Path ---
                            # The job exists remotely, but our DB wasn't updated. Let's fix it.
                            if remote_task:
                                task_id = remote_task.get("id")
                                if task_id is not None:
                                    logging.warning(
                                        f"Found orphaned remote task {task_id} for case "
                                        f"{case_id}. Recovering state to 'running'."
                                    )
                                    db_manager.update_case_pueue_task_id(case_id, task_id)
                                    db_manager.update_case_status(
                                        case_id, status="running", progress=30
                                    )
                                else:
                                    # This case is unlikely but possible if Pueue's output changes.
                                    logging.error(
                                        f"Remote task for case {case_id} has no ID. "
                                        "Cannot recover. Marking as failed."
                                    )
                                db_manager.update_case_completion(
                                    case_id, status="failed"
                                )
                                db_manager.release_gpu_resource(case_id)
                        elif status == "not_found":
                            # --- Rollback Path ---
                            # No remote job was found. The submission failed before starting.
                            # It's safe to mark as failed and release the resource.
                            logging.warning(
                                f"No remote task for case {case_id}. "
                                "Submission likely failed. Marking as 'failed'."
                            )
                            db_manager.update_case_completion(case_id, status="failed")
                            db_manager.release_gpu_resource(case_id)
                            logging.info(
                                f"Released GPU resource for failed case ID: {case_id}."
                            )
                        elif status == "unreachable":
                            # --- Do Nothing Path ---
                            # The HPC is unreachable, so we can't determine the status.
                            # Skip this case and let the main loop retry in the next cycle.
                            logging.warning(
                                f"HPC unreachable. Cannot check status for case "
                                f"{case_id}. Will retry."
                            )

                # --- Part 1: Manage all RUNNING cases (replaces old Part 0.5 and 1) ---
                running_cases = db_manager.get_cases_by_status("running")
                if running_cases:
                    logging.info(
                        f"Found {len(running_cases)} running case(s) to check."
                    )
                    for case in running_cases:
                        case_id = case["case_id"]
                        task_id = case["pueue_task_id"]
                        status_updated_at = datetime.fromisoformat(
                            case["status_updated_at"]
                        )

                        # A running case must have a task_id. If not, it's a critical logic error.
                        if task_id is None:
                            logging.error(
                                f"CRITICAL: Case {case_id} is 'running' but has no "
                                "pueue_task_id. Marking as failed."
                            )
                            db_manager.update_case_completion(case_id, status="failed")
                            db_manager.release_gpu_resource(case_id)
                            continue

                        # First, check for a timeout. This is independent of HPC reachability.
                        if datetime.now(KST) - status_updated_at > timeout_delta:
                            log_msg = (
                                f"Case {case_id} (Task {task_id}) timed out after "
                                f"{running_case_timeout_hours} hours. Marking as failed."
                            )
                            logging.critical(log_msg)
                            # Attempt to kill the remote job to prevent resource leaks on the HPC
                            kill_successful = workflow_submitter.kill_workflow(task_id)

                            # Mark the case as failed regardless of kill outcome
                            db_manager.update_case_completion(case_id, status="failed")

                            if kill_successful:
                                logging.info(
                                    f"Kill command for timed-out Task {task_id} "
                                    "succeeded. Releasing resource."
                                )
                                db_manager.release_gpu_resource(case_id)
                            else:
                                # If kill fails, the resource is in an unknown state. Mark it as 'zombie'.
                                pueue_group = case["pueue_group"]
                                logging.critical(
                                    f"Failed to kill timed-out Task {task_id}. "
                                    f"Marking group '{pueue_group}' as 'zombie'."
                                )
                                if pueue_group:
                                    db_manager.update_gpu_status(
                                        pueue_group, status="zombie", case_id=case_id
                                    )
                                else:
                                    logging.error(
                                        f"CRITICAL: Timed-out case {case_id} has no "
                                        "pueue_group. Cannot mark resource as zombie."
                                    )

                            continue

                        # If not timed out, check the remote status of the job.
                        remote_status = workflow_submitter.get_workflow_status(task_id)
                        logging.info(
                            f"Case ID {case_id} (Task {task_id}) has remote status: '{remote_status}'."
                        )

                        # --- Handle terminal states ---
                        # The order of operations is critical to prevent resource leaks on crash.
                        # Release the resource FIRST, then update the case status.
                        if remote_status == "success":
                            db_manager.release_gpu_resource(case_id)
                            db_manager.update_case_completion(case_id, status="completed")
                            logging.info(
                                f"Case {case_id} completed successfully. Resource released."
                            )
                        elif remote_status == "failure":
                            db_manager.release_gpu_resource(case_id)
                            db_manager.update_case_completion(case_id, status="failed")
                            logging.error(
                                f"Case {case_id} failed on remote HPC. Resource released."
                            )
                        elif remote_status == "not_found":
                            # This is an ambiguous state. The job might have succeeded or
                            # failed and was then pruned from the remote's history.
                            # We pessimistically mark it as failed but log a detailed warning.
                            db_manager.release_gpu_resource(case_id)
                            db_manager.update_case_completion(case_id, status="failed")
                            logging.warning(
                                f"Case {case_id} (Task {task_id}) not found on remote. "
                                "Marking as failed."
                            )

                        # If the HPC is unreachable or the job is still running, do nothing.
                        # The timeout logic above will eventually catch any truly stuck cases.
                        elif remote_status == "unreachable":
                            logging.warning(
                                f"HPC is unreachable. Cannot check status for case {case_id}."
                            )
                        elif remote_status == "running":
                            # It's running and not timed out, so we just wait for the next cycle.
                            pass

                # --- Part 1.5: Manage ZOMBIE resources ---
                zombie_resources = db_manager.get_resources_by_status("zombie")
                if zombie_resources:
                    logging.warning(
                        f"Found {len(zombie_resources)} zombie resources. Attempting recovery..."
                    )
                    for resource in zombie_resources:
                        case_id = resource["assigned_case_id"]
                        pueue_group = resource["pueue_group"]

                        # We need the original task_id from the associated (and now failed) case
                        zombie_case = db_manager.get_case_by_id(case_id)
                        if not zombie_case or not zombie_case["pueue_task_id"]:
                            logging.error(
                                f"Cannot recover zombie resource '{pueue_group}'. "
                                "Manual intervention required."
                            )
                            continue

                        task_id = zombie_case["pueue_task_id"]
                        logging.info(
                            f"Attempting to kill zombie Task {task_id} to recover "
                            f"resource '{pueue_group}'."
                        )

                        kill_successful = workflow_submitter.kill_workflow(task_id)
                        if kill_successful:
                            logging.info(
                                f"Successfully killed zombie Task {task_id}. "
                                f"Releasing resource '{pueue_group}'."
                            )
                            # release_gpu_resource uses case_id to find the resource, which is correct
                            db_manager.release_gpu_resource(case_id)
                        else:
                            logging.warning(
                                f"Failed to kill zombie Task {task_id}. Will retry."
                            )

                # --- Part 2: Process new SUBMITTED cases with dynamic resource allocation ---
                submitted_cases = db_manager.get_cases_by_status("submitted")
                if submitted_cases:
                    logging.info(f"Found {len(submitted_cases)} submitted case(s).")

                    # Iterate through submitted cases and try to assign them to available GPUs
                    for case_to_process in submitted_cases:
                        case_id = case_to_process["case_id"]
                        case_path = case_to_process["case_path"]

                        # --- Robust Resource Locking ---
                        # This logic makes the submission step idempotent. If the app crashed
                        # after assigning a resource but before changing the case status,
                        # this will recover the assigned resource instead of locking a new one.
                        locked_pueue_group = None
                        assigned_resource = db_manager.get_gpu_resource_by_case_id(
                            case_id
                        )

                        if assigned_resource:
                            logging.info(
                                f"Recovered assigned GPU '{assigned_resource['pueue_group']}' "
                                f"for case ID: {case_id}"
                            )
                            locked_pueue_group = assigned_resource["pueue_group"]
                        else:
                            # No resource assigned, try to lock a new one.
                            locked_pueue_group = (
                                db_manager.find_and_lock_any_available_gpu(case_id)
                            )

                        if locked_pueue_group:
                            # If we found a GPU, process the case
                            logging.info(
                                f"GPU resource '{locked_pueue_group}' locked for case ID: {case_id}"
                            )
                            try:
                                # Assign the locked group to the case
                                db_manager.update_case_pueue_group(
                                    case_id, locked_pueue_group
                                )

                                # Mark as 'submitting' to prevent re-processing
                                db_manager.update_case_status(
                                    case_id, status="submitting", progress=10
                                )

                                pueue_task_id = workflow_submitter.submit_workflow(
                                    case_id=case_id,
                                    case_path=case_path,
                                    pueue_group=locked_pueue_group,
                                )

                                if pueue_task_id is not None:
                                    db_manager.update_case_pueue_task_id(
                                        case_id, pueue_task_id
                                    )
                                    db_manager.update_case_status(
                                        case_id, status="running", progress=30
                                    )
                                    logging.info(
                                        f"Case {case_id} submitted to '{locked_pueue_group}' "
                                        f"as Task ID: {pueue_task_id}."
                                    )
                                else:
                                    # Handle case where submission succeeded but ID parsing failed
                                    logging.error(
                                        f"Case {case_id} submitted, but failed to parse "
                                        "Task ID. Marking as failed."
                                    )
                                    db_manager.update_case_completion(
                                        case_id, status="failed"
                                    )
                                    db_manager.release_gpu_resource(case_id)
                                    logging.info(f"Released GPU for failed case {case_id}.")

                            except Exception as e:
                                logging.error(
                                    f"Failed to process case {case_id}. Error: {e}",
                                    exc_info=True,
                                )
                                db_manager.update_case_completion(
                                    case_id, status="failed"
                                )
                                db_manager.release_gpu_resource(case_id)
                                logging.info(f"Released GPU for failed case {case_id}.")
                        else:
                            # If we failed to get a lock, it means no GPUs are available.
                            # We can stop trying for this cycle.
                            logging.info("No available GPUs. Will retry next cycle.")
                            break  # Exit the for loop

            except Exception as e:
                # Catch exceptions in the main loop itself to prevent crashing
                logging.error(
                    f"An unexpected error occurred in the main loop: {e}", exc_info=True
                )

            time.sleep(sleep_interval)

    except KeyboardInterrupt:
        logging.info("Shutdown signal received (KeyboardInterrupt).")
    except Exception as e:
        logging.critical(
            f"A critical unhandled exception occurred in main: {e}", exc_info=True
        )
    finally:
        logging.info("Initiating graceful shutdown...")
        if case_scanner and case_scanner.observer.is_alive():
            case_scanner.stop()
            logging.info("CaseScanner stopped.")
        if db_manager:
            db_manager.close()
            logging.info("Database connection closed.")
        logging.info("MQI Communicator application has shut down.")


if __name__ == "__main__":
    # Load config just for logging setup before the main function
    try:
        with open(CONFIG_PATH, "r") as f:
            initial_config = yaml.safe_load(f)
        setup_logging(initial_config)
    except FileNotFoundError:
        print(
            f"ERROR: Configuration file not found at '{CONFIG_PATH}'. The application cannot start."
        )
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to load or parse '{CONFIG_PATH}'. Error: {e}")
        sys.exit(1)

    main(initial_config)
