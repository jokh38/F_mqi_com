# Development Progress

## Initial Analysis

*   **Codebase:** The application consists of a main process (`main.py`) that manages a queue of simulation cases, a file scanner (`case_scanner.py`) to detect new cases, a workflow submitter (`workflow_submitter.py`) to run cases on a remote HPC, and a database manager (`db_manager.py`) using SQLite.
*   **Workflow:**
    1.  A new case (directory) is copied into the `new_cases/` directory.
    2.  `CaseScanner` detects the new directory and adds it to the database with `status='submitted'`.
    3.  The main loop picks up the 'submitted' case.
    4.  It locks an available GPU resource from the database.
    5.  It uses `scp` to transfer the case to the HPC and `ssh` to submit it to a `pueue` queue. The case status becomes 'running'.
    6.  The main loop monitors 'running' cases by checking their status via `ssh` and `pueue`.
    7.  When a case finishes, its status is updated to 'completed' or 'failed', and the GPU resource is released.

## Problem 1: Non-functional Dashboard

*   **Observation:** The `src/dashboard.py` script is meant to be a monitoring tool but it displays static, hardcoded data. It does not connect to the database to show the actual state of the system.
*   **Impact:** The dashboard is currently misleading and does not fulfill its purpose.
*   **Solution:** Modify `src/dashboard.py` to connect to the application's SQLite database. It should periodically fetch the status of all cases and GPU resources and display them in a live-updating table using the `rich` library.
*   **Resolution:**
    *   The `src/dashboard.py` script was completely refactored.
    *   It now loads the database path from `config/config.yaml`.
    *   It uses the `DatabaseManager` to connect to the SQLite database.
    *   A `rich.live.Live` display is used to create a terminal-based UI that refreshes every 2 seconds.
    *   The dashboard now displays two tables: one for the status of all cases and one for the status of all GPU resources.
    *   The script handles the case where the database file has not been created yet, displaying a helpful message to the user.
    *   Dependencies (`pyyaml`, `rich`) were identified as missing from the environment and were installed via `pip install -r requirements.txt`.
    *   The run command was corrected to `python -m src.dashboard` to resolve a `ModuleNotFoundError`.
    *   The dashboard is now a functional monitoring tool.
