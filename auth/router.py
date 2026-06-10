import json
import secrets
import asyncio
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from agent import agent
from config.settings import settings
from services.db import pg_execute, pg_query_all, pg_query_one
from services.json_utils import parse_json_field
from services.gmail import (
    GmailIntegrationError,
    create_notification_log,
    decode_pubsub_data,
    gmail_connections_for_email,
    start_gmail_watch,
    sync_gmail_history,
)
from services.invites import build_invite_link
from services.notifier import EmailDeliveryError, send_invite_email
from auth.utils import (
    hash_password, verify_password,
    generate_api_key, generate_session_token,
    verify_session_token
)

router = APIRouter(prefix="/auth")

GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
GMAIL_READONLY_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
]


# ─────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────

class RegisterPayload(BaseModel):
    invite_token: str
    email: str
    name: str
    password: str

class LoginPayload(BaseModel):
    email: str
    password: str

class TeamInvitePayload(BaseModel):
    email: str

class CreateOrgPayload(BaseModel):
    org_name: str
    admin_email: str


def _first_item(fields):
    items = _items_from_payload(fields)
    if items and isinstance(items[0], dict):
        return items[0]
    return {}


def _items_from_payload(fields):
    if not isinstance(fields, dict):
        return []

    items = (
        fields.get("pa_items")
        or fields.get("items")
        or fields.get("requested_items")
        or fields.get("requestedItems")
        or fields.get("services")
        or fields.get("procedures")
        or fields.get("line_items")
        or []
    )
    items = parse_json_field(items)

    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _item_requested_cost(item):
    amount = (
        _number(item.get("requested_cost"))
        or _number(item.get("estimated_cost"))
        or _number(item.get("cost"))
        or _number(item.get("amount"))
    )
    if amount is not None:
        return amount

    unit_cost = _number(item.get("unit_cost"))
    quantity = _number(item.get("quantity")) or 1
    return unit_cost * quantity if unit_cost is not None else 0


def _total_requested_cost(fields):
    if not isinstance(fields, dict):
        return None

    total = _number(fields.get("total_requested_cost"))
    if total is not None:
        return total

    items = _items_from_payload(fields)
    if not items:
        return None

    return sum(_item_requested_cost(item) for item in items)


def _has_dashboard_data(fields):
    if not isinstance(fields, dict):
        return False

    scalar_fields = ("request_id", "patient_id", "plan", "facility", "submitted_by", "total_requested_cost")
    if any(fields.get(key) not in (None, "", []) for key in scalar_fields):
        return True

    return bool(_items_from_payload(fields))


def _nested_value(fields, *path):
    current = fields
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _provider_label(value):
    if isinstance(value, dict):
        return value.get("name") or value.get("role") or value.get("email") or json.dumps(value)
    return value


def _dashboard_org_id(claims):
    return claims["org_id"]


async def is_platform_admin(claims):
    """SaaSPro platform admin = admin of the platform org named 'SAASPRO'.

    There is no separate role tier for "super-admin"; the only thing this
    helper checks is "are you an admin of the org we use to run the
    platform". An admin of a client org (e.g. AMAN) is NOT a platform
    admin and cannot create/edit other orgs or drill into them.
    """
    if claims.get("role") != "admin":
        return False
    row = await pg_query_one(
        "SELECT 1 FROM organizations WHERE id = $1 AND LOWER(name) = 'saaspro' AND is_active = TRUE",
        claims["org_id"],
    )
    return bool(row)


# NOTE (merge): kalycoding added _preauth_dashboard_org_id() on origin/main to
# pick the "busiest" org as a fallback when the JWT's org_id has no data.
# Kept as a defined helper so existing callers (if any are added later) still
# resolve, BUT the dashboard endpoint deliberately does NOT use it — it would
# silently override the JWT's org and break per-tenant isolation. The dashboard
# uses claims["org_id"] + the explicit platform-admin ?org_id= drill-in instead.
async def _preauth_dashboard_org_id(claims):
    fallback = await pg_query_one(
        """
        SELECT org_id
        FROM preauth_events
        WHERE org_id IS NOT NULL
        GROUP BY org_id
        ORDER BY COUNT(*) DESC, MAX(created_at) DESC NULLS LAST
        LIMIT 1
        """
    )
    if fallback:
        return fallback["org_id"]

    fallback = await pg_query_one(
        """
        SELECT org_id
        FROM preauth_logs
        WHERE org_id IS NOT NULL
        GROUP BY org_id
        ORDER BY COUNT(*) DESC, MAX(received_at) DESC NULLS LAST
        LIMIT 1
        """
    )
    return fallback["org_id"] if fallback else claims["org_id"]


def _dashboard_request(row):
    raw_payload = parse_json_field(row["raw_payload"]) if row["raw_payload"] else None
    extracted_fields = parse_json_field(row["extracted_fields"]) if row["extracted_fields"] else None
    agent_result = parse_json_field(row["agent_result"]) if row["agent_result"] else None
    agent_logs = parse_json_field(row["agent_logs"]) if row["agent_logs"] else []

    source = extracted_fields if _has_dashboard_data(extracted_fields) else raw_payload
    source = source if isinstance(source, dict) else {}
    raw_source = raw_payload if isinstance(raw_payload, dict) else {}
    item_count = len(_items_from_payload(source))
    item = _first_item(source)
    item_description = (
        f"{item_count} requested items"
        if item_count > 1
        else item.get("description") or item.get("name") or item.get("item_name")
    )
    patient_id = row["patient_id"]
    if not patient_id or patient_id == "unknown":
        patient_id = _nested_value(raw_source, "enrollee", "insurance_no") or patient_id

    reason = None
    confidence = None
    amount_approved = None
    if isinstance(agent_result, dict):
        reason = (
            agent_result.get("reasoning")
            or agent_result.get("denial_reason")
            or agent_result.get("escalation_reason")
        )
        confidence = agent_result.get("confidence")
        amount_approved = agent_result.get("amount_approved")

    processing_seconds = None
    if "agent_runtime_seconds" in row.keys() and row["agent_runtime_seconds"] is not None:
        processing_seconds = max(0, int(round(row["agent_runtime_seconds"])))
    elif row["processed_at"] and row["received_at"]:
        processed_at = row["processed_at"]
        received_at = row["received_at"]
        if processed_at.tzinfo and not received_at.tzinfo:
            processed_at = processed_at.replace(tzinfo=None)
        processing_seconds = max(0, int((processed_at - received_at).total_seconds()))

    return {
        "request_id": row["request_id"],
        "display_request_id": _nested_value(raw_source, "encounter", "checkin_id") or row["request_id"],
        "patient_id": patient_id,
        "patient_name": " ".join(
            value.strip()
            for value in [
                _nested_value(raw_source, "enrollee", "first_name") or "",
                _nested_value(raw_source, "enrollee", "surname") or "",
            ]
            if value and value.strip()
        ),
        "status": row["status"],
        "decision": row["decision"],
        "agent_step": row["agent_step"],
        "received_at": row["received_at"],
        "processed_at": row["processed_at"],
        "processing_seconds": processing_seconds,
        "callback_status": (row["callback_status"] if "callback_status" in row.keys() else None),
        "callback_http_status": (row["callback_http_status"] if "callback_http_status" in row.keys() else None),
        "callback_sent_at": (row["callback_sent_at"] if "callback_sent_at" in row.keys() else None),
        "callback_error": (row["callback_error"] if "callback_error" in row.keys() else None),
        "error_message": row["error_message"],
        "plan": source.get("plan") or _nested_value(raw_source, "policy", "plan_name") or _nested_value(raw_source, "policy", "insurance_package"),
        "item_type": item.get("type") or item.get("category_id"),
        "item_description": item_description,
        "estimated_cost": _total_requested_cost(source),
        "line_item_count": item_count,
        # Event-history fields come from the LATERAL join in /preauth-dashboard.
        # Other callers (e.g. /patient-history) don't include them — fall back
        # to defaults instead of raising KeyError.
        "event_count": (row["event_count"] if "event_count" in row.keys() else 0),
        "latest_event_sequence": (row["latest_event_sequence"] if "latest_event_sequence" in row.keys() else None),
        "latest_event_id": (row["latest_event_id"] if "latest_event_id" in row.keys() else None),
        "latest_items_added_count": (row["latest_items_added_count"] if "latest_items_added_count" in row.keys() else 0),
        "latest_items_added_total": (row["latest_items_added_total"] if "latest_items_added_total" in row.keys() else 0),
        "duplicate_event_attempts": (row["duplicate_event_attempts"] if "duplicate_event_attempts" in row.keys() else 0),
        "total_intake_value": (row["total_intake_value"] if "total_intake_value" in row.keys() else 0),
        "facility": item.get("facility") or source.get("facility") or _nested_value(raw_source, "encounter", "facility_name"),
        "requesting_provider": _provider_label(
            item.get("requesting_provider")
            or item.get("provider")
            or source.get("submitted_by")
            or _nested_value(raw_source, "submission", "submitted_by")
        ),
        "reason": reason or row["error_message"],
        "confidence": confidence,
        "amount_approved": amount_approved,
        "raw_payload": raw_payload,
        "extracted_fields": extracted_fields,
        "agent_result": agent_result,
        "agent_logs": agent_logs or [],
        "patient_pa_count": (row["patient_pa_count"] if "patient_pa_count" in row.keys() else None),
    }


# ─────────────────────────────────────────────
# Register (via invite link)
# ─────────────────────────────────────────────

