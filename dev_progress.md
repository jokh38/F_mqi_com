# Development Progress

## Initial Code Flow Analysis

1.  **Startup**: The application starts, loads `config/config.yaml`.
2.  **File Watching**: `CaseScanner` watches a directory for new cases. Upon finding a stable directory, it adds it to the database with 'submitted' status.
3.  **Main Loop**:
    - Fetches 'submitted' cases.
    - Locks an available GPU resource for a case.
    - Updates case status to 'submitting'.
    - `WorkflowSubmitter` copies files to HPC and submits a job to `pueue`.
    - On success, updates case status to 'running' with the pueue task ID.
    - Fetches 'running' cases and checks their status on the HPC.
    - On completion, updates status to 'completed' or 'failed' and releases the GPU.
---

## Problem 1: Critical Bug - Crash on Missing Config

**Analysis**: In `src/main.py`, if the `config/config.yaml` file is not found, the program prints an error message but continues execution. This leads to an `UnboundLocalError` because the `initial_config` variable is never assigned, causing the application to crash.

**Solution**: Modified the `if __name__ == "__main__"` block in `src/main.py`.
1.  Imported the `sys` module.
2.  Added `sys.exit(1)` to the `except FileNotFoundError` and `except Exception` blocks. This ensures the program terminates gracefully with a non-zero exit code if the configuration cannot be loaded.
3.  Improved the error messages to be more descriptive.

---

## Problem 2: Misleading Log Message

**Analysis**: When `workflow_submitter.submit_workflow` returns `None`, it specifically means the remote submission command succeeded, but the application failed to parse the task ID from the command's output. The existing log message was ambiguous and didn't clearly state this.

**Solution**: Updated the error log message in `src/main.py` to be more explicit: `Workflow for case ID {case_id} submitted, but failed to parse Pueue Task ID from output. Marking as failed.`. This provides clearer insight into the failure mode.

---

## Problem 3: Potential for Indefinitely Stuck Cases

**Analysis**: If the remote HPC becomes unreachable, `workflow_submitter.get_workflow_status` correctly returns `'running'` to handle transient network errors. However, if the issue is permanent, a case could remain in the `'running'` state in the local database forever, causing the application to poll its status endlessly.

**Solution**: Implemented a timeout mechanism to detect and handle these stuck cases.
1.  **DB Schema Change**: Added a `status_updated_at` DATETIME column to the `cases` table in `src/common/db_manager.py`. This timestamp is updated every time a case's status changes.
2.  **Configuration**: Added a `running_case_timeout_hours` option to `config/config.yaml` (defaulting to 24 hours).
3.  **Timeout Logic**: In `src/main.py`, added a new step to the main loop that checks all `'running'` cases. If a case has not had a status update in more than the configured timeout period, it is automatically marked as `'failed'`, and its GPU resource is released. This prevents cases from being stuck indefinitely.
