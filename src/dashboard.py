"""
This module provides a CLI dashboard to display the status of the MQI Communicator.

It uses the 'rich' library to render tables and live updates, showing the
state of active cases and GPU resources from the database.
"""

import time
import yaml
from pathlib import Path
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.align import Align
from datetime import datetime

from src.common.db_manager import DatabaseManager, KST

# Define the path to the configuration file
CONFIG_PATH = "config/config.yaml"


def create_tables(case_data, resource_data) -> Layout:
    """Creates the layout containing tables for cases and GPU resources."""
    layout = Layout()
    layout.split_column(
        Layout(name="main", ratio=3),
        Layout(name="footer", size=5),
    )

    # --- Cases Table ---
    case_table = Table(
        title=f"Live Case Status (Updated: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')})",
        expand=True,
    )
    case_table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    case_table.add_column("Case Path", style="magenta", max_width=50)
    case_table.add_column("Status", style="green")
    case_table.add_column("Progress", justify="right", style="yellow")
    case_table.add_column("Pueue Group", style="blue")
    case_table.add_column("Task ID", justify="right", style="dim")
    case_table.add_column("Submitted At", style="dim")
    case_table.add_column("Updated At", style="dim")

    for case in case_data:
        # Format progress with a percentage sign
        progress = f"{case['progress']}%"

        # Style status based on its value
        status = case["status"]
        if status == "failed":
            status_style = "[bold red]failed[/bold red]"
        elif status == "completed":
            status_style = "[bold green]completed[/bold green]"
        elif status == "running":
            status_style = "[yellow]running[/yellow]"
        else:
            status_style = f"[{status}]"

        case_table.add_row(
            str(case["case_id"]),
            case["case_path"],
            status_style,
            progress,
            case["pueue_group"] or "N/A",
            str(case["pueue_task_id"]) if case["pueue_task_id"] is not None else "N/A",
            case["submitted_at"],
            case["status_updated_at"],
        )

    # --- GPU Resources Table ---
    resource_table = Table(title="GPU Resource Status", expand=True)
    resource_table.add_column("Pueue Group", style="blue")
    resource_table.add_column("Status", style="green")
    resource_table.add_column("Assigned Case ID", justify="right", style="cyan")

    for resource in resource_data:
        status = resource["status"]
        if status == "assigned":
            status_style = "[bold yellow]assigned[/bold yellow]"
        else:
            status_style = "[green]available[/green]"

        resource_table.add_row(
            resource["pueue_group"],
            status_style,
            str(resource["assigned_case_id"])
            if resource["assigned_case_id"] is not None
            else "None",
        )

    layout["main"].update(
        Panel(Align.center(case_table, vertical="middle"), title="Cases")
    )
    layout["footer"].update(
        Panel(Align.center(resource_table, vertical="middle"), title="GPU Resources")
    )

    return layout


def display_dashboard() -> None:
    """
    Displays a live-updating dashboard with the status of cases and resources.
    """
    console = Console()
    db_manager = None

    try:
        # Load config to find the database
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)

        db_path = config.get("database", {}).get("path")
        if not db_path:
            console.print(
                f"[bold red]Error: Database path not found in '{CONFIG_PATH}'[/bold red]"
            )
            return

        if not Path(db_path).exists():
            console.print(f"[bold yellow]Database file not found at '{db_path}'.[/bold yellow]")
            console.print("Please run the main application first to create the database.")
            # Create dummy tables to show the structure
            with Live(create_tables([], []), screen=True, redirect_stderr=False) as live:
                time.sleep(5) # Show empty dashboard for 5 seconds
            return


        db_manager = DatabaseManager(db_path=db_path)
        console.print("[bold cyan]MQI Communicator Dashboard[/bold cyan]")
        console.print(f"Connected to database: [yellow]{db_path}[/yellow]")
        console.print("Press [bold]Ctrl+C[/bold] to exit.")

        with Live(
            create_tables([], []), screen=True, redirect_stderr=False
        ) as live:
            while True:
                # Fetch all cases and resources
                # A real implementation might be more selective, but for a few hundred cases this is fine.
                all_cases = db_manager.cursor.execute("SELECT * FROM cases ORDER BY case_id DESC").fetchall()
                all_resources = db_manager.cursor.execute("SELECT * FROM gpu_resources ORDER BY pueue_group").fetchall()

                # Convert rows to dictionaries for easier processing
                case_data = [dict(row) for row in all_cases]
                resource_data = [dict(row) for row in all_resources]

                live.update(create_tables(case_data, resource_data))
                time.sleep(2)  # Refresh interval

    except FileNotFoundError:
        console.print(f"[bold red]Error: Config file not found at '{CONFIG_PATH}'[/bold red]")
    except KeyboardInterrupt:
        console.print("\n[bold cyan]Dashboard closed.[/bold cyan]")
    except Exception as e:
        console.print(f"\n[bold red]An unexpected error occurred: {e}[/bold red]")
    finally:
        if db_manager:
            db_manager.close()


if __name__ == "__main__":
    display_dashboard()
