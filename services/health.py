"""
Health check services (SAA-83).

Provides shallow and deep health checks for the application.
"""

import asyncio
import time
from typing import Any

import httpx

from config.settings import settings
from config.logging import get_logger
from services.db import pg_query_one

logger = get_logger(__name__)


async def check_database() -> dict[str, Any]:
    """Check PostgreSQL connectivity."""
    start = time.perf_counter()
    try:
        result = await pg_query_one("SELECT 1 as ok")
        latency_ms = (time.perf_counter() - start) * 1000
        return {
            "status": "ok" if result and result.get("ok") == 1 else "degraded",
            "latency_ms": round(latency_ms, 2),
        }
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        logger.warning("health_check_db_failed", error=str(e))
        return {
            "status": "error",
            "latency_ms": round(latency_ms, 2),
            "error": str(e),
        }


async def check_anthropic() -> dict[str, Any]:
    """Check Anthropic API connectivity (just auth, no tokens used)."""
    if not settings.anthropic_api_key:
        return {"status": "not_configured"}
    
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Hit the models endpoint - minimal cost, verifies auth
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            latency_ms = (time.perf_counter() - start) * 1000
            
            if resp.status_code == 200:
                return {"status": "ok", "latency_ms": round(latency_ms, 2)}
            else:
                return {
                    "status": "degraded",
                    "latency_ms": round(latency_ms, 2),
                    "http_status": resp.status_code,
                }
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        logger.warning("health_check_anthropic_failed", error=str(e))
        return {
            "status": "error",
            "latency_ms": round(latency_ms, 2),
            "error": str(e),
        }


async def check_aman_callback() -> dict[str, Any]:
    """Check AMAN callback endpoint reachability (if configured)."""
    if not settings.aman_callback_enabled or not settings.aman_decisions_url:
        return {"status": "not_configured"}
    
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # HEAD request to check reachability without sending data
            resp = await client.head(settings.aman_decisions_url)
            latency_ms = (time.perf_counter() - start) * 1000
            
            # Any response means the endpoint is reachable
            return {
                "status": "ok" if resp.status_code < 500 else "degraded",
                "latency_ms": round(latency_ms, 2),
                "http_status": resp.status_code,
            }
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        logger.warning("health_check_aman_callback_failed", error=str(e))
        return {
            "status": "error",
            "latency_ms": round(latency_ms, 2),
            "error": str(e),
        }


async def deep_health_check() -> dict[str, Any]:
    """
    Run all health checks in parallel and return aggregate status.
    
    Returns:
        {
            "status": "ok" | "degraded" | "error",
            "checks": {
                "database": {...},
                "anthropic": {...},
                "aman_callback": {...},
            }
        }
    """
    # Run all checks in parallel
    db_check, claude_check, aman_check = await asyncio.gather(
        check_database(),
        check_anthropic(),
        check_aman_callback(),
        return_exceptions=True,
    )
    
    # Handle any exceptions from gather
    if isinstance(db_check, Exception):
        db_check = {"status": "error", "error": str(db_check)}
    if isinstance(claude_check, Exception):
        claude_check = {"status": "error", "error": str(claude_check)}
    if isinstance(aman_check, Exception):
        aman_check = {"status": "error", "error": str(aman_check)}
    
    checks = {
        "database": db_check,
        "anthropic": claude_check,
        "aman_callback": aman_check,
    }
    
    # Determine overall status
    statuses = [c.get("status") for c in checks.values() if c.get("status") != "not_configured"]
    
    if all(s == "ok" for s in statuses):
        overall = "ok"
    elif any(s == "error" for s in statuses):
        overall = "error"
    else:
        overall = "degraded"
    
    return {
        "status": overall,
        "checks": checks,
    }
