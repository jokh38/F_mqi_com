# Development Progress Log

## Phase 1: Code Tracing and Initial Analysis

### 1. Entry Point: `src/main.py`
- **Logical Flow**: The application starts by initializing the configuration, logging, and the `DatabaseManager`. It ensures GPU resources listed in `config.yaml` exist in the database.
- A background thread for `CaseScanner` is started to watch for new case directories.
- The main loop consists of two parts:
    1.  **Monitoring**: Checks the status of `running` cases via `pueue` and updates their status to `completed` or `failed`.
    2.  **Submission**: Fetches `submitted` cases and iterates through them. For each case, it attempts to acquire a GPU lock.
- **Observation**: The logic in `main.py` appears sound and follows the dynamic allocation principle described in the old `dev_progress.md`. It correctly attempts to process multiple submitted cases in one cycle. The critical dependency for this process is the `db_manager.find_and_lock_any_available_gpu` method.

### 2. Database Interaction: `src/common/db_manager.py`
- **Analysis of `find_and_lock_any_available_gpu`**: This method is intended to atomically find and lock an available GPU to prevent race conditions.
- **Code:**
  ```python
  def find_and_lock_any_available_gpu(self, case_id: int) -> Optional[str]:
      with self.conn:
          self.cursor.execute(
              "SELECT pueue_group FROM gpu_resources WHERE status = 'available' LIMIT 1"
          )
          resource = self.cursor.fetchone()
          if resource:
              pueue_group = resource["pueue_group"]
              self.cursor.execute(
                  "UPDATE gpu_resources SET status = 'assigned', ... WHERE pueue_group = ? AND status = 'available'",
                  (case_id, pueue_group),
              )
              if self.cursor.rowcount > 0:
                  return pueue_group
      return None
  ```

### 3. Problem Identification: Race Condition in GPU Locking
- **Issue**: The current implementation suffers from a "Time-of-check to time-of-use" (TOCTOU) race condition.
- **Scenario**:
    1. Two processes (P1, P2) call the function simultaneously.
    2. The `SELECT` statement in both P1 and P2 can fetch the *same* available `pueue_group` before either process can run the `UPDATE`.
    3. P1 runs its `UPDATE` and successfully locks the resource.
    4. P2 runs its `UPDATE`, but the `WHERE ... status = 'available'` check fails because P1 has already changed the status. P2's update affects 0 rows.
- **Impact**: P2 fails to acquire a lock and returns `None`, even if other GPU resources were available. This leads to inefficient resource utilization, as multiple processes might compete for the first-listed available GPU, ignoring others.

### 4. Proposed Solution
- **Strategy**: The `SELECT` and `UPDATE` operations must be combined into a single, atomic query. This ensures that finding an available resource and claiming it happens in one indivisible step.
- **Proposed Implementation**:
  ```python
  def find_and_lock_any_available_gpu(self, case_id: int) -> Optional[str]:
      with self.conn:
          # This atomic UPDATE finds and claims a resource in one step
          self.cursor.execute(
              """
              UPDATE gpu_resources
              SET status = 'assigned', assigned_case_id = ?
              WHERE rowid = (
                  SELECT rowid FROM gpu_resources
                  WHERE status = 'available'
                  ORDER BY pueue_group -- for deterministic selection
                  LIMIT 1
              )
              """,
              (case_id,)
          )
          # If the update succeeded, find out which group was locked
          if self.cursor.rowcount > 0:
              self.cursor.execute(
                  "SELECT pueue_group FROM gpu_resources WHERE assigned_case_id = ?",
                  (case_id,)
              )
              resource = self.cursor.fetchone()
              if resource:
                  return resource["pueue_group"]
      return None
  ```
This new implementation is free of race conditions and will correctly allocate distinct available GPUs to concurrent processes.

## Phase 2: Solution Implementation

### 1. `db_manager.py` Correction
- **Status**: Implemented.
- **Action**: The `find_and_lock_any_available_gpu` method in `src/common/db_manager.py` was refactored.
- **Change**: The previous non-atomic `SELECT` followed by `UPDATE` was replaced with a single, atomic `UPDATE ... WHERE rowid = (SELECT ...)` statement.
- **Outcome**: This change resolves the identified race condition, ensuring that concurrent processes can safely and efficiently lock separate available GPU resources without conflict. The system's resource allocation is now robust.

## Phase 3: Continuing Code Trace and Deeper Analysis

### 1. Service Analysis: `src/services/case_scanner.py`
- **Analysis**: The `CaseScanner` uses a `StableDirectoryEventHandler` to monitor for new directories and wait for file activity to cease before adding them to the database. This is intended to prevent processing of incomplete cases.
- **Problem Identification**: A logic flaw was found in the `on_any_event` method. The timer for a new directory is only reset for subsequent file events if the timer has *already* been created. If a file creation event is detected by the OS before the directory creation event, the timer is never started for that file's parent directory, which can lead to the case being processed prematurely or not at all.
- **Proposed Solution**: The `on_any_event` method should be made stateless and more robust. For any given filesystem event, it should programmatically determine the top-level case directory (the direct child of the main `watch_path`) and reset the timer for that directory. This removes the dependency on the order of events and ensures any activity within a case directory correctly and consistently resets the stability timer.

### 2. `case_scanner.py` Correction
- **Status**: Implemented.
- **Action**: The `StableDirectoryEventHandler` class was refactored to be aware of the `watch_path`. The `on_any_event` method was entirely replaced.
- **Change**:
    1.  The `StableDirectoryEventHandler` constructor now accepts `watch_path`.
    2.  The `CaseScanner` now passes the `watch_path` to the handler.
    3.  The new `on_any_event` logic robustly identifies the top-level case directory for any filesystem event and resets its stability timer. It no longer depends on receiving a "directory created" event first.
- **Outcome**: The case scanner is now resilient to out-of-order filesystem events, preventing cases from being missed or processed prematurely. This makes the case ingestion mechanism significantly more reliable.
