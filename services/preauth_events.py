import json
import uuid
from datetime import datetime

from services.db import get_pg_conn


def _json_param(value):
    return json.dumps(value)


def _nested(payload: dict, *keys):
    value = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _parse_timestamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _items_added(payload: dict):
    items = _nested(payload, "submission", "items_added")
    return items if isinstance(items, list) else []


def _sum_requested_cost(items: list):
    total = 0.0
    found = False
    for item in items:
        if not isinstance(item, dict):
            continue
        requested_cost = _to_float(item.get("requested_cost"))
        if requested_cost is None:
            continue
        total += requested_cost
        found = True
    return total if found else None


async def persist_preauth_intake_event(
    *,
    org_id: int,
    request_id: str,
    patient_id: str,
    payload: dict,
    extracted_fields: dict,
    payload_summary: dict,
):
    event_id = payload.get("event_id") or f"generated-{uuid.uuid4().hex}"
    checkin_id = payload_summary.get("checkin_id") or request_id
    items = extracted_fields.get("items")
    items_added = _items_added(payload)

    conn = await get_pg_conn()
    try:
        async with conn.transaction():
            existing_event = await conn.fetchrow(
                """
                SELECT id, preauth_log_id, event_sequence, duplicate_count
                FROM preauth_events
                WHERE event_id = $1
                FOR UPDATE
                """,
                str(event_id),
            )
            if existing_event:
                event_row = await conn.fetchrow(
                    """
                    UPDATE preauth_events
                    SET duplicate_count = duplicate_count + 1,
                        last_seen_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING *
                    """,
                    existing_event["id"],
                )
                preauth_row = None
                if existing_event["preauth_log_id"]:
                    preauth_row = await conn.fetchrow(
                        "SELECT id, request_id FROM preauth_logs WHERE id = $1",
                        existing_event["preauth_log_id"],
                    )
                return {
                    "preauth_row": preauth_row,
                    "event_row": event_row,
                    "duplicate_event": True,
                    "latest_state_updated": False,
                }

            preauth_row = await conn.fetchrow(
                """
                INSERT INTO preauth_logs (
                    org_id,
                    request_id,
                    patient_id,
                    raw_payload,
                    extracted_fields,
                    status
                )
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, 'pending')
                ON CONFLICT (request_id) DO UPDATE SET
                    org_id = EXCLUDED.org_id,
                    patient_id = EXCLUDED.patient_id,
                    raw_payload = EXCLUDED.raw_payload,
                    extracted_fields = EXCLUDED.extracted_fields,
                    received_at = NOW(),
                    status = 'pending',
                    agent_step = NULL,
                    decision = NULL,
                    agent_result = NULL,
                    error_message = NULL,
                    processed_at = NULL
                RETURNING id, request_id
                """,
                org_id,
                str(request_id),
                str(patient_id),
                _json_param(payload),
                _json_param(extracted_fields),
            )

            event_sequence = await conn.fetchval(
                """
                SELECT COALESCE(MAX(event_sequence), 0) + 1
                FROM preauth_events
                WHERE org_id = $1 AND checkin_id = $2
                """,
                org_id,
                str(checkin_id),
            )

            event_row = await conn.fetchrow(
                """
                INSERT INTO preauth_events (
                    org_id,
                    preauth_log_id,
                    event_id,
                    event_type,
                    correlation_id,
                    checkin_id,
                    request_id,
                    patient_id,
                    facility_name,
                    insurance_no,
                    policy_no,
                    plan_name,
                    event_sequence,
                    occurred_at,
                    submitted_at,
                    item_count,
                    total_requested_cost,
                    items_added_count,
                    items_added_total,
                    raw_payload,
                    extracted_fields,
                    payload_summary
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13, $14, $15,
                    $16, $17, $18, $19, $20::jsonb, $21::jsonb, $22::jsonb
                )
                RETURNING *
                """,
                org_id,
                preauth_row["id"],
                str(event_id),
                payload.get("event_type"),
                payload.get("correlation_id"),
                str(checkin_id),
                str(request_id),
                str(patient_id),
                payload_summary.get("facility_name"),
                payload_summary.get("insurance_no"),
                payload_summary.get("policy_no"),
                payload_summary.get("plan_name"),
                event_sequence,
                _parse_timestamp(payload.get("occurred_at")),
                _parse_timestamp(_nested(payload, "submission", "submitted_at")),
                len(items) if isinstance(items, list) else None,
                _to_float(extracted_fields.get("total_requested_cost")),
                len(items_added),
                _sum_requested_cost(items_added),
                _json_param(payload),
                _json_param(extracted_fields),
                _json_param(payload_summary),
            )

            return {
                "preauth_row": preauth_row,
                "event_row": event_row,
                "duplicate_event": False,
                "latest_state_updated": True,
            }
    finally:
        await conn.close()
