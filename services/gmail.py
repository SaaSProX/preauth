import base64
import html
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from config.settings import settings
from services.db import pg_execute, pg_query_all, pg_query_one


GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailIntegrationError(Exception):
    pass


def gmail_label_ids() -> list[str]:
    labels = [label.strip() for label in (settings.gmail_watch_label_ids or "").split(",")]
    return [label for label in labels if label]


def decode_pubsub_data(data: str | None) -> dict[str, Any]:
    if not data:
        raise GmailIntegrationError("Pub/Sub message did not include data")

    padded = data + "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        decoded = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise GmailIntegrationError(f"Could not decode Pub/Sub message data: {exc}") from exc

    if not isinstance(decoded, dict):
        raise GmailIntegrationError("Decoded Pub/Sub message is not an object")
    return decoded


async def gmail_connections_for_email(email: str):
    return await pg_query_all(
        """
        SELECT *
        FROM gmail_connections
        WHERE LOWER(email) = LOWER($1)
          AND provider = 'google'
          AND status = 'connected'
        ORDER BY updated_at DESC, created_at DESC
        """,
        email,
    )


async def create_notification_log(connection, raw_payload: dict, decoded_payload: dict, status: str = "received"):
    message = raw_payload.get("message") if isinstance(raw_payload, dict) else {}
    if not isinstance(message, dict):
        message = {}
    row = await pg_query_one(
        """
        INSERT INTO gmail_notification_logs (
            org_id,
            gmail_connection_id,
            email,
            history_id,
            pubsub_message_id,
            subscription,
            raw_payload,
            decoded_payload,
            processed_status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9)
        RETURNING id
        """,
        connection["org_id"] if connection else None,
        connection["id"] if connection else None,
        (decoded_payload.get("emailAddress") or "").strip().lower() or None,
        str(decoded_payload.get("historyId")) if decoded_payload.get("historyId") is not None else None,
        message.get("messageId") or message.get("message_id"),
        raw_payload.get("subscription") if isinstance(raw_payload, dict) else None,
        json.dumps(raw_payload),
        json.dumps(decoded_payload),
        status,
    )
    return row["id"]


async def mark_notification_log(log_id: int | None, status: str, message_count: int = 0, error: str | None = None):
    if not log_id:
        return
    await pg_execute(
        """
        UPDATE gmail_notification_logs
        SET processed_status = $2,
            message_count = $3,
            error_message = $4,
            processed_at = NOW()
        WHERE id = $1
        """,
        log_id,
        status,
        message_count,
        error,
    )


async def _store_access_token(connection_id: int, token_data: dict[str, Any]) -> str:
    access_token = token_data.get("access_token")
    if not access_token:
        raise GmailIntegrationError("Google did not return a refreshed access token")

    expires_in = token_data.get("expires_in")
    token_expiry = None
    if expires_in is not None:
        try:
            token_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            token_expiry = None

    await pg_execute(
        """
        UPDATE gmail_connections
        SET access_token = $2,
            token_expiry = $3,
            updated_at = NOW()
        WHERE id = $1
        """,
        connection_id,
        access_token,
        token_expiry,
    )
    return access_token


