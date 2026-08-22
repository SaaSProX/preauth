"""Shared helper functions for the auth module (SAA-81).

Extracted from auth/router.py.
"""

import json
from datetime import datetime, timezone
from fastapi import HTTPException
from services.db import pg_query_one
from services.json_utils import parse_json_field


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _nested_value(fields, *path):
    current = fields
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current

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

def _first_item(fields):
    items = _items_from_payload(fields)
    if items and isinstance(items[0], dict):
        return items[0]
    return {}

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

async def _resolve_read_org_id(claims: dict, requested_org_id: int | None = None) -> int:
    if requested_org_id is None or requested_org_id == claims["org_id"]:
        return claims["org_id"]
    if not await is_platform_admin(claims):
        raise HTTPException(status_code=403, detail="Only SaaSPro platform admins can view another organization")
    return requested_org_id

async def _resolve_mutation_org_id(claims: dict, requested_org_id: int | None = None) -> int:
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can perform this action")
    if requested_org_id is None:
        return claims["org_id"]
    if requested_org_id != claims["org_id"] and not await is_platform_admin(claims):
        raise HTTPException(status_code=403, detail="Only platform admins (SaaSPro org) can act on another org")
    return requested_org_id

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
        "clinical_review": (
            agent_result.get("clinical_review")
            if isinstance(agent_result, dict) and isinstance(agent_result.get("clinical_review"), dict)
            else None
        ),
        "agent_logs": agent_logs or [],
        "patient_pa_count": (row["patient_pa_count"] if "patient_pa_count" in row.keys() else None),
    }
