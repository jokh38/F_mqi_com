import sqlite3
import yaml
from datetime import datetime
from typing import Optional, Dict, Any


class DatabaseManager:
    """
    Manages all interactions with the SQLite database.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initializes the DatabaseManager.

        If db_path is not provided, it reads from the config file.

        Args:
            db_path: The path to the SQLite database file.
        """
        if db_path:
            self.db_path = db_path
        else:
            with open("config/config.yaml", "r") as f:
                config = yaml.safe_load(f)
            self.db_path = config["database"]["path"]

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # Use Row factory to allow accessing columns by name
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

    def _create_tables(self):
        """
        Creates the necessary tables if they don't exist.
        This is defined in the project specification.
        """
        # cases table
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
                case_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_path TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL,
                pueue_group TEXT NOT NULL,
                submitted_at DATETIME NOT NULL,
                completed_at DATETIME
            )
        """
        )

        # gpu_resources table
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_resources (
                pueue_group TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                assigned_case_id INTEGER,
                FOREIGN KEY (assigned_case_id) REFERENCES cases (case_id)
            )
        """
        )
        self.conn.commit()

    def init_db(self):
        """Public method to initialize the database."""
        self._create_tables()

    def add_case(self, case_path: str, pueue_group: str) -> Optional[int]:
        """
        Adds a new case to the database with 'submitted' status.

        Returns:
            The ID of the newly inserted case.
        """
        submitted_time = datetime.now()
        self.cursor.execute(
            """
            INSERT INTO cases (case_path, status, progress, pueue_group, submitted_at)
            VALUES (?, 'submitted', 0, ?, ?)
        """,
            (case_path, pueue_group, submitted_time),
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def get_case_by_id(self, case_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves a case by its primary key."""
        self.cursor.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,))
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def get_case_by_path(self, case_path: str) -> Optional[Dict[str, Any]]:
        """Retrieves a case by its path."""
        self.cursor.execute("SELECT * FROM cases WHERE case_path = ?", (case_path,))
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def update_case_status(self, case_id: int, status: str, progress: int):
        """Updates the status and progress of a case."""
        self.cursor.execute(
            """
            UPDATE cases
            SET status = ?, progress = ?
            WHERE case_id = ?
        """,
            (status, progress, case_id),
        )
        self.conn.commit()

    def update_case_completion(self, case_id: int, status: str):
        """Marks a case as 'completed' or 'failed' and sets progress to 100."""
        completion_time = datetime.now()
        self.cursor.execute(
            """
            UPDATE cases
            SET status = ?, progress = 100, completed_at = ?
            WHERE case_id = ?
        """,
            (status, completion_time, case_id),
        )
        self.conn.commit()

    def add_gpu_resource(self, pueue_group: str, status: str = "available"):
        """Adds a new GPU resource to the database."""
        self.cursor.execute(
            """
            INSERT INTO gpu_resources (pueue_group, status)
            VALUES (?, ?)
        """,
            (pueue_group, status),
        )
        self.conn.commit()

    def get_gpu_resource(self, pueue_group: str) -> Optional[Dict[str, Any]]:
        """Retrieves a GPU resource by its group name."""
        self.cursor.execute(
            "SELECT * FROM gpu_resources WHERE pueue_group = ?", (pueue_group,)
        )
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def update_gpu_status(
        self, pueue_group: str, status: str, case_id: Optional[int] = None
    ):
        """Updates the status and assigned case of a GPU resource."""
        self.cursor.execute(
            """
            UPDATE gpu_resources
            SET status = ?, assigned_case_id = ?
            WHERE pueue_group = ?
        """,
            (status, case_id, pueue_group),
        )
        self.conn.commit()

    def close(self):
        """Closes the database connection."""
        if self.conn:
            self.conn.close()
