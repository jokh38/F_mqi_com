import logging
import time
from datetime import datetime
from enum import IntEnum
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

from src.common.db_manager import DatabaseManager


class CasePriority(IntEnum):
    """Case priority levels (higher value = higher priority)."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4
    CRITICAL = 5


@dataclass
class PriorityConfig:
    """Configuration for priority-based scheduling algorithms."""
    algorithm: str = "weighted_fair"  # "weighted_fair", "strict_priority", "aging"
    aging_factor: float = 0.1  # Priority boost per hour of waiting
    starvation_threshold_hours: int = 24  # Hours after which low priority cases get boost
    priority_weights: Dict[int, float] = field(default_factory=lambda: {
        CasePriority.LOW: 1.0,
        CasePriority.NORMAL: 2.0,
        CasePriority.HIGH: 4.0,
        CasePriority.URGENT: 8.0,
        CasePriority.CRITICAL: 16.0
    })


@dataclass
class SchedulingMetrics:
    """Metrics for priority scheduling performance tracking."""
    cases_scheduled_by_priority: Dict[int, int] = field(default_factory=dict)
    average_wait_time_by_priority: Dict[int, float] = field(default_factory=dict)
    starvation_prevented: int = 0
    total_scheduling_decisions: int = 0
    algorithm_switches: int = 0
    
    def record_case_scheduled(self, priority: int, wait_time: float) -> None:
        """Record a case being scheduled."""
        self.cases_scheduled_by_priority[priority] = self.cases_scheduled_by_priority.get(priority, 0) + 1
        
        # Update average wait time
        current_avg = self.average_wait_time_by_priority.get(priority, 0.0)
        current_count = self.cases_scheduled_by_priority[priority]
        new_avg = ((current_avg * (current_count - 1)) + wait_time) / current_count
        self.average_wait_time_by_priority[priority] = new_avg
        
        self.total_scheduling_decisions += 1


class PriorityScheduler:
    """
    Priority-based scheduler for intelligent case scheduling with multiple algorithms
    and resource-aware optimization.
    """
    
    def __init__(
        self,
        db_manager: DatabaseManager,
        config: Optional[PriorityConfig] = None
    ):
        """
        Initialize the priority scheduler.
        
        Args:
            db_manager: Database manager instance
            config: Priority configuration, uses defaults if None
        """
        self.db_manager = db_manager
        self.config = config or PriorityConfig()
        self.metrics = SchedulingMetrics()
        
        # Ensure priority column exists in cases table
        self._ensure_priority_column()
        
        logging.info(
            f"PriorityScheduler initialized with algorithm: {self.config.algorithm}, "
            f"aging_factor: {self.config.aging_factor}"
        )
    
    def _ensure_priority_column(self) -> None:
        """Ensure the priority column exists in the cases table."""
        try:
            # Check if priority column exists by trying to select from it
            self.db_manager.cursor.execute("SELECT priority FROM cases LIMIT 1")
        except Exception:
            # Column doesn't exist, add it with default NORMAL priority
            self.db_manager.cursor.execute(
                "ALTER TABLE cases ADD COLUMN priority INTEGER DEFAULT 2"
            )
            self.db_manager.connection.commit()
            logging.info("Added priority column to cases table with default NORMAL priority")
    
    def set_case_priority(self, case_id: int, priority: CasePriority) -> bool:
        """
        Set the priority for a specific case.
        
        Args:
            case_id: Case ID to update
            priority: New priority level
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        try:
            self.db_manager.cursor.execute(
                "UPDATE cases SET priority = ? WHERE case_id = ?",
                (int(priority), case_id)
            )
            self.db_manager.connection.commit()
            
            if self.db_manager.cursor.rowcount > 0:
                logging.info(f"Set case {case_id} priority to {priority.name}")
                return True
            else:
                logging.warning(f"Case {case_id} not found when setting priority")
                return False
                
        except Exception as e:
            logging.error(f"Failed to set priority for case {case_id}: {e}")
            return False
    
    def get_prioritized_cases(self, status: str = "submitted", limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get cases ordered by priority using the configured scheduling algorithm.
        
        Args:
            status: Case status to filter by
            limit: Maximum number of cases to return
            
        Returns:
            List of cases ordered by scheduling priority
        """
        try:
            if self.config.algorithm == "strict_priority":
                return self._get_cases_strict_priority(status, limit)
            elif self.config.algorithm == "aging":
                return self._get_cases_with_aging(status, limit)
            else:  # Default to weighted_fair
                return self._get_cases_weighted_fair(status, limit)
        except Exception as e:
            logging.error(f"Failed to get prioritized cases: {e}")
            # Fallback to basic priority ordering
            return self._get_cases_basic_priority(status, limit)
    
    def _get_cases_strict_priority(self, status: str, limit: Optional[int]) -> List[Dict[str, Any]]:
        """Get cases using strict priority ordering (highest priority first)."""
        query = """
        SELECT * FROM cases 
        WHERE status = ? 
        ORDER BY priority DESC, created_at ASC
        """
        params = [status]
        
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        
        self.db_manager.cursor.execute(query, params)
        cases = [dict(row) for row in self.db_manager.cursor.fetchall()]
        
        logging.debug(f"Retrieved {len(cases)} cases using strict priority algorithm")
        return cases
    
    def _get_cases_with_aging(self, status: str, limit: Optional[int]) -> List[Dict[str, Any]]:
        """Get cases using aging algorithm (priority increases with wait time)."""
        current_time = datetime.now()
        
        # Get all cases and calculate aged priority
        query = "SELECT * FROM cases WHERE status = ? ORDER BY created_at ASC"
        self.db_manager.cursor.execute(query, (status,))
        cases = [dict(row) for row in self.db_manager.cursor.fetchall()]
        
        aged_cases = []
        for case in cases:
            # Calculate wait time in hours
            created_at = datetime.fromisoformat(case["created_at"])
            wait_hours = (current_time - created_at).total_seconds() / 3600.0
            
            # Calculate aged priority
            base_priority = case.get("priority", CasePriority.NORMAL)
            aged_priority = base_priority + (wait_hours * self.config.aging_factor)
            
            # Check for starvation prevention
            if wait_hours > self.config.starvation_threshold_hours and base_priority <= CasePriority.NORMAL:
                aged_priority += 2.0  # Significant boost for starved cases
                self.metrics.starvation_prevented += 1
                logging.info(f"Starvation prevention applied to case {case['case_id']} after {wait_hours:.1f}h wait")
            
            case["aged_priority"] = aged_priority
            aged_cases.append(case)
        
        # Sort by aged priority (descending) then by creation time (ascending)
        aged_cases.sort(key=lambda x: (-x["aged_priority"], x["created_at"]))
        
        if limit:
            aged_cases = aged_cases[:limit]
        
        logging.debug(f"Retrieved {len(aged_cases)} cases using aging algorithm")
        return aged_cases
    
    def _get_cases_weighted_fair(self, status: str, limit: Optional[int]) -> List[Dict[str, Any]]:
        """Get cases using weighted fair queuing algorithm."""
        current_time = datetime.now()
        
        # Get all cases and calculate weighted priority
        query = "SELECT * FROM cases WHERE status = ? ORDER BY created_at ASC"
        self.db_manager.cursor.execute(query, (status,))
        cases = [dict(row) for row in self.db_manager.cursor.fetchall()]
        
        weighted_cases = []
        for case in cases:
            # Calculate wait time in hours
            created_at = datetime.fromisoformat(case["created_at"])
            wait_hours = (current_time - created_at).total_seconds() / 3600.0
            
            # Get priority weight
            base_priority = case.get("priority", CasePriority.NORMAL)
            priority_weight = self.config.priority_weights.get(base_priority, 1.0)
            
            # Calculate weighted score (combines priority weight and wait time)
            weighted_score = priority_weight * (1.0 + (wait_hours * 0.05))  # 5% boost per hour
            
            # Apply starvation prevention
            if wait_hours > self.config.starvation_threshold_hours and base_priority <= CasePriority.NORMAL:
                weighted_score *= 2.0  # Double the weight for starved cases
                self.metrics.starvation_prevented += 1
                logging.info(f"Starvation prevention applied to case {case['case_id']}")
            
            case["weighted_score"] = weighted_score
            weighted_cases.append(case)
        
        # Sort by weighted score (descending) then by creation time (ascending)
        weighted_cases.sort(key=lambda x: (-x["weighted_score"], x["created_at"]))
        
        if limit:
            weighted_cases = weighted_cases[:limit]
        
        logging.debug(f"Retrieved {len(weighted_cases)} cases using weighted fair algorithm")
        return weighted_cases
    
    def _get_cases_basic_priority(self, status: str, limit: Optional[int]) -> List[Dict[str, Any]]:
        """Fallback method for basic priority ordering."""
        query = """
        SELECT * FROM cases 
        WHERE status = ? 
        ORDER BY COALESCE(priority, 2) DESC, created_at ASC
        """
        params = [status]
        
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        
        self.db_manager.cursor.execute(query, params)
        cases = [dict(row) for row in self.db_manager.cursor.fetchall()]
        
        logging.debug(f"Retrieved {len(cases)} cases using basic priority fallback")
        return cases
    
    def schedule_next_cases(self, available_gpus: int) -> List[Dict[str, Any]]:
        """
        Schedule the next cases to be processed based on available GPU resources.
        
        Args:
            available_gpus: Number of available GPU resources
            
        Returns:
            List of cases to be processed, ordered by priority
        """
        if available_gpus <= 0:
            return []
        
        # Get prioritized cases up to the number of available GPUs
        prioritized_cases = self.get_prioritized_cases("submitted", limit=available_gpus)
        
        # Record scheduling metrics
        current_time = datetime.now()
        for case in prioritized_cases:
            created_at = datetime.fromisoformat(case["created_at"])
            wait_time = (current_time - created_at).total_seconds() / 3600.0  # Hours
            priority = case.get("priority", CasePriority.NORMAL)
            self.metrics.record_case_scheduled(priority, wait_time)
        
        if prioritized_cases:
            priorities = [case.get("priority", CasePriority.NORMAL) for case in prioritized_cases]
            logging.info(
                f"Scheduled {len(prioritized_cases)} cases with priorities: {priorities} "
                f"for {available_gpus} available GPUs"
            )
        
        return prioritized_cases
    
    def get_priority_statistics(self) -> Dict[str, Any]:
        """
        Get priority scheduling statistics and performance metrics.
        
        Returns:
            Dictionary with priority statistics
        """
        total_scheduled = sum(self.metrics.cases_scheduled_by_priority.values())
        
        statistics = {
            "algorithm": self.config.algorithm,
            "total_cases_scheduled": total_scheduled,
            "starvation_prevented": self.metrics.starvation_prevented,
            "scheduling_decisions": self.metrics.total_scheduling_decisions,
            "cases_by_priority": dict(self.metrics.cases_scheduled_by_priority),
            "average_wait_times": dict(self.metrics.average_wait_time_by_priority),
            "configuration": {
                "aging_factor": self.config.aging_factor,
                "starvation_threshold_hours": self.config.starvation_threshold_hours,
                "priority_weights": dict(self.config.priority_weights)
            }
        }
        
        # Calculate priority distribution percentages
        if total_scheduled > 0:
            priority_percentages = {}
            for priority, count in self.metrics.cases_scheduled_by_priority.items():
                priority_percentages[priority] = (count / total_scheduled) * 100
            statistics["priority_distribution_percent"] = priority_percentages
        
        return statistics
    
    def update_algorithm(self, algorithm: str) -> bool:
        """
        Update the scheduling algorithm at runtime.
        
        Args:
            algorithm: New algorithm ("strict_priority", "aging", "weighted_fair")
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        valid_algorithms = ["strict_priority", "aging", "weighted_fair"]
        
        if algorithm not in valid_algorithms:
            logging.error(f"Invalid algorithm: {algorithm}. Valid options: {valid_algorithms}")
            return False
        
        old_algorithm = self.config.algorithm
        self.config.algorithm = algorithm
        self.metrics.algorithm_switches += 1
        
        logging.info(f"Scheduling algorithm updated from {old_algorithm} to {algorithm}")
        return True
    
    def reset_metrics(self) -> None:
        """Reset scheduling metrics for new measurement period."""
        self.metrics = SchedulingMetrics()
        logging.info("Priority scheduling metrics reset")