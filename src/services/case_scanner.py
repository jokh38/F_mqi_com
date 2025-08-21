import logging
import os
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from src.common.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class DirectoryCreatedEventHandler(FileSystemEventHandler):
    """
    Handles the event when a new directory is created in the watch folder.
    """

    def __init__(self, db_manager: DatabaseManager, pueue_group: str) -> None:
        self.db_manager = db_manager
        self.pueue_group = pueue_group

    def on_created(self, event: FileSystemEvent) -> None:
        """
        Called when a file or directory is created.
        """
        if not event.is_directory:
            return

        src_path_str = os.fsdecode(event.src_path)
        logger.info(f"New directory detected: {src_path_str}")

        try:
            # Add the case to the database. This is a critical step.
            # If it fails, the directory will be ignored until the next restart.
            self.db_manager.add_case(src_path_str, self.pueue_group)
            logger.info(f"Successfully added case '{src_path_str}' to the database.")
        except Exception as e:
            # Catching a broad exception here is acceptable because a failure
            # at this stage is critical and should be logged, but should not
            # crash the watchdog thread.
            logger.error(
                f"Failed to add case '{src_path_str}' to the database. " f"Error: {e}",
                exc_info=True,
            )


class CaseScanner:
    """Monitors a directory for new cases."""

    def __init__(
        self, watch_path: str, db_manager: DatabaseManager, pueue_group: str
    ) -> None:
        self.watch_path = watch_path
        self.db_manager = db_manager
        self.pueue_group = pueue_group
        self.event_handler = DirectoryCreatedEventHandler(
            db_manager=self.db_manager, pueue_group=self.pueue_group
        )
        self.observer = Observer()

    def start(self) -> None:
        """
        Starts the file system observer in a background thread.
        This method is non-blocking.
        """
        self.observer.schedule(self.event_handler, self.watch_path, recursive=False)
        self.observer.start()
        logger.info(f"Started watching directory: {self.watch_path}")

    def stop(self) -> None:
        """Stops the file system observer."""
        self.observer.stop()
        self.observer.join()
        logger.info("Stopped watching directory.")
