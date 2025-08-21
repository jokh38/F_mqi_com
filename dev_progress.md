# Development Progress Log

## Initial State Analysis

**Date:** 2025-08-21

### Logical Execution Flow Trace

1.  **Program Start (`src/main.py`)**: The application starts by loading the `config/config.yaml` file.
2.  **Logging Setup**: `setup_logging` is called, which correctly configures timezone-aware logging to both a file and the console.
3.  **Component Initialization**:
    *   `DatabaseManager` is initialized. It connects to the SQLite database specified in the config and calls `init_db()` to create the `cases` and `gpu_resources` tables if they don't exist.
    *   GPU resources listed in the config (`pueue.groups`) are added to the `gpu_resources` table via `ensure_gpu_resource_exists`. This correctly populates the available resources.
    *   `WorkflowSubmitter` is initialized with HPC connection details.
    *   `CaseScanner` is initialized. It sets up a `watchdog` observer to monitor the `new_cases` directory recursively.
4.  **Service Start**: `case_scanner.start()` is called, which starts the directory monitoring in a background thread.
5.  **Main Loop (`while True`)**: The application enters its main processing loop.
    *   **Idle State**: In an idle state (no cases in the database), the loop checks for cases in `submitting` and `running` states. Finding none, it then checks for `submitted` cases. Finding none, it sleeps for the configured interval.
    *   **New Case Detection**: When a new directory (e.g., `new_cases/case_001`) is created and its contents are stable, the `CaseScanner`'s event handler adds the case to the database with the status `submitted`.
    *   **Case Submission**: On the next loop iteration, the main loop finds the new case via `get_cases_by_status('submitted')`.
        *   It attempts to lock a GPU resource using `find_and_lock_any_available_gpu`. This is an atomic and safe operation.
        *   If a resource is locked, the case status is updated to `submitting`.
        *   The code then calls `workflow_submitter.submit_workflow()` to transfer the files and queue the job on the remote HPC.

---
## Problem 1 (Resolved)

**File**: `src/services/workflow_submitter.py`
**Function**: `submit_workflow`

**Problem Description**: The command passed to the remote `pueue add` service was constructed as a single string. This string was then passed as a single argument to `pueue add -- ...`. The `pueue` daemon would attempt to execute a program with that literal name (including spaces, `cd`, and `&&`), which would fail because no such executable exists. The shell operators `cd` and `&&` were not being interpreted.

**Resolution (2025-08-21)**: The remote command is now correctly wrapped to be executed by a remote shell. The arguments passed to `pueue add -- ...` are now `sh -c "cd ... && ..."` which ensures the shell meta-characters are interpreted correctly on the remote HPC. This was fixed by changing the construction of the `ssh_command` list in the `submit_workflow` method.

---
## Problem 2 (Resolved)

**File**: `src/main.py`, `src/common/db_manager.py`

**Problem Description**: If the application crashed after locking a GPU resource for a `submitted` case but before changing the case's status to `submitting`, a resource leak would occur on the next run. The `submitted` case would be processed again, locking a *new* GPU, because the logic did not check if one was already assigned. The original GPU resource would remain in the `assigned` state and unused for the duration of the run.

**Resolution (2025-08-21)**:
1.  A new method, `get_gpu_resource_by_case_id(case_id)`, was added to `DatabaseManager` to find which GPU (if any) is assigned to a case.
2.  The logic in `main.py` for handling `submitted` cases was updated. Before attempting to lock a new GPU, it now calls the new method. If a resource is already assigned (recovering from a crash), it uses that one. Otherwise, it attempts to lock a new one. This makes the submission step robust against this specific crash scenario.
