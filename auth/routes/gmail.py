"""Gmail integration routes."""

import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from auth.schemas import GmailDisconnectPayload, GmailWatchStartPayload
from auth.utils import verify_session_token
from auth.helpers import _resolve_read_org_id
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

router = APIRouter()

# Google OAuth endpoints
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
# Gmail Helpers
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# Gmail Routes
# ─────────────────────────────────────────────

@router.get("/integrations/gmail")
async def gmail_connection_status(org_id: int | None = None, claims: dict = Depends(verify_session_token)):
    """Get Gmail connection status for the organization."""
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
    """Initiate Gmail OAuth connection flow."""
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
    """Handle Gmail OAuth callback."""
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
    """Start Gmail realtime watch listener."""
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
    """Handle Gmail Pub/Sub push notifications."""
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
    """Disconnect a Gmail integration."""
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
