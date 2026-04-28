"""
Retry decorator with exponential backoff for API calls

Distinguishes between transient errors (network, rate limit, 5xx) which
should retry, and permanent errors (4xx, validation, auth) which should not.
"""

import logging
import random
import time
from functools import wraps
from typing import Callable, Tuple, Type

logger = logging.getLogger(__name__)


# Errors that warrant a retry (transient)
TRANSIENT_ERRORS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)

# HTTP status codes that warrant retry (when we can detect them)
RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def is_transient_error(exc: Exception) -> bool:
    """Determine if an exception represents a transient error worth retrying"""
    if isinstance(exc, TRANSIENT_ERRORS):
        return True

    # Check for HTTP status code on the exception (alpaca-py uses APIError)
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code in RETRY_STATUS_CODES:
        return True

    # Check exception message for known transient patterns
    msg = str(exc).lower()
    transient_patterns = [
        "timeout", "timed out",
        "connection", "connect",
        "rate limit", "too many requests",
        "service unavailable", "bad gateway",
        "internal server error",
        "temporary",
        "try again",
    ]
    if any(p in msg for p in transient_patterns):
        return True

    return False


def retry_on_failure(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
):
    """
    Decorator that retries a function with exponential backoff on transient failures.

    Args:
        max_attempts: Maximum number of attempts (including initial)
        initial_delay: Seconds to wait before first retry
        max_delay: Cap on retry delay
        backoff_factor: Multiplier for each successive delay
        jitter: Add random jitter to avoid thundering herd
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exc = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e

                    # Don't retry permanent errors
                    if not is_transient_error(e):
                        logger.debug(
                            f"{func.__name__} failed with permanent error: {e}"
                        )
                        raise

                    # Last attempt - re-raise
                    if attempt >= max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    # Calculate delay with optional jitter
                    sleep_time = min(delay, max_delay)
                    if jitter:
                        sleep_time *= (0.5 + random.random())

                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {sleep_time:.1f}s..."
                    )
                    time.sleep(sleep_time)
                    delay *= backoff_factor

            # Should never reach here, but just in case
            if last_exc:
                raise last_exc
            return None

        return wrapper
    return decorator


def retry_until(
    condition: Callable[[], bool],
    timeout: float = 60.0,
    interval: float = 2.0,
    description: str = "condition",
) -> bool:
    """
    Block until a condition returns True, with timeout.
    Returns True if condition met, False if timeout reached.
    """
    deadline = time.time() + timeout
    attempts = 0

    while time.time() < deadline:
        attempts += 1
        try:
            if condition():
                if attempts > 1:
                    logger.info(f"{description} satisfied after {attempts} attempts")
                return True
        except Exception as e:
            logger.debug(f"{description} check failed: {e}")

        time.sleep(interval)

    logger.error(f"{description} timed out after {timeout}s ({attempts} attempts)")
    return False
