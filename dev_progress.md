# MQI Communicator - Development and Debugging Trace

## Initial Analysis

The program execution starts at the `if __name__ == "__main__"` block in `src/main.py`.

**Logical Flow:**

1.  **Load Configuration:** The first step is to load the application configuration from `config/config.yaml`.
2.  **Setup Logging:** Based on the loaded configuration, it sets up a timezone-aware logger (`KSTFormatter`). If the config file is not found or is invalid, the program prints an error and exits.
3.  **Call Main Function:** The `main(initial_config)` function is called, which contains the core logic of the application.

Let's trace the execution into the `main` function.

---

## Trace Step 1: Database Initialization

- **File:** `src/main.py` -> `src/common/db_manager.py`
- **Functions:** `main()` -> `DatabaseManager()` -> `init_db()` -> `_create_tables()`

The application initializes the `DatabaseManager`, which connects to the SQLite database defined in `config.yaml`. The `init_db()` method ensures that two tables, `cases` and `gpu_resources`, are created if they do not already exist.

The `main` function then calls `db_manager.ensure_gpu_resource_exists(group)` for each GPU group listed in the config. This populates the `gpu_resources` table with the available processing queues (e.g., 'default', 'gpu_a', 'gpu_b'), marking them all as 'available'.

---

## Trace Step 2: Main Loop Analysis & First Problem Identification

- **File:** `src/main.py` (Main `while True` loop) -> `src/common/db_manager.py`
- **Functions:** `main()` -> `update_case_completion()`

The main loop continuously checks the status of 'running' cases. When a case is found to be completed (either "success" or "failure"), the following actions are taken in order:

1.  `db_manager.update_case_completion(case_id, status=final_status)`
2.  `db_manager.release_gpu_resource(case_id)`

### **Problem 1: Loss of Historical Data**

I've identified a design flaw in the `update_case_completion` function within `src/common/db_manager.py`.

**The Issue:**

The function was explicitly setting `pueue_group` and `pueue_task_id` to `NULL` upon case completion. This action erases valuable historical data, making it impossible to audit which resource a case ran on.

**Proposed Solution:**

Modify the `update_case_completion` function to remove the lines that set `pueue_group` and `pueue_task_id` to `NULL`. The historical data should be preserved in the `cases` table for completed records.

*Fix implemented by modifying `test_db_manager.py` first, then changing the function in `db_manager.py`.*

---

## Trace Step 3: Deeper Loop Analysis & Second Problem Identification

- **File:** `src/main.py` (Main `while True` loop, Part 1)
- **Functions:** `main()`

After fixing the first issue, I continued analyzing the main application loop. I focused on "Part 1: Manage all RUNNING cases".

### **Problem 2: HPC Resource Leak on Timeout**

I've identified a resource leak issue in the handling of timed-out jobs.

**The Issue:**

The logic correctly identifies when a case has been in the 'running' state for too long. However, it **does not** attempt to stop the actual job running on the remote HPC. This creates a "zombie" process, consuming an HPC resource slot even though the local application considers it free.

**Proposed Solution:**

When a timeout is detected, the application must actively attempt to kill the job on the remote HPC.

1.  **Implement `kill_workflow`:** A new method, `kill_workflow(task_id)`, was added to the `WorkflowSubmitter` class.
2.  **Integrate into Timeout Logic:** The main loop in `src/main.py` was updated to call `workflow_submitter.kill_workflow(task_id)` before marking a timed-out case as failed.

---

## Trace Step 4 (Round 2): Resilience of the Case Scanner

- **File:** `src/services/case_scanner.py`
- **Class:** `StableDirectoryEventHandler`
- **Method:** `_process_directory()`

In a second round of analysis, I focused on the resilience of the background services.

### **Problem 3: Lack of Resilience to DB Errors**

I've identified a critical flaw in the `StableDirectoryEventHandler`. If the `_process_directory` method fails to add a case to the database (due to a transient error like a temporary lock), the case is permanently dropped because the one-shot `threading.Timer` that triggered the processing is not rescheduled.

**Solution:**

A retry mechanism has been implemented directly within the `StableDirectoryEventHandler`.

1.  **Retry Logic:** The handler now maintains a retry counter for each directory path.
2.  **Updated `_process_directory`:**
    *   **On DB Success:** The case is added, and the timer and retry count for that path are cleared.
    *   **On DB Failure:** The handler logs the error and checks the retry count. If the count is below a configured maximum (defaulting to 3), it schedules a *new* timer to re-run `_process_directory` after a delay. If the maximum retries have been exceeded, it logs a critical error and gives up.
3.  **New Test Case:** A new test was added to `tests/services/test_case_scanner.py` to simulate a database failure and assert that the retry mechanism is correctly triggered.

This change significantly improves the robustness of the application, ensuring that temporary database issues do not lead to permanent data loss.
