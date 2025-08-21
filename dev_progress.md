# Development Progress

## Initial State Analysis

The application is designed to watch a directory for new "cases" (which are directories), copy them to a remote HPC via `scp`, and submit them as jobs to a `pueue` task manager via `ssh`. It then polls `pueue` to track the status of these jobs and updates a local SQLite database accordingly.

### Identified Potential Issues:

1.  **Race Condition in `CaseScanner`**: The system detects new directories immediately upon creation, not after they are fully copied. This can lead to processing incomplete data.
2.  **Error Handling in `WorkflowSubmitter`**: The `get_workflow_status` method masks all errors (network, parsing, etc.) by returning a default `"running"` status. This can cause infinite loops if a persistent error occurs.
3.  **Incomplete GPU Resource Management**: The database contains a `gpu_resources` table, but the application logic does not use it. This suggests an incomplete or abandoned feature for managing resource availability.

---

## Problem 1: Race Condition with File Copying

**Logical Flow:**
1. A user starts copying a large directory `case_ABC` into the `new_cases` folder.
2. The `watchdog` observer in `CaseScanner` fires an `on_created` event for the `case_ABC` directory as soon as it exists.
3. `CaseScanner` immediately adds `case_ABC` to the database with `status='submitted'`.
4. The `main` loop picks up the 'submitted' case.
5. `WorkflowSubmitter` is called, and it uses `scp -r` to copy the `case_ABC` directory to the remote server.
6. Because the user's original copy operation is not yet finished, `scp` copies an incomplete set of files.
7. The remote job is submitted to `pueue` but fails because the data is incomplete.

**Solution Implemented:**
I have replaced the simple `DirectoryCreatedEventHandler` with a new, more robust `StableDirectoryEventHandler` in `src/services/case_scanner.py`.

1.  **Event-Driven Timer:** The new handler listens for `on_any_event`. When a new directory is created, it starts a 5-second timer. If any subsequent file activity (creation, modification) occurs within that directory, the timer is reset.
2.  **Recursive Watching:** The `watchdog` observer is now set to be recursive (`recursive=True`). This is crucial as it allows the handler to detect file changes *inside* the new directories, which is necessary for the timer-reset logic.
3.  **Database Insertion on Stability:** The case is only added to the database by the timer's callback function, which executes only after the directory has been "quiet" for the entire 5-second duration.
4.  **Concurrency Control:** A `threading.Lock` is used to prevent race conditions within the event handler itself, ensuring that the dictionary of timers is managed safely.
5.  **Graceful Shutdown:** The `stop()` method in `CaseScanner` was updated to safely cancel all pending timers before shutting down the observer thread.

This change resolves the race condition by ensuring that directories are only processed after they are fully copied.

---

## Problem 2: Error Handling in `WorkflowSubmitter`

**Logical Flow:**
1. A running case is being monitored by the main loop.
2. The `get_workflow_status` method is called.
3. The `ssh` command to get the `pueue status --json` fails, perhaps due to a temporary network issue, or perhaps because the JSON output format from `pueue` has changed in an update.
4. The `except` block in `get_workflow_status` catches the `subprocess.CalledProcessError` or `json.JSONDecodeError`.
5. The function prints a warning to the console (which may not be monitored) and returns the status `"running"`.
6. The main loop receives the `"running"` status and takes no action, assuming the job is still executing correctly.
7. If the error is persistent (like the JSON format change), this will continue indefinitely. The application will never mark the job as failed and will be stuck in an infinite check-and-retry loop.

**Solution Implemented:**
I have refactored the `get_workflow_status` method in `src/services/workflow_submitter.py` to be more resilient and provide better diagnostics.

1.  **Integrated Logging:** All `print()` statements in the exception handling have been replaced with `logger.warning()` or `logger.error()`, ensuring that all issues are recorded in the application's log file. I also added logging to the `submit_workflow` method for more comprehensive tracking.
2.  **Differentiated Error Handling:** The single, broad `except` block has been replaced with multiple, specific blocks:
    *   `subprocess.TimeoutExpired` and `subprocess.CalledProcessError` are now caught separately. They are treated as potentially transient network/SSH issues, so they log a warning and return `'running'` to allow the application to retry.
    *   `json.JSONDecodeError` and `KeyError` are caught together. These errors indicate that the data from `pueue status --json` is not in the expected format. This is considered an unrecoverable error, so the function now logs an error and returns `'failure'`.
