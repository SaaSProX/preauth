from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config.settings import settings
from webhook.router import router
from auth.router import router as auth_router


app = FastAPI(title="Aman HMO Pre-Auth Agent")

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
if settings.rate_limit_enabled:
    from middleware.rate_limit import limiter, rate_limit_exceeded_handler, RateLimitExceeded
    
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Include routers
app.include_router(router)
app.include_router(auth_router)


@app.get("/health")
def health():
    """Health check endpoint - exempt from rate limiting."""
    return {"status": "ok"}