@router.post("/register")
async def register(payload: RegisterPayload):
    # Validate invite
    invite = await pg_query_one(
        "SELECT * FROM invites WHERE token = $1 AND used = FALSE",
        payload.invite_token
    )
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid or expired invite token")

    if invite["email"] != payload.email:
        raise HTTPException(status_code=400, detail="Email does not match invite")

    # Check not already registered
    existing = await pg_query_one("SELECT id FROM clients WHERE email = $1", payload.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create client
    password_hash = hash_password(payload.password)
    await pg_execute(
        """
        INSERT INTO clients (org_id, name, email, password_hash, role)
        VALUES ($1, $2, $3, $4, $5)
        """,
        invite["org_id"], payload.name, payload.email, password_hash, invite["role"]
    )

    # Mark invite used
    await pg_execute("UPDATE invites SET used = TRUE WHERE token = $1", payload.invite_token)

    org = await pg_query_one("SELECT name FROM organizations WHERE id = $1", invite["org_id"])

    return {
        "message": "Registration successful",
        "org_name": org["name"] if org else None
    }


# ─────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────

@router.post("/login")
async def login(payload: LoginPayload):
    client = await pg_query_one(
        """
        SELECT clients.*, organizations.name AS org_name
        FROM clients
        LEFT JOIN organizations ON organizations.id = clients.org_id
        WHERE clients.email = $1
        """,
        payload.email
    )

    if not client or not verify_password(payload.password, client["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not client["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    token = generate_session_token(client["id"], client["email"], client["org_id"], client["role"])

    return {
        "token": token,
        "role": client["role"],
        "name": client["name"],
        "org_name": client["org_name"]
    }


# ─────────────────────────────────────────────
# API keys (user-owned, multiple per user)
# ─────────────────────────────────────────────

class GenerateKeyPayload(BaseModel):
    name: str | None = None


class GmailDisconnectPayload(BaseModel):
    connection_id: int | None = None
    email: str | None = None


class GmailWatchStartPayload(BaseModel):
    connection_id: int | None = None
    org_id: int | None = None


def _mask_key(k: str) -> str:
    if not k or len(k) < 4:
        return "••••"
    return "••••" + k[-4:]


def _gmail_oauth_configured() -> bool:
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def _gmail_redirect_uri(request: Request) -> str:
    configured = (settings.google_oauth_redirect_uri or "").strip()
    if configured:
        return configured
    return str(request.url_for("gmail_oauth_callback"))


def _dashboard_redirect_url(status: str, detail: str | None = None) -> str:
    params = {"nav": "support", "gmail": status}
    if detail:
        params["detail"] = detail[:140]
    return f"{settings.dashboard_base_url.rstrip('/')}/?{urlencode(params)}"


def _build_gmail_state(claims: dict, org_id: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(claims["sub"]),
            "email": claims.get("email"),
            "org_id": org_id if org_id is not None else claims["org_id"],
            "role": claims.get("role"),
            "nonce": secrets.token_urlsafe(18),
            "iat": int(now.timestamp()),
            "exp": now + timedelta(minutes=15),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def _decode_gmail_state(state: str) -> dict:
    try:
        return jwt.decode(state, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Gmail connection expired. Please try again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid Gmail connection state")


async def _resolve_read_org_id(claims: dict, requested_org_id: int | None = None) -> int:
    if requested_org_id is None or requested_org_id == claims["org_id"]:
        return claims["org_id"]
    if not await is_platform_admin(claims):
        raise HTTPException(status_code=403, detail="Only SaaSPro platform admins can view another organization")
    return requested_org_id


def _gmail_connection_row(row):
    return {
        "id": row["id"],
        "email": row["email"],
        "provider": row["provider"],
        "status": row["status"],
        "scopes": parse_json_field(row["scopes"]) or [],
        "token_expiry": row["token_expiry"],
        "watch_history_id": row["watch_history_id"],
        "watch_expiration": row["watch_expiration"],
        "watch_status": row["watch_status"],
        "watch_started_at": row["watch_started_at"],
        "watch_last_notification_at": row["watch_last_notification_at"],
        "watch_error": row["watch_error"],
        "last_sync_at": row["last_sync_at"],
        "last_error": row["last_error"],
        "support_message_count": row.get("support_message_count", 0) if hasattr(row, "get") else row["support_message_count"],
        "last_message_received_at": row.get("last_message_received_at") if hasattr(row, "get") else row["last_message_received_at"],
        "connected_by_email": row["connected_by_email"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


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


@router.get("/api-key")
async def list_user_api_keys(claims: dict = Depends(verify_session_token)):
    last_used_column = await pg_query_one(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'api_clients'
              AND column_name = 'last_used_at'
        ) AS exists
        """
    )
    last_used_expr = (
        "last_used_at"
        if last_used_column and last_used_column["exists"]
        else "NULL::timestamptz AS last_used_at"
    )
    rows = await pg_query_all(
        f"""
        SELECT id, client_name, api_key, created_at, {last_used_expr}
        FROM api_clients
        WHERE org_id = $1 AND user_id = $2 AND is_active = TRUE
        ORDER BY created_at DESC
        """,
        claims["org_id"], int(claims["sub"])
    )
    return {
        "keys": [
            {
                "id": r["id"],
                "name": r["client_name"],
                "masked_api_key": _mask_key(r["api_key"]),
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
            }
            for r in rows
        ]
    }


@router.post("/api-key/generate")
async def generate_user_api_key(payload: GenerateKeyPayload, claims: dict = Depends(verify_session_token)):
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can generate API keys")
    org_id = claims["org_id"]
    user_id = int(claims["sub"])
    client = await pg_query_one(
        """
        SELECT clients.name, clients.email
        FROM clients
        JOIN organizations ON organizations.id = clients.org_id
        WHERE clients.id = $1 AND clients.org_id = $2
          AND clients.is_active = TRUE AND organizations.is_active = TRUE
        """,
        user_id, org_id
    )
    if not client:
        raise HTTPException(status_code=404, detail="User or organization not found")

    name = ((payload.name or "").strip()) or f"{client['name']} ({client['email']})"

    api_key = generate_api_key()
    row = await pg_query_one(
        """
        INSERT INTO api_clients (org_id, user_id, client_name, api_key)
        VALUES ($1, $2, $3, $4)
        RETURNING id, client_name, created_at
        """,
        org_id, user_id, name, api_key
    )

    return {
        "message": "API key generated",
        "id": row["id"],
        "name": row["client_name"],
        "api_key": api_key,
        "masked_api_key": _mask_key(api_key),
        "created_at": row["created_at"],
        "note": "Save your API key — it won't be shown again",
    }


@router.delete("/api-key/{key_id}")
async def revoke_user_api_key(key_id: int, claims: dict = Depends(verify_session_token)):
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can revoke API keys")
    # Revoke any key in the caller's org (not just keys the caller created
    # themselves). Org admins manage org-level credentials. The previous
    # `AND user_id = $3` clause made revocation impossible for keys issued
    # by a different admin.
    await pg_execute(
        "DELETE FROM api_clients WHERE id = $1 AND org_id = $2",
        key_id, claims["org_id"]
    )
    return {"message": "API key revoked"}


# ─────────────────────────────────────────────
# Gmail / Google Workspace support inbox integration
# ─────────────────────────────────────────────

@router.get("/integrations/gmail")
async def gmail_connection_status(org_id: int | None = None, claims: dict = Depends(verify_session_token)):
    resolved_org_id = await _resolve_read_org_id(claims, org_id)
    rows = await pg_query_all(
        """
        SELECT
            gc.id,
            gc.provider,
            gc.email,
            gc.scopes,
            gc.token_expiry,
            gc.status,
            gc.watch_history_id,
            gc.watch_expiration,
            gc.watch_status,
            gc.watch_started_at,
            gc.watch_last_notification_at,
            gc.watch_error,
            gc.last_sync_at,
            gc.last_error,
            gc.created_at,
            gc.updated_at,
            clients.email AS connected_by_email,
            COALESCE(msgs.support_message_count, 0)::int AS support_message_count,
            msgs.last_message_received_at
        FROM gmail_connections gc
        LEFT JOIN clients ON clients.id = gc.connected_by
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*)::int AS support_message_count,
                MAX(received_at) AS last_message_received_at
            FROM support_messages sm
            WHERE sm.gmail_connection_id = gc.id
        ) msgs ON TRUE
        WHERE gc.org_id = $1
        ORDER BY gc.updated_at DESC, gc.created_at DESC
        """,
        resolved_org_id,
    )
    connections = [_gmail_connection_row(row) for row in rows]
    return {
        "configured": _gmail_oauth_configured(),
        "connected": any(row["status"] == "connected" for row in connections),
        "connections": connections,
        "scopes_required": GMAIL_READONLY_SCOPES,
    }


@router.get("/integrations/gmail/connect")
async def gmail_connect(request: Request, org_id: int | None = None, claims: dict = Depends(verify_session_token)):
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can connect Gmail")
    if not _gmail_oauth_configured():
        raise HTTPException(status_code=503, detail="Google OAuth is not configured yet")

    resolved_org_id = await _resolve_read_org_id(claims, org_id)
    redirect_uri = _gmail_redirect_uri(request)
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_READONLY_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": _build_gmail_state(claims, resolved_org_id),
    }
    return {
        "auth_url": f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}",
        "redirect_uri": redirect_uri,
        "scopes": GMAIL_READONLY_SCOPES,
    }


@router.get("/integrations/gmail/callback", name="gmail_oauth_callback")
async def gmail_oauth_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(_dashboard_redirect_url("error", error))
    if not code or not state:
        return RedirectResponse(_dashboard_redirect_url("error", "Missing Google OAuth callback code or state"))
    if not _gmail_oauth_configured():
        return RedirectResponse(_dashboard_redirect_url("error", "Google OAuth is not configured yet"))

    try:
        state_claims = _decode_gmail_state(state)
    except HTTPException as exc:
        return RedirectResponse(_dashboard_redirect_url("error", str(exc.detail)))

    redirect_uri = _gmail_redirect_uri(request)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_response = await client.post(
                GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_oauth_client_id,
                    "client_secret": settings.google_oauth_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_response.raise_for_status()
            token_data = token_response.json()

            access_token = token_data.get("access_token")
            if not access_token:
                return RedirectResponse(_dashboard_redirect_url("error", "Google did not return an access token"))

            profile_response = await client.get(
                GOOGLE_GMAIL_PROFILE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:140] if exc.response is not None else str(exc)
        return RedirectResponse(_dashboard_redirect_url("error", detail))
    except httpx.HTTPError as exc:
        return RedirectResponse(_dashboard_redirect_url("error", str(exc)))

    mailbox = (profile.get("emailAddress") or state_claims.get("email") or "").strip().lower()
    if not mailbox:
        return RedirectResponse(_dashboard_redirect_url("error", "Could not identify connected Gmail mailbox"))

    expires_in = token_data.get("expires_in")
    token_expiry = None
    if expires_in is not None:
        try:
            token_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            token_expiry = None

    scopes = (token_data.get("scope") or " ".join(GMAIL_READONLY_SCOPES)).split()
    refresh_token = token_data.get("refresh_token")
    connected_by = int(state_claims["sub"])
    org_id = int(state_claims["org_id"])

    await pg_execute(
        """
        INSERT INTO gmail_connections (
            org_id,
            connected_by,
            provider,
            email,
            scopes,
            access_token,
            refresh_token,
            token_expiry,
            status,
            last_error
        )
        VALUES ($1, $2, 'google', $3, $4::jsonb, $5, $6, $7, 'connected', NULL)
        ON CONFLICT (org_id, provider, email)
        DO UPDATE SET
            connected_by = EXCLUDED.connected_by,
            scopes = EXCLUDED.scopes,
            access_token = EXCLUDED.access_token,
            refresh_token = COALESCE(EXCLUDED.refresh_token, gmail_connections.refresh_token),
            token_expiry = EXCLUDED.token_expiry,
            status = 'connected',
            last_error = NULL,
            updated_at = NOW()
        """,
        org_id,
        connected_by,
        mailbox,
        json.dumps(scopes),
        access_token,
        refresh_token,
        token_expiry,
    )

    return RedirectResponse(_dashboard_redirect_url("connected", mailbox))


@router.post("/integrations/gmail/watch/start")
async def gmail_start_watch(payload: GmailWatchStartPayload, claims: dict = Depends(verify_session_token)):
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can start Gmail listener")

    resolved_org_id = await _resolve_read_org_id(claims, payload.org_id)
    connection_id = payload.connection_id
    if connection_id is None:
        row = await pg_query_one(
            """
            SELECT id
            FROM gmail_connections
            WHERE org_id = $1
              AND provider = 'google'
              AND status = 'connected'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            resolved_org_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="No connected Gmail mailbox found")
        connection_id = row["id"]

    try:
        watch = await start_gmail_watch(connection_id, resolved_org_id)
    except GmailIntegrationError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        await pg_execute(
            """
            UPDATE gmail_connections
            SET watch_status = 'error',
                watch_error = $3,
                last_error = $3,
                updated_at = NOW()
            WHERE id = $1
              AND org_id = $2
            """,
            connection_id,
            resolved_org_id,
            detail,
        )
        raise HTTPException(status_code=502, detail=detail)
    except httpx.HTTPError as exc:
        detail = str(exc)
        await pg_execute(
            """
            UPDATE gmail_connections
            SET watch_status = 'error',
                watch_error = $3,
                last_error = $3,
                updated_at = NOW()
            WHERE id = $1
              AND org_id = $2
            """,
            connection_id,
            resolved_org_id,
            detail,
        )
        raise HTTPException(status_code=502, detail=detail)

    return {"message": "Gmail realtime listener started", "watch": watch}


@router.post("/integrations/gmail/pubsub")
async def gmail_pubsub_push(request: Request, background: BackgroundTasks):
    expected_token = (settings.gmail_pubsub_verification_token or "").strip()
    if expected_token:
        provided_token = (
            request.query_params.get("token")
            or request.headers.get("X-Saaspro-Webhook-Token")
            or ""
        ).strip()
        if provided_token != expected_token:
            raise HTTPException(status_code=403, detail="Invalid Gmail Pub/Sub token")

    try:
        payload = await request.json()
    except Exception:
        return {"ok": True, "status": "ignored", "reason": "invalid_json"}

    message = payload.get("message") if isinstance(payload, dict) else {}
    try:
        decoded = decode_pubsub_data(message.get("data") if isinstance(message, dict) else None)
    except GmailIntegrationError as exc:
        await create_notification_log(None, payload if isinstance(payload, dict) else {}, {"error": str(exc)}, "ignored")
        return {"ok": True, "status": "ignored", "reason": str(exc)}

    email = (decoded.get("emailAddress") or "").strip().lower()
    history_id = str(decoded.get("historyId") or "").strip()
    if not email or not history_id:
        await create_notification_log(None, payload, decoded, "ignored_missing_fields")
        return {"ok": True, "status": "ignored", "reason": "missing_email_or_history_id"}

    connections = await gmail_connections_for_email(email)
    if not connections:
        await create_notification_log(None, payload, decoded, "no_connection")
        return {"ok": True, "status": "no_connection"}

    for connection in connections:
        log_id = await create_notification_log(connection, payload, decoded)
        background.add_task(sync_gmail_history, connection["id"], history_id, log_id)

    return {"ok": True, "status": "queued", "connections": len(connections)}


@router.post("/integrations/gmail/disconnect")
async def gmail_disconnect(payload: GmailDisconnectPayload, claims: dict = Depends(verify_session_token)):
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can disconnect Gmail")

    if payload.connection_id is None and not payload.email:
        raise HTTPException(status_code=400, detail="connection_id or email is required")

    email = (payload.email or "").strip().lower() or None
    row = await pg_query_one(
        """
        UPDATE gmail_connections
        SET status = 'disconnected',
            access_token = NULL,
            refresh_token = NULL,
            last_error = NULL,
            updated_at = NOW()
        WHERE org_id = $1
          AND ($2::int IS NULL OR id = $2::int)
          AND ($3::text IS NULL OR LOWER(email) = $3::text)
        RETURNING id, email, status
        """,
        claims["org_id"],
        payload.connection_id,
        email,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Gmail connection not found")

    return {"message": f"Disconnected {row['email']}", "connection": dict(row)}


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


# ─────────────────────────────────────────────
# Incoming pre-auth payloads (admin only)
# ─────────────────────────────────────────────

@router.get("/preauth-payloads")
async def list_preauth_payloads(claims: dict = Depends(verify_session_token)):
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


@router.get("/preauth-dashboard")
async def preauth_dashboard(
    date_from: date | None = None,
    date_to: date | None = None,
    org_id: int | None = None,
    page: int = 1,
    page_size: int = 25,
    plan: str | None = None,
    q: str | None = None,
    claims: dict = Depends(verify_session_token)
):
    # Platform admins (SaaSPro org admins) can pass ?org_id= to view a
    # client org's activity. Anyone else is strictly scoped to their own
    # org_id from the JWT.
    if org_id is not None:
        if not await is_platform_admin(claims):
            raise HTTPException(status_code=403, detail="Only platform admins (SaaSPro org) can view another org's data")
    else:
        org_id = _dashboard_org_id(claims)

    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")

    date_from_start = datetime.combine(date_from, time.min) if date_from else None
    date_to_end = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None
    plan_filter = plan.strip() if plan and plan.strip() and plan.strip().lower() != 'all' else None
    # Trim + wildcard the search term once so every clause uses the same value.
    # Searches against patient_id, request_id, decision, and the raw payload
    # JSON-cast-to-text (catches patient names, facilities, plans, etc.).
    search_q = q.strip() if q and q.strip() else None
    search_pattern = f"%{search_q}%" if search_q else None
    # Africa/Lagos windowing for the preauth_events summaries added on
    # origin/main — keeps "today" aligned with operator-local midnight.
    lagos_tz = ZoneInfo("Africa/Lagos")
    event_date_from_start = datetime.combine(date_from, time.min, tzinfo=lagos_tz) if date_from else None
    event_date_to_end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=lagos_tz) if date_to else None
    today_start = datetime.now(lagos_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    summary = await pg_query_one(
        """
        SELECT
            COUNT(*)::int AS total,
            COUNT(*) FILTER (WHERE LOWER(p.status) = 'pending')::int AS pending,
            COUNT(*) FILTER (WHERE LOWER(p.status) = 'processing')::int AS processing,
            COUNT(*) FILTER (WHERE LOWER(p.status) IN ('approve', 'approved'))::int AS approved,
            COUNT(*) FILTER (WHERE LOWER(p.status) IN ('deny', 'denied', 'reject', 'rejected'))::int AS denied,
            COUNT(*) FILTER (WHERE LOWER(p.status) IN ('escalate', 'escalated'))::int AS escalated,
            COUNT(*) FILTER (WHERE LOWER(p.status) = 'error')::int AS errors,
            COUNT(*) FILTER (WHERE p.received_at >= NOW() - INTERVAL '24 hours')::int AS received_24h,
            COALESCE(
                SUM(
                    CASE
                        WHEN (p.extracted_fields->>'total_requested_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN (p.extracted_fields->>'total_requested_cost')::numeric
                        ELSE 0
                    END
                ),
                0
            )::float AS current_snapshot_value,
            COALESCE(
                SUM(
                    CASE
                        WHEN jsonb_typeof(p.extracted_fields->'items') = 'array'
                        THEN jsonb_array_length(p.extracted_fields->'items')
                        ELSE 0
                    END
                ),
                0
            )::int AS current_snapshot_line_items,
            COALESCE(
                SUM(
                    CASE
                        WHEN (p.agent_result->>'amount_approved') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN (p.agent_result->>'amount_approved')::numeric
                        ELSE 0
                    END
                ),
                0
            )::float AS total_amount_approved,
            COALESCE(SUM(item_stats.item_total), 0)::int AS item_total,
            COALESCE(SUM(item_stats.item_approved), 0)::int AS item_approved,
            COALESCE(SUM(item_stats.item_denied), 0)::int AS item_denied,
            COALESCE(SUM(item_stats.item_escalated), 0)::int AS item_escalated,
            COALESCE(SUM(item_stats.item_requested_value), 0)::float AS item_requested_value,
            COALESCE(SUM(item_stats.item_approved_value), 0)::float AS item_approved_value,
            COALESCE(SUM(item_stats.item_denied_value), 0)::float AS item_denied_value,
            COALESCE(SUM(item_stats.item_escalated_value), 0)::float AS item_escalated_value,
            COUNT(*) FILTER (
                WHERE (
                    item_stats.item_total > 0
                    AND item_stats.item_approved = item_stats.item_total
                )
                OR (
                    item_stats.item_total = 0
                    AND LOWER(COALESCE(p.decision, p.status, '')) IN ('approve', 'approved')
                )
            )::int AS pa_full_approved,
            COUNT(*) FILTER (
                WHERE item_stats.item_total > 0
                  AND item_stats.item_approved > 0
                  AND item_stats.item_approved < item_stats.item_total
            )::int AS pa_partial_approved,
            COUNT(*) FILTER (
                WHERE (
                    item_stats.item_total > 0
                    AND item_stats.item_approved = 0
                    AND item_stats.item_denied = item_stats.item_total
                )
                OR (
                    item_stats.item_total = 0
                    AND LOWER(COALESCE(p.decision, p.status, '')) IN ('deny', 'denied', 'reject', 'rejected')
                )
            )::int AS pa_rejected,
            COUNT(*) FILTER (
                WHERE (
                    item_stats.item_total > 0
                    AND item_stats.item_approved = 0
                    AND item_stats.item_denied < item_stats.item_total
                    AND item_stats.item_escalated > 0
                )
                OR (
                    item_stats.item_total = 0
                    AND LOWER(COALESCE(p.decision, p.status, '')) IN ('escalate', 'escalated')
                )
            )::int AS pa_review,
            COUNT(*) FILTER (WHERE p.callback_status = 'delivered')::int AS callback_delivered,
            COUNT(*) FILTER (
                WHERE p.callback_status IN ('auth_failed', 'scope_missing', 'stale_revision', 'network_error')
                   OR p.callback_status LIKE 'http_%'
            )::int AS callback_failed,
            COUNT(*) FILTER (WHERE p.callback_status = 'stale_revision')::int AS callback_stale_revision,
            COUNT(*) FILTER (WHERE p.callback_status = 'skipped_disabled')::int AS callback_skipped_disabled,
            COUNT(*) FILTER (WHERE p.callback_status = 'skipped_no_config')::int AS callback_skipped_no_config,
            COUNT(*) FILTER (WHERE p.callback_status = 'skipped_no_line_decisions')::int AS callback_skipped_no_line_decisions,
            COUNT(*) FILTER (
                WHERE p.decision IS NOT NULL
                  AND p.processed_at IS NOT NULL
                  AND p.callback_status IS NULL
            )::int AS callback_not_attempted,
            MAX(p.callback_sent_at) AS callback_last_sent_at,
            AVG(
                CASE
                    WHEN ar.first_log_at IS NOT NULL
                    THEN EXTRACT(EPOCH FROM (COALESCE(p.processed_at, ar.last_log_at) - ar.first_log_at))
                    WHEN p.processed_at IS NOT NULL
                    THEN EXTRACT(EPOCH FROM (p.processed_at - p.received_at))
                    ELSE NULL
                END
            )::float AS avg_processing_seconds
        FROM preauth_logs p
        LEFT JOIN LATERAL (
            WITH run_start AS (
                SELECT MAX(logged_at) AS first_log_at
                FROM agent_logs
                WHERE request_id = p.request_id
                  AND agent_num = 1
                  AND (p.processed_at IS NULL OR logged_at <= p.processed_at + INTERVAL '5 seconds')
            )
            SELECT run_start.first_log_at, MAX(al.logged_at) AS last_log_at
            FROM run_start
            LEFT JOIN agent_logs al
              ON al.request_id = p.request_id
             AND al.logged_at >= run_start.first_log_at
             AND (p.processed_at IS NULL OR al.logged_at <= p.processed_at + INTERVAL '5 seconds')
            GROUP BY run_start.first_log_at
        ) ar ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*)::int AS item_total,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(item.value->>'decision', '')) IN ('approve', 'approved')
                )::int AS item_approved,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(item.value->>'decision', '')) IN ('deny', 'denied', 'reject', 'rejected')
                )::int AS item_denied,
                COUNT(*) FILTER (
                    WHERE LOWER(COALESCE(item.value->>'decision', '')) IN ('escalate', 'escalated')
                )::int AS item_escalated,
                COALESCE(
                    SUM(
                        CASE
                            WHEN (item.value->>'requested_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                            THEN (item.value->>'requested_cost')::numeric
                            ELSE 0
                        END
                    ),
                    0
                )::float AS item_requested_value,
                COALESCE(
                    SUM(
                        CASE
                            WHEN LOWER(COALESCE(item.value->>'decision', '')) IN ('approve', 'approved')
                                AND (item.value->>'recommended_approved_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                            THEN (item.value->>'recommended_approved_cost')::numeric
                            ELSE 0
                        END
                    ),
                    0
                )::float AS item_approved_value,
                COALESCE(
                    SUM(
                        CASE
                            WHEN LOWER(COALESCE(item.value->>'decision', '')) IN ('deny', 'denied', 'reject', 'rejected')
                                AND (item.value->>'requested_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                            THEN (item.value->>'requested_cost')::numeric
                            ELSE 0
                        END
                    ),
                    0
                )::float AS item_denied_value,
                COALESCE(
                    SUM(
                        CASE
                            WHEN LOWER(COALESCE(item.value->>'decision', '')) IN ('escalate', 'escalated')
                                AND (item.value->>'requested_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                            THEN (item.value->>'requested_cost')::numeric
                            ELSE 0
                        END
                    ),
                    0
                )::float AS item_escalated_value
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(p.agent_result->'item_decisions') = 'array'
                    THEN p.agent_result->'item_decisions'
                    ELSE '[]'::jsonb
                END
            ) AS item(value)
        ) item_stats ON TRUE
        WHERE p.org_id = $1
          AND ($2::timestamp IS NULL OR p.received_at >= $2::timestamp)
          AND ($3::timestamp IS NULL OR p.received_at < $3::timestamp)
          AND ($4::text IS NULL OR COALESCE(p.extracted_fields->>'plan', p.raw_payload->'policy'->>'plan_name', p.raw_payload->'policy'->>'insurance_package') ILIKE $4)
          AND ($5::text IS NULL OR (
                p.patient_id ILIKE $5
                OR p.request_id ILIKE $5
                OR COALESCE(p.decision, '') ILIKE $5
                OR p.raw_payload::text ILIKE $5
          ))
        """,
        org_id, date_from_start, date_to_end, plan_filter, search_pattern
    )

    event_summary = await pg_query_one(
        """
        SELECT
            COUNT(*)::int AS event_count,
            COUNT(DISTINCT checkin_id)::int AS unique_pa_count,
            COALESCE(SUM(COALESCE(items_added_total, total_requested_cost, 0)), 0)::float AS intake_value,
            COALESCE(SUM(COALESCE(items_added_count, item_count, 0)), 0)::int AS added_line_items
        FROM preauth_events
        WHERE org_id = $1
          AND event_type = 'pa.submitted'
          AND ($2::timestamptz IS NULL OR created_at >= $2::timestamptz)
          AND ($3::timestamptz IS NULL OR created_at < $3::timestamptz)
        """,
        org_id, event_date_from_start, event_date_to_end
    )

    all_time_event_summary = await pg_query_one(
        """
        SELECT
            COUNT(*)::int AS event_count,
            COUNT(DISTINCT checkin_id)::int AS unique_pa_count,
            COALESCE(SUM(COALESCE(items_added_total, total_requested_cost, 0)), 0)::float AS intake_value,
            COALESCE(SUM(COALESCE(items_added_count, item_count, 0)), 0)::int AS added_line_items
        FROM preauth_events
        WHERE org_id = $1
          AND event_type = 'pa.submitted'
        """,
        org_id
    )

    today_event_summary = await pg_query_one(
        """
        SELECT
            COUNT(*)::int AS event_count,
            COUNT(DISTINCT checkin_id)::int AS unique_pa_count,
            COALESCE(SUM(COALESCE(items_added_total, total_requested_cost, 0)), 0)::float AS intake_value,
            COALESCE(SUM(COALESCE(items_added_count, item_count, 0)), 0)::int AS added_line_items
        FROM preauth_events
        WHERE org_id = $1
          AND event_type = 'pa.submitted'
          AND created_at >= $2::timestamptz
          AND created_at < $3::timestamptz
        """,
        org_id, today_start, today_end
    )

    duplicate_summary = await pg_query_one(
        """
        SELECT COUNT(*)::int AS duplicate_event_attempts
        FROM webhook_delivery_logs
        WHERE org_id = $1
          AND db_insert_status = 'duplicate_event_seen'
          AND ($2::timestamptz IS NULL OR created_at >= $2::timestamptz)
          AND ($3::timestamptz IS NULL OR created_at < $3::timestamptz)
        """,
        org_id, event_date_from_start, event_date_to_end
    )

    all_time_duplicate_summary = await pg_query_one(
        """
        SELECT COUNT(*)::int AS duplicate_event_attempts
        FROM webhook_delivery_logs
        WHERE org_id = $1
          AND db_insert_status = 'duplicate_event_seen'
        """,
        org_id
    )

    today_duplicate_summary = await pg_query_one(
        """
        SELECT COUNT(*)::int AS duplicate_event_attempts
        FROM webhook_delivery_logs
        WHERE org_id = $1
          AND db_insert_status = 'duplicate_event_seen'
          AND created_at >= $2::timestamptz
          AND created_at < $3::timestamptz
        """,
        org_id, today_start, today_end
    )

    rows = await pg_query_all(
        """
        SELECT
            p.request_id,
            p.patient_id,
            p.status,
            p.received_at,
            p.raw_payload,
            p.extracted_fields,
            p.agent_step,
            p.decision,
            p.agent_result,
            p.error_message,
            p.processed_at,
            p.callback_status,
            p.callback_http_status,
            p.callback_sent_at,
            p.callback_error,
            COALESCE(ev.event_count, 0)::int AS event_count,
            ev.latest_event_sequence,
            ev.latest_event_id,
            COALESCE(ev.latest_items_added_count, 0)::int AS latest_items_added_count,
            COALESCE(ev.latest_items_added_total, 0)::float AS latest_items_added_total,
            COALESCE(ev.duplicate_event_attempts, 0)::int AS duplicate_event_attempts,
            COALESCE(ev.total_intake_value, 0)::float AS total_intake_value,
            CASE
                WHEN ar.first_log_at IS NOT NULL
                THEN EXTRACT(EPOCH FROM (COALESCE(p.processed_at, ar.last_log_at) - ar.first_log_at))::float
                ELSE NULL
            END AS agent_runtime_seconds,
            COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'agent_num', al.agent_num,
                        'agent_name', al.agent_name,
                        'status', al.status,
                        'result', al.result,
                        'logged_at', al.logged_at
                    )
                    ORDER BY al.agent_num, al.logged_at
                ) FILTER (WHERE al.id IS NOT NULL),
                '[]'::jsonb
            ) AS agent_logs,
            CASE
                WHEN p.patient_id IS NULL OR p.patient_id = 'unknown' THEN 0
                ELSE (SELECT COUNT(*)::int FROM preauth_logs p2
                      WHERE p2.org_id = p.org_id AND p2.patient_id = p.patient_id)
            END AS patient_pa_count
        FROM preauth_logs p
        LEFT JOIN LATERAL (
            SELECT
                (COUNT(*) FILTER (WHERE e.event_type = 'pa.submitted'))::int AS event_count,
                (MAX(e.event_sequence) FILTER (WHERE e.event_type = 'pa.submitted'))::int AS latest_event_sequence,
                (ARRAY_AGG(e.event_id ORDER BY e.event_sequence DESC, e.created_at DESC) FILTER (WHERE e.event_type = 'pa.submitted'))[1] AS latest_event_id,
                (ARRAY_AGG(e.items_added_count ORDER BY e.event_sequence DESC, e.created_at DESC) FILTER (WHERE e.event_type = 'pa.submitted'))[1] AS latest_items_added_count,
                (ARRAY_AGG(e.items_added_total ORDER BY e.event_sequence DESC, e.created_at DESC) FILTER (WHERE e.event_type = 'pa.submitted'))[1] AS latest_items_added_total,
                COALESCE(SUM(e.duplicate_count), 0)::int AS duplicate_event_attempts,
                COALESCE(SUM(
                    CASE
                        WHEN e.event_type = 'pa.submitted'
                        THEN COALESCE(e.items_added_total, e.total_requested_cost, 0)
                        ELSE 0
                    END
                ), 0)::float AS total_intake_value
            FROM preauth_events e
            WHERE e.org_id = p.org_id
              AND e.checkin_id = COALESCE(
                  p.raw_payload->'encounter'->>'checkin_id',
                  p.extracted_fields->>'checkin_id',
                  p.request_id
              )
        ) ev ON TRUE
        LEFT JOIN LATERAL (
            WITH run_start AS (
                SELECT MAX(logged_at) AS first_log_at
                FROM agent_logs
                WHERE request_id = p.request_id
                  AND agent_num = 1
                  AND (p.processed_at IS NULL OR logged_at <= p.processed_at + INTERVAL '5 seconds')
            )
            SELECT run_start.first_log_at, MAX(al.logged_at) AS last_log_at
            FROM run_start
            LEFT JOIN agent_logs al
              ON al.request_id = p.request_id
             AND al.logged_at >= run_start.first_log_at
             AND (p.processed_at IS NULL OR al.logged_at <= p.processed_at + INTERVAL '5 seconds')
            GROUP BY run_start.first_log_at
        ) ar ON TRUE
        LEFT JOIN agent_logs al ON al.request_id = p.request_id
        WHERE p.org_id = $1
          AND ($2::timestamp IS NULL OR p.received_at >= $2::timestamp)
          AND ($3::timestamp IS NULL OR p.received_at < $3::timestamp)
          AND ($4::text IS NULL OR COALESCE(p.extracted_fields->>'plan', p.raw_payload->'policy'->>'plan_name', p.raw_payload->'policy'->>'insurance_package') ILIKE $4)
          AND ($5::text IS NULL OR (
                p.patient_id ILIKE $5
                OR p.request_id ILIKE $5
                OR COALESCE(p.decision, '') ILIKE $5
                OR p.raw_payload::text ILIKE $5
          ))
        GROUP BY
            p.id,
            p.request_id,
            p.patient_id,
            p.status,
            p.received_at,
            p.raw_payload,
            p.extracted_fields,
            p.agent_step,
            p.decision,
            p.agent_result,
            p.error_message,
            p.processed_at,
            p.callback_status,
            p.callback_http_status,
            p.callback_sent_at,
            p.callback_error,
            ev.event_count,
            ev.latest_event_sequence,
            ev.latest_event_id,
            ev.latest_items_added_count,
            ev.latest_items_added_total,
            ev.duplicate_event_attempts,
            ev.total_intake_value,
            ar.first_log_at,
            ar.last_log_at
        ORDER BY p.received_at DESC
        LIMIT $6 OFFSET $7
        """,
        org_id, date_from_start, date_to_end, plan_filter, search_pattern, page_size, offset
    )

    series_rows = await pg_query_all(
        """
        SELECT
            to_char(date(p.received_at), 'YYYY-MM-DD') AS day,
            COUNT(*)::int AS received,
            COALESCE(
                AVG(
                    CASE
                        WHEN ar.first_log_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (COALESCE(p.processed_at, ar.last_log_at) - ar.first_log_at))
                        WHEN p.processed_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (p.processed_at - p.received_at))
                        ELSE NULL
                    END
                ),
                0
            )::float AS avg_latency,
            COALESCE(
                SUM(
                    CASE
                        WHEN (p.agent_result->>'amount_approved') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN (p.agent_result->>'amount_approved')::numeric
                        ELSE 0
                    END
                ),
                0
            )::float AS approved_value
        FROM preauth_logs p
        LEFT JOIN LATERAL (
            WITH run_start AS (
                SELECT MAX(logged_at) AS first_log_at
                FROM agent_logs
                WHERE request_id = p.request_id
                  AND agent_num = 1
                  AND (p.processed_at IS NULL OR logged_at <= p.processed_at + INTERVAL '5 seconds')
            )
            SELECT run_start.first_log_at, MAX(al.logged_at) AS last_log_at
            FROM run_start
            LEFT JOIN agent_logs al
              ON al.request_id = p.request_id
             AND al.logged_at >= run_start.first_log_at
             AND (p.processed_at IS NULL OR al.logged_at <= p.processed_at + INTERVAL '5 seconds')
            GROUP BY run_start.first_log_at
        ) ar ON TRUE
        WHERE p.org_id = $1
          AND ($2::timestamp IS NULL OR p.received_at >= $2::timestamp)
          AND ($3::timestamp IS NULL OR p.received_at < $3::timestamp)
          AND ($4::text IS NULL OR COALESCE(p.extracted_fields->>'plan', p.raw_payload->'policy'->>'plan_name', p.raw_payload->'policy'->>'insurance_package') ILIKE $4)
          AND ($5::text IS NULL OR (
                p.patient_id ILIKE $5
                OR p.request_id ILIKE $5
                OR COALESCE(p.decision, '') ILIKE $5
                OR p.raw_payload::text ILIKE $5
          ))
        GROUP BY date(p.received_at)
        ORDER BY day
        """,
        org_id, date_from_start, date_to_end, plan_filter, search_pattern
    )

    plans_rows = await pg_query_all(
        """
        SELECT DISTINCT
            COALESCE(extracted_fields->>'plan', raw_payload->'policy'->>'plan_name', raw_payload->'policy'->>'insurance_package') AS plan
        FROM preauth_logs
        WHERE org_id = $1
          AND COALESCE(extracted_fields->>'plan', raw_payload->'policy'->>'plan_name', raw_payload->'policy'->>'insurance_package') IS NOT NULL
        ORDER BY plan
        """,
        org_id
    )
    # The full date span the org has ever received PAs over (ignores active filters).
    # Used by the dashboard header so the visible timeframe is always honest.
    window_row = await pg_query_one(
        "SELECT MIN(received_at) AS earliest, MAX(received_at) AS latest FROM preauth_logs WHERE org_id = $1",
        org_id
    )
    data_window = {
        "earliest": window_row["earliest"].isoformat() if window_row and window_row.get("earliest") else None,
        "latest": window_row["latest"].isoformat() if window_row and window_row.get("latest") else None,
    }

    # Dedupe case variants ("Gold" vs "GOLD") — prefer the non-all-caps form
    _plan_groups: dict[str, list[str]] = {}
    for r in plans_rows:
        p = r["plan"]
        if not p:
            continue
        _plan_groups.setdefault(p.lower(), []).append(p)
    available_plans: list[str] = []
    for variants in _plan_groups.values():
        non_upper = [v for v in variants if not v.isupper()]
        available_plans.append(non_upper[0] if non_upper else variants[0].title())
    available_plans.sort(key=lambda s: s.lower())

    total = summary["total"] if summary else 0
    return {
        "filters": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "plan": plan_filter,
        },
        "meta": {
            "plans": available_plans,
            "data_window": data_window,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": ((total + page_size - 1) // page_size) if page_size > 0 else 0,
        },
        "summary": {
            "total": summary["total"] if summary else 0,
            "pending": summary["pending"] if summary else 0,
            "processing": summary["processing"] if summary else 0,
            "approved": summary["approved"] if summary else 0,
            "denied": summary["denied"] if summary else 0,
            "escalated": summary["escalated"] if summary else 0,
            "errors": summary["errors"] if summary else 0,
            "received_24h": summary["received_24h"] if summary else 0,
            "current_snapshot_value": summary["current_snapshot_value"] if summary else 0,
            "current_snapshot_line_items": summary["current_snapshot_line_items"] if summary else 0,
            "total_amount_approved": summary["total_amount_approved"] if summary else 0,
            "item_total": summary["item_total"] if summary else 0,
            "item_approved": summary["item_approved"] if summary else 0,
            "item_denied": summary["item_denied"] if summary else 0,
            "item_escalated": summary["item_escalated"] if summary else 0,
            "item_requested_value": summary["item_requested_value"] if summary else 0,
            "item_approved_value": summary["item_approved_value"] if summary else 0,
            "item_denied_value": summary["item_denied_value"] if summary else 0,
            "item_escalated_value": summary["item_escalated_value"] if summary else 0,
            "pa_full_approved": summary["pa_full_approved"] if summary else 0,
            "pa_partial_approved": summary["pa_partial_approved"] if summary else 0,
            "pa_rejected": summary["pa_rejected"] if summary else 0,
            "pa_review": summary["pa_review"] if summary else 0,
            "callback_delivered": summary["callback_delivered"] if summary else 0,
            "callback_failed": summary["callback_failed"] if summary else 0,
            "callback_stale_revision": summary["callback_stale_revision"] if summary else 0,
            "callback_skipped_disabled": summary["callback_skipped_disabled"] if summary else 0,
            "callback_skipped_no_config": summary["callback_skipped_no_config"] if summary else 0,
            "callback_skipped_no_line_decisions": summary["callback_skipped_no_line_decisions"] if summary else 0,
            "callback_not_attempted": summary["callback_not_attempted"] if summary else 0,
            "callback_last_sent_at": summary["callback_last_sent_at"] if summary and summary["callback_last_sent_at"] else None,
            "avg_processing_seconds": summary["avg_processing_seconds"] if summary else None,
            "event_count": event_summary["event_count"] if event_summary else 0,
            "unique_pa_count": event_summary["unique_pa_count"] if event_summary else 0,
            "intake_value": event_summary["intake_value"] if event_summary else 0,
            "added_line_items": event_summary["added_line_items"] if event_summary else 0,
            "duplicate_event_attempts": duplicate_summary["duplicate_event_attempts"] if duplicate_summary else 0,
            "all_time_event_count": all_time_event_summary["event_count"] if all_time_event_summary else 0,
            "all_time_unique_pa_count": all_time_event_summary["unique_pa_count"] if all_time_event_summary else 0,
            "all_time_intake_value": all_time_event_summary["intake_value"] if all_time_event_summary else 0,
            "all_time_added_line_items": all_time_event_summary["added_line_items"] if all_time_event_summary else 0,
            "all_time_duplicate_event_attempts": all_time_duplicate_summary["duplicate_event_attempts"] if all_time_duplicate_summary else 0,
            "today_event_count": today_event_summary["event_count"] if today_event_summary else 0,
            "today_unique_pa_count": today_event_summary["unique_pa_count"] if today_event_summary else 0,
            "today_intake_value": today_event_summary["intake_value"] if today_event_summary else 0,
            "today_added_line_items": today_event_summary["added_line_items"] if today_event_summary else 0,
            "today_duplicate_event_attempts": today_duplicate_summary["duplicate_event_attempts"] if today_duplicate_summary else 0,
        },
        "requests": [_dashboard_request(row) for row in rows],
        "series": [
            {
                "day": r["day"],
                "received": r["received"],
                "avg_latency": r["avg_latency"],
                "approved_value": r["approved_value"],
            }
            for r in series_rows
        ],
    }


class AuditEventPayload(BaseModel):
    event_type: str
    target_kind: str | None = None
    target_id: str | None = None
    metadata: dict | None = None


class RetryPreauthPayload(BaseModel):
    request_id: str
    org_id: int | None = None


class RetryPendingPreauthPayload(BaseModel):
    org_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = None
    limit: int = 20


class SendPreauthDecisionPayload(BaseModel):
    request_id: str
    org_id: int | None = None


RETRYABLE_PREAUTH_STATUSES = {"pending", "processing", "received", "error"}


async def _resolve_mutation_org_id(claims: dict, requested_org_id: int | None = None) -> int:
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can perform this action")
    if requested_org_id is None:
        return claims["org_id"]
    if requested_org_id != claims["org_id"] and not await is_platform_admin(claims):
        raise HTTPException(status_code=403, detail="Only platform admins (SaaSPro org) can act on another org")
    return requested_org_id


async def _enqueue_preauth_retry(background: BackgroundTasks, row) -> None:
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


def _qa_norm_dec(value: str | None) -> str | None:
    """APPROVE / DENY / None — normalise messy AMAN/agent decision strings.

    ESCALATE counts as "no firm verdict" → None, which classifies as
    agent_skipped per the SAA-52 spec.
    """
    if not value:
        return None
    v = str(value).strip().upper()
    if v in ("APPROVE", "APPROVED"):
        return "APPROVE"
    if v in ("DENY", "DENIED", "REJECT", "REJECTED"):
        return "DENY"
    return None


def _qa_aman_from_items(pa_items: list[dict]) -> tuple[str | None, float, dict]:
    """Derive AMAN's final decision + amount from a pa_items[] snapshot.

    Returns (decision, approved_amount, counts) where counts breaks down
    pending/approved/queried/rejected so the UI can show partials honestly.

    Decision rule (mirrors how AMAN's reviewers actually finalize a PA):
      * Any item still 'pending' AND no approved/rejected acted on yet
            → None (still under AMAN review)
      * Any item approved (and the rest are non-pending)
            → APPROVE — even if some lines were rejected, AMAN paid SOMETHING.
              We keep the partials visible in `counts` so the drawer renders
              them per-line; the value flow uses approved_amount only.
      * All non-pending items rejected
            → DENY
    """
    # Lazy import to avoid a circular dep at module load time.
    from agent.agent import _item_status, _item_approved_cost

    counts = {"pending": 0, "approved": 0, "queried": 0, "rejected": 0, "unknown": 0}
    approved_amount = 0.0
    for item in (pa_items or []):
        s = _item_status(item)
        counts[s] = counts.get(s, 0) + 1
        if s == "approved":
            approved_amount += _item_approved_cost(item) or 0

    total_acted = counts["approved"] + counts["rejected"]
    if counts["pending"] > 0 and total_acted == 0:
        return (None, 0.0, counts)
    if counts["approved"] > 0:
        return ("APPROVE", float(approved_amount), counts)
    if counts["rejected"] > 0 and counts["approved"] == 0:
        return ("DENY", 0.0, counts)
    # All queried, or no items at all — treat as pending (AMAN hasn't acted).
    return (None, 0.0, counts)


def _qa_aman_per_item(pa_items: list[dict]) -> list[dict]:
    """Per-line shape for the drawer comparison panel."""
    from agent.agent import _item_status, _item_approved_cost, _item_cost

    out = []
    for item in (pa_items or []):
        name = (
            item.get("item_name")
            or item.get("name")
            or item.get("description")
            or "(unnamed item)"
        )
        out.append({
            "claim_item_id": item.get("claim_item_id") or item.get("id"),
            "facility_tariff_item_id": item.get("facility_tariff_item_id"),
            "name": str(name),
            "quantity": item.get("quantity"),
            "status": _item_status(item),            # pending / approved / queried / rejected
            "requested_cost": float(_item_cost(item) or 0),
            "approved_cost": float(_item_approved_cost(item) or 0) if _item_status(item) == "approved" else 0.0,
        })
    return out


def _qa_line_status(value) -> str:
    if value is None:
        return "unknown"
    v = str(value).strip().lower()
    if v in ("approve", "approved", "1"):
        return "approved"
    if v in ("deny", "denied", "reject", "rejected", "3"):
        return "rejected"
    if v in ("query", "queried", "escalate", "escalated", "2"):
        return "queried"
    if v in ("pending", "0"):
        return "pending"
    return "unknown"


def _qa_partner_recommendation_to_decision(value) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ("partial_approve", "partial-approved", "partial approved", "partial"):
        return "APPROVE"
    return _qa_norm_dec(v)


def _qa_aman_from_line_outcomes(line_outcomes: list[dict]) -> tuple[str | None, float, dict]:
    """Derive AMAN's final decision from explicit AMAN outcome events.

    `pa.approved` / future final events carry `line_outcomes[]`; those are the
    source of truth for the accuracy dashboard. Submitted payload snapshots are
    only request context.
    """
    counts = {"pending": 0, "approved": 0, "queried": 0, "rejected": 0, "unknown": 0}
    approved_amount = 0.0
    for line in (line_outcomes or []):
        if not isinstance(line, dict):
            continue
        decision = line.get("aman_decision") if isinstance(line.get("aman_decision"), dict) else {}
        status = _qa_line_status(decision.get("status"))
        counts[status] = counts.get(status, 0) + 1
        if status == "approved":
            approved_amount += _number(decision.get("approved_cost")) or 0

    total_acted = counts["approved"] + counts["rejected"] + counts["queried"]
    if counts["approved"] > 0:
        return ("APPROVE", float(approved_amount), counts)
    if counts["rejected"] > 0 and counts["approved"] == 0:
        return ("DENY", 0.0, counts)
    if counts["pending"] > 0 and total_acted == 0:
        return (None, 0.0, counts)
    return (None, 0.0, counts)


def _qa_partner_from_line_outcomes(line_outcomes: list[dict]) -> tuple[str | None, float, dict]:
    """Reconstruct the SaaSPro advisory AMAN actually received."""
    counts = {"pending": 0, "approved": 0, "queried": 0, "rejected": 0, "unknown": 0}
    approved_amount = 0.0
    for line in (line_outcomes or []):
        if not isinstance(line, dict):
            continue
        advisory = line.get("partner_advisory") if isinstance(line.get("partner_advisory"), dict) else {}
        decision = _qa_partner_recommendation_to_decision(advisory.get("recommendation"))
        if decision == "APPROVE":
            counts["approved"] += 1
            approved_amount += _number(advisory.get("recommended_approved_cost")) or 0
        elif decision == "DENY":
            counts["rejected"] += 1
        elif advisory:
            counts["queried"] += 1
        else:
            counts["unknown"] += 1

    if counts["approved"] > 0:
        return ("APPROVE", float(approved_amount), counts)
    if counts["rejected"] > 0 and counts["approved"] == 0:
        return ("DENY", 0.0, counts)
    return (None, 0.0, counts)


def _qa_parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _qa_scope_line_outcomes_to_submission(
    line_outcomes: list[dict],
    submitted_event_id: str | None,
    submitted_correlation_id: str | None,
    submitted_at: datetime | None,
) -> list[dict]:
    """Keep only AMAN line outcomes that belong to one submitted event.

    AMAN `pa.approved` payloads are cumulative for the check-in. A single final
    event can repeat older approved lines, so accuracy must score only the lines
    tied to the submitted event under review.
    """
    if not line_outcomes:
        return []

    correlation_keys = {
        str(value).strip()
        for value in (submitted_event_id, submitted_correlation_id)
        if str(value or "").strip()
    }
    if correlation_keys:
        matched = []
        for line in line_outcomes:
            if not isinstance(line, dict):
                continue
            advisory = line.get("partner_advisory") if isinstance(line.get("partner_advisory"), dict) else {}
            if str(advisory.get("correlation_id") or "").strip() in correlation_keys:
                matched.append(line)
        if matched:
            return matched

    if submitted_at is not None:
        # Fallback for legacy/manual lines without partner_advisory correlation.
        # Allow a small skew because AMAN timestamps may precede our webhook
        # receipt by a few seconds.
        threshold = submitted_at - timedelta(minutes=2)
        matched = []
        for line in line_outcomes:
            if not isinstance(line, dict):
                continue
            decision = line.get("aman_decision") if isinstance(line.get("aman_decision"), dict) else {}
            decided_at = _qa_parse_dt(decision.get("decided_at"))
            if decided_at is not None and decided_at >= threshold:
                matched.append(line)
        if matched:
            return matched

    return line_outcomes


def _qa_classify(agent: str | None, aman: str | None,
                 agent_amount: float, aman_amount: float,
                 tol: float, require_amount: bool,
                 aman_counts: dict | None = None) -> tuple[str, str | None]:
    """Bucket + (if mismatched) category.

    See the SAA-52 handoff for the exact rule. ESCALATE / None on either side
    routes to agent_skipped / pending_aman.
    """
    if agent is None:
        return ("agent_skipped", None)
    if aman is None:
        return ("pending_aman", None)
    if agent != aman:
        # Direction tells us who overruled whom; this is more useful than a
        # generic "coverage" label when we don't know AMAN's reason.
        if agent == "DENY" and aman == "APPROVE":
            return ("mismatched", "aman_over")
        if agent == "APPROVE" and aman == "DENY":
            return ("mismatched", "agent_over")
        return ("mismatched", "coverage")
    # Same decision. For APPROVE pairs, also check amount agreement —
    # this surfaces partial approvals (AMAN approved only some line items) as
    # amount mismatches.
    if agent == "APPROVE" and require_amount:
        base = float(agent_amount or 0)
        amt = float(aman_amount or 0)
        if base > 0:
            delta = abs(amt - base) / base
            if delta > tol:
                # If AMAN rejected SOME lines but kept others, the right label
                # is "limits" (they capped utilization), not raw "amount".
                if aman_counts and aman_counts.get("rejected", 0) > 0:
                    return ("mismatched", "limits")
                return ("mismatched", "amount")
        elif amt > 0:
            return ("mismatched", "amount")
    return ("matched", None)


def _qa_line_aman_decision(status: str | None) -> str | None:
    s = _qa_line_status(status)
    if s == "approved":
        return "APPROVE"
    if s == "rejected":
        return "DENY"
    if s == "queried":
        return "ESCALATE"
    return None


def _qa_classify_line_item(item: dict, tol: float, require_amount: bool) -> tuple[str, str | None]:
    """Classify one AMAN line outcome against the agent's line advisory.

    AMAN final events are PA-level approved, but line_outcomes[].aman_decision
    is the actual source of truth for approve/reject. The dashboard headline
    cards therefore need to count line items, not whole PAs.
    """
    agent = _qa_norm_dec(item.get("agent_decision"))
    aman = _qa_line_aman_decision(item.get("aman_status"))
    if agent is None and aman is None:
        return ("pending_aman", None)
    if agent is None:
        return ("agent_skipped", None)
    if aman is None:
        return ("pending_aman", None)
    if agent != aman:
        if agent == "DENY" and aman == "APPROVE":
            return ("mismatched", "aman_over")
        if agent == "APPROVE" and aman == "DENY":
            return ("mismatched", "agent_over")
        return ("mismatched", "coverage")
    if agent == "APPROVE" and require_amount:
        agent_amount = float(_number(item.get("agent_recommended_cost")) or 0)
        aman_amount = float(_number(item.get("aman_approved_cost")) or 0)
        base = max(abs(agent_amount), abs(aman_amount), 1)
        if abs(agent_amount - aman_amount) / base > tol:
            return ("mismatched", "amount")
    return ("matched", None)


def _qa_percentile(values: list[float], p: int) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return float(s[k])


@router.get("/qa/accuracy")
async def qa_accuracy(
    date_from: date | None = None,
    date_to: date | None = None,
    org_id: int | None = None,
    tolerance: float = 0.05,
    require_amount_match: bool = True,
    record_limit: int = 200,
    claims: dict = Depends(verify_session_token),
):
    """Agent-vs-AMAN accuracy dashboard (SAA-52).

    Single read-only endpoint. Returns:
      - window (from/to/label)
      - params (tolerance, require_amount_match)
      - aggregates per mode (all / advisory / applied):
          total, scored, matched, mismatched, pending_aman, agent_skipped,
          decision_match, amount_match, value{}, latency{}, categories[]
      - records (enriched PA list, mismatches first then most-recent)

    Where AMAN's finals come from:
        Submitted payloads remain the request/intake context. AMAN's final
        truth comes from explicit outcome events such as `pa.approved` carrying
        `line_outcomes[]`, where each line has the SaaSPro partner advisory and
        AMAN's final decision side by side.

    Multi-tenant via JWT org_id. Platform admins can pass ?org_id= to drill
    into a client org (same convention as /preauth-dashboard).
    """
    if org_id is not None:
        if not await is_platform_admin(claims):
            raise HTTPException(status_code=403, detail="Only platform admins (SaaSPro org) can view another org's data")
    else:
        org_id = _dashboard_org_id(claims)

    lagos_tz = ZoneInfo("Africa/Lagos")
    # Default to today when no window is given.
    today = datetime.now(lagos_tz).date()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = date_to
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")

    tol = max(0.0, min(1.0, float(tolerance)))
    date_from_start = datetime.combine(date_from, time.min, tzinfo=lagos_tz)
    date_to_end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=lagos_tz)
    record_limit = min(max(int(record_limit), 1), 500)

    # One row per PA, joined to its latest explicit AMAN outcome event. The
    # submitted row remains the intake context; `line_outcomes[]` is the AMAN
    # final source of truth for scoring.
    rows = await pg_query_all(
        """
        SELECT
            p.request_id,
            p.patient_id,
            p.status,
            p.received_at,
            p.processed_at,
            p.callback_mode,
            p.callback_status,
            p.callback_sent_at,
            p.decision           AS decision_raw,
            p.raw_payload        AS intake_payload,
            COALESCE(p.extracted_fields->>'plan',
                     p.raw_payload->'policy'->>'plan_name',
                     p.raw_payload->'policy'->>'insurance_package') AS plan,
            COALESCE(p.extracted_fields->>'patient_name',
                     NULLIF(TRIM(CONCAT_WS(' ',
                         p.raw_payload->'enrollee'->>'first_name',
                         p.raw_payload->'enrollee'->>'surname'
                     )), ''),
                     p.raw_payload->'patient'->>'name')             AS patient_name,
            COALESCE(p.extracted_fields->>'insurance_no',
                     p.raw_payload->'enrollee'->>'insurance_no',
                     p.raw_payload->'policy'->>'insurance_no')      AS insurance_no,
            COALESCE(p.extracted_fields->>'facility_name',
                     p.raw_payload->'encounter'->>'facility_name',
                     p.raw_payload->'facility'->>'name')            AS facility,
            COALESCE(p.extracted_fields->>'diagnosis',
                     p.raw_payload->'encounter'->>'diagnosis',
                     p.raw_payload->>'diagnosis')                   AS diagnosis,
            p.extracted_fields->'items'                             AS agent_items,
            p.agent_result                                          AS agent_result,
            (CASE
                WHEN (p.extracted_fields->>'total_requested_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                THEN (p.extracted_fields->>'total_requested_cost')::numeric
                ELSE NULL
             END)                                                   AS requested_amount,
            (CASE
                WHEN (p.agent_result->>'amount_approved') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                THEN (p.agent_result->>'amount_approved')::numeric
                ELSE 0
             END)                                                   AS agent_amount,
            EXTRACT(EPOCH FROM (p.processed_at - p.received_at))::float AS agent_latency_s,
            outcome.raw_payload                                     AS aman_payload,
            outcome.event_type                                      AS aman_event_type,
            COALESCE(outcome.occurred_at, outcome.last_seen_at, outcome.created_at) AS aman_event_at,
            submitted.event_id                                      AS submitted_event_id,
            submitted.correlation_id                                AS submitted_correlation_id,
            submitted.raw_payload                                   AS submitted_payload,
            submitted.extracted_fields                              AS submitted_extracted_fields,
            COALESCE(submitted.occurred_at, submitted.submitted_at, submitted.last_seen_at, submitted.created_at) AS submitted_event_at,
            submitted.created_at                                     AS submitted_webhook_received_at,
            agentlog.agent_logs                                      AS agent_logs
        FROM preauth_logs p
        LEFT JOIN LATERAL (
            SELECT raw_payload, event_type, occurred_at, last_seen_at, created_at, event_sequence
            FROM preauth_events e
            WHERE e.org_id = p.org_id
              AND LOWER(COALESCE(e.event_type, '')) IN ('pa.approved', 'pa.rejected', 'pa.finalized', 'pa.updated')
              AND e.raw_payload ? 'line_outcomes'
              AND (
                e.preauth_log_id = p.id
                OR e.request_id = p.request_id
                OR e.checkin_id = COALESCE(
                    p.raw_payload->'encounter'->>'checkin_id',
                    p.raw_payload->>'checkin_id',
                    p.extracted_fields->>'checkin_id',
                    p.request_id
                )
              )
            ORDER BY COALESCE(e.occurred_at, e.last_seen_at, e.created_at) DESC,
                     e.event_sequence DESC
            LIMIT 1
        ) outcome ON TRUE
        LEFT JOIN LATERAL (
            SELECT event_id, correlation_id, raw_payload, extracted_fields, occurred_at, submitted_at, last_seen_at, created_at, event_sequence
            FROM preauth_events e
            WHERE e.org_id = p.org_id
              AND LOWER(COALESCE(e.event_type, '')) = 'pa.submitted'
              AND (
                e.preauth_log_id = p.id
                OR e.request_id = p.request_id
                OR e.checkin_id = COALESCE(
                    p.raw_payload->'encounter'->>'checkin_id',
                    p.raw_payload->>'checkin_id',
                    p.extracted_fields->>'checkin_id',
                    p.request_id
                )
              )
            ORDER BY
              CASE
                WHEN outcome.raw_payload IS NOT NULL
                 AND (
                   e.event_id = outcome.raw_payload->>'correlation_id'
                   OR e.correlation_id = outcome.raw_payload->>'correlation_id'
                   OR e.correlation_id = outcome.raw_payload->>'event_id'
                 )
                THEN 0
                ELSE 1
              END,
              COALESCE(e.occurred_at, e.submitted_at, e.last_seen_at, e.created_at) DESC,
              e.event_sequence DESC
            LIMIT 1
        ) submitted ON TRUE
        LEFT JOIN LATERAL (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'agent_num', al.agent_num,
                        'agent_name', al.agent_name,
                        'status', al.status,
                        'result', al.result,
                        'logged_at', al.logged_at
                    )
                    ORDER BY al.logged_at, al.agent_num
                ) FILTER (WHERE al.id IS NOT NULL),
                '[]'::jsonb
            ) AS agent_logs
            FROM agent_logs al
            WHERE al.request_id = p.request_id
              AND (
                submitted.created_at IS NULL
                OR al.logged_at >= submitted.created_at - INTERVAL '3 minutes'
              )
              AND (
                outcome.raw_payload IS NULL
                OR al.logged_at <= COALESCE(outcome.occurred_at, outcome.last_seen_at, outcome.created_at) + INTERVAL '5 seconds'
              )
        ) agentlog ON TRUE
        WHERE p.org_id = $1
          AND outcome.raw_payload IS NOT NULL
          AND COALESCE(submitted.occurred_at, submitted.submitted_at, submitted.last_seen_at, submitted.created_at, p.received_at) >= $2::timestamptz
          AND COALESCE(submitted.occurred_at, submitted.submitted_at, submitted.last_seen_at, submitted.created_at, p.received_at) < $3::timestamptz
        ORDER BY COALESCE(submitted.occurred_at, submitted.submitted_at, submitted.last_seen_at, submitted.created_at, p.received_at) DESC
        """,
        org_id, date_from_start, date_to_end,
    )

    pending_followup_rows = await pg_query_all(
        """
        SELECT
            p.request_id,
            p.patient_id,
            p.callback_mode,
            p.callback_status,
            p.callback_sent_at,
            p.raw_payload AS latest_intake_payload,
            p.extracted_fields AS latest_extracted_fields,
            e.id AS submitted_event_db_id,
            e.event_id AS submitted_event_id,
            e.correlation_id AS submitted_correlation_id,
            e.event_sequence AS submitted_event_sequence,
            e.raw_payload AS submitted_payload,
            e.extracted_fields AS submitted_extracted_fields,
            e.items_added_total AS submitted_items_added_total,
            e.total_requested_cost AS submitted_total_requested_cost,
            COALESCE(e.occurred_at, e.submitted_at, e.last_seen_at, e.created_at) AS submitted_event_at,
            e.created_at AS submitted_webhook_received_at,
            EXTRACT(EPOCH FROM (agent3.logged_at - e.created_at))::float AS agent_latency_s,
            agent3.result AS agent3_result,
            agent3.logged_at AS agent3_logged_at,
            agent_run.agent_logs AS agent_logs
        FROM preauth_events e
        JOIN preauth_logs p ON p.id = e.preauth_log_id
        LEFT JOIN LATERAL (
            SELECT MIN(e2.created_at) AS next_submitted_at
            FROM preauth_events e2
            WHERE e2.org_id = e.org_id
              AND e2.checkin_id = e.checkin_id
              AND LOWER(COALESCE(e2.event_type, '')) = 'pa.submitted'
              AND e2.event_sequence > e.event_sequence
        ) next_sub ON TRUE
        LEFT JOIN LATERAL (
            SELECT al.result, al.logged_at
            FROM agent_logs al
            WHERE al.request_id = p.request_id
              AND al.agent_num = 3
              AND al.logged_at >= e.created_at
              AND (
                next_sub.next_submitted_at IS NULL
                OR al.logged_at < next_sub.next_submitted_at
              )
            ORDER BY al.logged_at DESC
            LIMIT 1
        ) agent3 ON TRUE
        LEFT JOIN LATERAL (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'agent_num', al.agent_num,
                        'agent_name', al.agent_name,
                        'status', al.status,
                        'result', al.result,
                        'logged_at', al.logged_at
                    )
                    ORDER BY al.logged_at, al.agent_num
                ) FILTER (WHERE al.id IS NOT NULL),
                '[]'::jsonb
            ) AS agent_logs
            FROM agent_logs al
            WHERE al.request_id = p.request_id
              AND al.logged_at >= e.created_at - INTERVAL '3 minutes'
              AND (
                next_sub.next_submitted_at IS NULL
                OR al.logged_at < next_sub.next_submitted_at
              )
        ) agent_run ON TRUE
        WHERE e.org_id = $1
          AND LOWER(COALESCE(e.event_type, '')) = 'pa.submitted'
          AND COALESCE(e.occurred_at, e.submitted_at, e.last_seen_at, e.created_at) >= $2::timestamptz
          AND COALESCE(e.occurred_at, e.submitted_at, e.last_seen_at, e.created_at) < $3::timestamptz
          AND NOT EXISTS (
            SELECT 1
            FROM preauth_events outcome
            WHERE outcome.org_id = e.org_id
              AND outcome.checkin_id = e.checkin_id
              AND LOWER(COALESCE(outcome.event_type, '')) IN ('pa.approved', 'pa.rejected', 'pa.finalized', 'pa.updated')
              AND outcome.raw_payload ? 'line_outcomes'
              AND (
                outcome.correlation_id = e.correlation_id
                OR outcome.raw_payload->>'correlation_id' = e.correlation_id
              )
          )
        ORDER BY e.created_at DESC, e.event_sequence DESC
        """,
        org_id, date_from_start, date_to_end,
    )

    value_rows = await pg_query_all(
        """
        WITH submitted AS (
            SELECT
                e.id AS submitted_event_db_id,
                e.event_id AS submitted_event_id,
                e.correlation_id AS submitted_correlation_id,
                e.checkin_id,
                e.request_id AS event_request_id,
                e.event_sequence,
                e.created_at,
                COALESCE(e.occurred_at, e.submitted_at, e.created_at) AS submitted_at,
                COALESCE(NULLIF(p.callback_mode, ''), 'advisory') AS mode,
                COALESCE(p.request_id, e.request_id, e.checkin_id) AS log_request_id,
                COALESCE(e.items_added_total, e.total_requested_cost, 0)::float AS submitted_value,
                COALESCE(e.items_added_count, e.item_count, 0)::int AS submitted_line_items
            FROM preauth_events e
            LEFT JOIN preauth_logs p ON p.id = e.preauth_log_id
            WHERE e.org_id = $1
              AND LOWER(COALESCE(e.event_type, '')) = 'pa.submitted'
              AND e.created_at >= $2::timestamptz
              AND e.created_at < $3::timestamptz
        ),
        event_agent AS (
            SELECT
                s.*,
                al.result AS agent_result
            FROM submitted s
            LEFT JOIN LATERAL (
                SELECT MIN(e2.created_at) AS next_submitted_at
                FROM preauth_events e2
                WHERE e2.org_id = $1
                  AND e2.checkin_id = s.checkin_id
                  AND LOWER(COALESCE(e2.event_type, '')) = 'pa.submitted'
                  AND e2.event_sequence > s.event_sequence
            ) next_sub ON TRUE
            LEFT JOIN LATERAL (
                SELECT al.result, al.logged_at
                FROM agent_logs al
                WHERE al.request_id = s.log_request_id
                  AND al.agent_num = 3
                  AND al.logged_at >= s.created_at
                  AND (
                    next_sub.next_submitted_at IS NULL
                    OR al.logged_at < next_sub.next_submitted_at
                  )
                ORDER BY al.logged_at DESC
                LIMIT 1
            ) al ON TRUE
        ),
        event_values AS (
            SELECT
                ea.mode,
                COUNT(*)::int AS submitted_events,
                COUNT(DISTINCT COALESCE(ea.checkin_id, ea.event_request_id))::int AS unique_pas,
                COALESCE(SUM(ea.submitted_line_items), 0)::int AS submitted_line_items,
                COALESCE(SUM(ea.submitted_value), 0)::float AS requested,
                COALESCE(SUM(agent_stats.agent_approved), 0)::float AS agent_approved
            FROM event_agent ea
            LEFT JOIN LATERAL (
                SELECT
                    COALESCE(
                        SUM(
                            CASE
                                WHEN LOWER(COALESCE(item.value->>'decision', '')) IN ('approve', 'approved')
                                THEN COALESCE(
                                    CASE
                                        WHEN (item.value->>'recommended_approved_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                        THEN (item.value->>'recommended_approved_cost')::numeric
                                        ELSE NULL
                                    END,
                                    CASE
                                        WHEN (item.value->>'requested_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                        THEN (item.value->>'requested_cost')::numeric
                                        ELSE 0
                                    END
                                )
                                ELSE 0
                            END
                        ),
                        0
                    )::float AS agent_approved
                FROM jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(ea.agent_result->'item_decisions') = 'array'
                        THEN ea.agent_result->'item_decisions'
                        ELSE '[]'::jsonb
                    END
                ) AS item(value)
            ) agent_stats ON TRUE
            GROUP BY ea.mode
        ),
        outcome_values AS (
            SELECT
                s.mode,
                COALESCE(SUM(outcome_stats.aman_approved), 0)::float AS aman_approved,
                COALESCE(SUM(outcome_stats.rejected), 0)::float AS rejected
            FROM submitted s
            LEFT JOIN LATERAL (
                SELECT oe.raw_payload
                FROM preauth_events oe
                WHERE oe.org_id = $1
                  AND LOWER(COALESCE(oe.event_type, '')) IN ('pa.approved', 'pa.rejected', 'pa.finalized', 'pa.updated')
                  AND oe.raw_payload ? 'line_outcomes'
                  AND (
                    oe.correlation_id = s.submitted_correlation_id
                    OR oe.correlation_id = s.submitted_event_id
                    OR oe.raw_payload->>'correlation_id' = s.submitted_correlation_id
                    OR oe.raw_payload->>'correlation_id' = s.submitted_event_id
                  )
                ORDER BY COALESCE(oe.occurred_at, oe.last_seen_at, oe.created_at) DESC,
                         oe.event_sequence DESC
                LIMIT 1
            ) outcome ON TRUE
            LEFT JOIN LATERAL (
                SELECT
                    COALESCE(
                        SUM(
                            CASE
                                WHEN LOWER(COALESCE(line.value->'aman_decision'->>'status', '')) IN ('approve', 'approved')
                                     AND (line.value->'aman_decision'->>'approved_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                THEN (line.value->'aman_decision'->>'approved_cost')::numeric
                                ELSE 0
                            END
                        ),
                        0
                    )::float AS aman_approved,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN LOWER(COALESCE(line.value->'aman_decision'->>'status', '')) IN ('deny', 'denied', 'reject', 'rejected')
                                     AND (line.value->>'requested_cost') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                THEN (line.value->>'requested_cost')::numeric
                                ELSE 0
                            END
                        ),
                        0
                    )::float AS rejected
                FROM jsonb_array_elements(
                    CASE
                        WHEN jsonb_typeof(outcome.raw_payload->'line_outcomes') = 'array'
                        THEN outcome.raw_payload->'line_outcomes'
                        ELSE '[]'::jsonb
                    END
                ) AS line(value)
                WHERE (
                    NULLIF(line.value->'partner_advisory'->>'correlation_id', '') IN (s.submitted_correlation_id, s.submitted_event_id)
                    OR (
                        (line.value->'aman_decision'->>'decided_at') ~ '^\\d{4}-\\d{2}-\\d{2}'
                        AND ((line.value->'aman_decision'->>'decided_at')::timestamp AT TIME ZONE 'UTC') >= (s.submitted_at - INTERVAL '2 minutes')
                    )
                )
            ) outcome_stats ON TRUE
            GROUP BY s.mode
        )
        SELECT
            ev.mode,
            ev.submitted_events,
            ev.unique_pas,
            ev.submitted_line_items,
            ev.requested,
            ev.agent_approved,
            COALESCE(ov.aman_approved, 0)::float AS aman_approved,
            COALESCE(ov.rejected, 0)::float AS rejected
        FROM event_values ev
        LEFT JOIN outcome_values ov ON ov.mode = ev.mode
        """,
        org_id, date_from_start, date_to_end,
    )

    def _maybe_load(value):
        """JSONB columns come back from asyncpg as strings — coerce to py types.
        Already-list / already-dict / None pass through untouched.
        """
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8")
        loaded = parse_json_field(value)
        return loaded if isinstance(loaded, (list, dict)) else None

    def _dict(value) -> dict:
        return value if isinstance(value, dict) else {}

    def _list_of_dicts(value) -> list[dict]:
        loaded = _maybe_load(value)
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    def _merge_context(base: dict, override: dict) -> dict:
        merged = {k: v for k, v in (base or {}).items() if v not in (None, "")}
        merged.update({k: v for k, v in (override or {}).items() if v not in (None, "")})
        return merged

    def _agent_from_item_decisions(decisions: list[dict]) -> tuple[str | None, float]:
        approved_amount = 0.0
        approved = 0
        denied = 0
        firm = 0
        for decision in decisions or []:
            dec = _qa_norm_dec(decision.get("decision"))
            if dec == "APPROVE":
                approved += 1
                firm += 1
                approved_amount += _number(decision.get("recommended_approved_cost")) or _number(decision.get("requested_cost")) or 0
            elif dec == "DENY":
                denied += 1
                firm += 1
        if approved > 0:
            return ("APPROVE", float(approved_amount))
        if denied > 0 and firm == denied:
            return ("DENY", 0.0)
        return (None, 0.0)

    enriched: list[dict] = []
    for r in rows:
        agent_lat_s = r.get("agent_latency_s")

        stored_intake_payload = _maybe_load(r.get("intake_payload")) or {}
        if not isinstance(stored_intake_payload, dict):
            stored_intake_payload = {}
        submitted_payload = _maybe_load(r.get("submitted_payload")) or {}
        if not isinstance(submitted_payload, dict):
            submitted_payload = {}
        submitted_fields = _maybe_load(r.get("submitted_extracted_fields")) or {}
        if not isinstance(submitted_fields, dict):
            submitted_fields = {}
        intake_payload = submitted_payload or stored_intake_payload
        outcome_payload = _maybe_load(r.get("aman_payload")) or {}
        if not isinstance(outcome_payload, dict):
            outcome_payload = {}
        line_outcomes = _list_of_dicts(outcome_payload.get("line_outcomes"))
        submitted_event_dt_for_scope = _qa_parse_dt(r.get("submitted_event_at"))
        line_outcomes = _qa_scope_line_outcomes_to_submission(
            line_outcomes,
            r.get("submitted_event_id"),
            r.get("submitted_correlation_id"),
            submitted_event_dt_for_scope,
        )

        intake_enrollee = _dict(intake_payload.get("enrollee"))
        outcome_enrollee = _dict(outcome_payload.get("enrollee"))
        aman_enrollee = _merge_context(intake_enrollee, outcome_enrollee)
        intake_encounter = _dict(intake_payload.get("encounter"))
        outcome_encounter = _dict(outcome_payload.get("encounter"))
        aman_encounter = _merge_context(intake_encounter, outcome_encounter)
        intake_policy = _dict(intake_payload.get("policy"))
        outcome_policy = _dict(outcome_payload.get("policy"))
        aman_policy = _merge_context(intake_policy, outcome_policy)
        aman_consumption = intake_payload.get("consumption") if isinstance(intake_payload.get("consumption"), dict) else None

        agent_items_raw = _maybe_load(r.get("agent_items")) or []
        if not isinstance(agent_items_raw, list):
            agent_items_raw = []
        agent_result_obj = _maybe_load(r.get("agent_result")) or {}
        if not isinstance(agent_result_obj, dict):
            agent_result_obj = {}
        agent_logs = _maybe_load(r.get("agent_logs")) or []
        if not isinstance(agent_logs, list):
            agent_logs = []
        agent_item_decisions = agent_result_obj.get("item_decisions") if isinstance(agent_result_obj.get("item_decisions"), list) else []
        intake_items_list = _list_of_dicts(intake_payload.get("pa_items"))

        outcome_agent, outcome_agent_amount, _partner_counts = _qa_partner_from_line_outcomes(line_outcomes)
        agent = outcome_agent or _qa_norm_dec(r.get("decision_raw"))
        agent_amount = float(outcome_agent_amount if outcome_agent is not None else (r.get("agent_amount") or 0))

        aman, aman_amount, aman_counts = _qa_aman_from_line_outcomes(line_outcomes)

        # AMAN review window usually starts when AMAN has our advisory. Some
        # fast/manual approvals can finalize before our callback is delivered;
        # in that case, fall back to submitted_at -> AMAN final instead of
        # leaving the review time blank.
        aman_event_at = r.get("aman_event_at")
        aman_event_dt = _qa_parse_dt(aman_event_at)
        callback_sent_at = r.get("callback_sent_at")
        submitted_anchor = (
            submitted_event_dt_for_scope
            or _qa_parse_dt(r.get("submitted_webhook_received_at"))
            or _qa_parse_dt(r.get("received_at"))
        )
        partner_anchor_candidates: list[datetime] = []
        for line in line_outcomes:
            advisory = line.get("partner_advisory") if isinstance(line.get("partner_advisory"), dict) else {}
            for key in ("received_at", "decided_at"):
                parsed = _qa_parse_dt(advisory.get(key))
                if parsed is not None:
                    partner_anchor_candidates.append(parsed)
        partner_anchor = min(partner_anchor_candidates) if partner_anchor_candidates else None
        aman_review_min = None
        if aman is not None and aman_event_dt is not None:
            advisory_anchor = (
                partner_anchor
                or _qa_parse_dt(callback_sent_at)
                or _qa_parse_dt(r.get("processed_at"))
                or _qa_parse_dt(r.get("received_at"))
            )
            if advisory_anchor and aman_event_dt >= advisory_anchor:
                aman_review_min = (aman_event_dt - advisory_anchor).total_seconds() / 60.0
            elif submitted_anchor and aman_event_dt >= submitted_anchor:
                aman_review_min = (aman_event_dt - submitted_anchor).total_seconds() / 60.0

        bucket, category = _qa_classify(
            agent, aman, agent_amount, aman_amount,
            tol, bool(require_amount_match), aman_counts,
        )

        total_end_to_end_min = None
        if aman_event_dt is not None and submitted_anchor and aman_event_dt >= submitted_anchor:
            total_end_to_end_min = (aman_event_dt - submitted_anchor).total_seconds() / 60.0
        elif aman_review_min is not None:
            total_end_to_end_min = aman_review_min + (agent_lat_s or 0) / 60.0

        agent_by_id: dict[str, dict] = {}
        for d in agent_item_decisions:
            cid = d.get("claim_item_id") or d.get("id")
            if cid is not None:
                agent_by_id[str(cid)] = d
        intake_by_id: dict[str, dict] = {}
        for d in intake_items_list:
            cid = d.get("claim_item_id") or d.get("id")
            if cid is not None:
                intake_by_id[str(cid)] = d

        item_compare = []
        for line in line_outcomes:
            cid_raw = line.get("claim_item_id") or line.get("id")
            cid = str(cid_raw) if cid_raw is not None else ""
            ag = agent_by_id.get(cid) or {}
            intake_item = intake_by_id.get(cid) or {}
            advisory = line.get("partner_advisory") if isinstance(line.get("partner_advisory"), dict) else {}
            aman_decision = line.get("aman_decision") if isinstance(line.get("aman_decision"), dict) else {}
            requested_cost = (
                _number(line.get("requested_cost"))
                or _number(intake_item.get("requested_cost"))
                or _number(ag.get("requested_cost"))
                or 0
            )
            recommended_cost = (
                _number(advisory.get("recommended_approved_cost"))
                or _number(ag.get("recommended_approved_cost"))
                or 0
            )
            approved_cost = _number(aman_decision.get("approved_cost")) or 0
            item_compare.append({
                "claim_item_id": cid_raw,
                "name": (
                    intake_item.get("item_name")
                    or intake_item.get("name")
                    or ag.get("name")
                    or ag.get("item_name")
                    or line.get("item_name")
                    or "(unnamed item)"
                ),
                "quantity": line.get("quantity") or intake_item.get("quantity") or ag.get("quantity"),
                "agent_decision": _qa_partner_recommendation_to_decision(advisory.get("recommendation")) or (_qa_norm_dec(ag.get("decision")) if ag else None),
                "agent_requested_cost": float(requested_cost),
                "agent_recommended_cost": float(recommended_cost),
                "agent_reason": advisory.get("rationale") or ag.get("reason"),
                "aman_status": _qa_line_status(aman_decision.get("status")),
                "aman_approved_cost": float(approved_cost),
                "aman_comment": aman_decision.get("comment"),
                "aman_auth_code": aman_decision.get("auth_code"),
                "aman_decided_at": aman_decision.get("decided_at"),
            })

        if not item_compare and agent_item_decisions:
            for ag in agent_item_decisions:
                item_compare.append({
                    "claim_item_id": ag.get("claim_item_id") or ag.get("id"),
                    "name": ag.get("name") or ag.get("item_name") or "(unnamed item)",
                    "quantity": ag.get("quantity"),
                    "agent_decision": _qa_norm_dec(ag.get("decision")),
                    "agent_requested_cost": float(ag.get("requested_cost") or 0),
                    "agent_recommended_cost": float(ag.get("recommended_approved_cost") or 0),
                    "agent_reason": ag.get("reason"),
                    "aman_status": None,
                    "aman_approved_cost": 0.0,
                    "aman_comment": None,
                    "aman_auth_code": None,
                    "aman_decided_at": None,
                })

        if not item_compare:
            for item in (agent_items_raw or intake_items_list):
                item_compare.append({
                    "claim_item_id": item.get("claim_item_id") or item.get("id"),
                    "name": item.get("item_name") or item.get("name") or "(unnamed item)",
                    "quantity": item.get("quantity"),
                    "agent_decision": agent,
                    "agent_requested_cost": float(_number(item.get("requested_cost")) or _number(item.get("amount")) or 0),
                    "agent_recommended_cost": 0.0,
                    "agent_reason": None,
                    "aman_status": None,
                    "aman_approved_cost": 0.0,
                    "aman_comment": None,
                    "aman_auth_code": None,
                    "aman_decided_at": None,
                })

        aman_items = [
            {
                "claim_item_id": item.get("claim_item_id"),
                "name": item.get("name"),
                "quantity": item.get("quantity"),
                "status": item.get("aman_status"),
                "requested_cost": item.get("agent_requested_cost"),
                "approved_cost": item.get("aman_approved_cost"),
            }
            for item in item_compare
        ]

        items_for_card = item_compare or agent_item_decisions or intake_items_list or agent_items_raw
        first_item_for_card = items_for_card[0] if items_for_card and isinstance(items_for_card[0], dict) else {}
        aman_patient_name = " ".join(
            str(part).strip()
            for part in (aman_enrollee.get("first_name"), aman_enrollee.get("surname"))
            if str(part or "").strip()
        ) or None
        diagnosis_value = r.get("diagnosis") or aman_encounter.get("diagnosis")
        if isinstance(diagnosis_value, (list, dict)):
            diagnosis_value = json.dumps(diagnosis_value)
        requested_amount = None
        if line_outcomes:
            requested_amount = float(sum(float(_number(item.get("requested_cost")) or 0) for item in line_outcomes))
        if requested_amount is None and r.get("requested_amount") is not None:
            requested_amount = float(r["requested_amount"] or 0)
        if requested_amount is None and item_compare:
            requested_amount = float(sum(float(item.get("agent_requested_cost") or 0) for item in item_compare))
        agent_reason = agent_result_obj.get("reason")
        if not agent_reason:
            for item in item_compare:
                if item.get("agent_reason"):
                    agent_reason = item.get("agent_reason")
                    break
        submitted_dt = (
            _qa_parse_dt(r.get("submitted_event_at"))
            or _qa_parse_dt(_nested_value(intake_payload, "submission", "submitted_at"))
            or _qa_parse_dt(intake_payload.get("occurred_at"))
            or _qa_parse_dt(r.get("received_at"))
        )
        webhook_received_dt = _qa_parse_dt(r.get("submitted_webhook_received_at")) or _qa_parse_dt(r.get("received_at"))
        enriched.append({
            "request_id": r["request_id"],
            "display_request_id": r["request_id"],
            "patient_id": r["patient_id"],
            "patient_name": submitted_fields.get("patient_name") or r.get("patient_name") or aman_patient_name,
            "insurance_no": submitted_fields.get("insurance_no") or r.get("insurance_no") or aman_enrollee.get("insurance_no"),
            "plan": submitted_fields.get("plan") or r.get("plan") or aman_policy.get("plan_name") or aman_policy.get("insurance_package"),
            "item_description": first_item_for_card.get("name") or first_item_for_card.get("item_name"),
            "line_item_count": len(items_for_card) if items_for_card else 0,
            "diagnosis": diagnosis_value,
            "facility": submitted_fields.get("facility_name") or r.get("facility") or aman_encounter.get("facility_name"),
            "requested_amount": requested_amount,
            "submitted_at": submitted_dt.isoformat() if submitted_dt else None,
            "received_at": submitted_dt.isoformat() if submitted_dt else None,
            "webhook_received_at": webhook_received_dt.isoformat() if webhook_received_dt else None,
            "advisory_received_at": partner_anchor.isoformat() if partner_anchor else None,
            "callback_mode": (r.get("callback_mode") or "advisory"),
            "callback_status": r.get("callback_status"),
            "callback_sent_at": callback_sent_at.isoformat() if callback_sent_at else None,
            "agent_decision": agent,
            "agent_amount": agent_amount,
            "agent_reason": agent_reason,
            "agent_latency_s": float(agent_lat_s) if agent_lat_s is not None else None,
            "aman_decision": aman,
            "aman_amount": float(aman_amount),
            "aman_review_min": float(aman_review_min) if aman_review_min is not None else None,
            "aman_finalized_at": aman_event_dt.isoformat() if aman is not None and aman_event_dt is not None else None,
            "aman_event_type": r.get("aman_event_type"),
            "aman_item_counts": aman_counts,
            "bucket": bucket,
            "mismatch_category": category,
            "total_end_to_end_min": total_end_to_end_min,
            "items_compare": item_compare,
            "aman_items": aman_items,
            "consumption": aman_consumption,
            "agent_logs": agent_logs,
        })

    for r in pending_followup_rows:
        submitted_payload = _maybe_load(r.get("submitted_payload")) or {}
        if not isinstance(submitted_payload, dict):
            submitted_payload = {}
        submitted_fields = _maybe_load(r.get("submitted_extracted_fields")) or {}
        if not isinstance(submitted_fields, dict):
            submitted_fields = {}
        latest_payload = _maybe_load(r.get("latest_intake_payload")) or {}
        if not isinstance(latest_payload, dict):
            latest_payload = {}
        agent3_result = _maybe_load(r.get("agent3_result")) or {}
        if not isinstance(agent3_result, dict):
            agent3_result = {}
        agent_logs = _maybe_load(r.get("agent_logs")) or []
        if not isinstance(agent_logs, list):
            agent_logs = []

        submitted_enrollee = _dict(submitted_payload.get("enrollee"))
        latest_enrollee = _dict(latest_payload.get("enrollee"))
        enrollee = _merge_context(latest_enrollee, submitted_enrollee)
        submitted_encounter = _dict(submitted_payload.get("encounter"))
        latest_encounter = _dict(latest_payload.get("encounter"))
        encounter = _merge_context(latest_encounter, submitted_encounter)
        submitted_policy = _dict(submitted_payload.get("policy"))
        latest_policy = _dict(latest_payload.get("policy"))
        policy = _merge_context(latest_policy, submitted_policy)
        consumption = submitted_payload.get("consumption") if isinstance(submitted_payload.get("consumption"), dict) else None

        item_decisions = agent3_result.get("item_decisions") if isinstance(agent3_result.get("item_decisions"), list) else []
        agent, agent_amount = _agent_from_item_decisions(item_decisions)
        item_compare = []
        for item in item_decisions:
            requested_cost = _number(item.get("requested_cost")) or 0
            item_compare.append({
                "claim_item_id": item.get("claim_item_id") or item.get("id"),
                "name": item.get("name") or item.get("item_name") or "(unnamed item)",
                "quantity": item.get("quantity"),
                "agent_decision": _qa_norm_dec(item.get("decision")),
                "agent_requested_cost": float(requested_cost),
                "agent_recommended_cost": float(_number(item.get("recommended_approved_cost")) or 0),
                "agent_reason": item.get("reason"),
                "aman_status": None,
                "aman_approved_cost": 0.0,
                "aman_comment": None,
                "aman_auth_code": None,
                "aman_decided_at": None,
            })

        if not item_compare:
            added_ids = {
                str(item.get("id"))
                for item in (_dict(submitted_payload.get("submission")).get("items_added") or [])
                if isinstance(item, dict) and item.get("id") is not None
            }
            pa_items = _list_of_dicts(submitted_payload.get("pa_items"))
            for item in pa_items:
                facility_id = item.get("facility_tariff_item_id")
                if added_ids and (facility_id is None or str(facility_id) not in added_ids):
                    continue
                requested_cost = _number(item.get("requested_cost")) or 0
                item_compare.append({
                    "claim_item_id": item.get("claim_item_id") or item.get("id"),
                    "name": item.get("item_name") or item.get("name") or "(unnamed item)",
                    "quantity": item.get("quantity"),
                    "agent_decision": agent,
                    "agent_requested_cost": float(requested_cost),
                    "agent_recommended_cost": float(agent_amount if agent == "APPROVE" else 0),
                    "agent_reason": None,
                    "aman_status": None,
                    "aman_approved_cost": 0.0,
                    "aman_comment": None,
                    "aman_auth_code": None,
                    "aman_decided_at": None,
                })

        patient_name = " ".join(
            str(part).strip()
            for part in (enrollee.get("first_name"), enrollee.get("surname"))
            if str(part or "").strip()
        ) or None
        diagnosis_value = submitted_fields.get("diagnosis") or encounter.get("diagnosis")
        if isinstance(diagnosis_value, (list, dict)):
            diagnosis_value = json.dumps(diagnosis_value)
        requested_amount = (
            _number(r.get("submitted_items_added_total"))
            or sum(float(item.get("agent_requested_cost") or 0) for item in item_compare)
            or _number(r.get("submitted_total_requested_cost"))
            or 0
        )
        aman_counts = {"pending": len(item_compare), "approved": 0, "queried": 0, "rejected": 0, "unknown": 0}
        bucket, category = _qa_classify(
            agent, None, float(agent_amount or 0), 0.0,
            tol, bool(require_amount_match), aman_counts,
        )
        first_item_for_card = item_compare[0] if item_compare else {}
        submitted_at = _qa_parse_dt(r.get("submitted_event_at")) or _qa_parse_dt(r.get("agent3_logged_at"))
        webhook_received_at = _qa_parse_dt(r.get("submitted_webhook_received_at"))
        enriched.append({
            "request_id": f"{r['request_id']}#event-{r['submitted_event_sequence']}",
            "display_request_id": r["request_id"],
            "patient_id": r["patient_id"],
            "patient_name": submitted_fields.get("patient_name") or patient_name,
            "insurance_no": submitted_fields.get("insurance_no") or enrollee.get("insurance_no"),
            "plan": submitted_fields.get("plan") or policy.get("plan_name") or policy.get("insurance_package"),
            "item_description": first_item_for_card.get("name") or first_item_for_card.get("item_name"),
            "line_item_count": len(item_compare),
            "diagnosis": diagnosis_value,
            "facility": submitted_fields.get("facility_name") or encounter.get("facility_name"),
            "requested_amount": float(requested_amount or 0),
            "submitted_at": submitted_at.isoformat() if submitted_at else None,
            "received_at": submitted_at.isoformat() if submitted_at else None,
            "webhook_received_at": webhook_received_at.isoformat() if webhook_received_at else None,
            "callback_mode": (r.get("callback_mode") or "advisory"),
            "callback_status": r.get("callback_status"),
            "callback_sent_at": r["callback_sent_at"].isoformat() if r.get("callback_sent_at") else None,
            "agent_decision": agent,
            "agent_amount": float(agent_amount or 0),
            "agent_reason": agent3_result.get("reason"),
            "agent_latency_s": float(r["agent_latency_s"]) if r.get("agent_latency_s") is not None else None,
            "aman_decision": None,
            "aman_amount": 0.0,
            "aman_review_min": None,
            "aman_finalized_at": None,
            "aman_event_type": None,
            "aman_item_counts": aman_counts,
            "bucket": bucket,
            "mismatch_category": category,
            "total_end_to_end_min": None,
            "items_compare": item_compare,
            "aman_items": [],
            "consumption": consumption,
            "agent_logs": agent_logs,
        })

    def _blank_value_totals() -> dict:
        return {
            "requested": 0.0,
            "agent_approved": 0.0,
            "aman_approved": 0.0,
            "rejected": 0.0,
            "submitted_events": 0,
            "unique_pas": 0,
            "submitted_line_items": 0,
        }

    value_totals_by_mode: dict[str, dict] = {}
    for row in value_rows:
        mode_key = (row.get("mode") or "advisory").lower()
        value_totals_by_mode[mode_key] = {
            "requested": float(row.get("requested") or 0),
            "agent_approved": float(row.get("agent_approved") or 0),
            "aman_approved": float(row.get("aman_approved") or 0),
            "rejected": float(row.get("rejected") or 0),
            "submitted_events": int(row.get("submitted_events") or 0),
            "unique_pas": int(row.get("unique_pas") or 0),
            "submitted_line_items": int(row.get("submitted_line_items") or 0),
        }
    all_value_totals = _blank_value_totals()
    for totals in value_totals_by_mode.values():
        for key in all_value_totals:
            all_value_totals[key] += totals.get(key, 0)
    value_totals_by_mode["all"] = all_value_totals

    def _value_totals(mode: str) -> dict:
        base = _blank_value_totals()
        base.update(value_totals_by_mode.get(mode, {}))
        return base

    def _aggregate(subset: list[dict], label: str, value_totals: dict | None = None) -> dict:
        line_rows: list[dict] = []
        for r in subset:
            items = r.get("items_compare") if isinstance(r.get("items_compare"), list) else []
            if not items:
                items = [{
                    "agent_decision": r.get("agent_decision"),
                    "agent_recommended_cost": r.get("agent_amount"),
                    "aman_status": "approved" if r.get("aman_decision") == "APPROVE" else "rejected" if r.get("aman_decision") == "DENY" else None,
                    "aman_approved_cost": r.get("aman_amount"),
                }]
            for item in items:
                if not isinstance(item, dict):
                    continue
                bucket, category = _qa_classify_line_item(item, tol, bool(require_amount_match))
                line_rows.append({
                    "record": r,
                    "item": item,
                    "bucket": bucket,
                    "category": category,
                })

        total = len(line_rows)
        scored_rows = [row for row in line_rows if row["bucket"] in ("matched", "mismatched")]
        scored = len(scored_rows)
        matched = sum(1 for row in line_rows if row["bucket"] == "matched")
        mismatched = sum(1 for row in line_rows if row["bucket"] == "mismatched")
        pending = sum(1 for row in line_rows if row["bucket"] == "pending_aman")
        skipped = sum(1 for row in line_rows if row["bucket"] == "agent_skipped")
        decision_match = 0
        amount_match = matched
        for row in scored_rows:
            item = row["item"]
            agent_decision = _qa_norm_dec(item.get("agent_decision"))
            aman_decision = _qa_line_aman_decision(item.get("aman_status"))
            if agent_decision and aman_decision and agent_decision == aman_decision:
                decision_match += 1

        value_totals = value_totals or _blank_value_totals()
        overturned = sum(
            float(_number(row["item"].get("aman_approved_cost")) or 0)
            for row in scored_rows
            if _qa_norm_dec(row["item"].get("agent_decision")) == "DENY"
            and _qa_line_aman_decision(row["item"].get("aman_status")) == "APPROVE"
        )

        agent_latencies = [row["record"]["agent_latency_s"] for row in line_rows if row["record"].get("agent_latency_s") is not None]
        aman_latencies = [row["record"]["aman_review_min"] for row in line_rows if row["record"].get("aman_review_min") is not None]
        latency = {
            "agent_s": (sum(agent_latencies) / len(agent_latencies)) if agent_latencies else None,
            "agent_p50": _qa_percentile(agent_latencies, 50),
            "agent_p95": _qa_percentile(agent_latencies, 95),
            "aman_min": (sum(aman_latencies) / len(aman_latencies)) if aman_latencies else None,
            "aman_p50": _qa_percentile(aman_latencies, 50),
            "aman_p95": _qa_percentile(aman_latencies, 95),
        }

        category_keys = ["coverage", "amount", "aman_over", "limits", "agent_over", "eligibility"]
        counts = {k: 0 for k in category_keys}
        for row in line_rows:
            if row["bucket"] == "mismatched":
                k = row["category"] or "coverage"
                counts[k] = counts.get(k, 0) + 1
        categories = [{"key": k, "v": counts.get(k, 0)} for k in category_keys]

        return {
            "label": label,
            "pa_total": len(subset),
            "total": total, "scored": scored,
            "matched": matched, "mismatched": mismatched,
            "pending_aman": pending, "agent_skipped": skipped,
            "decision_match": decision_match,
            "amount_match": amount_match,
            "value": {
                "requested": float(value_totals.get("requested") or 0),
                "agent_approved": float(value_totals.get("agent_approved") or 0),
                "aman_approved": float(value_totals.get("aman_approved") or 0),
                "rejected": float(value_totals.get("rejected") or 0),
                "overturned_denials": float(overturned),
                "submitted_events": int(value_totals.get("submitted_events") or 0),
                "unique_pas": int(value_totals.get("unique_pas") or 0),
                "submitted_line_items": int(value_totals.get("submitted_line_items") or 0),
            },
            "latency": latency,
            "categories": categories,
        }

    advisory_rows = [r for r in enriched if (r.get("callback_mode") or "advisory") == "advisory"]
    applied_rows = [r for r in enriched if r.get("callback_mode") == "applied"]
    aggregates = {
        "all": _aggregate(enriched, "All modes", _value_totals("all")),
        "advisory": _aggregate(advisory_rows, "Advisory mode", _value_totals("advisory")),
        "applied": _aggregate(applied_rows, "Applied mode", _value_totals("applied")),
    }

    # Records sample: mismatches first (so the drilldown's "Only mismatches"
    # view is full), then pending, then everything else by recency. Capped at
    # record_limit so the payload stays bounded.
    def _rank(b: str) -> int:
        return 0 if b == "mismatched" else 1 if b == "pending_aman" else 2

    def _ts(iso: str | None) -> int:
        if not iso:
            return 0
        digits = "".join(ch for ch in iso if ch.isdigit())[:14]
        return int(digits) if digits else 0

    enriched_sorted = sorted(enriched, key=lambda r: (_rank(r["bucket"]), -_ts(r.get("submitted_at") or r.get("received_at"))))[:record_limit]

    label_from = date_from.strftime("%b %-d") if hasattr(date_from, "strftime") else str(date_from)
    label_to = date_to.strftime("%b %-d, %Y") if hasattr(date_to, "strftime") else str(date_to)
    return {
        "window": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "label": f"{label_from} – {label_to}",
        },
        "params": {"tolerance": tol, "require_amount_match": bool(require_amount_match)},
        "aggregates": aggregates,
        "records": enriched_sorted,
    }


@router.get("/qa/comparison")
async def qa_comparison(
    date_from: date | None = None,
    date_to: date | None = None,
    checkin_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    claims: dict = Depends(verify_session_token),
):
    """PA decision comparison job (SAA-53).
    
    Compares agent decisions with AMAN's actual item decisions.
    Returns match status, amount delta, and mismatch category for each PA.
    
    Use this for:
    - QA accuracy tracking
    - Mismatch review workflow
    - Agent fine-tuning analysis
    """
    from services.pa_comparison import get_comparison_records
    from datetime import datetime, timezone
    
    org_id = _dashboard_org_id(claims)
    
    # Default to last 7 days
    today = datetime.now(timezone.utc).date()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = date_to - timedelta(days=6)
    
    lagos_tz = ZoneInfo("Africa/Lagos")
    date_from_dt = datetime.combine(date_from, time.min, tzinfo=lagos_tz)
    date_to_dt = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=lagos_tz)
    
    result = await get_comparison_records(
        org_id=org_id,
        date_from=date_from_dt,
        date_to=date_to_dt,
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
        checkin_id=checkin_id,
    )
    
    return result


@router.get("/qa/comparison/summary")
async def qa_comparison_summary(
    date_from: date | None = None,
    date_to: date | None = None,
    claims: dict = Depends(verify_session_token),
):
    """Mismatch summary by category (SAA-53).
    
    Returns breakdown of mismatches for weekly reporting.
    """
    from services.pa_comparison import get_mismatch_summary
    from datetime import datetime, timezone
    
    org_id = _dashboard_org_id(claims)
    
    today = datetime.now(timezone.utc).date()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = date_to - timedelta(days=6)
    
    lagos_tz = ZoneInfo("Africa/Lagos")
    date_from_dt = datetime.combine(date_from, time.min, tzinfo=lagos_tz)
    date_to_dt = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=lagos_tz)
    
    return await get_mismatch_summary(
        org_id=org_id,
        date_from=date_from_dt,
        date_to=date_to_dt,
    )


