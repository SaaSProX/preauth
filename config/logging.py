"""
Structured logging configuration using structlog (SAA-83).

Usage:
    from config.logging import configure_logging, get_logger
    
    configure_logging()  # Call once at app startup
    logger = get_logger()
    logger.info("something happened", user_id=123, action="login")

Output (JSON mode):
    {"event": "something happened", "user_id": 123, "action": "login", 
     "timestamp": "2026-06-24T22:30:00Z", "level": "info"}
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from config.settings import settings


def configure_logging() -> None:
    """
    Configure structlog for the application.
    
    - JSON output in production (log_json=True)
    - Pretty console output in development (log_json=False)
    """
    
    # Shared processors for all environments
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]
    
    if settings.log_json:
        # Production: JSON logs
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: Pretty console output
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.plain_traceback,
            ),
        ]
    
    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Also configure stdlib logging to use structlog
    # This catches logs from third-party libraries
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structlog logger instance.
    
    Args:
        name: Logger name (usually __name__). If None, uses root logger.
    
    Returns:
        A bound logger that can be used like:
            logger.info("message", key=value, ...)
    """
    return structlog.get_logger(name)


def bind_contextvars(**kwargs: Any) -> None:
    """
    Bind context variables that will be included in all subsequent log entries.
    
    Useful for request-scoped data like request_id, checkin_id, etc.
    
    Example:
        bind_contextvars(request_id="abc-123", checkin_id="DHAM/2026/...")
        logger.info("processing")  # Will include request_id and checkin_id
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_contextvars() -> None:
    """Clear all bound context variables. Call at start of each request."""
    structlog.contextvars.clear_contextvars()


def unbind_contextvars(*keys: str) -> None:
    """Remove specific context variables."""
    structlog.contextvars.unbind_contextvars(*keys)
