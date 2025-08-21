# MQI Communicator Development Progress

## Initial Analysis: Code Flow

The application, MQI Communicator, is designed to automate the process of running simulation cases on a remote High-Performance Computing (HPC) cluster.

1.  **Startup**: The application starts, loads `config/config.yaml`, and sets up logging and the database (`database/mqi_communicator.db`). It pre-populates a `gpu_resources` table based on the `pueue.groups` setting in the config.

2.  **Case Detection**: A file system watcher (`CaseScanner`) monitors the `new_cases` directory. When a new directory is placed there and remains stable (no file changes for a configured period), it's registered in the `cases` table with a `'submitted'` status.

3.  **Main Loop**: The core logic continuously cycles through the following steps:
    *   **Recovery of Stuck Submissions**: It checks for cases that have a `'submitting'` status, which might indicate a crash during a previous submission attempt. It tries to find the corresponding job on the remote HPC to either recover it (by updating its status to `'running'`) or mark it as `'failed'`.
    *   **Monitoring of Running Cases**: It polls the status of all `'running'` cases by querying the remote HPC. If a case has completed or failed, it updates the database and releases the GPU resource it was using. It also implements a timeout to prevent cases from running indefinitely.
    *   **Processing of New Cases**: It takes new `'submitted'` cases and attempts to assign them to an available GPU resource. If a resource is free, it locks it, marks the case as `'submitting'`, transfers the case files to the HPC, and submits the job. Upon successful submission, the case status is updated to `'running'`.

## Problem 1: Incorrect Failure on HPC Unreachable

*   **File**: `src/main.py` (Main Loop - Recovery Logic) and `src/services/workflow_submitter.py`
*   **Problem Description**: The recovery logic for cases stuck in the `'submitting'` state is flawed. The function `workflow_submitter.find_task_by_label` is called to check if a remote job already exists. This function returns `None` for two distinct conditions: (1) the job genuinely does not exist, or (2) the HPC is unreachable (e.g., network error, SSH timeout). The main loop treats both `None` returns as a confirmation that the job failed to submit, marking the case as `'failed'`. This is a critical bug, as it can cause the application to lose track of a running job and its GPU resource if a temporary network issue occurs during the check.
*   **Proposed Solution**:
    1.  Modify `workflow_submitter.find_task_by_label` to explicitly differentiate between "not found" and "unreachable" states. It will be refactored to return a tuple `(status, data)`, where `status` is a string literal (`"found"`, `"not_found"`, or `"unreachable"`).
    2.  Update the recovery logic in `main.py` to handle this new return structure. If the status is `"unreachable"`, the application will log a warning and skip the case for this cycle, allowing it to retry later, thus preventing the incorrect failure.

*   **Solution Implemented**:
    1.  **`src/services/workflow_submitter.py`**: The `find_task_by_label` function was modified to change its return type from `Optional[Dict]` to `tuple[Literal["found", "not_found", "unreachable"], Optional[Dict[str, Any]]]`. The function now returns `("found", task_info)` if the task is located, `("not_found", None)` if the remote query succeeds but the task is not in the list, and `("unreachable", None)` if any exception (e.g., `TimeoutExpired`, `CalledProcessError`) occurs during the SSH communication. The log message for the unreachable case was also updated for clarity.
    2.  **`src/main.py`**: The main loop's recovery logic for `'submitting'` cases was updated. It now unpacks the tuple returned by `find_task_by_label`. An `if/elif/elif` block checks the `status`:
        *   If `"found"`, the original recovery logic proceeds.
        *   If `"not_found"`, the case is marked as `'failed'`.
        *   If `"unreachable"`, a warning is logged, and the loop continues to the next case, leaving the current one in the `'submitting'` state to be retried on the next cycle.
    This change prevents the system from incorrectly failing cases during temporary HPC outages.

## Problem 2: Incorrect Failure on Status Parsing Error

*   **File**: `src/services/workflow_submitter.py`
*   **Problem Description**: In the `get_workflow_status` function, if the JSON output from the remote `pueue status` command is malformed or has an unexpected structure, a `JSONDecodeError` or `KeyError` is raised. The `except` block for these errors currently returns the status `'failure'`. This causes the main loop to incorrectly mark the corresponding case as 'failed' in the database, even though the remote job might still be running perfectly fine. The issue is a failure to parse the status, not a failure of the job itself.
*   **Proposed Solution**: The `except` block for `JSONDecodeError` and `KeyError` in the `get_workflow_status` function will be modified. Instead of returning `'failure'`, it will return `'unreachable'`. This correctly signals to the main loop that the job's true status could not be determined. The main loop's existing logic for the `'unreachable'` state will then handle this gracefully by logging a warning and retrying on the next cycle.

*   **Solution Implemented**:
    *   In `src/services/workflow_submitter.py`, the `except (json.JSONDecodeError, KeyError)` block within the `get_workflow_status` function was modified. It now returns the string `'unreachable'` instead of `'failure'`. The corresponding error log message was also updated to reflect that the action is to retry rather than to mark as a failure. This makes the system more resilient to transient data corruption or future non-breaking changes in the remote API.

## Problem 3: Stale Data in `cases` Table on Completion

*   **File**: `src/common/db_manager.py`
*   **Problem Description**: When a case finishes (either `completed` or `failed`), the associated GPU resource is correctly released. However, the entry for the case in the `cases` table is left with stale data in the `pueue_group` and `pueue_task_id` columns. A case that is in a terminal state should not be associated with a resource it is no longer using. This represents a minor data integrity issue that could lead to confusion during debugging or if the application logic were extended.
*   **Proposed Solution**: To ensure data is properly cleaned up, the `update_case_completion` function in `db_manager.py` will be modified. When it updates a case's status to `completed` or `failed`, it will also set the `pueue_group` and `pueue_task_id` fields to `NULL`. This ensures that terminal cases have no lingering resource associations, improving data consistency.

*   **Solution Implemented**:
    *   The `update_case_completion` function in `src/common/db_manager.py` was updated. The `UPDATE` query within this function now also sets `pueue_group = NULL` and `pueue_task_id = NULL`.
    *   A new test, `test_update_case_completion_clears_resource_fields`, was added to `tests/common/test_db_manager.py` to verify this new behavior.
    *   All 36 tests pass, confirming the change is correct and introduces no regressions.