@router.get("/qa/comparison/{checkin_id:path}")
async def qa_score_single(
    checkin_id: str,
    claims: dict = Depends(verify_session_token),
):
    """Score a single PA by checkin_id (SAA-53)."""
    from services.pa_comparison import score_single_pa
    
    org_id = _dashboard_org_id(claims)
    return await score_single_pa(org_id=org_id, checkin_id=checkin_id)


@router.get("/applied-mode/config")
async def get_applied_mode_config(
    claims: dict = Depends(verify_session_token),
):
    """Get current applied-mode guardrail configuration (SAA-61).
    
    Returns thresholds, allowlists, and denylists for applied mode.
    Useful for dashboard display and debugging.
    """
    from services.applied_guardrails import get_guardrail_config
    return get_guardrail_config()


@router.post("/applied-mode/check")
async def check_applied_mode_eligibility(
    request_id: str,
    claims: dict = Depends(verify_session_token),
):
    """Check if a specific PA is eligible for applied mode (SAA-61).
    
    Returns guardrail check result with mode and any violations.
    """
    from services.applied_guardrails import check_guardrails
    import json
    
    org_id = _dashboard_org_id(claims)
    
    row = await pg_query_one(
        """
        SELECT request_id, raw_payload, agent_result, decision
        FROM preauth_logs
        WHERE org_id = $1 AND request_id = $2
        """,
        org_id, request_id.strip()
    )
    
    if not row:
        raise HTTPException(status_code=404, detail="PA not found")
    
    raw = row.get("raw_payload") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raw = {}
    
    ar = row.get("agent_result") or {}
    if isinstance(ar, str):
        try:
            ar = json.loads(ar)
        except (json.JSONDecodeError, ValueError):
            ar = {}
    
    enc = raw.get("encounter") if isinstance(raw.get("encounter"), dict) else {}
    items = raw.get("pa_items") or raw.get("items") or raw.get("requested_items") or []
    
    def _num(v):
        try:
            return float(v)
        except:
            return None
    
    total_requested = sum(
        float(_num(item.get("requested_cost")) or _num(item.get("cost")) or 0)
        for item in items if isinstance(item, dict)
    )
    
    util = raw.get("utilization") or {}
    annual_used = _num(util.get("maximum_annual_benefit_used"))
    annual_limit = _num(util.get("maximum_annual_benefit_limit"))
    utilization_pct = (annual_used / annual_limit) if annual_used and annual_limit else None
    
    elig = raw.get("eligibility") or ar.get("agent1", {}).get("checks", {})
    eligibility_complete = bool(
        elig and 
        elig.get("status_active") is not None and
        elig.get("not_expired") is not None
    )
    
    result = check_guardrails(
        agent_decision=row.get("decision") or ar.get("decision") or "",
        agent_confidence=ar.get("confidence") or "",
        agent_amount=_num(ar.get("amount_approved")),
        total_requested=total_requested,
        items=items,
        care_type=enc.get("care_type"),
        plan_name=raw.get("policy", {}).get("plan_name"),
        utilization_pct=utilization_pct,
        eligibility_complete=eligibility_complete,
    )
    
    return {
        "request_id": request_id,
        "can_apply": result.can_apply,
        "mode": result.mode,
        "reason": result.reason,
        "violations": result.violations,
    }


