# Development Progress Report

## Part 1: Logical Code Flow Analysis

The application is designed to process simulation cases by sending them to a remote High-Performance Computing (HPC) environment managed by `pueue`. The logical flow for a new case is as follows:

1.  **Detection**: A `CaseScanner` service watches a directory (`new_cases/`) for new case folders.
2.  **DB Registration**: Upon detection, the new case path is registered in a local SQLite database with the status `submitted`.
3.  **Processing Loop**: The main application loop in `main.py` periodically queries the database for cases with the `submitted` status.
4.  **Resource Locking**: For each `submitted` case, the application attempts to lock an available GPU resource. The resource is represented by a `pueue` group (e.g., `gpu_a`). If successful, the resource's status is changed to `assigned` in the database.
5.  **Pre-Submission State Change**: The case's status is updated to `submitting`. **This is a critical point in the original code.**
6.  **Remote Submission**: The `WorkflowSubmitter` class is used to:
    a.  Transfer the case files to the HPC via `scp`.
    b.  Submit a new job to the remote `pueue` daemon via `ssh`.
7.  **Post-Submission State Change**: If the submission is successful and a `pueue_task_id` is received, the case's status is updated to `running` in the database.

## Part 2: Predicted Problem and Root Cause

A critical flaw was identified in the original workflow. The state of the application could become inconsistent if a crash or shutdown occurred during the "danger zone"—the period after the case status was set to `submitting` but before the remote job's `pueue_task_id` was successfully stored in the local database.

**Problem Trace:**

1.  A case `C1` is picked up, and a GPU resource is locked for it.
2.  The status of `C1` is updated to `submitting` in the database.
3.  The `submit_workflow` function successfully creates a job on the remote HPC.
4.  **The application crashes** before the `pueue_task_id` for the new job is saved for `C1` and its status is updated to `running`.

**Consequence on Restart:**

1.  The application starts up and its recovery logic finds case `C1` in the `submitting` state.
2.  The original code treated this state as an unrecoverable error. It would automatically change the case's status to `failed` and release the locked GPU resource in the database.
3.  **This created a severe inconsistency:**
    *   **Local State:** The database shows case `C1` as `failed` and the GPU as `available`.
    *   **Remote State:** An **orphaned job** for case `C1` is still running (or queued) on the HPC, consuming a real GPU.
4.  The application, now believing the GPU is free, would likely assign a new case to it, leading to resource conflicts, job failures, and an unreliable system.

## Part 3: Implemented Solution

To resolve this, the system was refactored to be self-healing and resilient to crashes during submission. The solution involved adding traceability to remote jobs and implementing intelligent recovery logic.

### 1. Enhanced Workflow Submitter for Traceability

The `src/services/workflow_submitter.py` file was modified:

*   The `submit_workflow` method was updated to accept the `case_id`. It now uses the `pueue add` command's `--label` flag to tag each remote job with a unique, predictable identifier (e.g., `mqic_case_123`). This creates a durable link between the local database record and the remote process.
*   A new method, `find_task_by_label(label)`, was created. This method connects to the HPC, queries `pueue status --json`, and searches for a job with the specified label, returning the job's details if found.

### 2. Implemented Self-Healing Recovery Logic

The main loop in `src/main.py` was significantly improved:

*   The logic for handling cases in the `submitting` state at startup was completely replaced.
*   The new "self-healing" logic is as follows:
    1.  For each case found in the `submitting` state, construct the expected label (e.g., `mqic_case_123`).
    2.  Call the new `workflow_submitter.find_task_by_label()` method to check if a corresponding job already exists on the HPC.
    3.  **If a matching remote task is found:** This means the submission succeeded, but the local update failed. The application now recovers automatically by:
        *   Extracting the `pueue_task_id` from the remote job's data.
        *   Updating the local case record with this `pueue_task_id`.
        *   Changing the case's status to `running`.
        *   This action successfully "recovers" the orphaned process and brings the system back into a consistent state.
    4.  **If no matching remote task is found:** This confirms that the submission process failed before a job was created. It is now safe to:
        *   Mark the case as `failed`.
        *   Release the GPU resource in the database.

This solution eliminates the race condition and ensures that the application's state remains consistent even in the event of a crash during the critical submission phase.
