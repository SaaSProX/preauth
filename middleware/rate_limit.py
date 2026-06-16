"""Rate limiting middleware using slowapi.

Provides configurable rate limits for API endpoints:
- Auth endpoints (login, register): Stricter limits to prevent brute force
- Webhook endpoints: Higher limits for incoming AMAN webhooks  
- General API: Standard limits for dashboard/authenticated requests
- Health check: Exempt from rate limiting

Limits are configurable via environment variables.
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from fastapi import Request

from config.settings import settings


def get_client_identifier(request: Request) -> str:
    """Get client identifier for rate limiting.
    
    Uses API key if present (for authenticated requests),
    otherwise falls back to IP address.
    """
    # Check for API key first (more granular limiting for API clients)
    api_key = request.headers.get("X-API-Key")
    if api_key:
        # Use last 8 chars of API key as identifier (don't expose full key in logs)
        return f"apikey:{api_key[-8:]}"
    
    # Check for JWT auth (dashboard users)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        # Use a hash of the token for identification
        token = auth_header[7:]
        if len(token) > 16:
            return f"jwt:{token[-8:]}"
    
    # Fall back to IP address
    return get_remote_address(request)


# Create limiter instance with custom key function
limiter = Limiter(
    key_func=get_client_identifier,
    default_limits=[settings.rate_limit_default],
    storage_uri=settings.rate_limit_storage_uri,
    strategy="fixed-window",
)


# Rate limit decorators for different endpoint types
def auth_limit():
    """Stricter rate limit for auth endpoints (login, register)."""
    return limiter.limit(settings.rate_limit_auth)


def webhook_limit():
    """Higher rate limit for incoming webhooks."""
    return limiter.limit(settings.rate_limit_webhook)


def api_limit():
    """Standard rate limit for API endpoints."""
    return limiter.limit(settings.rate_limit_api)


def dashboard_limit():
    """Rate limit for dashboard queries (can be expensive)."""
    return limiter.limit(settings.rate_limit_dashboard)


# Export handler for the app to use
rate_limit_exceeded_handler = _rate_limit_exceeded_handler
RateLimitExceeded = RateLimitExceeded
