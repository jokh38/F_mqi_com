# Development Progress Log

## Initial Code Analysis

The application is an MQI Communicator designed to automate a workflow involving a remote High-Performance Computing (HPC) system managed by a tool called `pueue`.

### Core Components:
- **`main.py`**: The main application entry point. It runs a loop to manage the lifecycle of "cases".
- **`config.yaml`**: Configuration file for database paths, HPC connection details, and `pueue` settings.
- **`db_manager.py`**: An SQLite-based state manager for cases and GPU resources.
- **`case_scanner.py`**: Uses `watchdog` to monitor a directory for new case directories. It includes a delay mechanism to ensure directories are fully copied before being processed.
- **`workflow_submitter.py`**: Handles the remote interaction, specifically copying files via `scp` and submitting jobs to `pueue` via `ssh`.

### High-Level Workflow:
1.  `CaseScanner` detects a new directory (a "case") in the `new_cases` watch folder.
2.  It adds the case to the database with a `submitted` status.
3.  The main loop in `main.py` picks up `submitted` cases.
4.  It finds an available GPU resource from the database and "locks" it for the case.
5.  It updates the case status to `submitting`.
6.  `WorkflowSubmitter` copies the case files to the HPC and submits a job to `pueue`.
7.  If successful, the case status is updated to `running` and the `pueue` task ID is stored.
8.  The main loop periodically checks the status of `running` cases using `pueue status --json`.
9.  When a case is `Done` in `pueue`, its status is updated in the database to `completed` or `failed`, and the GPU resource is released.

---

## Logical Trace & Issue Identification

### Trace 1: Application Startup and Idle State
- **Flow**: The application starts, initializes logging, connects to the database (`init_db`), ensures GPU resources from the config exist in the DB, and starts the `CaseScanner` thread.
- **Main Loop (Idle)**: The loop runs, checks for cases in `submitting` or `running` states, finds none, checks for new `submitted` cases, finds none, and sleeps.
- **Observation**: The idle flow is logically sound.

### Trace 2: New Case Submission
- **Flow**: A new case directory is copied into `new_cases`. `CaseScanner` waits for file activity to stop, then adds the case to the DB with `status='submitted'`.
- **Main Loop (Processing)**:
    1.  The loop finds the `submitted` case.
    2.  It calls `find_and_lock_any_available_gpu()`. This correctly uses an atomic `UPDATE` to claim a GPU resource.
    3.  The case status is changed to `submitting`.
    4.  `submit_workflow()` is called, which runs `scp` and then `ssh ... pueue add`.
    5.  The `pueue` task ID is parsed from the output.
    6.  The case status is updated to `running` with the new task ID.
- **Observation**: The happy-path submission process is logically sound.

### Trace 3: Failure during Submission
- **Flow**: Same as above, but the `ssh ... pueue add` command fails.
- **Main Loop (Failure Handling)**:
    1.  `submit_workflow()` raises a `WorkflowSubmissionError`.
    2.  The `except Exception as e:` block in `main.py` catches it.
    3.  The case is marked as `failed`, and the GPU resource is released.
- **Observation**: Handling of submission failures appears robust.

### Trace 4: Application Crash During Submission
- **Scenario**: The application is forcefully terminated (e.g., `Ctrl+C` or a crash) after a case has been updated to `submitting` but before it has been updated to `running` or `failed`.
- **State at Crash**:
    - `cases` table: A case exists with `status='submitting'`.
    - `gpu_resources` table: A GPU is `assigned` to that case.
- **Flow on Restart**:
    1.  The application starts up.
    2.  The main loop begins. In "Part 0", it calls `db_manager.get_cases_by_status("submitting")`.
    3.  It finds the stuck case.
    4.  The recovery logic is triggered: "Resetting case ID {case_id} to 'submitted' and releasing its GPU."
    5.  The case status is reverted to `submitted`.
    6.  On the same loop iteration, "Part 2" finds this `submitted` case and begins the entire submission process again.
- **Identified Problem**: The `submit_workflow` function is not idempotent. Specifically, the `pueue add` command is executed again for the same case. If the original crash happened *after* the job was submitted to `pueue` but *before* the local database could be updated, this recovery mechanism creates a **duplicate job** on the remote HPC. Repeated crashes could lead to many duplicate jobs, wasting significant resources.

### Proposed Solution
The current recovery mechanism for "stuck" `submitting` cases is unsafe. A better approach is to treat such cases as unrecoverable failures that require manual inspection. The safest automatic action is to mark the case as `failed` instead of re-trying the submission. This prevents the creation of duplicate jobs and alerts the operator that something went wrong.

**Change Required**: In `src/main.py`, the logic for handling `submitting` cases should be changed from resetting them to `submitted` to updating them to `failed`.

**Update**: This change has been applied. The new logic marks the case as `failed` and releases the GPU resource, which is a safer recovery path.

---

## Logical Trace & Issue Identification (Round 2)

### Trace 5: Handling of Running Cases during HPC Unavailability
- **Scenario**: A case is `running`, and the remote HPC becomes unreachable (e.g., network outage, SSH daemon down).
- **Previous Flow**:
    1.  `get_workflow_status()` would encounter an `ssh` timeout or error. It was programmed to log a warning and return `'running'`.
    2.  The main loop would receive the `'running'` status and do nothing, assuming the job was fine.
    3.  Meanwhile, the separate timeout logic in "Part 0.5" would continue to count against the `status_updated_at` timestamp.
    4.  If the outage lasted longer than `running_case_timeout_hours` (e.g., 24 hours), the application would time out the case and mark it as `failed`.
- **Identified Problem 2**: The application would incorrectly fail a potentially healthy job on the HPC just because it couldn't communicate with it. This would release the local GPU lock but leave an **orphan job** running on the remote system, which the application would no longer track.

### Proposed Solution 2
The logic for handling running cases needed to be consolidated. A case should only be timed out if the application can confirm with the HPC that the job is *still* in a `running` state. If the HPC is unreachable, no decision should be made.
1.  Modify `workflow_submitter.get_workflow_status()` to return a new, distinct status, `'unreachable'`, when it cannot connect to the HPC.
2.  Refactor the main loop in `main.py` to combine the status checking and timeout logic. The new flow for a running case is:
    a. Check remote status.
    b. If terminal (`success`, `failure`, `not_found`), update the DB and finish.
    c. If `'unreachable'`, log a warning and stop processing this case for this cycle.
    d. If, and only if, the status is `'running'`, then check the timeout condition.

**Update**: These changes have been applied to `src/services/workflow_submitter.py` and `src/main.py`. The new logic is more robust and prevents the creation of orphan jobs during network outages.

---