# ─────────────────────────────────────────────
# Mismatch Review Workflow (SAA-54)
# ─────────────────────────────────────────────

class CreateMismatchReviewPayload(BaseModel):
    request_id: str
    checkin_id: str | None = None
    mismatch_type: str
    cause_category: str
    agent_decision: str | None = None
    agent_amount: float | None = None
    aman_decision: str | None = None
    aman_amount: float | None = None
    notes: str | None = None
    follow_up_action: str | None = None


class UpdateMismatchReviewPayload(BaseModel):
    cause_category: str | None = None
    notes: str | None = None
    follow_up_action: str | None = None
    fix_status: str | None = None


@router.get("/mismatch-reviews")
async def list_mismatch_reviews(
    fix_status: str | None = None,
    cause_category: str | None = None,
    limit: int = 50,
    offset: int = 0,
    claims: dict = Depends(verify_session_token),
):
    """List mismatch reviews with optional filters (SAA-54)."""
    from services.mismatch_review import get_reviews
    
    org_id = _dashboard_org_id(claims)
    return await get_reviews(
        org_id=org_id,
        fix_status=fix_status,
        cause_category=cause_category,
        limit=min(max(limit, 1), 200),
        offset=max(offset, 0),
    )


@router.get("/mismatch-reviews/summary")
async def get_mismatch_summary(
    claims: dict = Depends(verify_session_token),
):
    """Get mismatch review summary for weekly reporting (SAA-54)."""
    from services.mismatch_review import get_review_summary
    
    org_id = _dashboard_org_id(claims)
    return await get_review_summary(org_id=org_id)


