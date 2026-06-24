from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from config.sentry import init_sentry
from config.logging import configure_logging, get_logger
from middleware.request_logging import RequestLoggingMiddleware
from webhook.router import router
from auth.router import router as auth_router

# Initialize observability (SAA-83)
configure_logging()
sentry_enabled = init_sentry()

logger = get_logger(__name__)

app = FastAPI(title="Aman HMO Pre-Auth Agent")

# Add request logging middleware (must be added before CORS)
app.add_middleware(RequestLoggingMiddleware)

_allowed_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(auth_router)


@app.on_event("startup")
async def startup_event():
    """Log startup information."""
    logger.info(
        "app_started",
        sentry_enabled=sentry_enabled,
        environment=settings.sentry_environment,
        agent_enabled=settings.agent_enabled,
        applied_mode_enabled=settings.applied_mode_enabled,
    )


@app.get("/health")
def health():
    return {"status": "ok"}
