# MQI Communicator - Development Progress Report (Cycle 2)

## Initial State Analysis

This analysis begins with the codebase in a state where the previous fixes have been applied. Specifically:
- The logic in `main.py` now explicitly handles `'success'`, `'failure'`, and the ambiguous `'not_found'` statuses for remote jobs.
- The unit tests in `tests/test_main.py` have been repaired and the full test suite passes.

The goal of this second analysis cycle is to identify more subtle issues that might arise in less common scenarios, such as prolonged network outages or other error conditions. The logical trace will now explore these edge cases.

---

## Problem 3: HPC Resource Leak on Unsuccessful Timeout Kill

### Description

A critical issue was identified when a case times out while the remote HPC is unreachable. The logical flow is as follows:

1.  A `running` case exceeds its `running_case_timeout_hours` limit.
2.  The application correctly identifies the timeout and attempts to kill the remote job by calling `workflow_submitter.kill_workflow(task_id)`.
3.  If the HPC is unreachable, the `ssh` command within `kill_workflow` fails, causing the method to return `False`.
4.  The main loop logs a warning but proceeds to the next steps.
5.  It calls `db_manager.update_case_completion(case_id, status="failed")`.
6.  Crucially, it calls `db_manager.release_gpu_resource(case_id)`, which sets the corresponding `gpu_resources` entry status to `'available'`.

This creates a dangerous state inconsistency. The application now believes the GPU resource is free and can assign a new job to it. However, the original job on the HPC was never terminated and may still be running or queued, consuming the actual physical resource.

### Impact

This can lead to a resource leak on the HPC, where jobs that have timed out in the application continue to occupy hardware. It can also cause resource contention, where the application submits a new job to a GPU that it believes is free, but is actually still occupied by the "zombie" job that failed to be killed. This could lead to unpredictable errors or failures on the HPC side.

### Proposed Solution: The 'Zombie' State

To address this, a new state, `'zombie'`, will be introduced for GPU resources. This state indicates that the resource's true status on the HPC is unknown and untrusted.

1.  **Modified Timeout Logic**: When a case times out and the subsequent `kill_workflow` command fails (e.g., due to an unreachable HPC), the application will **not** release the GPU resource. Instead, it will update the resource's status to `'zombie'`. The `assigned_case_id` will be kept for future reference.

2.  **Quarantine**: The `find_and_lock_any_available_gpu` method will continue to only look for resources with the `'available'` status. This effectively quarantines the `'zombie'` resource, preventing it from being assigned to any new cases.

3.  **New Zombie Recovery Loop**: A new section will be added to the main application loop to manage zombie resources. This loop will:
    - Query the database for all resources with `status = 'zombie'`.
    - For each zombie resource, find its associated `pueue_task_id` from the linked case.
    - Periodically attempt to re-run `kill_workflow` for that `pueue_task_id`.
    - If the kill command eventually succeeds, the application will then, and only then, set the resource's status back to `'available'` and clear its associated case ID.

This design ensures that a resource is not considered free until the application has positive confirmation that the rogue job on the HPC has been terminated, thus preventing the resource leak.

---

## Final Summary of Cycle 2

The second analysis cycle successfully identified and resolved a critical resource leak issue.

1.  **Problem Identified**: A scenario was discovered where a case timing out during an HPC outage would lead to the application releasing a GPU resource locally, even though the remote job was never confirmed to be terminated.
2.  **Solution Implemented**: To fix this, a `'zombie'` state for GPU resources was introduced. This quarantines resources whose remote status is unknown. A recovery loop was added to the main application to periodically attempt to kill the associated remote jobs and, upon success, return the resource to the `'available'` pool.
3.  **Code Quality**: The implementation was accompanied by corresponding updates to the unit tests. All code quality checks (`black`, `flake8`, `mypy`, `pytest`) now pass.

The application is now significantly more resilient to network failures and better protected against HPC resource leaks.
