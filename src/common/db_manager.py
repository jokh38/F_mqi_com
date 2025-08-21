import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List


# Define Korea Standard Time (KST) as UTC+9
KST = timezone(timedelta(hours=9))


class DatabaseManager:
    """
    Manages all interactions with the SQLite database.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initializes the DatabaseManager.

        The database path is determined in one of two ways:
        1. Directly via the `db_path` argument (primarily for testing).
        2. From the `config` dictionary.

        Args:
            db_path: The path to the SQLite database file.
            config: The application's configuration dictionary.

        Raises:
            ValueError: If neither db_path nor config is provided.
        """
        if db_path:
            self.db_path = db_path
        elif config:
            self.db_path = config["database"]["path"]
        else:
            raise ValueError("Either db_path or config must be provided.")

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # Use Row factory to allow accessing columns by name
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

    def _create_tables(self) -> None:
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
                pueue_task_id INTEGER,
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

    def init_db(self) -> None:
        """Public method to initialize the database."""
        self._create_tables()

    def add_case(self, case_path: str, pueue_group: str) -> Optional[int]:
        """
        Adds a new case to the database with 'submitted' status.

        Returns:
            The ID of the newly inserted case.
        """
        # Get current time in KST and format as ISO 8601 string
        submitted_time = datetime.now(KST).isoformat()
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

    def get_cases_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Retrieves all cases with a given status."""
        self.cursor.execute("SELECT * FROM cases WHERE status = ?", (status,))
        rows = self.cursor.fetchall()
        return [dict(row) for row in rows]

    def update_case_status(self, case_id: int, status: str, progress: int) -> None:
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

    def update_case_pueue_task_id(self, case_id: int, pueue_task_id: int) -> None:
        """Stores the Pueue task ID for a given case."""
        self.cursor.execute(
            """
            UPDATE cases
            SET pueue_task_id = ?
            WHERE case_id = ?
        """,
            (pueue_task_id, case_id),
        )
        self.conn.commit()

    def update_case_completion(self, case_id: int, status: str) -> None:
        """Marks a case as 'completed' or 'failed' and sets progress to 100."""
        # Get current time in KST and format as ISO 8601 string
        completion_time = datetime.now(KST).isoformat()
        self.cursor.execute(
            """
            UPDATE cases
            SET status = ?, progress = 100, completed_at = ?
            WHERE case_id = ?
        """,
            (status, completion_time, case_id),
        )
        self.conn.commit()

    def add_gpu_resource(self, pueue_group: str, status: str = "available") -> None:
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
    ) -> None:
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

    def close(self) -> None:
        """Closes the database connection."""
        if self.conn:
            self.conn.close()
