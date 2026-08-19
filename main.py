from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from config.sentry import init_sentry
from config.logging import configure_logging, get_logger
from middleware.request_logging import RequestLoggingMiddleware
from middleware.exception_handler import ExceptionHandlerMiddleware
from middleware.rate_limit import limiter, rate_limit_exceeded_handler, RateLimitExceeded
from services.db import init_pg_pool, close_pg_pool
from webhook.router import router
from auth.router import router as auth_router

# Initialize observability (SAA-83)
configure_logging()
sentry_enabled = init_sentry()

logger = get_logger(__name__)

app = FastAPI(title="Aman HMO Pre-Auth Agent")

# Middleware order matters! Exception handler wraps everything.
app.add_middleware(ExceptionHandlerMiddleware)
app.add_middleware(RequestLoggingMiddleware)

# CORS middleware
_allowed_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting middleware (SAA-84)
app.state.limiter = limiter
if settings.rate_limit_enabled:
    from slowapi.middleware import SlowAPIMiddleware

    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    # SlowAPIMiddleware is what actually applies `rate_limit_default` to every
    # route - without it, only routes with an explicit @limiter.limit(...)
    # decorator are protected, leaving every other endpoint unlimited despite
    # the "default limit for all endpoints" setting existing.
    app.add_middleware(SlowAPIMiddleware)

# Include routers
app.include_router(router)
app.include_router(auth_router)


@app.on_event("startup")
async def startup_event():
    """Log startup information and open the shared DB pool."""
    await init_pg_pool()
    logger.info(
        "app_started",
        sentry_enabled=sentry_enabled,
        environment=settings.sentry_environment,
        agent_enabled=settings.agent_enabled,
        applied_mode_enabled=settings.applied_mode_enabled,
    )


@app.on_event("shutdown")
async def shutdown_event():
    """Close the shared DB pool."""
    await close_pg_pool()


@app.get("/health")
@limiter.exempt
def health():
    """Shallow health check - just confirms app is running. Exempt from rate limiting."""
    return {"status": "ok"}


@app.get("/health/deep")
async def health_deep():
    """
    Deep health check - verifies all dependencies.
    
    Use for monitoring/alerting. Don't hit this on every request.
    """
    from services.health import deep_health_check
    return await deep_health_check()