@router.post("/mismatch-reviews")
async def create_mismatch_review(
    payload: CreateMismatchReviewPayload,
    claims: dict = Depends(verify_session_token),
):
    """Create a new mismatch review (SAA-54)."""
    from services.mismatch_review import create_review
    
    org_id = _dashboard_org_id(claims)
    reviewer_id = int(claims.get("sub", 0))
    reviewer_email = claims.get("email", "")
    
    try:
        return await create_review(
            org_id=org_id,
            request_id=payload.request_id,
            checkin_id=payload.checkin_id,
            reviewer_id=reviewer_id,
            reviewer_email=reviewer_email,
            mismatch_type=payload.mismatch_type,
            cause_category=payload.cause_category,
            agent_decision=payload.agent_decision,
            agent_amount=payload.agent_amount,
            aman_decision=payload.aman_decision,
            aman_amount=payload.aman_amount,
            notes=payload.notes,
            follow_up_action=payload.follow_up_action,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/mismatch-reviews/{review_id}")
async def update_mismatch_review(
    review_id: int,
    payload: UpdateMismatchReviewPayload,
    claims: dict = Depends(verify_session_token),
):
    """Update a mismatch review (SAA-54)."""
    from services.mismatch_review import update_review
    
    org_id = _dashboard_org_id(claims)
    
    try:
        return await update_review(
            review_id=review_id,
            org_id=org_id,
            cause_category=payload.cause_category,
            notes=payload.notes,
            follow_up_action=payload.follow_up_action,
            fix_status=payload.fix_status,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/mismatch-reviews/categories")
async def get_mismatch_categories(
    claims: dict = Depends(verify_session_token),
):
    """Get valid cause categories and fix statuses (SAA-54)."""
    from services.mismatch_review import CAUSE_CATEGORIES, FIX_STATUSES
    
    return {
        "cause_categories": CAUSE_CATEGORIES,
        "fix_statuses": FIX_STATUSES,
    }


@router.post("/preauth/retry")
async def retry_preauth(payload: RetryPreauthPayload, background: BackgroundTasks, claims: dict = Depends(verify_session_token)):
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
    """Append-only record of compliance-sensitive UI actions.

    The client posts here when an operator does something we want a trail for
    (PDF export, drill-in view, override, etc.). Org-scoped via the JWT — a
    client can't write an event into a different org's audit log.
    """
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


@router.get("/audit/events")
async def list_audit_events(
    event_type: str | None = None,
    target_kind: str | None = None,
    target_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
    claims: dict = Depends(verify_session_token),
):
    """List audit events for the caller's org. Admin-only — members can't
    inspect who's been exporting what.
    """
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view the audit trail")
    org_id = _dashboard_org_id(claims)
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    rows = await pg_query_all(
        """
        SELECT id, user_id, user_email, event_type, target_kind, target_id, metadata, created_at
        FROM audit_events
        WHERE org_id = $1
          AND ($2::text IS NULL OR event_type = $2)
          AND ($3::text IS NULL OR target_kind = $3)
          AND ($4::text IS NULL OR target_id = $4)
        ORDER BY created_at DESC
        LIMIT $5 OFFSET $6
        """,
        org_id, event_type, target_kind, target_id, page_size, offset,
    )
    total_row = await pg_query_one(
        """
        SELECT COUNT(*)::int AS total FROM audit_events
        WHERE org_id = $1
          AND ($2::text IS NULL OR event_type = $2)
          AND ($3::text IS NULL OR target_kind = $3)
          AND ($4::text IS NULL OR target_id = $4)
        """,
        org_id, event_type, target_kind, target_id,
    )
    return {
        "pagination": {"page": page, "page_size": page_size, "total": total_row["total"] if total_row else 0},
        "events": [
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "user_email": r["user_email"],
                "event_type": r["event_type"],
                "target_kind": r["target_kind"],
                "target_id": r["target_id"],
                "metadata": parse_json_field(r["metadata"]) if r["metadata"] else {},
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }


@router.get("/patients")
async def patients_list(
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    outcome: str | None = None,  # 'all' | 'denials' | 'escalations' | 'approvals' | 'open'
    sort: str | None = None,     # 'latest' | 'count' | 'requested' | 'approved' | 'denials'
    page: int = 1,
    page_size: int = 25,
    org_id: int | None = None,
    claims: dict = Depends(verify_session_token),
):
    """Group preauth_logs by patient_id and return per-patient aggregates.

    Org-scoped via the JWT (or via platform-admin ?org_id= drill-in).
    Skips rows with patient_id IS NULL or 'unknown' — those aren't reliably
    the same patient and shouldn't be grouped.
    """
    if org_id is not None:
        if not await is_platform_admin(claims):
            raise HTTPException(status_code=403, detail="Only platform admins (SaaSPro org) can view another org's data")
    else:
        org_id = _dashboard_org_id(claims)

    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")
    date_from_start = datetime.combine(date_from, time.min) if date_from else None
    date_to_end = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None

    search_q = q.strip() if q and q.strip() else None
    search_pattern = f"%{search_q}%" if search_q else None

    order_clause = {
        "latest":    "latest_received_at DESC NULLS LAST",
        "count":     "pa_count DESC, latest_received_at DESC NULLS LAST",
        "requested": "total_requested DESC NULLS LAST, latest_received_at DESC NULLS LAST",
        "approved":  "total_approved DESC NULLS LAST, latest_received_at DESC NULLS LAST",
        "denials":   "deny_count DESC, escalate_count DESC, latest_received_at DESC NULLS LAST",
    }.get(sort or "latest", "latest_received_at DESC NULLS LAST")

    outcome_clause = {
        "denials":     "deny_count > 0",
        "escalations": "escalate_count > 0",
        "approvals":   "approve_count > 0",
        "open":        "(processing_count + pending_count + received_count) > 0",
    }.get(outcome or "all", "TRUE")

    # Single aggregating query: filter rows, compute per-row requested cost
    # from raw_payload, group by patient_id, and pull the most recent payload
    # for each patient to read their display name + insurance no + plan.
    base_sql = f"""
        WITH pa_with_cost AS (
            SELECT
                p.id,
                p.patient_id,
                p.received_at,
                p.status,
                p.decision,
                p.agent_result,
                p.raw_payload,
                COALESCE(
                    CASE
                        WHEN NULLIF(p.raw_payload->>'total_requested_cost', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN (p.raw_payload->>'total_requested_cost')::numeric
                    END,
                    (
                        SELECT COALESCE(SUM(
                            COALESCE(
                                CASE
                                    WHEN NULLIF(it->>'requested_cost', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN (it->>'requested_cost')::numeric
                                END,
                                CASE
                                    WHEN NULLIF(it->>'estimated_cost', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN (it->>'estimated_cost')::numeric
                                END,
                                CASE
                                    WHEN NULLIF(it->>'cost', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN (it->>'cost')::numeric
                                END,
                                CASE
                                    WHEN NULLIF(it->>'amount', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN (it->>'amount')::numeric
                                END,
                                CASE
                                    WHEN NULLIF(it->>'unit_cost', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN (it->>'unit_cost')::numeric * COALESCE(
                                        CASE
                                            WHEN NULLIF(it->>'quantity', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                            THEN (it->>'quantity')::numeric
                                        END,
                                        1
                                    )
                                END,
                                0
                            )
                        ), 0)
                        FROM jsonb_array_elements(
                            COALESCE(
                                CASE jsonb_typeof(p.raw_payload->'pa_items') WHEN 'array' THEN p.raw_payload->'pa_items' END,
                                CASE jsonb_typeof(p.raw_payload->'items')    WHEN 'array' THEN p.raw_payload->'items'    END,
                                '[]'::jsonb
                            )
                        ) AS it
                    ),
                    0
                ) AS req_cost
            FROM preauth_logs p
            WHERE p.org_id = $1
              AND p.patient_id IS NOT NULL
              AND p.patient_id <> 'unknown'
              AND ($2::timestamp IS NULL OR p.received_at >= $2::timestamp)
              AND ($3::timestamp IS NULL OR p.received_at <  $3::timestamp)
        ),
        aggregated AS (
            SELECT
                patient_id,
                COUNT(*)::int AS pa_count,
                SUM(req_cost)::float AS total_requested,
                SUM(
                    CASE
                        WHEN (agent_result->>'amount_approved') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN (agent_result->>'amount_approved')::numeric
                        ELSE 0
                    END
                )::float AS total_approved,
                COUNT(*) FILTER (WHERE LOWER(COALESCE(decision, status, '')) IN ('approve', 'approved'))::int AS approve_count,
                COUNT(*) FILTER (WHERE LOWER(COALESCE(decision, status, '')) IN ('deny', 'denied', 'reject', 'rejected'))::int AS deny_count,
                COUNT(*) FILTER (WHERE LOWER(COALESCE(decision, status, '')) IN ('escalate', 'escalated'))::int AS escalate_count,
                COUNT(*) FILTER (WHERE LOWER(status) = 'processing')::int AS processing_count,
                COUNT(*) FILTER (WHERE LOWER(status) = 'pending')::int    AS pending_count,
                COUNT(*) FILTER (WHERE LOWER(status) = 'received')::int   AS received_count,
                COUNT(*) FILTER (WHERE LOWER(status) = 'error')::int      AS error_count,
                MAX(received_at) AS latest_received_at
            FROM pa_with_cost
            GROUP BY patient_id
        ),
        enriched AS (
            SELECT
                a.*,
                (SELECT raw_payload FROM pa_with_cost p2
                 WHERE p2.patient_id = a.patient_id
                 ORDER BY p2.received_at DESC NULLS LAST
                 LIMIT 1) AS latest_payload
            FROM aggregated a
            WHERE {outcome_clause}
        )
        SELECT
            e.patient_id,
            e.pa_count,
            e.total_requested,
            e.total_approved,
            e.approve_count,
            e.deny_count,
            e.escalate_count,
            e.processing_count,
            e.pending_count,
            e.received_count,
            e.error_count,
            e.latest_received_at,
            TRIM(
                COALESCE(e.latest_payload->'enrollee'->>'first_name', '')
                || ' '
                || COALESCE(e.latest_payload->'enrollee'->>'surname', '')
            ) AS patient_name,
            COALESCE(
                e.latest_payload->'enrollee'->>'insurance_no',
                e.latest_payload->'policy'->>'insurance_no'
            ) AS insurance_no,
            COALESCE(
                e.latest_payload->'policy'->>'plan_name',
                e.latest_payload->'policy'->>'insurance_package'
            ) AS plan
        FROM enriched e
        WHERE ($4::text IS NULL OR (
            e.patient_id ILIKE $4
            OR COALESCE(e.latest_payload->'enrollee'->>'first_name', '') ILIKE $4
            OR COALESCE(e.latest_payload->'enrollee'->>'surname',    '') ILIKE $4
            OR COALESCE(e.latest_payload->'enrollee'->>'insurance_no', '') ILIKE $4
        ))
        ORDER BY {order_clause}
        LIMIT $5 OFFSET $6
    """

    rows = await pg_query_all(base_sql, org_id, date_from_start, date_to_end, search_pattern, page_size, offset)

    # Total count (post-filter) for pagination — reuses the same CTEs minus LIMIT/OFFSET
    count_sql = f"""
        WITH pa_with_cost AS (
            SELECT p.patient_id, p.received_at, p.status, p.decision, p.agent_result, p.raw_payload
            FROM preauth_logs p
            WHERE p.org_id = $1
              AND p.patient_id IS NOT NULL
              AND p.patient_id <> 'unknown'
              AND ($2::timestamp IS NULL OR p.received_at >= $2::timestamp)
              AND ($3::timestamp IS NULL OR p.received_at <  $3::timestamp)
        ),
        aggregated AS (
            SELECT
                patient_id,
                COUNT(*) FILTER (WHERE LOWER(COALESCE(decision, status, '')) IN ('approve', 'approved'))::int AS approve_count,
                COUNT(*) FILTER (WHERE LOWER(COALESCE(decision, status, '')) IN ('deny', 'denied', 'reject', 'rejected'))::int AS deny_count,
                COUNT(*) FILTER (WHERE LOWER(COALESCE(decision, status, '')) IN ('escalate', 'escalated'))::int AS escalate_count,
                COUNT(*) FILTER (WHERE LOWER(status) = 'processing')::int AS processing_count,
                COUNT(*) FILTER (WHERE LOWER(status) = 'pending')::int AS pending_count,
                COUNT(*) FILTER (WHERE LOWER(status) = 'received')::int AS received_count
            FROM pa_with_cost
            GROUP BY patient_id
        ),
        outcome_filtered AS (
            SELECT a.patient_id,
                (SELECT raw_payload FROM pa_with_cost p2 WHERE p2.patient_id = a.patient_id ORDER BY p2.received_at DESC NULLS LAST LIMIT 1) AS latest_payload
            FROM aggregated a
            WHERE {outcome_clause}
        )
        SELECT COUNT(*)::int AS total FROM outcome_filtered
        WHERE ($4::text IS NULL OR (
            patient_id ILIKE $4
            OR COALESCE(latest_payload->'enrollee'->>'first_name', '') ILIKE $4
            OR COALESCE(latest_payload->'enrollee'->>'surname',    '') ILIKE $4
            OR COALESCE(latest_payload->'enrollee'->>'insurance_no', '') ILIKE $4
        ))
    """
    total_row = await pg_query_one(count_sql, org_id, date_from_start, date_to_end, search_pattern)
    total = total_row["total"] if total_row else 0

    # Org-wide window (not filtered) — useful for the page subtitle
    window_row = await pg_query_one(
        """
        SELECT MIN(received_at) AS earliest, MAX(received_at) AS latest,
               COUNT(DISTINCT patient_id) FILTER (WHERE patient_id IS NOT NULL AND patient_id <> 'unknown')::int AS distinct_patients
        FROM preauth_logs WHERE org_id = $1
        """,
        org_id,
    )

    return {
        "filters": {
            "q": search_q,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "outcome": outcome or "all",
            "sort": sort or "latest",
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": ((total + page_size - 1) // page_size) if page_size > 0 else 0,
        },
        "meta": {
            "distinct_patients_org_total": window_row["distinct_patients"] if window_row else 0,
            "data_window": {
                "earliest": window_row["earliest"].isoformat() if window_row and window_row.get("earliest") else None,
                "latest":   window_row["latest"].isoformat()   if window_row and window_row.get("latest")   else None,
            },
        },
        "patients": [
            {
                "patient_id":          r["patient_id"],
                "patient_name":        r["patient_name"] or None,
                "insurance_no":        r["insurance_no"] or None,
                "plan":                r["plan"] or None,
                "pa_count":            r["pa_count"],
                "total_requested":     float(r["total_requested"] or 0),
                "total_approved":      float(r["total_approved"] or 0),
                "latest_received_at":  r["latest_received_at"].isoformat() if r["latest_received_at"] else None,
                "outcome_counts": {
                    "approve":    r["approve_count"],
                    "deny":       r["deny_count"],
                    "escalate":   r["escalate_count"],
                    "processing": r["processing_count"],
                    "pending":    r["pending_count"],
                    "received":   r["received_count"],
                    "error":      r["error_count"],
                },
            }
            for r in rows
        ],
    }


@router.get("/patient-history")
async def patient_history(
    patient_id: str,
    org_id: int | None = None,
    claims: dict = Depends(verify_session_token),
):
    """Return every PA in the caller's org for a single patient.

    Matches on the stored patient_id, and also (when stored is null/'unknown')
    on raw_payload.enrollee.insurance_no — that's the same fallback used when
    surfacing patient_id to the dashboard. Returns an empty list for a bare
    'unknown' so we never group unrelated parse-failure rows together.
    """
    if org_id is not None:
        if not await is_platform_admin(claims):
            raise HTTPException(status_code=403, detail="Only platform admins (SaaSPro org) can view another org's data")
    else:
        org_id = _dashboard_org_id(claims)

    pid = (patient_id or "").strip()
    if not pid or pid.lower() == "unknown":
        return {"patient_id": pid, "requests": []}

    rows = await pg_query_all(
        """
        SELECT
            p.id,
            p.request_id,
            p.patient_id,
            p.status,
            p.received_at,
            p.raw_payload,
            p.extracted_fields,
            p.agent_step,
            p.decision,
            p.agent_result,
            p.error_message,
            p.processed_at,
            p.callback_status,
            p.callback_http_status,
            p.callback_sent_at,
            p.callback_error,
            CASE
                WHEN ar.first_log_at IS NOT NULL
                THEN EXTRACT(EPOCH FROM (COALESCE(p.processed_at, ar.last_log_at) - ar.first_log_at))::float
                ELSE NULL
            END AS agent_runtime_seconds,
            COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'agent_num', al.agent_num,
                        'agent_name', al.agent_name,
                        'status', al.status,
                        'result', al.result,
                        'logged_at', al.logged_at
                    )
                    ORDER BY al.agent_num, al.logged_at
                ) FILTER (WHERE al.id IS NOT NULL),
                '[]'::jsonb
            ) AS agent_logs
        FROM preauth_logs p
        LEFT JOIN LATERAL (
            WITH run_start AS (
                SELECT MAX(logged_at) AS first_log_at
                FROM agent_logs
                WHERE request_id = p.request_id
                  AND agent_num = 1
                  AND (p.processed_at IS NULL OR logged_at <= p.processed_at + INTERVAL '5 seconds')
            )
            SELECT run_start.first_log_at, MAX(al.logged_at) AS last_log_at
            FROM run_start
            LEFT JOIN agent_logs al
              ON al.request_id = p.request_id
             AND al.logged_at >= run_start.first_log_at
             AND (p.processed_at IS NULL OR al.logged_at <= p.processed_at + INTERVAL '5 seconds')
            GROUP BY run_start.first_log_at
        ) ar ON TRUE
        LEFT JOIN agent_logs al ON al.request_id = p.request_id
        WHERE p.org_id = $1
          AND (
                p.patient_id = $2
                OR ((p.patient_id IS NULL OR p.patient_id = 'unknown') AND p.raw_payload->'enrollee'->>'insurance_no' = $2)
              )
        GROUP BY p.id, p.request_id, p.patient_id, p.status, p.received_at,
                 p.raw_payload, p.extracted_fields, p.agent_step, p.decision,
                 p.agent_result, p.error_message, p.processed_at,
                 p.callback_status, p.callback_http_status, p.callback_sent_at, p.callback_error,
                 ar.first_log_at, ar.last_log_at
        ORDER BY p.received_at DESC
        """,
        org_id, pid,
    )
    return {"patient_id": pid, "requests": [_dashboard_request(row) for row in rows]}


@router.get("/webhook-delivery-logs")
async def webhook_delivery_logs(
    date_from: date | None = None,
    date_to: date | None = None,
    org_id: int | None = None,
    failed_only: bool = False,
    status: str = "all",
    limit: int = 100,
    claims: dict = Depends(verify_session_token)
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")

    delivery_status = (status or "all").strip().lower()
    if failed_only and delivery_status == "all":
        delivery_status = "failed"
    allowed_statuses = {
        "all",
        "accepted",
        "failed",
        "auth_failed",
        "invalid_payload",
        "db_failed",
        "http_failed",
        "duplicates",
    }
    if delivery_status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Unsupported delivery status filter: {status}")

    if org_id is not None:
        if not await is_platform_admin(claims):
            raise HTTPException(status_code=403, detail="Only platform admins (SaaSPro org) can view another org's data")
    else:
        org_id = _dashboard_org_id(claims)
    can_view_all = False
    safe_limit = min(max(limit, 1), 1000)
    date_from_start = datetime.combine(date_from, time.min) if date_from else None
    date_to_end = datetime.combine(date_to + timedelta(days=1), time.min) if date_to else None

    summary = await pg_query_one(
        """
        SELECT
            COUNT(*)::int AS total_received,
            (COUNT(event_id) - COUNT(DISTINCT event_id))::int AS duplicate_event_attempts,
            (COUNT(checkin_id) - COUNT(DISTINCT checkin_id))::int AS repeated_checkin_attempts,
            COUNT(*) FILTER (WHERE auth_status = 'auth_success')::int AS auth_success,
            COUNT(*) FILTER (
                WHERE auth_status IN ('missing_api_key', 'invalid_api_key', 'auth_error')
            )::int AS auth_failed,
            COUNT(*) FILTER (WHERE payload_valid = TRUE)::int AS payload_valid,
            COUNT(*) FILTER (
                WHERE payload_status IN ('invalid_json', 'not_json_object')
            )::int AS payload_invalid,
            COUNT(*) FILTER (
                WHERE db_insert_status IN (
                    'db_upsert_success',
                    'event_saved_latest_state_updated',
                    'duplicate_event_seen'
                )
            )::int AS db_saved,
            COUNT(*) FILTER (WHERE db_insert_status = 'db_insert_failed')::int AS db_failed,
            COUNT(*) FILTER (WHERE http_status_returned BETWEEN 200 AND 299)::int AS http_success,
            COUNT(*) FILTER (WHERE http_status_returned >= 400)::int AS http_failed,
            AVG(processing_time_ms)::float AS avg_processing_time_ms,
            MAX(created_at) AS latest_received_at
        FROM webhook_delivery_logs w
        WHERE ($1::boolean = TRUE OR w.org_id = $2)
          AND ($3::timestamp IS NULL OR w.created_at >= $3::timestamp)
          AND ($4::timestamp IS NULL OR w.created_at < $4::timestamp)
          AND (
              $5::text = 'all'
              OR (
                  $5::text = 'accepted'
                  AND w.final_status IN ('accepted', 'accepted_duplicate_event')
                  AND (w.http_status_returned IS NULL OR w.http_status_returned < 400)
                  AND w.auth_status = 'auth_success'
                  AND w.payload_valid = TRUE
                  AND w.db_insert_status <> 'db_insert_failed'
              )
              OR (
                  $5::text = 'failed'
                  AND (
                      w.final_status NOT IN ('accepted', 'accepted_duplicate_event')
                      OR w.http_status_returned >= 400
                      OR w.auth_status <> 'auth_success'
                      OR w.payload_valid = FALSE
                      OR w.db_insert_status = 'db_insert_failed'
                  )
              )
              OR ($5::text = 'auth_failed' AND w.auth_status <> 'auth_success')
              OR ($5::text = 'invalid_payload' AND (w.payload_valid = FALSE OR w.payload_status IN ('invalid_json', 'not_json_object')))
              OR ($5::text = 'db_failed' AND w.db_insert_status = 'db_insert_failed')
              OR ($5::text = 'http_failed' AND w.http_status_returned >= 400)
              OR ($5::text = 'duplicates' AND (w.final_status = 'accepted_duplicate_event' OR w.db_insert_status = 'duplicate_event_seen'))
          )
        """,
        can_view_all, org_id, date_from_start, date_to_end, delivery_status
    )

    rows = await pg_query_all(
        """
        SELECT
            w.delivery_id,
            w.provider,
            w.org_id,
            w.api_client_id,
            ac.client_name AS api_client_name,
            w.api_key_hint,
            w.request_method,
            w.request_path,
            w.request_ip,
            w.event_id,
            w.event_type,
            w.correlation_id,
            w.checkin_id,
            w.facility_name,
            w.insurance_no,
            w.policy_no,
            w.plan_name,
            w.auth_status,
            w.payload_received,
            w.payload_valid,
            w.payload_status,
            w.payload_size_bytes,
            w.payload_summary,
            w.db_insert_status,
            w.preauth_request_id,
            w.preauth_log_id,
            w.preauth_event_id,
            w.http_status_returned,
            w.final_status,
            w.error_message,
            w.processing_time_ms,
            w.created_at,
            w.updated_at
        FROM webhook_delivery_logs w
        LEFT JOIN api_clients ac ON ac.id = w.api_client_id
        WHERE ($1::boolean = TRUE OR w.org_id = $2)
          AND ($3::timestamp IS NULL OR w.created_at >= $3::timestamp)
          AND ($4::timestamp IS NULL OR w.created_at < $4::timestamp)
          AND (
              $5::text = 'all'
              OR (
                  $5::text = 'accepted'
                  AND w.final_status IN ('accepted', 'accepted_duplicate_event')
                  AND (w.http_status_returned IS NULL OR w.http_status_returned < 400)
                  AND w.auth_status = 'auth_success'
                  AND w.payload_valid = TRUE
                  AND w.db_insert_status <> 'db_insert_failed'
              )
              OR (
                  $5::text = 'failed'
                  AND (
                      w.final_status NOT IN ('accepted', 'accepted_duplicate_event')
                      OR w.http_status_returned >= 400
                      OR w.auth_status <> 'auth_success'
                      OR w.payload_valid = FALSE
                      OR w.db_insert_status = 'db_insert_failed'
                  )
              )
              OR ($5::text = 'auth_failed' AND w.auth_status <> 'auth_success')
              OR ($5::text = 'invalid_payload' AND (w.payload_valid = FALSE OR w.payload_status IN ('invalid_json', 'not_json_object')))
              OR ($5::text = 'db_failed' AND w.db_insert_status = 'db_insert_failed')
              OR ($5::text = 'http_failed' AND w.http_status_returned >= 400)
              OR ($5::text = 'duplicates' AND (w.final_status = 'accepted_duplicate_event' OR w.db_insert_status = 'duplicate_event_seen'))
          )
        ORDER BY w.created_at DESC
        LIMIT $6
        """,
        can_view_all, org_id, date_from_start, date_to_end, delivery_status, safe_limit
    )

    return {
        "filters": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "status": delivery_status,
            "failed_only": failed_only,
            "limit": safe_limit,
        },
        "summary": {
            "total_received": summary["total_received"] if summary else 0,
            "duplicate_event_attempts": summary["duplicate_event_attempts"] if summary else 0,
            "repeated_checkin_attempts": summary["repeated_checkin_attempts"] if summary else 0,
            "auth_success": summary["auth_success"] if summary else 0,
            "auth_failed": summary["auth_failed"] if summary else 0,
            "payload_valid": summary["payload_valid"] if summary else 0,
            "payload_invalid": summary["payload_invalid"] if summary else 0,
            "db_saved": summary["db_saved"] if summary else 0,
            "db_failed": summary["db_failed"] if summary else 0,
            "http_success": summary["http_success"] if summary else 0,
            "http_failed": summary["http_failed"] if summary else 0,
            "avg_processing_time_ms": summary["avg_processing_time_ms"] if summary else None,
            "latest_received_at": summary["latest_received_at"] if summary else None,
        },
        "logs": [
            {
                "delivery_id": str(row["delivery_id"]),
                "provider": row["provider"],
                "org_id": row["org_id"],
                "api_client_id": row["api_client_id"],
                "api_client_name": row["api_client_name"],
                "api_key_hint": row["api_key_hint"],
                "request_method": row["request_method"],
                "request_path": row["request_path"],
                "request_ip": row["request_ip"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "correlation_id": row["correlation_id"],
                "checkin_id": row["checkin_id"],
                "facility_name": row["facility_name"],
                "insurance_no": row["insurance_no"],
                "policy_no": row["policy_no"],
                "plan_name": row["plan_name"],
                "auth_status": row["auth_status"],
                "payload_received": row["payload_received"],
                "payload_valid": row["payload_valid"],
                "payload_status": row["payload_status"],
                "payload_size_bytes": row["payload_size_bytes"],
                "payload_summary": parse_json_field(row["payload_summary"]),
                "db_insert_status": row["db_insert_status"],
                "preauth_request_id": row["preauth_request_id"],
                "preauth_log_id": row["preauth_log_id"],
                "preauth_event_id": row["preauth_event_id"],
                "http_status_returned": row["http_status_returned"],
                "final_status": row["final_status"],
                "error_message": row["error_message"],
                "processing_time_ms": row["processing_time_ms"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ],
    }


@router.get("/webhook-audit-trail")
async def webhook_audit_trail(
    event_id: str | None = None,
    checkin_id: str | None = None,
    request_id: str | None = None,
    include_payload: bool = False,
    limit: int = 50,
    org_id: int | None = None,
    claims: dict = Depends(verify_session_token)
):
    if org_id is not None:
        if not await is_platform_admin(claims):
            raise HTTPException(status_code=403, detail="Only platform admins (SaaSPro org) can view another org's data")
    else:
        org_id = _dashboard_org_id(claims)
    can_view_all = False
    safe_limit = min(max(limit, 1), 100)

    rows = await pg_query_all(
        """
        SELECT
            w.delivery_id,
            w.provider,
            w.org_id,
            w.event_id,
            w.event_type,
            w.correlation_id,
            w.checkin_id,
            w.facility_name,
            w.insurance_no,
            w.policy_no,
            w.plan_name,
            w.auth_status,
            w.payload_status,
            w.db_insert_status,
            w.final_status,
            w.http_status_returned,
            w.error_message,
            w.processing_time_ms,
            w.created_at AS delivery_received_at,
            w.preauth_request_id,
            w.preauth_log_id,
            w.preauth_event_id,
            p.id AS resolved_preauth_log_id,
            p.request_id AS resolved_request_id,
            p.patient_id,
            p.status AS preauth_status,
            p.decision,
            p.agent_step,
            p.received_at AS preauth_received_at,
            p.processed_at,
            p.raw_payload,
            p.extracted_fields,
            p.agent_result,
            COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'agent_num', al.agent_num,
                        'agent_name', al.agent_name,
                        'status', al.status,
                        'result', al.result,
                        'logged_at', al.logged_at
                    )
                    ORDER BY al.agent_num, al.logged_at
                ) FILTER (WHERE al.id IS NOT NULL),
                '[]'::jsonb
            ) AS agent_logs
        FROM webhook_delivery_logs w
        LEFT JOIN preauth_logs p
            ON p.id = w.preauth_log_id
            OR (w.preauth_log_id IS NULL AND p.request_id = w.preauth_request_id)
        LEFT JOIN agent_logs al ON al.request_id = p.request_id
        WHERE ($1::boolean = TRUE OR w.org_id = $2)
          AND ($3::text IS NULL OR w.event_id = $3::text)
          AND ($4::text IS NULL OR w.checkin_id = $4::text)
          AND (
              $5::text IS NULL
              OR w.preauth_request_id = $5::text
              OR p.request_id = $5::text
          )
        GROUP BY
            w.id,
            p.id,
            p.request_id,
            p.patient_id,
            p.status,
            p.decision,
            p.agent_step,
            p.received_at,
            p.processed_at,
            p.raw_payload,
            p.extracted_fields,
            p.agent_result
        ORDER BY w.created_at DESC
        LIMIT $6
        """,
        can_view_all,
        org_id,
        event_id,
        checkin_id,
        request_id,
        safe_limit,
    )

    return {
        "filters": {
            "event_id": event_id,
            "checkin_id": checkin_id,
            "request_id": request_id,
            "include_payload": include_payload,
            "limit": safe_limit,
        },
        "traces": [
            {
                "delivery": {
                    "delivery_id": str(row["delivery_id"]),
                    "provider": row["provider"],
                    "org_id": row["org_id"],
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "correlation_id": row["correlation_id"],
                    "checkin_id": row["checkin_id"],
                    "facility_name": row["facility_name"],
                    "insurance_no": row["insurance_no"],
                    "policy_no": row["policy_no"],
                    "plan_name": row["plan_name"],
                    "auth_status": row["auth_status"],
                    "payload_status": row["payload_status"],
                    "db_insert_status": row["db_insert_status"],
                    "final_status": row["final_status"],
                    "http_status_returned": row["http_status_returned"],
                    "error_message": row["error_message"],
                    "processing_time_ms": row["processing_time_ms"],
                    "received_at": row["delivery_received_at"],
                    "preauth_event_id": row["preauth_event_id"],
                },
                "preauth": {
                    "raw_payload_reference": {
                        "preauth_log_id": row["resolved_preauth_log_id"] or row["preauth_log_id"],
                        "request_id": row["resolved_request_id"] or row["preauth_request_id"],
                    },
                    "preauth_log_id": row["resolved_preauth_log_id"],
                    "request_id": row["resolved_request_id"],
                    "patient_id": row["patient_id"],
                    "status": row["preauth_status"],
                    "decision": row["decision"],
                    "agent_step": row["agent_step"],
                    "received_at": row["preauth_received_at"],
                    "processed_at": row["processed_at"],
                    "raw_payload": parse_json_field(row["raw_payload"]) if include_payload else None,
                    "extracted_fields": parse_json_field(row["extracted_fields"]) if include_payload else None,
                },
                "agent": {
                    "agent_result": parse_json_field(row["agent_result"]) if row["agent_result"] else None,
                    "agent_logs": parse_json_field(row["agent_logs"]) if row["agent_logs"] else [],
                },
            }
            for row in rows
        ],
    }


@router.get("/preauth-events")
async def preauth_events(
    event_id: str | None = None,
    checkin_id: str | None = None,
    request_id: str | None = None,
    include_payload: bool = False,
    include_outcomes: bool = False,
    limit: int = 50,
    org_id: int | None = None,
    claims: dict = Depends(verify_session_token)
):
    if org_id is not None:
        if not await is_platform_admin(claims):
            raise HTTPException(status_code=403, detail="Only platform admins (SaaSPro org) can view another org's data")
    else:
        org_id = _dashboard_org_id(claims)
    can_view_all = False
    safe_limit = min(max(limit, 1), 100)

    rows = await pg_query_all(
        """
        SELECT
            e.id,
            e.org_id,
            e.preauth_log_id,
            e.event_id,
            e.event_type,
            e.correlation_id,
            e.checkin_id,
            e.request_id,
            e.patient_id,
            e.facility_name,
            e.insurance_no,
            e.policy_no,
            e.plan_name,
            e.event_sequence,
            e.occurred_at,
            e.submitted_at,
            e.item_count,
            e.total_requested_cost,
            e.items_added_count,
            e.items_added_total,
            e.duplicate_count,
            e.first_seen_at,
            e.last_seen_at,
            e.created_at,
            e.updated_at,
            e.raw_payload,
            e.extracted_fields,
            e.payload_summary,
            p.status AS preauth_status,
            p.decision AS preauth_decision,
            w.delivery_id,
            w.final_status AS delivery_status,
            w.http_status_returned,
            w.processing_time_ms,
            COALESCE(wa.delivery_attempts, 0)::int AS delivery_attempts
        FROM preauth_events e
        LEFT JOIN preauth_logs p ON p.id = e.preauth_log_id
        LEFT JOIN LATERAL (
            SELECT
                delivery_id,
                final_status,
                http_status_returned,
                processing_time_ms
            FROM webhook_delivery_logs
            WHERE preauth_event_id = e.id
            ORDER BY created_at DESC
            LIMIT 1
        ) w ON TRUE
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS delivery_attempts
            FROM webhook_delivery_logs
            WHERE preauth_event_id = e.id
        ) wa ON TRUE
        WHERE ($1::boolean = TRUE OR e.org_id = $2)
          AND ($3::text IS NULL OR e.event_id = $3::text)
          AND ($4::text IS NULL OR e.checkin_id = $4::text)
          AND ($7::boolean = TRUE OR LOWER(COALESCE(e.event_type, '')) = 'pa.submitted')
          AND (
              $5::text IS NULL
              OR e.request_id = $5::text
              OR p.request_id = $5::text
          )
        ORDER BY e.checkin_id DESC, e.event_sequence DESC, e.created_at DESC
        LIMIT $6
        """,
        can_view_all,
        org_id,
        event_id,
        checkin_id,
        request_id,
        safe_limit,
        include_outcomes,
    )

    return {
        "filters": {
            "event_id": event_id,
            "checkin_id": checkin_id,
            "request_id": request_id,
            "include_payload": include_payload,
            "include_outcomes": include_outcomes,
            "limit": safe_limit,
        },
        "events": [
            {
                "id": row["id"],
                "org_id": row["org_id"],
                "preauth_log_id": row["preauth_log_id"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "correlation_id": row["correlation_id"],
                "checkin_id": row["checkin_id"],
                "request_id": row["request_id"],
                "patient_id": row["patient_id"],
                "facility_name": row["facility_name"],
                "insurance_no": row["insurance_no"],
                "policy_no": row["policy_no"],
                "plan_name": row["plan_name"],
                "event_sequence": row["event_sequence"],
                "occurred_at": row["occurred_at"],
                "submitted_at": row["submitted_at"],
                "item_count": row["item_count"],
                "total_requested_cost": float(row["total_requested_cost"]) if row["total_requested_cost"] is not None else None,
                "items_added_count": row["items_added_count"],
                "items_added_total": float(row["items_added_total"]) if row["items_added_total"] is not None else None,
                "duplicate_count": row["duplicate_count"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "preauth_status": row["preauth_status"],
                "preauth_decision": row["preauth_decision"],
                "delivery": {
                    "delivery_id": str(row["delivery_id"]) if row["delivery_id"] else None,
                    "status": row["delivery_status"],
                    "http_status_returned": row["http_status_returned"],
                    "processing_time_ms": row["processing_time_ms"],
                    "attempts": row["delivery_attempts"],
                },
                "payload_summary": parse_json_field(row["payload_summary"]),
                "raw_payload": parse_json_field(row["raw_payload"]) if include_payload else None,
                "extracted_fields": parse_json_field(row["extracted_fields"]) if include_payload else None,
            }
            for row in rows
        ],
    }


# ─────────────────────────────────────────────
# Invite team member (admin only)
# ─────────────────────────────────────────────

@router.get("/team")
async def list_team_members(claims: dict = Depends(verify_session_token)):
    rows = await pg_query_all(
        """
        SELECT name, email, role, created_at, 'active' AS status
        FROM clients
        WHERE org_id = $1 AND is_active = TRUE

        UNION ALL

        SELECT email AS name, email, role, created_at, 'pending' AS status
        FROM invites
        WHERE org_id = $1 AND used = FALSE

        ORDER BY created_at DESC
        """,
        claims["org_id"]
    )

    return {
        "members": [
            {
                "name": row["name"],
                "email": row["email"],
                "role": row["role"],
                "status": row["status"],
                "created_at": row["created_at"],
                "can_delete": row["email"] != claims["email"],
            }
            for row in rows
        ]
    }


@router.delete("/team-member/{email}")
async def delete_team_member(email: str, claims: dict = Depends(verify_session_token)):
    if claims["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete team members")

    if email == claims["email"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    deleted_invite = await pg_query_one(
        "DELETE FROM invites WHERE org_id = $1 AND email = $2 AND used = FALSE RETURNING id",
        claims["org_id"], email
    )
    if deleted_invite:
        return {"message": f"Pending invite deleted for {email}"}

    member = await pg_query_one(
        "SELECT id FROM clients WHERE org_id = $1 AND email = $2 AND is_active = TRUE",
        claims["org_id"], email
    )
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")

    await pg_execute(
        "DELETE FROM api_clients WHERE org_id = $1 AND user_id = $2",
        claims["org_id"], member["id"]
    )
    await pg_execute(
        "UPDATE clients SET is_active = FALSE WHERE id = $1",
        member["id"]
    )

    return {"message": f"Team member deleted: {email}"}


@router.post("/invite-member")
async def invite_member(request: Request, claims: dict = Depends(verify_session_token)):
    # Only admins can invite
    if claims["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can invite team members")

    try:
        body = await request.json()
        if isinstance(body, str):
            body = json.loads(body)
        payload = TeamInvitePayload(**body)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Request body must include an email")

    # Check not already invited or registered
    existing_invite = await pg_query_one("SELECT id FROM invites WHERE email = $1", payload.email)
    if existing_invite:
        raise HTTPException(status_code=400, detail="Invite already sent to this email")

    existing_client = await pg_query_one("SELECT id FROM clients WHERE email = $1", payload.email)
    if existing_client:
        raise HTTPException(status_code=400, detail="Email already registered")

    token = secrets.token_hex(16)

    await pg_execute(
        """
        INSERT INTO invites (org_id, email, token, role, invited_by)
        VALUES ($1, $2, $3, 'member', $4)
        """,
        claims["org_id"], payload.email, token, int(claims["sub"])
    )

    invite_link = build_invite_link(token, payload.email)
    inviter = await pg_query_one(
        """
        SELECT clients.name AS inviter_name, organizations.name AS org_name
        FROM clients
        JOIN organizations ON organizations.id = clients.org_id
        WHERE clients.id = $1 AND clients.org_id = $2
        """,
        int(claims["sub"]), claims["org_id"]
    )

    try:
        email_result = await asyncio.to_thread(
            send_invite_email,
            payload.email,
            invite_link,
            inviter["org_name"] if inviter else "your organization",
            inviter["inviter_name"] if inviter else None
        )
    except EmailDeliveryError as exc:
        return {
            "message": f"Invite created for {payload.email}, but email was not sent: {exc}",
            "email_sent": False,
        }

    return {
        "message": f"Invite email sent to {payload.email}",
        "email_sent": True,
        "email_id": email_result.get("id")
    }


# ─────────────────────────────────────────────
# Onboarding (platform admin: cross-org)
# Platform admin = admin of the SAASPRO platform org. Only role+org
# membership determines this — there's no separate flag, table, or URL.
# ─────────────────────────────────────────────

@router.get("/onboarding/orgs")
async def onboarding_list_orgs(claims: dict = Depends(verify_session_token)):
    if not await is_platform_admin(claims):
        raise HTTPException(status_code=403, detail="Only SaaSPro platform admins can list organizations")

    rows = await pg_query_all(
        """
        SELECT
            o.id,
            o.name,
            o.is_active,
            o.created_at,
            (SELECT COUNT(*) FROM clients WHERE org_id = o.id AND is_active = TRUE)::int AS members,
            (SELECT COUNT(*) FROM invites WHERE org_id = o.id AND used = FALSE)::int AS pending_invites,
            (SELECT COUNT(*) FROM api_clients WHERE org_id = o.id AND is_active = TRUE)::int AS api_keys,
            (SELECT COUNT(*) FROM preauth_logs WHERE org_id = o.id)::int AS requests,
            (SELECT MAX(received_at) FROM preauth_logs WHERE org_id = o.id) AS last_activity
        FROM organizations o
        ORDER BY o.created_at DESC
        """
    )
    return {"orgs": [dict(r) for r in rows]}


@router.post("/onboarding/create-org")
async def onboarding_create_org(payload: CreateOrgPayload, claims: dict = Depends(verify_session_token)):
    if not await is_platform_admin(claims):
        raise HTTPException(status_code=403, detail="Only SaaSPro platform admins can create organizations")

    org_name = payload.org_name.strip()
    admin_email = payload.admin_email.strip().lower()
    if not org_name or not admin_email or "@" not in admin_email:
        raise HTTPException(status_code=400, detail="org_name and a valid admin_email are required")

    existing_client = await pg_query_one(
        "SELECT id FROM clients WHERE LOWER(email) = $1",
        admin_email,
    )
    if existing_client:
        raise HTTPException(status_code=400, detail="That email is already registered to an organization")

    existing_org = await pg_query_one(
        "SELECT id, name FROM organizations WHERE LOWER(name) = LOWER($1) AND is_active = TRUE",
        org_name,
    )
    if existing_org:
        raise HTTPException(status_code=400, detail=f"Organization '{existing_org['name']}' already exists")

    org = await pg_query_one(
        "INSERT INTO organizations (name) VALUES ($1) RETURNING id, name, created_at",
        org_name,
    )

    token = secrets.token_hex(16)
    await pg_execute(
        """
        INSERT INTO invites (org_id, email, token, role, invited_by)
        VALUES ($1, $2, $3, 'admin', $4)
        """,
        org["id"], admin_email, token, int(claims["sub"]),
    )

    invite_link = build_invite_link(token, admin_email)

    email_sent = False
    email_error = None
    try:
        await asyncio.to_thread(
            send_invite_email,
            admin_email,
            invite_link,
            org_name,
            None,
        )
    except EmailDeliveryError as exc:
        email_error = str(exc)
    else:
        email_sent = True

    return {
        "org": {"id": org["id"], "name": org["name"], "created_at": org["created_at"]},
        "invite": {
            "email": admin_email,
            "invite_link": invite_link,
            "email_sent": email_sent,
            "email_error": email_error,
        },
    }


class UpdateOrgPayload(BaseModel):
    name: str | None = None
    is_active: bool | None = None


@router.patch("/onboarding/orgs/{org_id}")
async def onboarding_update_org(org_id: int, payload: UpdateOrgPayload, claims: dict = Depends(verify_session_token)):
    if not await is_platform_admin(claims):
        raise HTTPException(status_code=403, detail="Only SaaSPro platform admins can edit organizations")

    org = await pg_query_one(
        "SELECT id, name, is_active FROM organizations WHERE id = $1",
        org_id,
    )
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if payload.is_active is False and org["name"].upper() == "SAASPRO":
        raise HTTPException(status_code=400, detail="Cannot deactivate the SaaSPro platform org")

    new_name = payload.name.strip() if payload.name is not None else None
    if new_name:
        existing = await pg_query_one(
            "SELECT id FROM organizations WHERE LOWER(name) = LOWER($1) AND id <> $2",
            new_name, org_id,
        )
        if existing:
            raise HTTPException(status_code=400, detail=f"Another organization is already named '{new_name}'")

    updates = []
    args = [org_id]
    if new_name:
        args.append(new_name)
        updates.append(f"name = ${len(args)}")
    if payload.is_active is not None:
        args.append(payload.is_active)
        updates.append(f"is_active = ${len(args)}")

    if not updates:
        return {"org": {"id": org["id"], "name": org["name"], "is_active": org["is_active"]}, "message": "No changes"}

    sql = f"UPDATE organizations SET {', '.join(updates)} WHERE id = $1 RETURNING id, name, is_active, created_at"
    updated = await pg_query_one(sql, *args)
    return {"org": dict(updated), "message": "Updated"}


# ─────────────────────────────────────────────
# Get current user info
# ─────────────────────────────────────────────

@router.get("/me")
async def me(claims: dict = Depends(verify_session_token)):
    client = await pg_query_one(
        """
        SELECT clients.id, clients.name, clients.email, clients.role, clients.created_at,
               organizations.name AS org_name
        FROM clients
        LEFT JOIN organizations ON organizations.id = clients.org_id
        WHERE clients.id = $1
        """,
        int(claims["sub"])
    )
    if not client:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(client)


# ─────────────────────────────────────────────
# PA Comments / Feedback
# ─────────────────────────────────────────────

class AddPACommentPayload(BaseModel):
    request_id: str
    comment_text: str
    org_id: int | None = None


@router.post("/pa-comments")
async def add_pa_comment(payload: AddPACommentPayload, claims: dict = Depends(verify_session_token)):
    """Add a comment/feedback to a pre-auth request."""
    org_id = await _resolve_read_org_id(claims, payload.org_id)
    request_id = payload.request_id.strip()
    comment_text = payload.comment_text.strip()

    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")
    if not comment_text:
        raise HTTPException(status_code=400, detail="comment_text is required")

    # Verify the PA exists and belongs to this org
    pa = await pg_query_one(
        "SELECT id FROM preauth_logs WHERE org_id = $1 AND request_id = $2",
        org_id, request_id
    )
    if not pa:
        raise HTTPException(status_code=404, detail="Pre-auth request not found")

    # Get user info
    user_id = int(claims["sub"])
    user = await pg_query_one(
        "SELECT name, email FROM clients WHERE id = $1",
        user_id
    )

    # Insert comment
    row = await pg_query_one(
        """
        INSERT INTO pa_comments (org_id, request_id, user_id, user_email, user_name, comment_text)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, created_at
        """,
        org_id,
        request_id,
        user_id,
        user["email"] if user else claims.get("email"),
        user["name"] if user else None,
        comment_text
    )

    return {
        "id": row["id"],
        "org_id": org_id,
        "request_id": request_id,
        "user_name": user["name"] if user else None,
        "user_email": user["email"] if user else claims.get("email"),
        "comment_text": comment_text,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


async def _list_pa_comments_response(request_id: str, claims: dict, org_id: int | None = None):
    org_id = await _resolve_read_org_id(claims, org_id)
    request_id = request_id.strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")

    rows = await pg_query_all(
        """
        SELECT id, user_id, user_email, user_name, comment_text, created_at
        FROM pa_comments
        WHERE org_id = $1 AND request_id = $2
        ORDER BY created_at DESC
        """,
        org_id, request_id
    )

    return {
        "request_id": request_id,
        "comments": [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "user_email": row["user_email"],
                "comment_text": row["comment_text"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ],
        "count": len(rows),
    }


@router.get("/pa-comments")
async def list_pa_comments_by_query(
    request_id: str,
    org_id: int | None = None,
    claims: dict = Depends(verify_session_token),
):
    """List all comments for a pre-auth request."""
    return await _list_pa_comments_response(request_id, claims, org_id)


@router.get("/pa-comments/{request_id:path}")
async def list_pa_comments(
    request_id: str,
    org_id: int | None = None,
    claims: dict = Depends(verify_session_token),
):
    """List all comments for a pre-auth request."""
    return await _list_pa_comments_response(request_id, claims, org_id)
