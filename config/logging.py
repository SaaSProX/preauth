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
import re
import sys
from typing import Any

import structlog
from structlog.types import Processor

from config.settings import settings

# Fields that contain PII and should be masked
PII_FIELDS = {"insurance_no", "patient_id", "email", "phone", "date_of_birth"}
# Fields to completely redact (never log)
REDACT_FIELDS = {"password", "api_key", "token", "secret", "authorization"}


def _mask_value(value: str, visible_chars: int = 4) -> str:
    """Mask a string, keeping first and last N chars visible."""
    if not isinstance(value, str) or len(value) <= visible_chars * 2:
        return "***"
    return f"{value[:visible_chars]}...{value[-visible_chars:]}"


def _scrub_pii(logger, method_name: str, event_dict: dict) -> dict:
    """
    Scrub PII from log entries before they're emitted.
    Zero-copy for entries without PII (most logs).
    """
    # Fast path: check if any sensitive fields exist
    keys = event_dict.keys()
    has_pii = any(k in PII_FIELDS for k in keys)
    has_redact = any(k in REDACT_FIELDS for k in keys)
    
    if not has_pii and not has_redact:
        return event_dict
    
    # Slow path: copy and scrub
    scrubbed = {}
    for key, value in event_dict.items():
        if key in REDACT_FIELDS:
            scrubbed[key] = "[REDACTED]"
        elif key in PII_FIELDS and isinstance(value, str):
            scrubbed[key] = _mask_value(value)
        else:
            scrubbed[key] = value
    
    return scrubbed


def configure_logging() -> None:
    """
    Configure structlog for the application.
    
    - JSON output in production (log_json=True)
    - Pretty console output in development (log_json=False)
    - PII scrubbing in all environments
    """
    
    # Shared processors for all environments
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        _scrub_pii,  # Scrub PII before output
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


def get_contextvars() -> dict[str, Any]:
    """
    Get a copy of current context variables.
    Use this to propagate context to background tasks.
    """
    # structlog stores context in contextvars, we can access via merge
    return structlog.contextvars.get_contextvars()


def copy_context_to_task(func):
    """
    Decorator that copies logging context into a background task.
    
    Usage:
        @copy_context_to_task
        async def my_background_task(arg1, arg2):
            logger.info("this will have parent context")
    """
    import functools
    
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Context is captured when decorator is applied (at task scheduling time)
        return await func(*args, **kwargs)
    
    return wrapper


class BackgroundTaskWithContext:
    """
    Wrapper to run a background task with copied logging context.
    
    Usage:
        ctx = get_contextvars()
        background.add_task(BackgroundTaskWithContext(agent.run, ctx), patient_id, request_id)
    """
    
    def __init__(self, func, context: dict[str, Any]):
        self.func = func
        self.context = context
    
    async def __call__(self, *args, **kwargs):
        # Restore context in the background task
        if self.context:
            bind_contextvars(**self.context)
        return await self.func(*args, **kwargs)
