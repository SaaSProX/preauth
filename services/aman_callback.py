"""
Send the agent's decision back to Aman (advisory callback).

Implements integration direction (ii): POST to Aman's
/v2/integrations/saaspro/pa-decisions with the kpa_ inbound key, and
record the outcome on preauth_logs.callback_*.

Safe by design: never raises. Failures are logged and recorded.
If AMAN_DECISIONS_URL or KPA_KEY are not configured, it logs and skips.
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
    if d == "DENY":
        return "reject"
    if d == "ESCALATE":
        return "review"
    return "review"


def _item_decision_to_recommendation(decision: str) -> str:
    d = (decision or "").upper()
    if d == "APPROVE":
        return "approve"
    if d == "DENY":
        return "reject"
    return "review"


def _coerce_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _line_decisions(raw_payload, decision, agent_result):
    raw = _coerce_dict(raw_payload)
    items = raw.get("pa_items") or []
    if not isinstance(items, list):
        return []
    ar = _coerce_dict(agent_result)
    item_results = ar.get("item_decisions") if isinstance(ar.get("item_decisions"), list) else []
    item_results_by_claim_id = {}
    for item in item_results:
        if not isinstance(item, dict):
            continue
        for key in (item.get("claim_item_id"), item.get("id"), item.get("facility_tariff_item_id")):
            if key is not None:
                item_results_by_claim_id[str(key)] = item
    rationale = (
        ar.get("reasoning")
        or ar.get("denial_reason")
        or ar.get("escalation_reason")
        or "AI advisory decision"
    )
    conf_map = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}
    conf_num = conf_map.get(str(ar.get("confidence") or "MEDIUM").upper(), 0.7)
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        item_result = item_results_by_claim_id.get(str(it.get("claim_item_id") or it.get("id") or it.get("facility_tariff_item_id")))
        rec = _item_decision_to_recommendation(item_result.get("decision")) if item_result else _decision_to_recommendation(decision)
        approved = None
        item_rationale = rationale
        if item_result:
            item_rationale = item_result.get("reason") or item_result.get("coverage_reason") or rationale
            approved = item_result.get("recommended_approved_cost")
        if rec == "approve":
            if approved is None:
                requested = it.get("requested_cost")
                try:
                    if requested is not None:
                        approved = float(requested)
                    else:
                        approved = float(it.get("unit_cost") or 0) * (float(it.get("quantity")) or 1)
                except (TypeError, ValueError):
                    approved = None
        out.append({
            "claim_item_id": it.get("claim_item_id") or it.get("id"),
            "recommendation": rec,
            "recommended_approved_cost": approved,
            "confidence": conf_num,
            "rationale": item_rationale,
            "policy_citations": [],
        })
    return out


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
    return {
        "event_type": "pa.decision.advisory",
        "event_id": str(uuid.uuid4()),
        "correlation_id": raw.get("correlation_id") or raw.get("event_id"),
        "checkin_id": enc.get("checkin_id") or raw.get("checkin_id"),
        "submission_revision": enc.get("submission_revision") or 0,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "overall_recommendation": _decision_to_recommendation(decision),
        "overall_rationale": rationale,
        "line_decisions": _line_decisions(raw, decision, ar),
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


async def send_decision_to_aman(request_id: str) -> dict:
    """Read the stored decision and POST it back to Aman.

    Records the outcome on preauth_logs.callback_* and returns a short
    status dict. Never raises.
    """
    url = settings.aman_decisions_url
    key = settings.kpa_key

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
