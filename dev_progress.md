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

The function is defined as follows:

```python
def update_case_completion(self, case_id: int, status: str) -> None:
    # ...
    self.cursor.execute(
        """
        UPDATE cases
        SET status = ?,
            progress = 100,
            completed_at = ?,
            status_updated_at = ?,
            pueue_group = NULL,      -- This line erases the GPU group info
            pueue_task_id = NULL   -- This line erases the remote task ID
        WHERE case_id = ?
        """,
        # ...
    )
    # ...
```

When a case is marked as `completed` or `failed`, this query explicitly sets `pueue_group` and `pueue_task_id` to `NULL`. This action erases valuable historical data. After a case is finished, it becomes impossible to determine which GPU resource it ran on or what its corresponding remote task ID was. This information is crucial for auditing, debugging, and performance analysis.

The subsequent call to `db_manager.release_gpu_resource(case_id)` correctly handles freeing the resource in the `gpu_resources` table, so nullifying these fields in the `cases` table is not necessary for the system's resource management logic.

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

The logic correctly identifies when a case has been in the 'running' state for too long (defaulting to 24 hours). When a timeout is detected, the application performs these steps:
1.  Logs a critical error.
2.  Updates the case status to `failed` in the local database.
3.  Releases the GPU resource in the local database, making it available for new cases.

However, it **does not** attempt to stop the actual job running on the remote HPC. This creates a "zombie" process. The local application thinks the GPU is free, but the corresponding `pueue` slot on the HPC is still occupied by the timed-out job. This will prevent new jobs assigned to that `pueue` group from running, effectively creating a resource leak on the HPC.

**Proposed Solution:**

When a timeout is detected, the application must actively attempt to kill the job on the remote HPC before updating its local state.

1.  **Implement `kill_workflow`:** A new method, `kill_workflow(task_id)`, was added to the `WorkflowSubmitter` class. This method executes `pueue kill <task_id>` on the remote HPC via SSH.
2.  **Integrate into Timeout Logic:** The main loop in `src/main.py` was updated. Now, when a timeout occurs, it calls `workflow_submitter.kill_workflow(task_id)` and logs the result before proceeding to update the local database. This ensures that a best-effort attempt is made to clean up the remote resource, preventing the leak.
