# MQI Communicator Development Log

## Phase 1: Initial Code Analysis and Problem Prediction

### Code Flow Analysis (Pre-correction)

1.  **Application Start**: The `main` function in `src/main.py` is the entry point.
2.  **Initialization**: It loads configuration from `config/config.yaml`, sets up logging, and initializes `DatabaseManager`, `WorkflowSubmitter`, and `CaseScanner`.
3.  **Directory Watching**: `CaseScanner` is started in a background thread. It monitors the `scanner.watch_path` (e.g., `new_cases/`) for new directories. Upon finding a new directory, it registers it in the database with a status of `"submitted"`.
4.  **Main Processing Loop**: The application enters an infinite loop that performs the following actions:
    - It queries the database for cases with the status `"submitted"`.
    - It iterates through these "submitted" cases.
    - For each case, it updates its status to `"running"`.
    - It then calls `workflow_submitter.submit_workflow()` to send the job to the remote HPC using `scp` and `pueue`.
    - The loop then repeats, only ever looking for new `"submitted"` cases.

### Predicted Problem

- **Orphaned Jobs**: The main loop's logic is flawed. It changes a case's status from `"submitted"` to `"running"` but never checks on it again. The system has no mechanism to determine if a "running" job has completed successfully or failed on the remote HPC. This will result in all processed cases being permanently stuck in the `"running"` state in the database. The application "fires and forgets" its jobs, which is not a viable long-term strategy for a reliable system.

---

## Phase 2: Problem Resolution and Implementation

### Problem 1: Fire-and-Forget Job Submission

- **Issue**: The main loop in `src/main.py` only processed `submitted` cases and had no logic to monitor `running` cases.
- **Root Cause**: Lack of a feedback mechanism to check the status of jobs after they were submitted to the remote HPC's `Pueue` service.

### Solution Implementation

To fix this, several components were modified to create a full feedback loop:

1.  **Database Schema Update (`src/common/db_manager.py`):**
    - Added a `pueue_task_id` column to the `cases` table. This is crucial for linking a local case to its remote job.
    - Added a new method, `update_case_pueue_task_id`, to store this ID in the database.

2.  **Workflow Submission Enhancement (`src/services/workflow_submitter.py`):**
    - The `submit_workflow` method was modified to capture the output from the `pueue add` command and parse it to extract the newly created `pueue_task_id`. This ID is now returned by the method.
    - A new method, `get_workflow_status`, was implemented. It connects to the remote HPC via SSH, executes `pueue status --json`, and parses the result to determine the true status of a job (`success`, `failure`, `running`, or `not_found`).

3.  **Main Loop Logic Correction (`src/main.py`):**
    - The main application loop was fundamentally restructured.
    - **Part 1 (Status Check):** The loop now first queries for all cases with the `running` status. For each one, it uses the stored `pueue_task_id` to call `get_workflow_status` and updates the case to `completed` or `failed` based on the result.
    - **Part 2 (New Submissions):** The loop then processes `submitted` cases. When a case is successfully submitted, the returned `pueue_task_id` is immediately saved to the database, and the status is updated to `running`.

### Corrected Code Flow

1.  **Application Start & Initialization**: Same as before.
2.  **Directory Watching**: Same as before (`CaseScanner` adds new cases as `"submitted"`).
3.  **Main Processing Loop (Corrected)**: The infinite loop now performs two distinct functions in each cycle:
    a. **Check Running Jobs**: It queries the DB for `running` cases. For each one, it calls `get_workflow_status` to check the job on the remote HPC. If a job is done (`success` or `failure`), the case's status is updated to `completed` or `failed` in the DB.
    b. **Process New Jobs**: It queries the DB for `submitted` cases. It calls `submit_workflow`, receives a `pueue_task_id`, and updates the case in the DB with this new ID and a status of `running`.
    c. **Sleep**: The loop waits for the configured interval before repeating.

This new flow ensures that all jobs are tracked from submission to completion, resolving the original "orphaned jobs" problem.
