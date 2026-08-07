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


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _item_cost(item: dict) -> float:
    amount = _number(item.get("requested_cost")) or _number(item.get("estimated_cost")) or _number(item.get("cost")) or _number(item.get("amount"))
    if amount is not None:
        return amount
    unit_cost = _number(item.get("unit_cost"))
    quantity = _number(item.get("quantity")) or 1
    return float(unit_cost * quantity) if unit_cost is not None else 0.0


def _close_amount(left, right, tolerance: float = 1.0) -> bool:
    left_num = _number(left)
    right_num = _number(right)
    if left_num is None or right_num is None:
        return False
    return abs(left_num - right_num) <= tolerance


def _same_quantity(left, right) -> bool:
    left_num = _number(left)
    right_num = _number(right)
    if left_num is None or right_num is None:
        return True
    return abs(left_num - right_num) < 0.01


def _pending_claim_ids(raw_payload) -> set[str] | None:
    raw = _coerce_dict(raw_payload)
    items = raw.get("pa_items")
    if not isinstance(items, list):
        return None
    pending = [
        item
        for item in items
        if (
            isinstance(item, dict)
            and item.get("claim_item_id") is not None
            and _item_status(item) == "pending"
        )
    ]
    added = raw.get("submission", {}).get("items_added") if isinstance(raw.get("submission"), dict) else None
    if not isinstance(added, list) or not added:
        return {str(item.get("claim_item_id")) for item in pending}

    matched = []
    used_indexes: set[int] = set()

    def add_match(index: int):
        if index in used_indexes:
            return
        used_indexes.add(index)
        matched.append(pending[index])

    for added_item in [item for item in added if isinstance(item, dict)]:
        added_id = added_item.get("id")
        added_ref = str(added_item.get("item_ref") or "").lower()
        for index, item in enumerate(pending):
            if index in used_indexes:
                continue
            if (
                added_id is not None
                and item.get("facility_tariff_item_id") is not None
                and str(item.get("facility_tariff_item_id")) == str(added_id)
            ):
                add_match(index)
                break
            if (
                added_id is not None
                and "claim_item" in added_ref
                and str(item.get("claim_item_id")) == str(added_id)
            ):
                add_match(index)
                break

    for added_item in [item for item in added if isinstance(item, dict)]:
        # AMAN appends new pa_items after earlier lines. When direct IDs are
        # unavailable, duplicate drugs can share category/quantity/amount; the
        # newest matching pending line is the current submission.
        for index in reversed(range(len(pending))):
            item = pending[index]
            if index in used_indexes:
                continue
            if added_item.get("category_id") is not None and item.get("category_id") is not None:
                if str(added_item.get("category_id")) != str(item.get("category_id")):
                    continue
            if not _same_quantity(added_item.get("quantity"), item.get("quantity")):
                continue
            if not _close_amount(added_item.get("requested_cost"), _item_cost(item)):
                continue
            add_match(index)
            break

    return {str(item.get("claim_item_id")) for item in (matched or pending)}


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


async def send_decision_to_aman(request_id: str, *, force: bool = False, check_guardrails: bool = True) -> dict:
    """Read the stored decision and POST it back to Aman.

    Records the outcome on preauth_logs.callback_* and returns a short
    status dict. Never raises.
    
    When check_guardrails=True (default), the decision mode is determined by
    the applied_guardrails service. Decisions outside guardrails are sent as
    advisory-only even if applied_mode_enabled=True.
    """
    from services.applied_guardrails import check_guardrails as run_guardrails, GuardrailResult
    
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

    # Check guardrails to determine applied vs advisory mode
    callback_mode = "advisory"
    guardrail_result = None
    
    if check_guardrails:
        raw = _coerce_dict(row.get("raw_payload"))
        ar = _coerce_dict(row.get("agent_result"))
        enc = raw.get("encounter") if isinstance(raw.get("encounter"), dict) else {}
        
        # Extract items for guardrail check
        items = raw.get("pa_items") or raw.get("items") or raw.get("requested_items") or []
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except (json.JSONDecodeError, ValueError):
                items = []
        
        # Calculate total requested
        total_requested = sum(_item_cost(item) for item in items if isinstance(item, dict))
        
        # Get utilization percentage if available
        util = raw.get("utilization") or {}
        annual_used = _number(util.get("maximum_annual_benefit_used"))
        annual_limit = _number(util.get("maximum_annual_benefit_limit"))
        utilization_pct = (annual_used / annual_limit) if annual_used and annual_limit else None
        
        # Check eligibility completeness
        elig = raw.get("eligibility") or ar.get("agent1", {}).get("checks", {})
        eligibility_complete = bool(
            elig and 
            elig.get("status_active") is not None and
            elig.get("not_expired") is not None
        )
        
        guardrail_result = run_guardrails(
            agent_decision=row.get("decision") or ar.get("decision") or "",
            agent_confidence=ar.get("confidence") or "",
            agent_amount=_number(ar.get("amount_approved")),
            total_requested=total_requested,
            items=items,
            care_type=enc.get("care_type"),
            plan_name=raw.get("policy", {}).get("plan_name"),
            utilization_pct=utilization_pct,
            eligibility_complete=eligibility_complete,
        )
        
        callback_mode = guardrail_result.mode
        logger.info(
            "[AmanCallback] guardrails request_id=%s mode=%s violations=%s",
            request_id, callback_mode, guardrail_result.violations
        )
    
    # Record the callback mode
    try:
        await pg_execute(
            "UPDATE preauth_logs SET callback_mode = $2 WHERE request_id = $1",
            str(request_id), callback_mode
        )
    except Exception:
        logger.exception("[AmanCallback] failed to record callback_mode for %s", request_id)

    payload = _build_payload(dict(row))
    
    # Update event_type based on mode
    if callback_mode == "applied":
        payload["event_type"] = "pa.decision.applied"
    else:
        payload["event_type"] = "pa.decision.advisory"
    
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
        # Alert only on "integration broken" outcomes (auth/scope/5xx). 409
        # stale_revision is an expected race and is left un-alerted.
        if status in {"auth_failed", "scope_missing"} or code >= 500:
            from services.alerts import alert_pipeline_failure
            await alert_pipeline_failure(
                "AMAN write-back failed",
                request_id=str(request_id),
                error_class=f"{status} (HTTP {code})",
                dedup_key=f"aman_callback:{status}",
                cooldown_seconds=300,
            )
        return {"status": status, "http_status": code}
    except httpx.HTTPError as exc:
        await _record_callback(request_id, status="network_error", http_status=None, error=str(exc))
        logger.exception("[AmanCallback] network error request_id=%s", request_id)
        from services.alerts import alert_pipeline_failure
        await alert_pipeline_failure(
            "AMAN write-back failed",
            request_id=str(request_id),
            error_class=f"network_error ({type(exc).__name__})",
            dedup_key="aman_callback:network_error",
            cooldown_seconds=300,
        )
        return {"status": "network_error", "error": str(exc)}
