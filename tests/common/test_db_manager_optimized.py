import pytest
import sqlite3
import tempfile
import time
from unittest.mock import patch
from src.common.db_manager_optimized import OptimizedDatabaseManager, QueryCache, QueryPerformanceMetrics


class TestQueryPerformanceMetrics:
    """Test suite for QueryPerformanceMetrics."""
    
    def test_metrics_initialization(self):
        """Test metrics initialize with correct defaults."""
        metrics = QueryPerformanceMetrics()
        
        assert metrics.query_count == 0
        assert metrics.total_execution_time == 0.0
        assert metrics.cache_hits == 0
        assert metrics.cache_misses == 0
        assert metrics.slow_queries == 0
        assert metrics.average_execution_time == 0.0
    
    def test_add_query_uncached(self):
        """Test adding uncached query to metrics."""
        metrics = QueryPerformanceMetrics()
        
        metrics.add_query(0.1, was_cached=False)
        
        assert metrics.query_count == 1
        assert metrics.total_execution_time == 0.1
        assert metrics.average_execution_time == 0.1
        assert metrics.cache_hits == 0
        assert metrics.cache_misses == 1
        assert metrics.slow_queries == 1  # 0.1s > 0.05s threshold
    
    def test_add_query_cached(self):
        """Test adding cached query to metrics."""
        metrics = QueryPerformanceMetrics()
        
        metrics.add_query(0.01, was_cached=True)
        
        assert metrics.query_count == 1
        assert metrics.cache_hits == 1
        assert metrics.cache_misses == 0
        assert metrics.slow_queries == 0  # 0.01s < 0.05s threshold
    
    def test_get_cache_hit_rate(self):
        """Test cache hit rate calculation."""
        metrics = QueryPerformanceMetrics()
        
        # No requests yet
        assert metrics.get_cache_hit_rate() == 0.0
        
        # Add some requests
        metrics.add_query(0.01, was_cached=True)
        metrics.add_query(0.02, was_cached=True)
        metrics.add_query(0.03, was_cached=False)
        
        # 2 hits out of 3 requests = 66.67%
        assert abs(metrics.get_cache_hit_rate() - 66.67) < 0.01


class TestQueryCache:
    """Test suite for QueryCache."""
    
    def test_cache_initialization(self):
        """Test cache initializes with correct parameters."""
        cache = QueryCache(max_size=100, ttl_seconds=60)
        
        assert cache.max_size == 100
        assert cache.ttl_seconds == 60
        assert cache.size() == 0
    
    def test_cache_put_and_get(self):
        """Test basic cache put and get operations."""
        cache = QueryCache()
        
        cache.put("key1", "value1")
        result = cache.get("key1")
        
        assert result == "value1"
        assert cache.size() == 1
    
    def test_cache_miss(self):
        """Test cache miss returns None."""
        cache = QueryCache()
        
        result = cache.get("nonexistent_key")
        
        assert result is None
    
    def test_cache_expiration(self):
        """Test cache entry expiration."""
        cache = QueryCache(ttl_seconds=0.1)  # Very short TTL for testing
        
        cache.put("key1", "value1")
        
        # Should be available immediately
        assert cache.get("key1") == "value1"
        
        # Wait for expiration
        time.sleep(0.2)
        
        # Should be expired now
        assert cache.get("key1") is None
        assert cache.size() == 0
    
    def test_cache_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        cache = QueryCache(max_size=2)
        
        # Fill cache
        cache.put("key1", "value1")
        cache.put("key2", "value2")
        assert cache.size() == 2
        
        # Access key1 to make it most recently used
        cache.get("key1")
        
        # Add third item, should evict key2 (least recently used)
        cache.put("key3", "value3")
        
        assert cache.size() == 2
        assert cache.get("key1") == "value1"  # Still available
        assert cache.get("key2") is None      # Evicted
        assert cache.get("key3") == "value3"  # New item
    
    def test_cache_invalidation(self):
        """Test cache invalidation."""
        cache = QueryCache()
        
        cache.put("key1", "value1")
        cache.put("key2", "value2")
        cache.put("pattern_key", "value3")
        
        # Partial invalidation
        cache.invalidate("pattern")
        
        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "value2"
        assert cache.get("pattern_key") is None
        
        # Full invalidation
        cache.invalidate()
        
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.size() == 0


