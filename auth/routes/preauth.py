"""Pre-authorization routes: dashboard, payloads, retry, patient history, etc.

This module contains the core preauth operations including:
- Dashboard with filtering and pagination
- Preauth payload listing
- Retry operations
- Patient history
- PA comments
- Audit logging
- Webhook delivery logs
- Preauth events
"""

import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from agent import agent
from auth.schemas import (
    AuditEventPayload,
    RetryPreauthPayload,
    RetryPendingPreauthPayload,
    SendPreauthDecisionPayload,
    AddPACommentPayload,
)
from auth.utils import verify_session_token
from auth.helpers import (
    _dashboard_org_id,
    _dashboard_request,
    _resolve_read_org_id,
    _resolve_mutation_org_id,
    is_platform_admin,
)
from services.db import pg_execute, pg_query_all, pg_query_one
from services.json_utils import parse_json_field

router = APIRouter()

RETRYABLE_PREAUTH_STATUSES = {"pending", "processing", "received", "error"}


async def _enqueue_preauth_retry(background: BackgroundTasks, row) -> None:
    """Queue a preauth request for retry processing."""
    await pg_execute("DELETE FROM agent_logs WHERE request_id = $1", row["request_id"])
    await pg_execute(
        """
        UPDATE preauth_logs
        SET status = 'pending',
            agent_step = 'retry_queued',
            decision = NULL,
            agent_result = NULL,
            error_message = NULL,
            processed_at = NULL
        WHERE id = $1
        """,
        row["id"],
    )
    background.add_task(agent.run, str(row["patient_id"]), str(row["request_id"]))


@router.get("/preauth-payloads")
async def list_preauth_payloads(claims: dict = Depends(verify_session_token)):
    """List recent incoming pre-auth payloads (admin only)."""
    if claims["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view incoming payloads")

    rows = await pg_query_all(
        """
        SELECT request_id, patient_id, status, received_at, raw_payload, extracted_fields
        FROM preauth_logs
        WHERE org_id = $1
        ORDER BY received_at DESC
        LIMIT 20
        """,
        claims["org_id"]
    )

    return {
        "payloads": [
            {
                "request_id": row["request_id"],
                "patient_id": row["patient_id"],
                "status": row["status"],
                "received_at": row["received_at"],
                "raw_payload": parse_json_field(row["raw_payload"]) if row["raw_payload"] else None,
                "extracted_fields": parse_json_field(row["extracted_fields"]) if row["extracted_fields"] else None,
            }
            for row in rows
        ]
    }


