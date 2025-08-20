import pytest
import sqlite3
import os
from typing import Generator

# This import will fail until we create the actual module
from src.common.db_manager import DatabaseManager

# Define the path for the test database
TEST_DB_PATH = "test_communicator.db"


@pytest.fixture
def db_manager() -> Generator[DatabaseManager, None, None]:
    """
    Pytest fixture to set up and tear down a test database.
    This fixture creates a new DatabaseManager instance and initializes
    a fresh database for each test function.
    After the test runs, it removes the test database file.
    """
    # Ensure no old test db exists
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

    # Setup: create a DatabaseManager instance and initialize the database
    # We will pass the test DB path directly, bypassing the config for tests.
    manager = DatabaseManager(db_path=TEST_DB_PATH)
    manager.init_db()

    yield manager

    # Teardown: close connection and remove the database file
    manager.close()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


# --- Test Suite ---


def test_database_initialization(db_manager: DatabaseManager):
    """
    Tests if the database and the required tables ('cases' and 'gpu_resources')
    are created successfully.
    """
    assert os.path.exists(TEST_DB_PATH)

    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()

    # Check if 'cases' table exists by querying the schema
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cases';"
    )
    assert cursor.fetchone() is not None, "'cases' table was not created."

    # Check if 'gpu_resources' table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gpu_resources';"
    )
    assert cursor.fetchone() is not None, "'gpu_resources' table was not created."

    conn.close()


def test_add_and_get_case(db_manager: DatabaseManager):
    """

    Tests adding a new case and retrieving it by its ID.
    """
    case_path = "/path/to/case1"
    pueue_group = "gpu0"

    # Add a case
    case_id = db_manager.add_case(case_path=case_path, pueue_group=pueue_group)
    assert case_id is not None
    assert isinstance(case_id, int)
    assert case_id > 0

    # Retrieve the case
    case = db_manager.get_case_by_id(case_id)

    assert case is not None
    assert case["case_id"] == case_id
    assert case["case_path"] == case_path
    assert case["status"] == "submitted"
    assert case["progress"] == 0
    assert case["pueue_group"] == pueue_group
    assert "submitted_at" in case and case["submitted_at"] is not None
    assert case["completed_at"] is None


def test_update_case_status(db_manager: DatabaseManager):
    """
    Tests updating the status and progress of an existing case.
    """
    case_id = db_manager.add_case("/path/to/case2", "gpu1")

    # Update status
    db_manager.update_case_status(case_id=case_id, status="running", progress=50)

    case = db_manager.get_case_by_id(case_id)
    assert case is not None
    assert case["status"] == "running"
    assert case["progress"] == 50


def test_update_case_completion(db_manager: DatabaseManager):
    """
    Tests marking a case as completed or failed.
    """
    case_id = db_manager.add_case("/path/to/case3", "gpu0")

    # Mark as completed
    db_manager.update_case_completion(case_id=case_id, status="completed")

    case = db_manager.get_case_by_id(case_id)
    assert case is not None
    assert case["status"] == "completed"
    assert case["progress"] == 100
    assert case["completed_at"] is not None


def test_add_and_get_gpu_resource(db_manager: DatabaseManager):
    """
    Tests adding a new GPU resource and retrieving it.
    """
    pueue_group = "gpu_test"
    db_manager.add_gpu_resource(pueue_group=pueue_group, status="available")

    resource = db_manager.get_gpu_resource(pueue_group)
    assert resource is not None
    assert resource["pueue_group"] == pueue_group
    assert resource["status"] == "available"
    assert resource["assigned_case_id"] is None


def test_update_gpu_status(db_manager: DatabaseManager):
    """
    Tests updating the status of a GPU resource (e.g., to 'busy').
    """
    case_id = db_manager.add_case("/path/to/case4", "gpu_update")
    db_manager.add_gpu_resource("gpu_update", "available")

    # Update status to busy
    db_manager.update_gpu_status(
        pueue_group="gpu_update", status="busy", case_id=case_id
    )

    resource = db_manager.get_gpu_resource("gpu_update")
    assert resource is not None
    assert resource["status"] == "busy"
    assert resource["assigned_case_id"] == case_id

    # Update status back to available
    db_manager.update_gpu_status(pueue_group="gpu_update", status="available")

    resource = db_manager.get_gpu_resource("gpu_update")
    assert resource is not None
    assert resource["status"] == "available"
    assert resource["assigned_case_id"] is None


def test_get_case_by_path(db_manager: DatabaseManager):
    """
    Tests retrieving a case by its path.
    """
    case_path = "/path/to/unique_case"
    case_id = db_manager.add_case(case_path, "gpu0")

    case = db_manager.get_case_by_path(case_path)
    assert case is not None
    assert case["case_id"] == case_id


def test_get_case_by_path_not_found(db_manager: DatabaseManager):
    """
    Tests that get_case_by_path returns None for a non-existent path.
    """
    case = db_manager.get_case_by_path("/non/existent/path")
    assert case is None
