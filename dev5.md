# Developer 5: CI/CD and Test Lead - Progress Log

This document tracks the work of Developer 5, who is responsible for testing, CI/CD, and the implementation of the dashboard.

## End-to-End (E2E) Test Scenario

This scenario describes the complete, successful workflow of the MQI Communicator, which will be used as the basis for integration and end-to-end testing.

### Prerequisites

1.  **Local (Windows):**
    *   The MQI Communicator application is running.
    *   The `watch_directory` specified in `config.yaml` exists and is empty.
    *   The `download_directory` specified in `config.yaml` exists and is empty.
    *   The SQLite database is initialized with the `cases` and `gpu_resources` tables.
    *   At least one GPU resource in `gpu_resources` is marked as `available`.

2.  **Remote (HPC - Ubuntu):**
    *   The `pueue` daemon is running.
    *   The target directories on the HPC exist and are accessible via `scp`.
    *   The remote simulation scripts (`interpreter.py`, `moquisim.py`) are in place.

### Test Steps

1.  **Case Creation (Local):**
    *   A new directory (e.g., `case_001`) containing the necessary input files is manually created inside the `watch_directory`.
    *   **Expected `case_scanner` Behavior:** The file system watcher detects the new directory.
    *   **Expected DB State:** A new entry is created in the `cases` table with `status: 'submitted'` and `progress: 0`. The corresponding GPU resource in `gpu_resources` is updated to `status: 'busy'` and `assigned_case_id` is set.

2.  **Workflow Submission (Local):**
    *   **Expected `workflow_submitter` Behavior:**
        *   The `case_001` directory is compressed into a `.zip` file.
        *   The `.zip` file is transferred to the HPC via `scp`.
        *   A `pueue` command is constructed and executed over SSH to start the remote workflow. This workflow should include steps to unzip the case, run `interpreter.py`, run `moquisim.py`, and finally, package the results.
    *   **Expected DB State:** The `cases` table entry for `case_001` is updated to `status: 'running'` and `progress` is updated (e.g., to 25%).

3.  **Remote Processing (HPC):**
    *   **Expected `pueue` Behavior:** The tasks for `case_001` are executed sequentially.
    *   **Verification:** This step is verified by SSHing into the HPC and checking `pueue status` and `pueue logs`. The simulation scripts should generate output files.

4.  **Result Retrieval (Local):**
    *   After the remote `pueue` workflow completes, the `workflow_submitter` (or a dedicated result fetcher) should detect completion.
    *   **Expected Behavior:** The final result files are transferred from the HPC back to the local `download_directory` via `scp`.
    *   **Verification:** The result files for `case_001` should appear in the local `download_directory`.

5.  **Finalization (Local):**
    *   **Expected Behavior:** The local application confirms the results have been downloaded.
    *   **Expected DB State:**
        *   The `cases` table entry for `case_001` is updated to `status: 'completed'` and `progress: 100`. The `completed_at` timestamp is set.
        *   The corresponding `gpu_resources` entry is reset to `status: 'available'` and `assigned_case_id` is set to `NULL`.

### Failure Scenario (Brief)
A separate test should be designed for failures. For example, if `moquisim.py` fails:
*   `pueue` reports a failed task.
*   The `cases` table is updated to `status: 'failed'`.
*   The `gpu_resources` table is freed up (`status: 'available'`).
