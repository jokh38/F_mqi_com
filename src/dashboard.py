"""
This module provides a CLI dashboard to display the status of the MQI Communicator.

It uses the 'rich' library to render tables and live updates, showing the
state of active cases and GPU resources from the database.
"""

import time
from rich.console import Console
from rich.live import Live
from rich.table import Table


# Placeholder for the main function that will run the dashboard
def display_dashboard() -> None:
    """
    Displays a live-updating dashboard with the status of cases and resources.
    """
    console = Console()
    console.print("[bold cyan]MQI Communicator Dashboard[/bold cyan]")
    console.print("Initializing...")

    # This is a placeholder for the live-updating table.
    # In the future, this will fetch data from the db_manager.
    table = Table(title="Live Case Status")
    table.add_column("Case ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Case Path", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Progress", justify="right", style="yellow")
    table.add_column("Pueue Group", style="blue")

    # Example row
    table.add_row("1", "/path/to/case_001", "running", "50%", "gpu0")

    with Live(table, screen=True, redirect_stderr=False) as live:
        live.update(table)
        # In a real implementation, this would be a loop that periodically
        # fetches new data and updates the table.
        time.sleep(5)

    console.print("Dashboard closed.")


if __name__ == "__main__":
    display_dashboard()
