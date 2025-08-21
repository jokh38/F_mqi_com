# Development Progress Report - Cycle 3

## Part 1: Logical Code Flow Analysis (Running Case Timeouts)

After hardening the submission and case detection logic, the next area for analysis is the management of long-running cases and timeouts. The intended flow is:

1.  **Get Running Cases**: The main loop queries the database for all cases with the status `running`.
2.  **Check Remote Status**: For each running case, the application connects to the remote HPC via `ssh` to get the current status of the corresponding `pueue` task.
3.  **Check for Timeout**: If the remote status is also `running`, the application then checks if the total time elapsed since the case entered the 'running' state has exceeded the configured timeout (e.g., 24 hours).
4.  **Handle Unreachability**: If the HPC cannot be reached, `get_workflow_status` returns `unreachable`, and the application logs a warning and moves to the next case, effectively skipping the timeout check.

## Part 2: Predicted Problem: Timeout Clock Paused During HPC Outages

A subtle but significant flaw exists in the timeout logic. The dependency on a successful HPC connection means the timeout mechanism is not guaranteed to be enforced correctly.

**Problem Trace:**

1.  A case, `C1`, starts running at time `T`. Its `status_updated_at` is set to `T`.
2.  An hour later, the network connection to the HPC is lost. The HPC itself is fine, and job `C1` continues to run, but the orchestrator application cannot reach it.
3.  For the next 30 hours, on every cycle, the application's call to `get_workflow_status` returns `unreachable`.
4.  Because the timeout check is inside an `if status == "running":` block, **the timeout logic is never executed** during this 30-hour outage. The timeout clock is effectively "paused".
5.  At time `T + 31 hours`, the network connection is restored.
6.  On the next cycle, `get_workflow_status` successfully connects and returns `running`.
7.  The application now *finally* executes the timeout check: `(T + 31 hours) - T` is greater than the 24-hour limit.
8.  The case `C1` is immediately marked as `failed` due to a timeout.

This behavior is incorrect. The timeout should represent the maximum allowed wall-clock time for a case, regardless of network conditions. A 24-hour timeout should expire after 24 hours, not 31.

## Part 3: Implemented Solution: Time-First Timeout Logic

To fix this, the logic for handling `running` cases in `src/main.py` was refactored to prioritize the timeout check above all else.

**New Logic:**

1.  **Check Timeout First**: For each `running` case, the application now **first** checks if the case has exceeded its time-to-live (`datetime.now(KST) - status_updated_at > timeout_delta`). This check is performed immediately after retrieving the case from the database, without any dependency on the remote HPC's status.
2.  **Act on Timeout**: If a case has timed out, it is immediately marked as `failed`, its GPU resource is released, and the loop continues to the next case.
3.  **Check Remote Status**: Only if a case has *not* timed out does the application proceed to check its status on the remote HPC.

This new "time-first" approach ensures that the timeout is a strict and reliable limit. It correctly handles prolonged HPC connectivity issues, preventing cases from running indefinitely and ensuring that the system's state reflects the intended operational constraints.
