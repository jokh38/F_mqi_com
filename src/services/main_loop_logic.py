import logging
from datetime import datetime, timedelta
from typing import Dict, Any

# Note: To avoid circular imports, type hint the manager classes
# instead of importing them directly.
from src.common.db_manager import DatabaseManager
from src.services.workflow_submitter import WorkflowSubmitter


def recover_stuck_submitting_cases(
    db_manager: DatabaseManager, workflow_submitter: WorkflowSubmitter
) -> None:
    """
    Finds cases stuck in the 'submitting' state and attempts to recover them.
    This can happen if the application crashes after a job has been submitted
    to the HPC but before the local database could be updated.
    """
    stuck_submitting_cases = db_manager.get_cases_by_status("submitting")
    if not stuck_submitting_cases:
        return

    logging.warning(
        f"Found {len(stuck_submitting_cases)} stuck cases. Attempting recovery..."
    )
    for case in stuck_submitting_cases:
        case_id = case["case_id"]
        label = f"mqic_case_{case_id}"
        logging.info(f"Checking remote task with label '{label}' for case {case_id}.")

        status, remote_task = workflow_submitter.find_task_by_label(label)

        if status == "found":
            if remote_task and (task_id := remote_task.get("id")) is not None:
                logging.warning(
                    f"Found orphaned remote task {task_id} for case {case_id}. "
                    "Recovering state to 'running'."
                )
                db_manager.update_case_pueue_task_id(case_id, task_id)
                db_manager.update_case_status(case_id, status="running", progress=30)
            else:
                logging.error(
                    f"Remote task for case {case_id} has no ID. Cannot recover. "
                    "Marking as failed."
                )
                db_manager.update_case_completion(case_id, status="failed")
                db_manager.release_gpu_resource(case_id)
        elif status == "not_found":
            logging.warning(
                f"No remote task for case {case_id}. Submission likely failed. "
                "Marking as 'failed'."
            )
            db_manager.update_case_completion(case_id, status="failed")
            db_manager.release_gpu_resource(case_id)
        elif status == "unreachable":
            logging.warning(
                f"HPC unreachable. Cannot check status for case {case_id}. Will retry."
            )


def manage_running_cases(
    db_manager: DatabaseManager,
    workflow_submitter: WorkflowSubmitter,
    timeout_delta: timedelta,
    kst: Any,
) -> None:
    """
    Checks the status of all 'running' cases, handling timeouts, successes,
    and failures.
    """
    running_cases = db_manager.get_cases_by_status("running")
    if not running_cases:
        return

    logging.info(f"Found {len(running_cases)} running case(s) to check.")
    for case in running_cases:
        case_id = case["case_id"]
        task_id = case["pueue_task_id"]
        status_updated_at = datetime.fromisoformat(case["status_updated_at"])

        if task_id is None:
            logging.error(
                f"CRITICAL: Case {case_id} is 'running' but has no pueue_task_id. "
                "Marking as failed."
            )
            db_manager.update_case_completion(case_id, status="failed")
            db_manager.release_gpu_resource(case_id)
            continue

        # Check for timeout
        if datetime.now(kst) - status_updated_at > timeout_delta:
            log_msg = (
                f"Case {case_id} (Task {task_id}) timed out after "
                f"{timeout_delta.total_seconds() / 3600} hours. Marking as failed."
            )
            logging.critical(log_msg)
            kill_successful = workflow_submitter.kill_workflow(task_id)
            db_manager.update_case_completion(case_id, status="failed")

            if kill_successful:
                logging.info(
                    f"Kill command for timed-out Task {task_id} succeeded. "
                    "Releasing resource."
                )
                db_manager.release_gpu_resource(case_id)
            else:
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
                        f"CRITICAL: Timed-out case {case_id} has no pueue_group. "
                        "Cannot mark resource as zombie."
                    )
            continue

        # Check remote status
        remote_status = workflow_submitter.get_workflow_status(task_id)
        logging.info(
            f"Case ID {case_id} (Task {task_id}) has remote status: '{remote_status}'."
        )

        if remote_status in ("success", "failure", "not_found"):
            db_manager.release_gpu_resource(case_id)
            final_status = (
                "completed" if remote_status == "success" else "failed"
            )
            db_manager.update_case_completion(case_id, status=final_status)
            if final_status == "completed":
                logging.info(
                    f"Case {case_id} completed successfully. Resource released."
                )
            else:
                log_level = (
                    logging.WARNING
                    if remote_status == "not_found"
                    else logging.ERROR
                )
                logging.log(
                    log_level,
                    f"Case {case_id} finished with status '{remote_status}'. "
                    "Marked as failed. Resource released.",
                )
        elif remote_status == "unreachable":
            logging.warning(
                f"HPC is unreachable. Cannot check status for case {case_id}."
            )


