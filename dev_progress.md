# Development Progress

## Initial Analysis

### Code Flow
1.  **Startup**: `main.py` loads the config, initializes the database, and populates `gpu_resources` from the `pueue.groups` list in `config.yaml`.
2.  **Scanning**: `CaseScanner` is started in a background thread. It watches the `new_cases` directory.
3.  **Case Detection**: When a new directory is copied into `new_cases`, `CaseScanner` waits for it to be stable (no more file changes).
4.  **Database Insertion**: `CaseScanner` inserts a new record into the `cases` table with `status='submitted'` and a `pueue_group` that is hardcoded to be the first group from the config list (e.g., 'default').
5.  **Main Loop (Submission)**: The main loop queries for cases with `status='submitted'`. It tries to lock a GPU *only* for the specific `pueue_group` assigned to the case.
6.  **Main Loop (Monitoring)**: The main loop queries for `status='running'` cases, checks their status on the remote HPC via `pueue`, and updates them to `completed` or `failed` upon completion.

### Identified Problems
*   **Problem 1: Inflexible Case Assignment**: All new cases are assigned to a single, hardcoded default `pueue_group` by the `CaseScanner`. This prevents any ability to direct workflows to different resource pools.
*   **Problem 2: Inflexible GPU Allocation**: The application only attempts to acquire a GPU from the group pre-assigned to the case. If that specific group is busy, it waits, even if other GPU groups are free. This creates a resource bottleneck and fails to utilize all available hardware.

## Solution Implementation

The implemented solution shifts the application from a rigid, pre-assigned group system to a dynamic resource allocation model. This ensures that all available GPU resources are utilized efficiently.

### 1. `DatabaseManager` Modifications (`src/common/db_manager.py`)
*   **Schema Change**: The `pueue_group` column in the `cases` table was altered to allow `NULL` values. This allows a case to exist without an initial group assignment.
*   **Decoupled Case Creation**: The `add_case` method was modified to no longer accept a `pueue_group`. New cases are now added with a `NULL` group, to be assigned later.
*   **Dynamic Resource Locking**: The old `find_and_lock_available_gpu` method was replaced with `find_and_lock_any_available_gpu`. This new, transaction-safe method finds the first available GPU across *all* groups, locks it for a specific `case_id`, and returns the name of the group that was locked.
*   **Group Assignment Method**: A new `update_case_pueue_group` method was added to assign a `pueue_group` to a case after a resource has been successfully locked.

### 2. `CaseScanner` Refactoring (`src/services/case_scanner.py`)
*   The `CaseScanner` and its handler, `StableDirectoryEventHandler`, were refactored to be completely unaware of `pueue_group`s. Their sole responsibility is now to detect new, stable case directories and add them to the database using the simplified `add_case` method.

### 3. Main Loop Logic Update (`src/main.py`)
*   **Dynamic Allocation**: The main application loop was significantly changed. Instead of iterating through submitted cases and checking their pre-assigned (and bottlenecked) group, the new logic does the following:
    1.  It checks if there are any submitted cases.
    2.  It takes the oldest submitted case.
    3.  It calls `find_and_lock_any_available_gpu` to find and lock a resource from the general pool.
    4.  If a resource is locked, it updates the case with the assigned `pueue_group`, marks it as 'submitting', and sends it to the `WorkflowSubmitter`.
    5.  If no resources are available, it logs this and waits for the next cycle, preventing wasted processing.
*   This "first-come, first-served" approach to the next available resource ensures that all GPU groups are used, resolving the original bottleneck.