async def valid_gmail_access_token(connection) -> str:
    access_token = connection["access_token"]
    expiry = connection["token_expiry"]
    if access_token and expiry:
        now = datetime.now(timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry > now + timedelta(seconds=90):
            return access_token

    refresh_token = connection["refresh_token"]
    if not refresh_token:
        raise GmailIntegrationError("Gmail refresh token is missing. Reconnect the mailbox.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            GOOGLE_OAUTH_TOKEN_URL,
            data={
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        return await _store_access_token(connection["id"], response.json())


async def start_gmail_watch(connection_id: int, org_id: int):
    topic_name = (settings.google_pubsub_topic_name or "").strip()
    if not topic_name:
        raise GmailIntegrationError("GOOGLE_PUBSUB_TOPIC_NAME is not configured")

    connection = await pg_query_one(
        """
        SELECT *
        FROM gmail_connections
        WHERE id = $1
          AND org_id = $2
          AND provider = 'google'
          AND status = 'connected'
        """,
        connection_id,
        org_id,
    )
    if not connection:
        raise GmailIntegrationError("Connected Gmail mailbox not found")

    labels = gmail_label_ids()
    body = {"topicName": topic_name}
    if labels:
        body["labelIds"] = labels
        body["labelFilterBehavior"] = "INCLUDE"

    access_token = await valid_gmail_access_token(connection)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GOOGLE_GMAIL_API_BASE}/watch",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
        response.raise_for_status()
        watch = response.json()

    expiration = None
    if watch.get("expiration") is not None:
        try:
            expiration = datetime.fromtimestamp(int(watch["expiration"]) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            expiration = None

    row = await pg_query_one(
        """
        UPDATE gmail_connections
        SET watch_history_id = $3,
            watch_expiration = $4,
            watch_status = 'active',
            watch_started_at = NOW(),
            watch_error = NULL,
            last_error = NULL,
            updated_at = NOW()
        WHERE id = $1
          AND org_id = $2
        RETURNING id, email, watch_history_id, watch_expiration, watch_status, watch_started_at
        """,
        connection_id,
        org_id,
        str(watch.get("historyId")) if watch.get("historyId") is not None else None,
        expiration,
    )
    return dict(row)


def _message_header(message: dict, name: str) -> str | None:
    headers = ((message.get("payload") or {}).get("headers") or [])
    for header in headers:
        if (header.get("name") or "").lower() == name.lower():
            return header.get("value")
    return None


def _decode_body_data(data: str | None) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8"))
    except ValueError:
        return ""
    return decoded.decode("utf-8", errors="replace")


def _walk_parts(part: dict):
    yield part
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            yield from _walk_parts(child)


def _strip_html(value: str) -> str:
    no_script = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    no_tags = re.sub(r"(?s)<[^>]+>", " ", no_script)
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def _message_body_text(message: dict) -> str:
    payload = message.get("payload") or {}
    plain = ""
    html_body = ""
    for part in _walk_parts(payload):
        mime_type = part.get("mimeType")
        body_data = (part.get("body") or {}).get("data")
        if not body_data:
            continue
        text = _decode_body_data(body_data).strip()
        if not text:
            continue
        if mime_type == "text/plain" and not plain:
            plain = text
        elif mime_type == "text/html" and not html_body:
            html_body = _strip_html(text)
    return plain or html_body


def _message_received_at(message: dict) -> datetime | None:
    internal_date = message.get("internalDate")
    if internal_date is not None:
        try:
            return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass

    date_header = _message_header(message, "Date")
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    return None


async def _store_support_message(connection, message: dict, history_id: str | None):
    received_at = _message_received_at(message)
    row = await pg_query_one(
        """
        INSERT INTO support_messages (
            org_id,
            gmail_connection_id,
            provider,
            mailbox_email,
            gmail_message_id,
            gmail_thread_id,
            history_id,
            from_email,
            to_email,
            subject,
            snippet,
            body_text,
            internal_date,
            received_at,
            label_ids,
            raw_payload
        )
        VALUES (
            $1, $2, 'google', $3, $4, $5, $6, $7, $8, $9,
            $10, $11, $12, $13, $14::jsonb, $15::jsonb
        )
        ON CONFLICT (org_id, provider, gmail_message_id)
        DO UPDATE SET
            gmail_connection_id = EXCLUDED.gmail_connection_id,
            gmail_thread_id = EXCLUDED.gmail_thread_id,
            history_id = EXCLUDED.history_id,
            from_email = EXCLUDED.from_email,
            to_email = EXCLUDED.to_email,
            subject = EXCLUDED.subject,
            snippet = EXCLUDED.snippet,
            body_text = EXCLUDED.body_text,
            internal_date = EXCLUDED.internal_date,
            received_at = EXCLUDED.received_at,
            label_ids = EXCLUDED.label_ids,
            raw_payload = EXCLUDED.raw_payload,
            updated_at = NOW()
        RETURNING id
        """,
        connection["org_id"],
        connection["id"],
        connection["email"],
        message.get("id"),
        message.get("threadId"),
        history_id,
        _message_header(message, "From"),
        _message_header(message, "To"),
        _message_header(message, "Subject"),
        message.get("snippet"),
        _message_body_text(message),
        received_at,
        received_at,
        json.dumps(message.get("labelIds") or []),
        json.dumps(message),
    )
    return row["id"]


async def _fetch_message(client: httpx.AsyncClient, access_token: str, message_id: str) -> dict:
    response = await client.get(
        f"{GOOGLE_GMAIL_API_BASE}/messages/{message_id}",
        params={"format": "full"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    return response.json()


async def sync_gmail_history(connection_id: int, notification_history_id: str, log_id: int | None = None):
    connection = await pg_query_one("SELECT * FROM gmail_connections WHERE id = $1", connection_id)
    if not connection:
        await mark_notification_log(log_id, "failed", error="Gmail connection not found")
        return

    start_history_id = connection["watch_history_id"] or notification_history_id
    if not start_history_id:
        await mark_notification_log(log_id, "skipped", error="No Gmail history cursor available")
        return

    try:
        access_token = await valid_gmail_access_token(connection)
        message_ids: set[str] = set()
        page_token = None
        latest_history_id = notification_history_id
        labels = gmail_label_ids()

        async with httpx.AsyncClient(timeout=20.0) as client:
            while True:
                params = {
                    "startHistoryId": start_history_id,
                    "historyTypes": "messageAdded",
                }
                if labels:
                    params["labelId"] = labels[0]
                if page_token:
                    params["pageToken"] = page_token

                response = await client.get(
                    f"{GOOGLE_GMAIL_API_BASE}/history",
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
                payload = response.json()
                latest_history_id = str(payload.get("historyId") or notification_history_id)

                for history in payload.get("history") or []:
                    for added in history.get("messagesAdded") or []:
                        message = added.get("message") or {}
                        message_id = message.get("id")
                        if message_id:
                            message_ids.add(message_id)

                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

            stored = 0
            for message_id in sorted(message_ids):
                message = await _fetch_message(client, access_token, message_id)
                await _store_support_message(connection, message, latest_history_id)
                stored += 1

        await pg_execute(
            """
            UPDATE gmail_connections
            SET watch_history_id = $2,
                watch_last_notification_at = NOW(),
                last_sync_at = NOW(),
                last_error = NULL,
                watch_error = NULL,
                updated_at = NOW()
            WHERE id = $1
            """,
            connection_id,
            latest_history_id or notification_history_id,
        )
        await mark_notification_log(log_id, "processed", stored)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        await _mark_sync_failed(connection_id, log_id, detail)
    except Exception as exc:
        await _mark_sync_failed(connection_id, log_id, str(exc))


async def _mark_sync_failed(connection_id: int, log_id: int | None, error: str):
    await pg_execute(
        """
        UPDATE gmail_connections
        SET last_error = $2,
            watch_error = $2,
            updated_at = NOW()
        WHERE id = $1
        """,
        connection_id,
        error[:1000],
    )
    await mark_notification_log(log_id, "failed", error=error[:1000])
