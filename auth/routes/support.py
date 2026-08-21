"""Support inbox message listing (SAA-81).

Extracted from auth/router.py.
"""

from fastapi import APIRouter, Depends

from auth.utils import verify_session_token
from auth.helpers import _resolve_read_org_id
from services.db import pg_query_all
from services.json_utils import parse_json_field

router = APIRouter()


def _support_message_row(row):
    received_at = row["received_at"] or row["internal_date"] or row["created_at"]
    return {
        "id": row["id"],
        "provider": row["provider"],
        "channel": "gmail" if row["provider"] == "google" else row["provider"],
        "mailbox_email": row["mailbox_email"],
        "message_id": row["gmail_message_id"],
        "thread_id": row["gmail_thread_id"],
        "from_email": row["from_email"],
        "to_email": row["to_email"],
        "subject": row["subject"] or "(No subject)",
        "snippet": row["snippet"],
        "body_text": row["body_text"],
        "received_at": received_at,
        "label_ids": parse_json_field(row["label_ids"]) or [],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "agent_activity": [
            {
                "step": "Intake",
                "status": "done",
                "title": "Message captured",
                "detail": "Inbound message received from Gmail and stored in the support inbox.",
                "at": row["created_at"],
            },
            {
                "step": "Normalize",
                "status": "done",
                "title": "Headers and body extracted",
                "detail": "Sender, recipient, subject, snippet, labels, and readable body text were extracted.",
                "at": row["updated_at"],
            },
            {
                "step": "Agent review",
                "status": "pending",
                "title": "Waiting for support agent workflow",
                "detail": "Next step is classification, routing, answer drafting, and escalation rules.",
                "at": None,
            },
        ],
    }

@router.get("/support/messages")
async def list_support_messages(
    org_id: int | None = None,
    provider: str = "all",
    status: str = "all",
    page: int = 1,
    page_size: int = 25,
    claims: dict = Depends(verify_session_token),
):
    resolved_org_id = await _resolve_read_org_id(claims, org_id)
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size
    provider_filter = (provider or "all").strip().lower()
    status_filter = (status or "all").strip().lower()

    rows = await pg_query_all(
        """
        SELECT
            id,
            provider,
            mailbox_email,
            gmail_message_id,
            gmail_thread_id,
            from_email,
            to_email,
            subject,
            snippet,
            body_text,
            internal_date,
            received_at,
            label_ids,
            status,
            created_at,
            updated_at,
            COUNT(*) OVER()::int AS total_count
        FROM support_messages
        WHERE org_id = $1
          AND ($2 = 'all' OR provider = $2)
          AND ($3 = 'all' OR status = $3)
        ORDER BY COALESCE(received_at, internal_date, created_at) DESC, id DESC
        LIMIT $4 OFFSET $5
        """,
        resolved_org_id,
        provider_filter,
        status_filter,
        page_size,
        offset,
    )
    total = rows[0]["total_count"] if rows else 0
    return {
        "messages": [_support_message_row(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, (total + page_size - 1) // page_size) if total else 0,
        },
    }
