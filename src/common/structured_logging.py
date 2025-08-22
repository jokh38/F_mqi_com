"""
Structured logging implementation for enhanced context and observability.
Provides consistent log formatting with contextual information.
"""
import json
import logging
from typing import Dict, Any, Optional, Union
from dataclasses import dataclass


@dataclass
class LogContext:
    """
    Context information for structured logging.
    
    Encapsulates common contextual data like case ID, operation type,
    and additional metadata for enhanced log observability.
    """
    case_id: Optional[str] = None
    operation: Optional[str] = None
    gpu_group: Optional[str] = None
    task_id: Optional[int] = None
    extra_data: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Initialize extra_data as empty dict if not provided."""
        if self.extra_data is None:
            self.extra_data = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for structured logging."""
        result = {}
        
        # Add non-None fields
        if self.case_id is not None:
            result['case_id'] = self.case_id
        if self.operation is not None:
            result['operation'] = self.operation
        if self.gpu_group is not None:
            result['gpu_group'] = self.gpu_group
        if self.task_id is not None:
            result['task_id'] = self.task_id
        
        # Merge extra_data
        if self.extra_data:
            result.update(self.extra_data)
            
        return result


class StructuredLogger:
    """
    Enhanced logger that provides structured logging with context.
    
    Wraps standard Python logging to include consistent contextual information
    and structured formatting for better observability.
    """
    
    def __init__(self, name: str, default_context: Optional[Dict[str, Any]] = None):
        """
        Initialize structured logger.
        
        Args:
            name: Logger name
            default_context: Default context included in all log messages
        """
        self.logger = logging.getLogger(name)
        self.default_context = default_context or {}
    
    def _build_context(self, context: Optional[LogContext] = None) -> Dict[str, Any]:
        """Build complete context by merging default and specific context."""
        full_context = self.default_context.copy()
        
        if context:
            full_context.update(context.to_dict())
            
        return full_context
    
    def _log_with_context(
        self, 
        level: int, 
        message: str, 
        context: Optional[LogContext] = None,
        **kwargs
    ):
        """Internal method to log with structured context."""
        full_context = self._build_context(context)
        structured_message = format_structured_message(message, full_context)
        self.logger.log(level, structured_message, **kwargs)
    
    def debug(self, message: str, context: Optional[LogContext] = None, **kwargs):
        """Log debug message with context."""
        self._log_with_context(logging.DEBUG, message, context, **kwargs)
    
    def info(self, message: str, context: Optional[LogContext] = None, **kwargs):
        """Log info message with context."""
        self._log_with_context(logging.INFO, message, context, **kwargs)
    
    def warning(self, message: str, context: Optional[LogContext] = None, **kwargs):
        """Log warning message with context."""
        self._log_with_context(logging.WARNING, message, context, **kwargs)
    
    def error(self, message: str, context: Optional[LogContext] = None, **kwargs):
        """Log error message with context."""
        self._log_with_context(logging.ERROR, message, context, **kwargs)
    
    def critical(self, message: str, context: Optional[LogContext] = None, **kwargs):
        """Log critical message with context."""
        self._log_with_context(logging.CRITICAL, message, context, **kwargs)


def format_structured_message(message: str, context: Dict[str, Any]) -> str:
    """
    Format a log message with structured context.
    
    Args:
        message: The main log message
        context: Dictionary of contextual key-value pairs
        
    Returns:
        Formatted message with context information
    """
    if not context:
        return message
    
    context_parts = []
    
    for key, value in context.items():
        # Handle complex values by JSON encoding them
        if isinstance(value, (dict, list)):
            try:
                formatted_value = json.dumps(value, separators=(',', ':'))
            except (TypeError, ValueError):
                formatted_value = str(value)
        else:
            formatted_value = str(value)
        
        context_parts.append(f"{key}={formatted_value}")
    
    context_str = " ".join(context_parts)
    return f"{message} | {context_str}"


# Convenience function for creating structured loggers
def get_structured_logger(
    name: str, 
    default_context: Optional[Dict[str, Any]] = None
) -> StructuredLogger:
    """
    Create a structured logger with optional default context.
    
    Args:
        name: Logger name
        default_context: Default context for all log messages
        
    Returns:
        StructuredLogger instance
    """
    return StructuredLogger(name, default_context)