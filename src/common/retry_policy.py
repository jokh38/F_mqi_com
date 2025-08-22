"""
Retry policy implementation for handling transient failures.
Provides exponential backoff and error categorization.
"""
import time
import logging
import socket
import subprocess
from typing import Any, Callable, Type, Tuple
from functools import wraps


logger = logging.getLogger(__name__)


class TransientError(Exception):
    """Exception for errors that may be resolved by retrying."""
    pass


class PermanentError(Exception):
    """Exception for errors that should not be retried."""
    pass


class RetryExhaustedError(Exception):
    """Exception raised when all retry attempts have been exhausted."""
    pass


class RetryPolicy:
    """
    Implements retry logic with exponential backoff for transient failures.
    
    Automatically classifies common network and system exceptions as transient
    or permanent errors.
    """
    
    # Common transient exception types
    TRANSIENT_EXCEPTION_TYPES = (
        socket.timeout,
        socket.gaierror,
        ConnectionRefusedError,
        ConnectionResetError,
        ConnectionAbortedError,
        subprocess.TimeoutExpired,
        OSError,  # Generic system error that might be transient
    )
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_multiplier: float = 2.0
    ):
        """
        Initialize retry policy.
        
        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Initial delay in seconds
            max_delay: Maximum delay between retries in seconds
            backoff_multiplier: Multiplier for exponential backoff
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_multiplier = backoff_multiplier
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for the given attempt number."""
        delay = self.base_delay * (self.backoff_multiplier ** attempt)
        return min(delay, self.max_delay)
    
    def _is_transient_error(self, exception: Exception) -> bool:
        """
        Determine if an exception should be treated as transient.
        
        Args:
            exception: The exception to classify
            
        Returns:
            True if the exception is transient and should be retried
        """
        if isinstance(exception, (TransientError, *self.TRANSIENT_EXCEPTION_TYPES)):
            return True
        
        if isinstance(exception, PermanentError):
            return False
            
        # Check for specific error conditions in subprocess errors
        if isinstance(exception, subprocess.CalledProcessError):
            # Some return codes might be transient (e.g., temporary resource unavailable)
            # For now, treat all CalledProcessError as permanent unless explicitly wrapped
            return False
            
        # Default: treat unknown exceptions as permanent to avoid infinite loops
        return False
    
    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with retry logic.
        
        Args:
            func: The function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            The result of the successful function call
            
        Raises:
            PermanentError: For errors that should not be retried
            RetryExhaustedError: When all retry attempts are exhausted
        """
        last_exception = None
        
        for attempt in range(self.max_retries + 1):  # +1 for initial attempt
            try:
                result = func(*args, **kwargs)
                if attempt > 0:
                    logger.info(f"Function succeeded after {attempt} retries")
                return result
                
            except Exception as e:
                last_exception = e
                
                if not self._is_transient_error(e):
                    logger.error(f"Permanent error encountered: {e}")
                    raise e
                
                if attempt < self.max_retries:
                    delay = self._calculate_delay(attempt)
                    logger.warning(
                        f"Transient error on attempt {attempt + 1}/{self.max_retries + 1}: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Exhausted all {self.max_retries} retry attempts")
        
        # If we get here, all attempts failed
        raise RetryExhaustedError(
            f"Failed after {self.max_retries + 1} attempts. Last error: {last_exception}"
        ) from last_exception


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_multiplier: float = 2.0
):
    """
    Decorator for applying retry logic to functions.
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay between retries in seconds
        backoff_multiplier: Multiplier for exponential backoff
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            policy = RetryPolicy(max_retries, base_delay, max_delay, backoff_multiplier)
            return policy.execute(func, *args, **kwargs)
        return wrapper
    return decorator