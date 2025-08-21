import logging
import os
import threading
from pathlib import Path
from typing import Dict, Callable

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from src.common.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

# Time in seconds to wait for file activity to cease before processing a directory
STABILITY_DELAY_SECONDS = 5.0


class StableDirectoryEventHandler(FileSystemEventHandler):
    """
    An event handler that waits for a directory to be "stable" before processing.

    A directory is considered stable if no file system events have occurred
    within it for a specified delay period. This helps prevent processing
    incomplete directories that are still being copied.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        pueue_group: str,
        stability_delay: float = STABILITY_DELAY_SECONDS,
    ):
        self.db_manager = db_manager
        self.pueue_group = pueue_group
        self.stability_delay = stability_delay
        self.timers: Dict[str, threading.Timer] = {}
        self.lock = threading.Lock()

    def _process_directory(self, path_str: str) -> None:
        """The callback executed when a directory is deemed stable."""
        with self.lock:
            # Remove the timer from the tracking dictionary
            self.timers.pop(path_str, None)

        logger.info(
            f"Directory '{path_str}' is stable. Adding to database."
        )
        try:
            # Check if the case already exists to prevent duplicates from multiple events
            if not self.db_manager.get_case_by_path(path_str):
                self.db_manager.add_case(path_str, self.pueue_group)
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
        Catches all filesystem events.
        """
        if event.is_directory:
            # If a new directory is created, start a timer for it.
            if event.event_type == "created":
                src_path_str = os.fsdecode(event.src_path)
                logger.info(f"New directory detected: {src_path_str}. Starting stability timer.")
                self._reset_timer(src_path_str)
        else:
            # If a file inside a potential case directory changes, reset the timer.
            # This handles files being created, modified, or moved within the directory.
            src_path = Path(os.fsdecode(event.src_path))
            parent_dir_str = str(src_path.parent)

            # Check if the parent directory is one we are watching
            with self.lock:
                if parent_dir_str in self.timers:
                    logger.debug(
                        f"Activity detected in '{parent_dir_str}'. Resetting timer."
                    )
                    self._reset_timer(parent_dir_str)

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
        self, watch_path: str, db_manager: DatabaseManager, pueue_group: str
    ) -> None:
        self.watch_path = watch_path
        self.db_manager = db_manager
        self.pueue_group = pueue_group
        self.event_handler = StableDirectoryEventHandler(
            db_manager=self.db_manager, pueue_group=self.pueue_group
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
