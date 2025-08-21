# MQI Communicator - Final Debugging Report

This document tracks the logical execution flow of the MQI Communicator application, identifies existing logic, finds latent bugs, and records the corresponding fixes.

## Phase 1: Initial Code Analysis and Tracing

### 1.1. Application Entry (`src/main.py`)
- **Trace**: Execution starts by loading `config/config.yaml`. If this file is missing, the application prints an error and exits, which is correct behavior. It then calls `setup_logging()`.
- **Assessment**: The initial startup logic is sound.

### 1.2. Database Initialization (`src/common/db_manager.py`)
- **Trace**: `main()` instantiates `DatabaseManager` and calls `init_db()`. This connects to the SQLite file specified in the config and runs `CREATE TABLE IF NOT EXISTS` for the `cases` and `gpu_resources` tables.
- **ISSUE #1 (Bug Found)**: The code attempts to connect to `data/mqi_communicator.db` without ensuring the parent directory (`data/`) exists.
- **Impact**: This will cause a `sqlite3.OperationalError` and crash the application on first run in a clean environment.
- **FIX #1 (Applied)**: In `src/common/db_manager.py`, added `os.makedirs` to the `__init__` method to ensure the database's parent directory exists before the `sqlite3.connect` call.

### 1.3. Component Initialization (`src/main.py`)
- **Trace**: `main()` proceeds to initialize the `WorkflowSubmitter` and `CaseScanner` components.
- **ISSUE #2 (Bug Found)**: The `CaseScanner` is initialized with a `watch_path` from the config (`new_cases/`). The underlying `watchdog` library requires this path to exist before it can be observed. The code did not ensure this.
- **Impact**: This will raise an exception and crash the application on first run in a clean environment.
- **FIX #2 (Applied)**: In `src/main.py`, added `os.makedirs` to ensure the `watch_path` exists before `CaseScanner` is initialized.

## Phase 2: Full Application Lifecycle Simulation

### 2.1. New Case Detection (`src/services/case_scanner.py`)
- **Trace**: A new directory copied into `watch_path` triggers events in `CaseScanner`. The `StableDirectoryEventHandler` correctly uses a timer to wait for file activity to stop before processing.
- **Assessment**: The debouncing logic is sound and prevents processing of incomplete data. Once stable, the case is added to the database with `status='submitted'`. This works as intended.

### 2.2. Case Submission (`src/main.py`)
- **Trace**: The main loop finds the `submitted` case. It calls `db_manager.find_and_lock_any_available_gpu()`, which correctly performs an atomic update to assign an available GPU resource. The case status is changed to `submitting`. `workflow_submitter.submit_workflow()` is called, which shells out to `scp` and `ssh` to transfer files and queue the job remotely. The case status is finally updated to `running`.
- **Assessment**: The submission flow, resource locking, and state transitions are logically correct.

### 2.3. Case Monitoring and Timeout Handling (`src/main.py`)
- **Trace**: The main loop contains logic to handle potentially problematic cases.
    - **Stuck "submitting" cases**: If a case is found in the `submitting` state at the start of a loop, it is reset to `submitted` and its GPU is released. This correctly handles a crash that might occur during the submission process itself.
    - **Timed-out "running" cases**: The `cases` table includes a `status_updated_at` field. The main loop checks if any `running` case has not had a status update in over 24 hours (configurable). If a timeout is detected, the case is marked as `failed` and its GPU is released.
- **Assessment**: The pre-existing logic for handling stuck and timed-out cases is robust and essential for long-term stability.

### 2.4. Case Completion (`src/main.py`)
- **Trace**: For active `running` cases, the main loop calls `workflow_submitter.get_workflow_status()`. When the remote job finishes, the status is updated to `completed` or `failed` in the database, and `db_manager.release_gpu_resource()` is called.
- **Assessment**: The completion and resource cleanup logic is sound.

## Summary of Changes
- **`src/common/db_manager.py`**: Fixed a startup crash by ensuring the database directory exists.
- **`src/main.py`**: Fixed a startup crash by ensuring the case scanner's watch directory exists.
- **`dev_progress.md`**: This file has been created to provide a comprehensive record of the debugging process and its findings.

The application is now considered logically sound and robust for deployment.

---

# Phase 3: Directory Structure Refactoring

## 3.1. Objective
- The objective is to change the name of the directory where the database is stored from `data` to `database`.
- This requires updating the configuration and verifying that no part of the application relies on the old hardcoded path.

## 3.2. Logical Execution Trace and Problem Identification

### Step 1: Program Startup
- **Trace**: The application starts, and `src/main.py` loads the configuration from `config/config.yaml`.
- **Code**: `db_manager = DatabaseManager(config=config)` is called.
- **Problem Prediction**: The `DatabaseManager` will read the `database.path` key from the configuration. Currently, this value is `data/mqi_communicator.db`. As a result, the application will create a directory named `data`, not `database`. This contradicts the new requirement.

### Step 1: Solution
- **Action**: Modified `config/config.yaml`.
- **Change**: Updated the `database.path` from `data/mqi_communicator.db` to `database/mqi_communicator.db`.
- **Outcome**: The application will now create the `database` directory and store the SQLite database inside it, as per the requirement.

### Step 2: Search for Hardcoded Paths
- **Trace**: After fixing the configuration, the next logical step is to ensure no other part of the application uses a hardcoded path to the old `data` directory. A `grep` search was performed.
- **Problem Prediction**: The `grep` search revealed hardcoded `"data/"` paths in `.gitignore` and a commented-out line in `backups/backup.bat`. While the test suite in `tests/common/test_db_manager.py` does not use the `data` directory (it correctly uses a temporary database in the root for testing), these other files need to be updated to maintain consistency and prevent future issues.

### Step 2: Solution
- **Action**: Modified `.gitignore` and `backups/backup.bat`.
- **Change 1**: In `.gitignore`, changed `data/` to `database/` to ensure the new database directory is ignored by version control.
- **Change 2**: In `backups/backup.bat`, updated the commented-out example command to use the new `database/` path for consistency.
- **Outcome**: All hardcoded references to the old `data/` directory have been updated. The application is now consistent.

## 3.3. Final Verification
- **Verification**: A final review of all changes confirms that the `config.yaml` file has been updated, and all other discovered references to the old `data/` path in `.gitignore` and `backups/backup.bat` have been corrected to `database/`.
- **Conclusion**: The application is now correctly configured to use the `database` directory for its database storage. The logical flow is sound, and no further issues are predicted related to this change.
