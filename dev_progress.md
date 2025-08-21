# MQI Communicator - Development Progress Report

## Initial Code Execution Trace

The analysis begins by tracing the code execution from the main entry point (`src/main.py`).

1.  **Application Startup**: The program initializes, loading configuration from `config/config.yaml`, and sets up logging. The `DatabaseManager` is initialized, creating `database/mqi_communicator.db` and defining the `cases` and `gpu_resources` tables. GPU resources specified in the config (`default`, `gpu_a`, `gpu_b`) are added to the `gpu_resources` table with an 'available' status. The `CaseScanner` starts in a background thread, monitoring the `new_cases/` directory.

2.  **New Case Simulation**: A new directory, `new_cases/case_001`, is created. The `CaseScanner` detects this directory. After a 5-second quiescence period (no new file modifications), it adds the case to the database with the status 'submitted'.

3.  **Main Loop - Case Processing**:
    - The main loop queries for cases with 'submitted' status and finds `case_001`.
    - It calls `db_manager.find_and_lock_any_available_gpu()`, which successfully locks the 'default' GPU resource and assigns it to `case_001`.
    - The case status is updated to 'submitting'.
    - The application then calls `workflow_submitter.submit_workflow()` to send the case to the remote High-Performance Computing (HPC) system.

---

## Problem 1: Hard-coded HPC Configuration and Command Execution Failure

### Description

The `workflow_submitter.submit_workflow` function attempts to execute `scp` and `ssh` commands using connection details from `config/config.yaml`. The default configuration contains placeholder values:

-   `hpc.host`: "your_hpc_hostname"
-   `hpc.user`: "your_username"

When `subprocess.run` is called with these placeholders, the commands will inevitably fail, raising a `subprocess.CalledProcessError` or `subprocess.TimeoutExpired`. This exception is caught in `main.py`, and the case is immediately marked as 'failed', preventing any further progress in the logical flow.

### Proposed Solution for Simulation

To continue tracing the application's logic without a real HPC, the `WorkflowSubmitter` class will be modified to simulate the behavior of the external `scp` and `ssh` commands. The `subprocess.run` calls will be replaced with mock logic that simulates successful command execution. This will allow the trace to proceed as if the application were interacting with a functional HPC environment.

---

## Trace 2: Successful Submission with Mocked `WorkflowSubmitter`

With the modified `WorkflowSubmitter`, the trace continues.

1.  **`submit_workflow` Execution**: The call to `workflow_submitter.submit_workflow()` now executes the mocked version.
    - It logs the simulated file transfer and job submission.
    - It returns a fake Pueue Task ID (e.g., `101`).
    - It internally stores this new task in a dictionary, `mock_tasks`, with an initial status of `'running'`.

2.  **Database Updates**: The main loop receives the task ID `101`.
    - It calls `db_manager.update_case_pueue_task_id(1, 101)`.
    - It calls `db_manager.update_case_status(1, status="running", progress=30)`.

3.  **Result**: The case `case_001` is now successfully in the `running` state in the database, with an associated `pueue_task_id` of `101`. The application is ready to monitor this case in the next loop iteration.

---

## Trace 3: Handling of a 'Running' Case

The trace continues into the next iteration of the main loop.

1.  **Query for 'Running' Cases**: The loop's first major action is to query for cases with the 'running' status. It finds `case_001`.
2.  **Check Remote Status**: The application calls `workflow_submitter.get_workflow_status(101)`.
    - The mocked `get_workflow_status` function is executed.
    - It looks up task `101` in its internal `mock_tasks` dictionary.
    - It returns the current status, which is `'running'`.
3.  **Conditional Logic**:
    - The `main.py` script receives the `'running'` status.
    - The conditional logic `if status in ("success", "failure", "not_found")` evaluates to false.
    - The `elif status == "running"` block is executed, which contains a `pass`.
4.  **Result**: The application correctly determines that the job is still active on the (simulated) remote HPC and takes no action. It will poll again in the next cycle. The logic for handling a properly running case is sound.

---

## Trace 4: Handling of a 'Completed' Case

To test the completion logic, the mocked `get_workflow_status` is adjusted to return `'success'` for task ID `101`.

1.  **Query and Status Check**: The main loop finds `case_001` as 'running' and calls `get_workflow_status(101)`, which now returns `'success'`.
2.  **Conditional Logic**: The condition `if status in ("success", "failure", "not_found")` is now **true**.
3.  **Database Updates**:
    - `final_status` is set to `'completed'`.
    - `db_manager.release_gpu_resource(1)` is called. The 'default' GPU resource is set back to 'available'.
    - `db_manager.update_case_completion(1, status='completed')` is called. The case's status is updated to 'completed' and progress to 100.
4.  **Result**: The application correctly processes a successfully completed job, updates the database state, and releases the resource. The "happy path" for a finished case works as expected.

---

## Problem 2: Ambiguous `not_found` Status Leads to Incorrect Case Outcome

### Description

The logic for handling terminal states in `main.py` is:
```python
if status in ("success", "failure", "not_found"):
    final_status = "completed" if status == "success" else "failed"
    # ... release resource and update case
```
This code groups `'not_found'` with `'failure'`. A remote task being "not found" is ambiguous. It could mean the job failed and was removed, but it could also mean the job **succeeded** and was later pruned from the remote system's finished task list.

The current implementation pessimistically assumes any "not found" job is a failure. This can lead to a situation where a case that ran successfully is incorrectly marked as 'failed' in the application's database simply due to the timing of the status check.

### Impact

This leads to incorrect final state information in the database. A user might see that a case has failed, when in reality it produced the correct output. This can cause confusion and waste time on unnecessary investigations or re-submissions.

### Proposed Solution

The logic should be refactored to handle the `'not_found'` state explicitly. While marking the case as 'failed' remains the only safe, pessimistic option, the code should:
1.  Separate the conditional logic to treat each terminal state (`'success'`, `'failure'`, `'not_found'`) individually.
2.  Log a more specific and descriptive warning when a case is marked as failed due to a `'not_found'` status, clearly stating that the outcome is ambiguous.

This change will make the code's behavior more transparent and provide better information for anyone debugging potential discrepancies between the application's database and the actual results on the HPC.

---

## Final Summary

The logical trace of the MQI Communicator application is complete. The investigation involved:
1.  A full trace of the application's lifecycle, from startup to case processing and completion.
2.  Modification of the `WorkflowSubmitter` to use a mock state, enabling the simulation of interactions with a remote HPC.
3.  Identification and correction of a logical flaw in `main.py` where an ambiguous `'not_found'` status for a remote job was not handled explicitly, potentially leading to incorrect data in the database.

The corrected code is more robust and provides clearer logging, which will aid in future maintenance and debugging. The application's core logic now appears sound.
