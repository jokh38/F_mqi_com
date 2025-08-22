"""
Dynamic GPU Resource Manager for HPC Systems.

This module provides dynamic detection and management of GPU resources
on remote HPC systems using Pueue queue management. It auto-detects
available GPU groups and maintains optimal resource allocation.
"""

import subprocess
import json
import logging
import re
from typing import Dict, Any, List, Optional
from src.common.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class GpuDetectionError(Exception):
    """Custom exception for GPU resource detection failures."""
    pass


class DynamicGpuManager:
    """
    Manages dynamic detection and allocation of GPU resources on HPC systems.
    
    This class interfaces with the remote Pueue daemon to detect available
    GPU groups, monitor their utilization, and provide optimal resource
    assignment for new computational cases.
    """

    def __init__(self, config: Dict[str, Any], db_manager: DatabaseManager) -> None:
        """
        Initialize the DynamicGpuManager.

        Args:
            config: Application configuration dictionary containing HPC settings
            db_manager: DatabaseManager instance for resource persistence
        """
        self.hpc_config = config["hpc"]
        self.db_manager = db_manager
        self.host = self.hpc_config["host"]
        self.user = self.hpc_config["user"]
        self.ssh_cmd = self.hpc_config.get("ssh_command", "ssh")
        self.pueue_cmd = self.hpc_config.get("pueue_command", "pueue")

    def detect_available_gpu_groups(self) -> List[str]:
        """
        Detect available GPU groups from the remote Pueue daemon.
        
        Connects to the remote HPC system and queries Pueue for all
        available groups that can be used for job submission.
        
        Returns:
            List of available GPU group names
            
        Raises:
            GpuDetectionError: If detection fails due to connection or parsing issues
        """
        ssh_command = [
            self.ssh_cmd,
            f"{self.user}@{self.host}",
            self.pueue_cmd,
            "group"
        ]
        
        try:
            logger.info(f"Detecting GPU groups on {self.host}...")
            result = subprocess.run(
                ssh_command, 
                check=True, 
                capture_output=True, 
                text=True, 
                timeout=30
            )
            
            # Parse group names from the output
            groups = []
            lines = result.stdout.strip().split('\n')
            
            for line in lines:
                # Look for group names (format: "group_name (running: X, queued: Y)")
                match = re.match(r'^(\w+)\s+\(running:', line.strip())
                if match:
                    groups.append(match.group(1))
            
            logger.info(f"Detected {len(groups)} GPU groups: {groups}")
            return groups
            
        except Exception as e:
            error_msg = f"Failed to detect GPU groups from {self.host}: {str(e)}"
            logger.error(error_msg)
            raise GpuDetectionError(error_msg) from e

    def get_gpu_resource_utilization(self) -> Dict[str, Dict[str, int]]:
        """
        Get current utilization of all GPU resources.
        
        Queries the remote Pueue daemon to get real-time utilization
        statistics for all available GPU groups.
        
        Returns:
            Dictionary mapping group names to utilization stats:
            {
                "gpu_a": {"running": 2, "queued": 1, "total_load": 3},
                "gpu_b": {"running": 0, "queued": 0, "total_load": 0}
            }
            
        Raises:
            GpuDetectionError: If utilization data cannot be retrieved
        """
        ssh_command = [
            self.ssh_cmd,
            f"{self.user}@{self.host}",
            self.pueue_cmd,
            "status",
            "--json"
        ]
        
        try:
            logger.debug("Querying GPU resource utilization...")
            result = subprocess.run(
                ssh_command,
                check=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            status_data = json.loads(result.stdout)
            utilization = {}
            
            groups = status_data.get("groups", {})
            for group_name, group_data in groups.items():
                running = group_data.get("running", 0)
                queued = group_data.get("queued", 0)
                total_load = running + queued
                
                utilization[group_name] = {
                    "running": running,
                    "queued": queued,
                    "total_load": total_load
                }
            
            return utilization
            
        except Exception as e:
            error_msg = f"Failed to get GPU utilization from {self.host}: {str(e)}"
            logger.error(error_msg)
            raise GpuDetectionError(error_msg) from e

    def sync_gpu_resources_with_database(self) -> None:
        """
        Synchronize detected GPU resources with the local database.
        
        Ensures that all currently available GPU groups are represented
        in the local database as manageable resources.
        """
        try:
            # Get currently detected groups
            detected_groups = self.detect_available_gpu_groups()
            
            # Ensure all detected groups exist in database
            for group in detected_groups:
                self.db_manager.ensure_gpu_resource_exists(group)
                logger.debug(f"Ensured GPU resource '{group}' exists in database")
            
            logger.info(f"Synchronized {len(detected_groups)} GPU resources with database")
            
        except GpuDetectionError:
            # Re-raise detection errors
            raise
        except Exception as e:
            error_msg = f"Failed to sync GPU resources with database: {str(e)}"
            logger.error(error_msg)
            raise GpuDetectionError(error_msg) from e

    def get_optimal_gpu_assignment(self) -> Optional[str]:
        """
        Get the optimal GPU group for assigning a new case.
        
        Analyzes current utilization and database availability to determine
        the best GPU group for a new computational case.
        
        Returns:
            Name of the optimal GPU group, or None if no resources available
        """
        try:
            # Get current utilization from remote system
            utilization = self.get_gpu_resource_utilization()
            
            # Find available resources in database and their utilization
            available_groups = []
            for group_name in utilization.keys():
                resource = self.db_manager.get_gpu_resource(group_name)
                if resource and resource["status"] == "available":
                    available_groups.append((group_name, utilization[group_name]["total_load"]))
            
            if not available_groups:
                logger.warning("No GPU resources available for assignment")
                return None
            
            # Sort by total load (ascending) to get least loaded resource
            available_groups.sort(key=lambda x: x[1])
            optimal_group = available_groups[0][0]
            
            logger.info(f"Selected optimal GPU group: {optimal_group}")
            return optimal_group
            
        except Exception as e:
            logger.error(f"Failed to determine optimal GPU assignment: {e}")
            return None

    def refresh_gpu_resources(self) -> Dict[str, Any]:
        """
        Perform a complete refresh of GPU resource information.
        
        This method combines detection, synchronization, and utilization
        analysis to provide a comprehensive update of GPU resource status.
        
        Returns:
            Dictionary containing detected groups and current utilization
        """
        try:
            # Detect available groups
            detected_groups = self.detect_available_gpu_groups()
            
            # Sync with database
            self.sync_gpu_resources_with_database()
            
            # Get current utilization
            utilization = self.get_gpu_resource_utilization()
            
            result = {
                "detected_groups": detected_groups,
                "utilization": utilization
            }
            
            logger.info(f"GPU resource refresh completed successfully")
            return result
            
        except Exception as e:
            logger.error(f"GPU resource refresh failed: {e}")
            raise