3.  **No `main.py` Changes Needed:** Because the unrecoverable errors now return the standard `'failure'` status, the existing logic in the `main.py` loop correctly handles them by marking the case as failed in the database. This avoids introducing a new status type and keeps the change isolated to the `WorkflowSubmitter`.

This change prevents the application from getting stuck in an infinite loop on persistent data parsing errors and improves the overall observability of the submission and status checking process.

---

## Problem 3: Incomplete GPU Resource Management

**Logical Flow:**
1. The `db_manager.py` defines a `gpu_resources` table, implying a system for managing a pool of available GPU resources.
2. The `main.py` logic for processing new cases (`get_cases_by_status("submitted")`) completely ignores this table.
3. It immediately submits a new case to a `pueue_group` without checking if a resource in that group is available.
4. This could lead to overloading the remote system if more cases are submitted than there are available GPUs/slots, or it could simply mean that the intended resource management feature is not working.

**Solution Implemented:**
I have successfully implemented the GPU resource management feature by modifying both the database manager and the main application loop.

1.  **New Database Methods:**
    *   In `src/common/db_manager.py`, I added the `find_and_lock_available_gpu(pueue_group, case_id)` method. It uses a `with self.conn:` block to ensure an atomic transaction, preventing race conditions where two processes might try to grab the same resource. It finds a resource matching the `pueue_group` with a status of `'available'`, and if found, updates its status to `'assigned'` and links the `case_id`.
    *   I also added the `release_gpu_resource(case_id)` method, which finds the resource assigned to a completed or failed case and resets its status to `'available'`.

2.  **Main Loop Integration (`src/main.py`):**
    *   **Locking:** The loop for processing `'submitted'` cases has been updated. It now iterates through submitted cases and first calls `find_and_lock_available_gpu`. If the lock is successful, it proceeds to submit the case. If not, it logs that the resource is busy and skips the case, which will be retried on the next cycle.
    *   **Releasing:** The logic for handling terminal states (`'success'`, `'failure'`, `'not_found'`) has been refactored. In all these scenarios, a call to `release_gpu_resource(case_id)` is now made, ensuring that the resource becomes available for another case.
    *   **Failure Safety:** The `try...except` block for case submission was also updated to ensure that if the submission process fails for any reason *after* a resource has been locked, the resource is immediately released, preventing it from being stuck in an `'assigned'` state.

This completes the implementation of the resource management feature, making the application more robust and preventing the system from being overloaded.

---

## Problem 4: Initializing GPU Resources

**Logical Flow:**
1. The new GPU resource management logic is now in place.
2. However, the `gpu_resources` table in the database is created empty.
3. The application will run, but the `find_and_lock_available_gpu` function will never find any resources to lock.
4. All submitted cases will be skipped because no resources are 'available', effectively halting the entire process.

**Solution Implemented:**
I have implemented a mechanism to automatically populate the `gpu_resources` table at application startup.

1.  **Configuration Update:** In `config/config.yaml`, I changed the `pueue` configuration from a single `default_group` key to a `groups` key holding a list of all manageable GPU group names (e.g., `["default", "gpu_a"]`).
2.  **New Database Method:** In `src/common/db_manager.py`, I added an idempotent `ensure_gpu_resource_exists(pueue_group)` method. This method checks if a resource for the given group exists and, if not, creates it with a default status of `'available'`. This prevents duplicate entries if the application is restarted.
3.  **Initialization in `main.py`:** During the startup sequence in `src/main.py`, I added logic that reads the list of groups from the config file and calls `ensure_gpu_resource_exists()` for each one, ensuring the database is always in sync with the configuration. The first group in the config list is now used as the default group for newly scanned cases.

This change makes the GPU resource management feature fully operational, as the resources are now correctly initialized and ready to be used by the locking mechanism.

---
**All identified problems have now been addressed.**