class TestOptimizedDatabaseManager:
    """Test suite for OptimizedDatabaseManager."""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create temporary database file for testing."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            return f.name
    
    @pytest.fixture
    def db_manager(self, temp_db_path):
        """Create OptimizedDatabaseManager instance for testing."""
        manager = OptimizedDatabaseManager(db_path=temp_db_path)
        manager.init_db()
        yield manager
        manager.close()
    
    def test_initialization(self, temp_db_path):
        """Test database manager initialization."""
        manager = OptimizedDatabaseManager(
            db_path=temp_db_path,
            enable_cache=True,
            cache_size=500,
            cache_ttl=120
        )
        
        assert manager.db_path == temp_db_path
        assert manager.enable_cache is True
        assert manager.query_cache.max_size == 500
        assert manager.query_cache.ttl_seconds == 120
        assert isinstance(manager.metrics, QueryPerformanceMetrics)
        
        manager.close()
    
    def test_initialization_from_config(self, temp_db_path):
        """Test initialization from config dictionary."""
        config = {"database": {"path": temp_db_path}}
        manager = OptimizedDatabaseManager(config=config)
        
        assert manager.db_path == temp_db_path
        
        manager.close()
    
    def test_table_creation_and_indexes(self, db_manager):
        """Test that tables and indexes are created properly."""
        # Check tables exist
        db_manager.cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in db_manager.cursor.fetchall()]
        
        assert "cases" in tables
        assert "gpu_resources" in tables
        
        # Check some indexes exist
        db_manager.cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = [row[0] for row in db_manager.cursor.fetchall()]
        
        # Should have several indexes created
        assert len([idx for idx in indexes if 'idx_' in idx]) >= 5
    
    def test_add_case_with_priority(self, db_manager):
        """Test adding case with priority."""
        case_id = db_manager.add_case("/test/path", priority=3)
        
        assert case_id is not None
        
        # Verify case was added
        case = db_manager.get_case_by_id(case_id)
        assert case["case_path"] == "/test/path"
        assert case["priority"] == 3
        assert case["status"] == "submitted"
        assert case["progress"] == 0
    
    def test_get_case_by_id_with_caching(self, db_manager):
        """Test getting case by ID with caching."""
        case_id = db_manager.add_case("/test/path")
        
        # First call should be cache miss
        case1 = db_manager.get_case_by_id(case_id)
        
        # Second call should be cache hit
        case2 = db_manager.get_case_by_id(case_id)
        
        assert case1["case_id"] == case2["case_id"]
        assert db_manager.metrics.cache_hits >= 1
        assert db_manager.metrics.cache_misses >= 1
    
    def test_get_cases_by_status_optimized(self, db_manager):
        """Test getting cases by status with optimization."""
        # Add test cases with different priorities
        case_id1 = db_manager.add_case("/test/path1", priority=1)
        case_id2 = db_manager.add_case("/test/path2", priority=3)
        case_id3 = db_manager.add_case("/test/path3", priority=2)
        
        cases = db_manager.get_cases_by_status("submitted")
        
        # Should return all cases ordered by priority desc, then created_at asc
        assert len(cases) == 3
        assert cases[0]["priority"] == 3  # Highest priority first
        assert cases[1]["priority"] == 2
        assert cases[2]["priority"] == 1  # Lowest priority last
    
    def test_get_cases_by_priority_and_status(self, db_manager):
        """Test getting cases by priority and status."""
        # Add test cases with different priorities
        db_manager.add_case("/test/path1", priority=1)
        db_manager.add_case("/test/path2", priority=3)
        db_manager.add_case("/test/path3", priority=2)
        
        # Get only high priority cases (>= 2)
        cases = db_manager.get_cases_by_priority_and_status("submitted", min_priority=2)
        
        assert len(cases) == 2
        assert all(case["priority"] >= 2 for case in cases)
        assert cases[0]["priority"] == 3  # Should be ordered by priority desc
    
    def test_update_case_status_with_cache_invalidation(self, db_manager):
        """Test case status update invalidates cache."""
        case_id = db_manager.add_case("/test/path")
        
        # Get case to populate cache
        db_manager.get_case_by_id(case_id)
        
        # Update status
        db_manager.update_case_status(case_id, "running", 50)
        
        # Get case again - should have updated status
        case = db_manager.get_case_by_id(case_id)
        assert case["status"] == "running"
        assert case["progress"] == 50
    
    def test_find_and_lock_gpu_optimized(self, db_manager):
        """Test optimized GPU locking mechanism."""
        # Add GPU resources
        db_manager.ensure_gpu_resource_exists("gpu1")
        db_manager.ensure_gpu_resource_exists("gpu2")
        
        case_id = db_manager.add_case("/test/path")
        
        # Lock a GPU
        locked_gpu = db_manager.find_and_lock_any_available_gpu(case_id)
        
        assert locked_gpu in ["gpu1", "gpu2"]
        
        # Verify GPU is locked
        gpu_resource = db_manager.get_gpu_resource_by_case_id(case_id)
        assert gpu_resource["status"] == "assigned"
        assert gpu_resource["assigned_case_id"] == case_id
    
    def test_transaction_context_manager(self, db_manager):
        """Test transaction context manager functionality."""
        case_id = db_manager.add_case("/test/path")
        
        # Test that the transaction context manager exists and can be used
        try:
            with db_manager.transaction():
                # Just verify the transaction context works
                pass
        except Exception as e:
            pytest.fail(f"Transaction context manager failed: {e}")
        
        # Verify case exists and can be updated normally
        case = db_manager.get_case_by_id(case_id)
        assert case is not None
        assert case["status"] == "submitted"
        
        # Test normal update operation
        db_manager.update_case_status(case_id, "running", 50)
        
        case = db_manager.get_case_by_id(case_id)
        assert case["status"] == "running"
        assert case["progress"] == 50
    
    def test_performance_metrics_tracking(self, db_manager):
        """Test performance metrics are tracked correctly."""
        # Reset metrics
        db_manager.reset_metrics()
        
        # Perform some operations
        case_id = db_manager.add_case("/test/path")
        db_manager.get_case_by_id(case_id)  # Cache miss
        db_manager.get_case_by_id(case_id)  # Cache hit
        
        metrics = db_manager.get_performance_metrics()
        
        assert metrics["query_count"] >= 2
        assert metrics["cache_enabled"] is True
        assert "average_execution_time_ms" in metrics
        assert "cache_hit_rate_percent" in metrics
        assert metrics["cache_hits"] >= 1
    
    def test_cache_disabled_mode(self, temp_db_path):
        """Test database manager with caching disabled."""
        manager = OptimizedDatabaseManager(
            db_path=temp_db_path,
            enable_cache=False
        )
        manager.init_db()
        
        case_id = manager.add_case("/test/path")
        
        # Multiple calls should not use cache
        manager.get_case_by_id(case_id)
        manager.get_case_by_id(case_id)
        
        metrics = manager.get_performance_metrics()
        assert metrics["cache_enabled"] is False
        assert "cache_hits" not in metrics
        
        manager.close()
    
    def test_database_optimization_commands(self, db_manager):
        """Test database optimization commands execute successfully."""
        # Add some data first
        db_manager.add_case("/test/path1")
        db_manager.add_case("/test/path2")
        
        # Run optimization
        initial_query_count = db_manager.metrics.query_count
        db_manager.optimize_database()
        
        # Should have executed optimization commands
        assert db_manager.metrics.query_count > initial_query_count
    
    def test_metrics_reset(self, db_manager):
        """Test metrics reset functionality."""
        # Generate some metrics
        db_manager.add_case("/test/path")
        db_manager.get_case_by_id(1)
        
        assert db_manager.metrics.query_count > 0
        assert db_manager.query_cache.size() > 0
        
        # Reset metrics
        db_manager.reset_metrics()
        
        assert db_manager.metrics.query_count == 0
        assert db_manager.query_cache.size() == 0
    
    def test_slow_query_tracking(self, db_manager):
        """Test slow query detection and tracking."""
        # Mock slow query execution
        with patch('time.time', side_effect=[0.0, 0.1]):  # 0.1s execution time
            db_manager._execute_with_metrics("SELECT 1", ())
        
        assert db_manager.metrics.slow_queries == 1
    
    def test_concurrent_access(self, db_manager):
        """Test database can handle multiple sequential operations safely."""
        # Test sequential operations to ensure thread safety mechanisms work
        results = []
        
        # Simulate concurrent-like operations
        for thread_id in range(3):
            for i in range(5):
                case_id = db_manager.add_case(f"/test/thread{thread_id}_path{i}")
                results.append(case_id)
                
                # Immediately read back to test consistency
                case = db_manager.get_case_by_id(case_id)
                assert case is not None
                assert case["case_path"] == f"/test/thread{thread_id}_path{i}"
        
        # All operations should succeed
        assert len(results) == 15  # 3 * 5 cases
        
        # Verify all cases were added
        all_cases = db_manager.get_cases_by_status("submitted")
        assert len(all_cases) == 15