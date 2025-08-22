"""
Tests for the DynamicGpuManager service.

This module tests dynamic GPU resource detection and management for HPC systems.
The DynamicGpuManager auto-detects available GPU resources from the remote HPC
and maintains them in the database.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from src.services.dynamic_gpu_manager import DynamicGpuManager, GpuDetectionError
from src.common.db_manager import DatabaseManager
import json


class TestDynamicGpuManager:
    """Tests for the DynamicGpuManager class."""

    def test_initialization_with_valid_config(self):
        """Test that DynamicGpuManager initializes properly with valid config."""
        config = {
            "hpc": {
                "host": "test.hpc.com",
                "user": "testuser",
                "ssh_command": "ssh",
                "pueue_command": "pueue"
            }
        }
        db_manager = Mock(spec=DatabaseManager)
        
        gpu_manager = DynamicGpuManager(config, db_manager)
        
        assert gpu_manager.hpc_config == config["hpc"]
        assert gpu_manager.db_manager == db_manager
        assert gpu_manager.host == "test.hpc.com"
        assert gpu_manager.user == "testuser"

    def test_detect_available_gpu_groups_success(self):
        """Test successful detection of available GPU groups from Pueue."""
        config = {
            "hpc": {
                "host": "test.hpc.com", 
                "user": "testuser",
                "ssh_command": "ssh",
                "pueue_command": "pueue"
            }
        }
        db_manager = Mock(spec=DatabaseManager)
        gpu_manager = DynamicGpuManager(config, db_manager)
        
        # Mock pueue group output
        mock_groups_output = """Groups
======
default (running: 0, queued: 0)
gpu_a (running: 1, queued: 2) 
gpu_b (running: 0, queued: 0)
gpu_c (running: 2, queued: 1)"""
        
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = mock_groups_output
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            
            groups = gpu_manager.detect_available_gpu_groups()
            
            expected_groups = ["default", "gpu_a", "gpu_b", "gpu_c"]
            assert groups == expected_groups

    def test_detect_gpu_groups_ssh_failure(self):
        """Test handling of SSH connection failure during GPU detection."""
        config = {
            "hpc": {
                "host": "unreachable.hpc.com",
                "user": "testuser", 
                "ssh_command": "ssh",
                "pueue_command": "pueue"
            }
        }
        db_manager = Mock(spec=DatabaseManager)
        gpu_manager = DynamicGpuManager(config, db_manager)
        
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("Connection refused")
            
            with pytest.raises(GpuDetectionError) as exc_info:
                gpu_manager.detect_available_gpu_groups()
            
            assert "Failed to detect GPU groups" in str(exc_info.value)
            assert "Connection refused" in str(exc_info.value)

    def test_get_gpu_resource_utilization_success(self):
        """Test successful retrieval of GPU resource utilization."""
        config = {
            "hpc": {
                "host": "test.hpc.com",
                "user": "testuser",
                "ssh_command": "ssh", 
                "pueue_command": "pueue"
            }
        }
        db_manager = Mock(spec=DatabaseManager)
        gpu_manager = DynamicGpuManager(config, db_manager)
        
        # Mock pueue status JSON output
        mock_status = {
            "groups": {
                "default": {"running": 0, "queued": 0},
                "gpu_a": {"running": 1, "queued": 2},
                "gpu_b": {"running": 0, "queued": 0}
            }
        }
        
        with patch('subprocess.run') as mock_run:
            mock_run.return_value.stdout = json.dumps(mock_status)
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            
            utilization = gpu_manager.get_gpu_resource_utilization()
            
            expected_utilization = {
                "default": {"running": 0, "queued": 0, "total_load": 0},
                "gpu_a": {"running": 1, "queued": 2, "total_load": 3},
                "gpu_b": {"running": 0, "queued": 0, "total_load": 0}
            }
            assert utilization == expected_utilization

    def test_sync_gpu_resources_with_database(self):
        """Test synchronization of detected GPU resources with database."""
        config = {
            "hpc": {
                "host": "test.hpc.com",
                "user": "testuser",
                "ssh_command": "ssh",
                "pueue_command": "pueue"
            }
        }
        db_manager = Mock(spec=DatabaseManager)
        gpu_manager = DynamicGpuManager(config, db_manager)
        
        # Mock detected groups
        with patch.object(gpu_manager, 'detect_available_gpu_groups') as mock_detect:
            mock_detect.return_value = ["default", "gpu_a", "gpu_b", "gpu_new"]
            
            # Mock database methods
            db_manager.get_resources_by_status.return_value = [
                {"pueue_group": "default", "status": "available"},
                {"pueue_group": "gpu_old", "status": "available"}  # This should be removed
            ]
            
            gpu_manager.sync_gpu_resources_with_database()
            
            # Should ensure new resources exist
            db_manager.ensure_gpu_resource_exists.assert_any_call("gpu_a")
            db_manager.ensure_gpu_resource_exists.assert_any_call("gpu_b") 
            db_manager.ensure_gpu_resource_exists.assert_any_call("gpu_new")

    def test_get_optimal_gpu_assignment(self):
        """Test optimal GPU assignment based on current utilization."""
        config = {
            "hpc": {
                "host": "test.hpc.com",
                "user": "testuser",
                "ssh_command": "ssh",
                "pueue_command": "pueue"
            }
        }
        db_manager = Mock(spec=DatabaseManager)
        gpu_manager = DynamicGpuManager(config, db_manager)
        
        # Mock utilization data
        with patch.object(gpu_manager, 'get_gpu_resource_utilization') as mock_util:
            mock_util.return_value = {
                "gpu_a": {"running": 2, "queued": 1, "total_load": 3},
                "gpu_b": {"running": 0, "queued": 0, "total_load": 0},
                "gpu_c": {"running": 1, "queued": 0, "total_load": 1}
            }
            
            # Mock database availability
            db_manager.get_gpu_resource.side_effect = lambda group: {
                "pueue_group": group, 
                "status": "available" if group in ["gpu_b", "gpu_c"] else "assigned"
            }
            
            optimal_group = gpu_manager.get_optimal_gpu_assignment()
            
            # Should return gpu_b (least loaded and available)
            assert optimal_group == "gpu_b"

    def test_refresh_gpu_resources_complete_workflow(self):
        """Test the complete workflow of refreshing GPU resources."""
        config = {
            "hpc": {
                "host": "test.hpc.com", 
                "user": "testuser",
                "ssh_command": "ssh",
                "pueue_command": "pueue"
            }
        }
        db_manager = Mock(spec=DatabaseManager)
        gpu_manager = DynamicGpuManager(config, db_manager)
        
        # Mock all the sub-methods
        with patch.object(gpu_manager, 'detect_available_gpu_groups') as mock_detect, \
             patch.object(gpu_manager, 'sync_gpu_resources_with_database') as mock_sync, \
             patch.object(gpu_manager, 'get_gpu_resource_utilization') as mock_util:
            
            mock_detect.return_value = ["gpu_a", "gpu_b"]
            mock_util.return_value = {
                "gpu_a": {"running": 1, "queued": 0, "total_load": 1},
                "gpu_b": {"running": 0, "queued": 0, "total_load": 0}
            }
            
            result = gpu_manager.refresh_gpu_resources()
            
            mock_detect.assert_called_once()
            mock_sync.assert_called_once()
            mock_util.assert_called_once()
            
            expected_result = {
                "detected_groups": ["gpu_a", "gpu_b"],
                "utilization": {
                    "gpu_a": {"running": 1, "queued": 0, "total_load": 1},
                    "gpu_b": {"running": 0, "queued": 0, "total_load": 0}
                }
            }
            assert result == expected_result