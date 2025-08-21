# Development Progress

## Initial State

The program is designed to monitor a directory for new cases, submit them to a remote HPC via `scp` and `ssh` for processing with `pueue`, and track their status in a local SQLite database.

## Logical Flow Trace (Part 1)

1.  **Startup**: `main.py` is executed.
2.  **Initialization**:
    - Configuration is loaded from `config/config.yaml`.
    - Logging is set up.
    - `DatabaseManager` is initialized, creating `cases` and `gpu_resources` tables.
    - GPU resources defined in `config.yaml` are added to the `gpu_resources` table with 'available' status.
    - `WorkflowSubmitter` and `CaseScanner` are initialized.
    - `CaseScanner` starts watching the `new_cases` directory in a background thread.
3.  **Main Loop Starts**: The application enters its main `while True` loop.
4.  **New Case Arrival**: A new directory, `case_01`, is copied into `new_cases`.
5.  **Case Detection**: `CaseScanner` detects the new directory, waits for file operations to stabilize, and then calls `db_manager.add_case('new_cases/case_01')`. A new entry is created in the `cases` table with `status='submitted'`.
6.  **Case Submission**:
    - The main loop queries for 'submitted' cases and finds `case_01`.
    - It locks an available GPU resource by calling `db_manager.find_and_lock_any_available_gpu()`.
    - It updates the case status to 'submitting'.
    - It calls `workflow_submitter.submit_workflow()`.
    - `submit_workflow` successfully copies the files via `scp` and submits the job to `pueue` via `ssh`, receiving a `task_id` (e.g., 123).
    - The case status is updated to 'running' and the `pueue_task_id` is saved to the database.
7.  **Status Monitoring**:
    - On a subsequent loop, the main loop queries for 'running' cases and finds `case_01`.
    - It calls `workflow_submitter.get_workflow_status(123)` to check the remote job's status.
    - Inside `get_workflow_status`, an `ssh` command is executed to run `pueue status --json`.
    - The `stdout` of this command is captured to be parsed.

---

## Problem 1: Incorrect parsing of `pueue status --json` output

### Issue Description

In `src/services/workflow_submitter.py`, the `get_workflow_status` method assumes that the `tasks` key in the JSON output from `pueue status --json` contains a dictionary of tasks keyed by their ID. However, based on the `pueue` documentation, the `tasks` key contains a *list* of task objects. The current code, `tasks = status_data.get("tasks", {})`, attempts to treat this list as a dictionary. When it tries to access a task by its ID using `tasks[str(task_id)]`, it will fail with a `TypeError` because list indices must be integers, not strings. This will cause the status check for all running jobs to fail, preventing the application from ever recognizing that a job has completed or failed.

### Solution for Problem 1

The `get_workflow_status` method in `src/services/workflow_submitter.py` was modified to correctly interpret the status of `Done` tasks. It now checks the `result` field of the task information. If the status is `Done`, the function will only return `'success'` if the `result` is also `'success'`. Otherwise, it returns `'failure'`. This prevents failed jobs from being marked as completed.

---

## Logical Flow Trace (Part 2)

With the first bug fixed, the logical trace continues. The application can now correctly identify when a job succeeds or fails. The process of updating the case status in the database to `'completed'` or `'failed'` and releasing the GPU resource via `db_manager.release_gpu_resource()` appears to be logically sound.

However, a potential issue arises if the application were to crash at a specific moment.

1.  **Main Loop**: The loop picks up a `'submitted'` case.
2.  **Resource Lock**: A GPU resource is successfully locked for the case.
3.  **Status Update**: The case status is changed to `'submitting'` in the database via `db_manager.update_case_status()`.
4.  **CRASH**: The application crashes *after* the status update but *before* `workflow_submitter.submit_workflow()` returns and the `pueue_task_id` is saved.

Upon restart, this case is now in a limbo state.

---

## Problem 2: Cases stuck in 'submitting' state are not handled

### Issue Description

If the application terminates unexpectedly after a case has been marked as `'submitting'` but before its `pueue_task_id` has been persisted, the case becomes orphaned. The main loop only queries for cases with `'running'` or `'submitted'` status, so it will never process this orphaned case again. Furthermore, the GPU resource assigned to this case remains locked in the database with status `'assigned'`, leading to a permanent resource leak that will prevent new cases from being processed if all GPUs become locked this way.

### Solution for Problem 2

The main loop in `src/main.py` was modified to include a recovery mechanism for orphaned cases. At the start of each loop, the application now queries for any cases stuck in the `'submitting'` state. For each such case, it releases the GPU resource that was assigned to it and resets the case status to `'submitted'`. This change prevents both resource leaks and permanently stuck cases, allowing the application to self-heal from crashes during the submission process.
