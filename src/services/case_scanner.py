import logging
import os
import threading
from pathlib import Path
from typing import Dict, Any, Callable

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from src.common.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class StableDirectoryEventHandler(FileSystemEventHandler):
    """
    An event handler that waits for a directory to be "stable" before processing.

    A directory is considered stable if no file system events have occurred
    within it for a specified delay period. This helps prevent processing
    incomplete directories that are still being copied.
    """

    def __init__(
        self,
        watch_path: str,
        db_manager: DatabaseManager,
        stability_delay: float,
    ):
        self.watch_path = watch_path
        self.db_manager = db_manager
        self.stability_delay = stability_delay
        self.timers: Dict[str, threading.Timer] = {}
        self.lock = threading.Lock()

    def _process_directory(self, path_str: str) -> None:
        """The callback executed when a directory is deemed stable."""
        with self.lock:
            # Remove the timer from the tracking dictionary
            self.timers.pop(path_str, None)

        # Before processing, ensure the directory still exists.
        if not os.path.isdir(path_str):
            logger.warning(
                f"Directory '{path_str}' was deleted before it could be processed. Skipping."
            )
            return

        logger.info(f"Directory '{path_str}' is stable. Adding to database.")
        try:
            # Check if the case already exists to prevent duplicates from multiple events
            if not self.db_manager.get_case_by_path(path_str):
                # The pueue_group is no longer assigned here.
                self.db_manager.add_case(path_str)
                logger.info(f"Successfully added case '{path_str}' to the database.")
            else:
                logger.warning(
                    f"Case '{path_str}' already exists in the database. Skipping."
                )
        except Exception as e:
            logger.error(
                f"Failed to add case '{path_str}' to the database. Error: {e}",
                exc_info=True,
            )

    def on_any_event(self, event: FileSystemEvent) -> None:
        """
        Catches all filesystem events and resets the stability timer for the
        relevant top-level case directory. This approach is robust against
        out-of-order event delivery.
        """
        try:
            # Use a set to handle events that have both src_path and dest_path (e.g., move)
            paths_to_check = set()
            if hasattr(event, 'src_path'):
                paths_to_check.add(Path(os.fsdecode(event.src_path)))
            if hasattr(event, 'dest_path'):
                paths_to_check.add(Path(os.fsdecode(event.dest_path)))

            if not paths_to_check:
                return

            watch_path = Path(self.watch_path).resolve()

            # Determine the case directory for each relevant path
            case_dirs_to_reset = set()
            for path in paths_to_check:
                # Resolve the path to handle relative paths correctly
                path = path.resolve()

                # Check if the event path is inside the watched directory
                if watch_path in path.parents:
                    relative_path = path.relative_to(watch_path)
                    if relative_path.parts:
                        case_dir_name = relative_path.parts[0]
                        case_dir_path = str(watch_path / case_dir_name)
                        case_dirs_to_reset.add(case_dir_path)

            # Reset the timer for each affected case directory
            for case_dir in case_dirs_to_reset:
                logger.debug(f"Activity for case '{case_dir}' detected. Resetting timer.")
                self._reset_timer(case_dir)

        except Exception as e:
            logger.error(f"Error processing filesystem event: {e}", exc_info=True)

    def _reset_timer(self, path_str: str) -> None:
        """Resets the stability timer for a given path."""
        with self.lock:
            # If there's an existing timer, cancel it.
            if path_str in self.timers:
                self.timers[path_str].cancel()

            # Create and start a new timer.
            timer = threading.Timer(
                self.stability_delay, self._process_directory, args=[path_str]
            )
            self.timers[path_str] = timer
            timer.start()


class CaseScanner:
    """Monitors a directory for new, stable cases."""

    def __init__(
        self, watch_path: str, db_manager: DatabaseManager, config: Dict[str, Any]
    ) -> None:
        self.watch_path = watch_path
        self.db_manager = db_manager

        # Get stability delay from config, with a fallback default.
        scanner_config = config.get("scanner", {})
        stability_delay = scanner_config.get("quiescence_period_seconds", 5.0)

        self.event_handler = StableDirectoryEventHandler(
            watch_path=self.watch_path,
            db_manager=self.db_manager,
            stability_delay=stability_delay,
        )
        # We need to watch recursively to detect file changes inside new directories
        self.observer = Observer()
        self.observer.schedule(self.event_handler, self.watch_path, recursive=True)

    def start(self) -> None:
        """
        Starts the file system observer in a background thread.
        This method is non-blocking.
        """
        self.observer.start()
        logger.info(f"Started watching directory: {self.watch_path} (recursive)")

    def stop(self) -> None:
        """Stops the file system observer and cancels any pending timers."""
        with self.event_handler.lock:
            for timer in self.event_handler.timers.values():
                timer.cancel()
        self.observer.stop()
        self.observer.join()
        logger.info("Stopped watching directory.")
