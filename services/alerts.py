"""
Domain alert builders for Slack (PHI-safe).

Thin layer over ``services.notifier.send_slack_alert`` that formats consistent,
PHI-FREE operational alerts for the pre-auth pipeline and posts them to the
configured Slack Incoming Webhook.

HARD RULE — no PHI in Slack:
    AMAN payloads carry protected health information (enrollee names,
    insurance_no, date_of_birth, diagnosis). NEVER pass any of those, raw
    exception strings, or upstream response bodies into these helpers. Use
    only: request_id / checkin_id / counts / status labels / exception CLASS
    names. That is why call-sites pass ``error_class=type(exc).__name__`` and
    never ``str(exc)``.

Safe by design:
    - No-op when Slack is not configured (helper returns None).
    - Never raises: failures to alert must never break the request/agent flow
      that triggered them.
    - Async helpers run the (blocking, stdlib urllib) send off the event loop
      and time-bound it, so a slow Slack never stalls a serverless invocation.

Serverless note:
    ``cooldown_seconds`` de-dup is process-local (a warm instance only). It
    blunts bursts from a single broken dependency within one instance's life;
    it is NOT global throttling. True aggregate throttling belongs in the
    metrics/digest layer.
"""
import asyncio
import time

from config.logging import get_logger
from config.settings import settings
from services.notifier import send_slack_alert

logger = get_logger(__name__)

# Upper bound on how long an alert send may take before we give up. Alerts are
# a side-channel; they must not extend a failing request toward a function
# timeout.
_ALERT_TIMEOUT_SECONDS = 6

# Process-local last-sent timestamps for optional cooldown de-dup.
_last_sent: dict[str, float] = {}


def _environment() -> str:
    return getattr(settings, "sentry_environment", None) or "unknown"


def _cooldown_ok(dedup_key: str | None, cooldown_seconds: float) -> bool:
    """Return True if we may send now; record the send when we do."""
    if not dedup_key or cooldown_seconds <= 0:
        return True
    now = time.monotonic()
    last = _last_sent.get(dedup_key)
    if last is not None and (now - last) < cooldown_seconds:
        return False
    _last_sent[dedup_key] = now
    return True


def _build_blocks(
    *,
    emoji: str,
    title: str,
    request_id: str | None = None,
    checkin_id: str | None = None,
    fields: dict | None = None,
    detail: str | None = None,
) -> list:
    header_fields = []
    if request_id:
        header_fields.append(f"*request_id:* `{request_id}`")
    if checkin_id:
        header_fields.append(f"*checkin_id:* `{checkin_id}`")
    for key, value in (fields or {}).items():
        header_fields.append(f"*{key}:* {value}")

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {title}"[:150]}},
    ]
    if header_fields:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(header_fields)}}
        )
    if detail:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"```{str(detail)[:300]}```"}}
        )
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"env: `{_environment()}`"}],
        }
    )
    return blocks


def _send(text: str, blocks: list) -> dict | None:
    """Synchronous best-effort send; never raises."""
    try:
        return send_slack_alert(text, blocks)
    except Exception:  # notifier is already best-effort; this is belt-and-braces
        logger.warning("slack_alert_send_failed")
        return None


async def _asend(text: str, blocks: list) -> dict | None:
    """Async best-effort send: off-thread + time-bounded; never raises."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_send, text, blocks),
            timeout=_ALERT_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.warning("slack_alert_async_failed")
        return None


async def alert_pipeline_failure(
    kind: str,
    *,
    request_id: str | None = None,
    checkin_id: str | None = None,
    error_class: str | None = None,
    fields: dict | None = None,
    dedup_key: str | None = None,
    cooldown_seconds: float = 0,
) -> dict | None:
    """PHI-safe critical alert: a PA broke somewhere in the pipeline.

    ``error_class`` should be an exception class name (e.g. ``type(exc).__name__``)
    or a short status label — never a raw exception string.
    """
    if not _cooldown_ok(dedup_key, cooldown_seconds):
        return None
    title = f"PreAuth failure: {kind}"
    text = f"🔴 {title} (request_id={request_id or 'n/a'}, env={_environment()})"
    blocks = _build_blocks(
        emoji="🔴",
        title=title,
        request_id=request_id,
        checkin_id=checkin_id,
        fields=fields,
        detail=(f"error: {error_class}" if error_class else None),
    )
    return await _asend(text, blocks)


async def alert_quality_warning(
    kind: str,
    *,
    request_id: str | None = None,
    checkin_id: str | None = None,
    fields: dict | None = None,
    detail: str | None = None,
    dedup_key: str | None = None,
    cooldown_seconds: float = 0,
) -> dict | None:
    """PHI-safe degraded-quality alert (e.g. agent JSON parse failure).

    ``detail`` must be PHI-free (status labels / counts / class names only).
    """
    if not _cooldown_ok(dedup_key, cooldown_seconds):
        return None
    title = f"PreAuth quality: {kind}"
    text = f"⚠️ {title} (request_id={request_id or 'n/a'}, env={_environment()})"
    blocks = _build_blocks(
        emoji="⚠️",
        title=title,
        request_id=request_id,
        checkin_id=checkin_id,
        fields=fields,
        detail=detail,
    )
    return await _asend(text, blocks)