@router.post("/preauth/retry")
async def retry_preauth(payload: RetryPreauthPayload, background: BackgroundTasks, claims: dict = Depends(verify_session_token)):
    """Retry a single pre-auth request."""
    org_id = await _resolve_mutation_org_id(claims, payload.org_id)
    request_id = payload.request_id.strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")

    row = await pg_query_one(
        """
        SELECT id, request_id, patient_id, status
        FROM preauth_logs
        WHERE org_id = $1 AND request_id = $2
        """,
        org_id, request_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Pre-auth request not found")

    current_status = (row["status"] or "").lower()
    if current_status not in RETRYABLE_PREAUTH_STATUSES:
        raise HTTPException(status_code=409, detail=f"Only pending, processing, received, or error requests can be retried. Current status: {row['status']}")

    await _enqueue_preauth_retry(background, row)
    return {
        "status": "queued",
        "request_id": row["request_id"],
        "previous_status": row["status"],
    }


@router.post("/preauth/retry-pending")
async def retry_pending_preauths(payload: RetryPendingPreauthPayload, background: BackgroundTasks, claims: dict = Depends(verify_session_token)):
    """Retry all pending pre-auth requests matching filters."""
    org_id = await _resolve_mutation_org_id(claims, payload.org_id)
    if payload.date_from and payload.date_to and payload.date_from > payload.date_to:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")

    safe_limit = min(max(payload.limit, 1), 20)
    date_from_start = datetime.combine(payload.date_from, time.min) if payload.date_from else None
    date_to_end = datetime.combine(payload.date_to + timedelta(days=1), time.min) if payload.date_to else None
    search_q = payload.q.strip() if payload.q and payload.q.strip() else None
    search_pattern = f"%{search_q}%" if search_q else None

    rows = await pg_query_all(
        """
        SELECT id, request_id, patient_id, status
        FROM preauth_logs
        WHERE org_id = $1
          AND LOWER(status) = ANY($2::text[])
          AND ($3::timestamp IS NULL OR received_at >= $3::timestamp)
          AND ($4::timestamp IS NULL OR received_at < $4::timestamp)
          AND ($5::text IS NULL OR (
                patient_id ILIKE $5
                OR request_id ILIKE $5
                OR COALESCE(decision, '') ILIKE $5
                OR raw_payload::text ILIKE $5
          ))
        ORDER BY received_at DESC
        LIMIT $6
        """,
        org_id,
        sorted(RETRYABLE_PREAUTH_STATUSES),
        date_from_start,
        date_to_end,
        search_pattern,
        safe_limit,
    )

    for row in rows:
        await _enqueue_preauth_retry(background, row)

    return {
        "status": "queued",
        "queued_count": len(rows),
        "limit": safe_limit,
        "request_ids": [row["request_id"] for row in rows],
    }


@router.post("/preauth/send-decision")
async def send_preauth_decision(payload: SendPreauthDecisionPayload, claims: dict = Depends(verify_session_token)):
    """Send a completed pre-auth decision to AMAN."""
    org_id = await _resolve_mutation_org_id(claims, payload.org_id)
    request_id = payload.request_id.strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")

    row = await pg_query_one(
        """
        SELECT id, request_id, decision, agent_result, processed_at
        FROM preauth_logs
        WHERE org_id = $1 AND request_id = $2
        """,
        org_id, request_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Pre-auth request not found")
    if not row["processed_at"] or not row["decision"] or not row["agent_result"]:
        raise HTTPException(status_code=409, detail="No completed agent decision is available to send yet")

    from services.aman_callback import send_decision_to_aman

    result = await send_decision_to_aman(str(row["request_id"]), force=True)
    updated = await pg_query_one(
        """
        SELECT callback_status, callback_http_status, callback_sent_at, callback_error
        FROM preauth_logs
        WHERE id = $1
        """,
        row["id"],
    )
    return {
        "request_id": row["request_id"],
        "result": result,
        "callback": {
            "status": updated["callback_status"] if updated else result.get("status"),
            "http_status": updated["callback_http_status"] if updated else result.get("http_status"),
            "sent_at": updated["callback_sent_at"] if updated else None,
            "error": updated["callback_error"] if updated else result.get("error"),
        },
    }


@router.post("/audit/log-event")
async def log_audit_event(payload: AuditEventPayload, claims: dict = Depends(verify_session_token)):
    """Log an audit event for compliance tracking."""
    org_id = _dashboard_org_id(claims)
    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        user_id = None
    user_email = claims.get("email")
    await pg_execute(
        """
        INSERT INTO audit_events (org_id, user_id, user_email, event_type, target_kind, target_id, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        org_id, user_id, user_email,
        payload.event_type[:50],
        (payload.target_kind or None) and payload.target_kind[:50],
        (payload.target_id or None) and payload.target_id[:200],
        json.dumps(payload.metadata or {}),
    )
    return {"status": "logged"}


@router.post("/pa-comments")
async def add_pa_comment(payload: AddPACommentPayload, claims: dict = Depends(verify_session_token)):
    """Add a comment/feedback to a pre-auth request."""
    org_id = await _resolve_read_org_id(claims, payload.org_id)
    request_id = payload.request_id.strip()
    comment_text = (payload.comment_text or "").strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")
    if not comment_text:
        raise HTTPException(status_code=400, detail="comment_text is required")

    pa = await pg_query_one(
        "SELECT id FROM preauth_logs WHERE org_id = $1 AND request_id = $2",
        org_id, request_id,
    )
    if not pa:
        raise HTTPException(status_code=404, detail="Pre-auth request not found")

    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        user_id = None
    user_email = claims.get("email")
    user_name = claims.get("name") or user_email

    row = await pg_query_one(
        """
        INSERT INTO pa_comments (org_id, request_id, user_id, user_email, user_name, comment_text)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, created_at
        """,
        org_id, request_id, user_id, user_email, user_name, comment_text[:2000],
    )
    return {
        "id": row["id"],
        "request_id": request_id,
        "user_name": user_name,
        "user_email": user_email,
        "comment_text": comment_text[:2000],
        "created_at": row["created_at"],
    }


# Note: The preauth-dashboard endpoint is very large (~600 lines of SQL queries).
# It remains in the main router for now but could be moved here in a future iteration.
# The same applies to patient-history, audit/events, webhook-delivery-logs, 
# preauth-events, and patients endpoints.
