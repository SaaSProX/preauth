"""
Send the agent's decision back to Aman (advisory callback).

Implements integration direction (ii): POST to Aman's
/v2/integrations/saaspro/pa-decisions with the kpa_ inbound key, and
record the outcome on preauth_logs.callback_*.

Safe by design: never raises. Failures are logged and recorded.
If AMAN_CALLBACK_ENABLED is false, or AMAN_DECISIONS_URL/KPA_KEY are not
configured, it logs and skips.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx

from config.settings import settings
from services.db import pg_execute, pg_query_one

logger = logging.getLogger(__name__)


def _decision_to_recommendation(decision: str) -> str:
    d = (decision or "").upper()
    if d == "APPROVE":
        return "approve"
    if d in {"PARTIAL", "PARTIAL_APPROVE"}:
        return "partial_approve"
    if d == "DENY":
        return "reject"
    if d == "ESCALATE":
        return "query"
    return "query"


def _item_decision_to_recommendation(decision: str) -> str:
    d = (decision or "").upper()
    if d == "APPROVE":
        return "approve"
    if d in {"PARTIAL", "PARTIAL_APPROVE"}:
        return "partial_approve"
    if d == "DENY":
        return "reject"
    return "query"


def _coerce_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _confidence_number(agent_result: dict) -> float:
    conf_map = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}
    return conf_map.get(str(agent_result.get("confidence") or "MEDIUM").upper(), 0.7)


def _item_status(item: dict) -> str:
    value = item.get("status", item.get("claim_item_status"))
    if isinstance(value, str):
        return value.strip().lower()
    labels = {0: "pending", 1: "approved", 2: "queried", 3: "rejected"}
    try:
        return labels.get(int(value), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _pending_claim_ids(raw_payload) -> set[str] | None:
    raw = _coerce_dict(raw_payload)
    items = raw.get("pa_items")
    if not isinstance(items, list):
        return None
    return {
        str(item.get("claim_item_id"))
        for item in items
        if (
            isinstance(item, dict)
            and item.get("claim_item_id") is not None
            and _item_status(item) == "pending"
        )
    }


def _line_decisions(raw_payload, agent_result):
    ar = _coerce_dict(agent_result)
    pending_claim_ids = _pending_claim_ids(raw_payload)
    item_results = ar.get("item_decisions") if isinstance(ar.get("item_decisions"), list) else []
    rationale = (
        ar.get("reasoning")
        or ar.get("denial_reason")
        or ar.get("escalation_reason")
        or "AI advisory decision"
    )
    conf_num = _confidence_number(ar)
    out = []
    for item_result in item_results:
        if not isinstance(item_result, dict):
            continue

        claim_item_id = item_result.get("claim_item_id") or item_result.get("id")
        if claim_item_id is None:
            continue

        # AMAN's pa_items can include existing approved lines for the same
        # check-in. Advisory responses should only cover current pending lines.
        if pending_claim_ids is not None and str(claim_item_id) not in pending_claim_ids:
            continue

        rec = _item_decision_to_recommendation(item_result.get("decision"))
        approved = item_result.get("recommended_approved_cost")
        if rec == "approve" and approved is None:
            approved = item_result.get("requested_cost")
        elif rec == "reject" and approved is None:
            approved = 0

        out.append({
            "claim_item_id": claim_item_id,
            "recommendation": rec,
            "recommended_approved_cost": approved,
            "confidence": conf_num,
            "rationale": item_result.get("reason") or item_result.get("coverage_reason") or rationale,
            "policy_citations": [],
        })
    return out


def _overall_recommendation(line_decisions: list[dict], agent_result: dict, fallback_decision: str) -> str:
    if not line_decisions:
        return _decision_to_recommendation(fallback_decision)

    recs = {str(item.get("recommendation") or "").lower() for item in line_decisions}
    if recs == {"approve"}:
        return "approve"
    if recs == {"reject"}:
        return "reject"
    if recs == {"query"}:
        return "query"
    if "approve" in recs and ({"reject", "query", "partial_approve"} & recs):
        return "partial_approve"

    pa_decision = agent_result.get("pa_decision") or agent_result.get("overall_recommendation")
    if pa_decision:
        return _decision_to_recommendation(pa_decision)
    return _decision_to_recommendation(fallback_decision)


def _build_payload(row: dict) -> dict:
    raw = _coerce_dict(row.get("raw_payload"))
    ar = _coerce_dict(row.get("agent_result"))
    enc = raw.get("encounter") if isinstance(raw.get("encounter"), dict) else {}
    decision = row.get("decision") or ar.get("decision") or "REVIEW"
    rationale = (
        ar.get("reasoning")
        or ar.get("denial_reason")
        or ar.get("escalation_reason")
        or ""
    )
    line_decisions = _line_decisions(raw, ar)
    return {
        "event_type": "pa.decision.advisory",
        "event_id": str(uuid.uuid4()),
        "correlation_id": raw.get("correlation_id") or raw.get("event_id"),
        "checkin_id": enc.get("checkin_id") or raw.get("checkin_id"),
        "submission_revision": enc.get("submission_revision") or 0,
        "decided_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "overall_recommendation": _overall_recommendation(line_decisions, ar, decision),
        "overall_rationale": rationale,
        "overall_confidence": _confidence_number(ar),
        "line_decisions": line_decisions,
    }


async def _record_callback(request_id, *, status, http_status, error):
    try:
        await pg_execute(
            """
            UPDATE preauth_logs
            SET callback_status      = $2,
                callback_http_status = $3,
                callback_error       = $4,
                callback_sent_at     = NOW()
            WHERE request_id = $1
            """,
            str(request_id),
            status,
            http_status,
            (error[:500] if error else None),
        )
    except Exception:
        logger.exception("[AmanCallback] failed to record status for %s", request_id)


async def send_decision_to_aman(request_id: str, *, force: bool = False) -> dict:
    """Read the stored decision and POST it back to Aman.

    Records the outcome on preauth_logs.callback_* and returns a short
    status dict. Never raises.
    """
    url = settings.aman_decisions_url
    key = settings.kpa_key

    if not force and not settings.aman_callback_enabled:
        await _record_callback(request_id, status="skipped_disabled", http_status=None, error=None)
        return {"status": "skipped_disabled"}

    if not url or not key:
        await _record_callback(request_id, status="skipped_no_config", http_status=None, error=None)
        return {"status": "skipped_no_config"}

    row = await pg_query_one(
        "SELECT request_id, raw_payload, agent_result, decision FROM preauth_logs WHERE request_id = $1",
        str(request_id),
    )
    if not row:
        await _record_callback(request_id, status="skipped_no_row", http_status=None, error=None)
        return {"status": "skipped_no_row"}

    payload = _build_payload(dict(row))
    if not payload.get("line_decisions"):
        await _record_callback(
            request_id,
            status="skipped_no_line_decisions",
            http_status=None,
            error="No pending item-level advisory decisions were available to send.",
        )
        logger.info("[AmanCallback] skipped request_id=%s no line decisions", request_id)
        return {"status": "skipped_no_line_decisions"}

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        code = response.status_code
        if 200 <= code < 300:
            await _record_callback(request_id, status="delivered", http_status=code, error=None)
            logger.info("[AmanCallback] delivered request_id=%s http=%s", request_id, code)
            return {"status": "delivered", "http_status": code}
        # Map Aman's documented failure codes for clearer logs.
        if code == 401:
            status = "auth_failed"
        elif code == 403:
            status = "scope_missing"
        elif code == 409:
            status = "stale_revision"
        else:
            status = f"http_{code}"
        await _record_callback(request_id, status=status, http_status=code, error=response.text)
        logger.warning("[AmanCallback] %s request_id=%s http=%s", status, request_id, code)
        return {"status": status, "http_status": code}
    except httpx.HTTPError as exc:
        await _record_callback(request_id, status="network_error", http_status=None, error=str(exc))
        logger.exception("[AmanCallback] network error request_id=%s", request_id)
        return {"status": "network_error", "error": str(exc)}
