"""
Global exception handler (SAA-83).

Catches unhandled exceptions and returns consistent JSON responses
instead of HTML error pages. Also ensures errors are logged and sent to Sentry.
"""

import sentry_sdk
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config.logging import get_logger

logger = get_logger(__name__)


class ExceptionHandlerMiddleware(BaseHTTPMiddleware):
    """
    Catches unhandled exceptions and returns a clean JSON response.
    
    This runs OUTSIDE the request logging middleware, so it catches
    everything including middleware failures.
    """
    
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            # Log the exception (Sentry will also capture via integration)
            logger.exception(
                "unhandled_exception",
                path=request.url.path,
                method=request.method,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            
            # Ensure Sentry captures it (belt and suspenders)
            sentry_sdk.capture_exception(exc)
            
            # Return clean JSON error
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal server error",
                    "detail": "An unexpected error occurred. This has been logged.",
                    # Don't expose internal error details in production
                },
                headers={
                    "X-Request-ID": request.headers.get("X-Request-ID", "unknown"),
                },
            )
