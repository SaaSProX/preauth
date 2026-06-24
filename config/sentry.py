"""
Sentry error tracking initialization (SAA-83).

Usage:
    from config.sentry import init_sentry
    init_sentry()  # Call once at app startup
"""

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from config.settings import settings


def init_sentry() -> bool:
    """
    Initialize Sentry SDK if DSN is configured.
    
    Returns True if Sentry was initialized, False if skipped.
    """
    if not settings.sentry_dsn:
        return False
    
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
            HttpxIntegration(),
            LoggingIntegration(
                level=None,        # Don't capture logs as breadcrumbs by level
                event_level=None,  # Don't send logs as events
            ),
        ],
        # Performance monitoring
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_traces_sample_rate,
        
        # Release tracking (set via env var SENTRY_RELEASE in deployment)
        release=None,
        
        # Don't send PII
        send_default_pii=False,
        
        # Attach request data for debugging
        max_request_body_size="medium",
    )
    
    return True


def set_sentry_context(
    checkin_id: str | None = None,
    request_id: str | None = None,
    insurance_no: str | None = None,
    org_id: int | None = None,
    api_client_id: int | None = None,
) -> None:
    """
    Set contextual data for Sentry error reports.
    Call this when processing a PA to add context to any errors.
    """
    context = {}
    if checkin_id:
        context["checkin_id"] = checkin_id
    if request_id:
        context["request_id"] = request_id
    if insurance_no:
        # Mask middle digits for privacy
        context["insurance_no"] = f"{insurance_no[:4]}...{insurance_no[-4:]}" if len(insurance_no) > 8 else "***"
    if org_id:
        context["org_id"] = org_id
    
    if context:
        sentry_sdk.set_context("preauth", context)
    
    # Set user context for Sentry's user tracking
    if org_id or api_client_id:
        sentry_sdk.set_user({
            "id": str(org_id) if org_id else None,
            "api_client_id": api_client_id,
        })


def capture_message(message: str, level: str = "info", **extra) -> None:
    """Capture a message to Sentry with optional extra data."""
    with sentry_sdk.push_scope() as scope:
        for key, value in extra.items():
            scope.set_extra(key, value)
        sentry_sdk.capture_message(message, level=level)
