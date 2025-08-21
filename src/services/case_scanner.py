import logging
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# A placeholder for the DatabaseManager to allow type hinting without circular imports
# if we decide to type hint the constructor.
from src.common.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class DirectoryCreatedEventHandler(FileSystemEventHandler):
    """
    Handles the event when a new directory is created in the watch folder.
    """

    def __init__(self, db_manager: DatabaseManager, pueue_group: str):
        self.db_manager = db_manager
        self.pueue_group = pueue_group

    def on_created(self, event):
        """
        Called when a file or directory is created.
        """
        if event.is_directory:
            logger.info(f"New directory detected: {event.src_path}")
            # This is the logic that the test expects.
            self.db_manager.add_case(event.src_path, self.pueue_group)


class CaseScanner:
    """Monitors a directory for new cases."""

    def __init__(self, watch_path: str, db_manager: DatabaseManager, pueue_group: str):
        self.watch_path = watch_path
        self.db_manager = db_manager
        self.pueue_group = pueue_group
        self.event_handler = DirectoryCreatedEventHandler(
            db_manager=self.db_manager, pueue_group=self.pueue_group
        )
        self.observer = Observer()

    def start(self):
        """
        Starts the file system observer in a background thread.
        This method is non-blocking.
        """
        self.observer.schedule(self.event_handler, self.watch_path, recursive=False)
        self.observer.start()
        logger.info(f"Started watching directory: {self.watch_path}")

    def stop(self):
        """Stops the file system observer."""
        self.observer.stop()
        self.observer.join()
        logger.info("Stopped watching directory.")