def manage_zombie_resources(
    db_manager: DatabaseManager, workflow_submitter: WorkflowSubmitter
) -> None:
    """
    Attempts to recover 'zombie' resources by killing the associated task.
    A resource becomes a zombie if its task timed out but could not be killed.
    """
    zombie_resources = db_manager.get_resources_by_status("zombie")
    if not zombie_resources:
        return

    logging.warning(
        f"Found {len(zombie_resources)} zombie resources. Attempting recovery..."
    )
    for resource in zombie_resources:
        case_id = resource["assigned_case_id"]
        pueue_group = resource["pueue_group"]
        zombie_case = db_manager.get_case_by_id(case_id)

        if not zombie_case or not (task_id := zombie_case.get("pueue_task_id")):
            logging.error(
                f"Cannot recover zombie resource '{pueue_group}'. Manual intervention required."
            )
            continue

        logging.info(
            f"Attempting to kill zombie Task {task_id} to recover resource '{pueue_group}'."
        )
        if workflow_submitter.kill_workflow(task_id):
            logging.info(
                f"Successfully killed zombie Task {task_id}. "
                f"Releasing resource '{pueue_group}'."
            )
            db_manager.release_gpu_resource(case_id)
        else:
            logging.warning(f"Failed to kill zombie Task {task_id}. Will retry.")


def process_new_submitted_cases(
    db_manager: DatabaseManager, workflow_submitter: WorkflowSubmitter
) -> None:
    """

    Processes new cases with 'submitted' status by assigning them to available
    GPU resources and submitting them to the HPC.
    """
    submitted_cases = db_manager.get_cases_by_status("submitted")
    if not submitted_cases:
        return

    logging.info(f"Found {len(submitted_cases)} submitted case(s).")
    for case_to_process in submitted_cases:
        case_id = case_to_process["case_id"]
        locked_pueue_group = db_manager.get_gpu_resource_by_case_id(
            case_id
        ) or db_manager.find_and_lock_any_available_gpu(case_id)

        if not locked_pueue_group:
            logging.info("No available GPUs. Will retry next cycle.")
            break  # No need to check other cases if no GPUs are free

        group_name = (
            locked_pueue_group
            if isinstance(locked_pueue_group, str)
            else locked_pueue_group["pueue_group"]
        )

        logging.info(f"GPU resource '{group_name}' locked for case ID: {case_id}")
        try:
            db_manager.update_case_pueue_group(case_id, group_name)
            db_manager.update_case_status(case_id, status="submitting", progress=10)

            pueue_task_id = workflow_submitter.submit_workflow(
                case_id=case_id,
                case_path=case_to_process["case_path"],
                pueue_group=group_name,
            )

            if pueue_task_id is not None:
                db_manager.update_case_pueue_task_id(case_id, pueue_task_id)
                db_manager.update_case_status(case_id, status="running", progress=30)
                logging.info(
                    f"Case {case_id} submitted to '{group_name}' as Task ID: {pueue_task_id}."
                )
            else:
                raise ValueError("Failed to parse Pueue Task ID from submission.")

        except Exception as e:
            logging.error(
                f"Failed to process case {case_id}. Error: {e}", exc_info=True
            )
            db_manager.update_case_completion(case_id, status="failed")
            db_manager.release_gpu_resource(case_id)
            logging.info(f"Released GPU for failed case {case_id}.")
