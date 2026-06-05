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


def parse_json_field(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


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
                COUNT(*)::int AS event_count,
                MAX(e.event_sequence)::int AS latest_event_sequence,
                (ARRAY_AGG(e.event_id ORDER BY e.event_sequence DESC, e.created_at DESC))[1] AS latest_event_id,
                (ARRAY_AGG(e.items_added_count ORDER BY e.event_sequence DESC, e.created_at DESC))[1] AS latest_items_added_count,
                (ARRAY_AGG(e.items_added_total ORDER BY e.event_sequence DESC, e.created_at DESC))[1] AS latest_items_added_total,
                COALESCE(SUM(e.duplicate_count), 0)::int AS duplicate_event_attempts,
                COALESCE(SUM(COALESCE(e.items_added_total, e.total_requested_cost, 0)), 0)::float AS total_intake_value
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
    )

    return {
        "filters": {
            "event_id": event_id,
            "checkin_id": checkin_id,
            "request_id": request_id,
            "include_payload": include_payload,
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
