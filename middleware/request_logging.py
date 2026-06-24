"""
Request logging middleware (SAA-83).

Adds structured logging context for every request:
- request_id (from header or generated)
- path, method
- timing information

Usage:
    app.add_middleware(RequestLoggingMiddleware)
"""

import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config.logging import get_logger, bind_contextvars, clear_contextvars
from config.sentry import set_sentry_context

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that:
    1. Generates/extracts request_id
    2. Binds request context for structured logging
    3. Logs request start/end with timing
    4. Adds request_id to response headers
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        
        # Clear any stale context and bind fresh request context
        clear_contextvars()
        bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )
        
        # Also set Sentry context
        set_sentry_context(request_id=request_id)
        
        # Skip noisy health check logs
        is_health_check = request.url.path in ("/health", "/", "/favicon.ico")
        
        start_time = time.perf_counter()
        
        if not is_health_check:
            logger.info("request_started")
        
        # Process request
        try:
            response = await call_next(request)
        except Exception as e:
            # Log unhandled exceptions (Sentry will also capture these)
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.exception(
                "request_failed",
                duration_ms=round(duration_ms, 2),
                error=str(e),
            )
            raise
        
        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        if not is_health_check:
            logger.info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )
        
        # Add request ID to response headers for tracing
        response.headers["X-Request-ID"] = request_id
        
        return response
