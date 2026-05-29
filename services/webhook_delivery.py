import json
import logging
import uuid

from services.db import pg_execute


logger = logging.getLogger(__name__)


WEBHOOK_LOG_UPDATE_FIELDS = {
    "org_id",
    "api_client_id",
    "event_id",
    "event_type",
    "correlation_id",
    "checkin_id",
    "facility_name",
    "insurance_no",
    "policy_no",
    "plan_name",
    "auth_status",
    "payload_received",
    "payload_valid",
    "payload_status",
    "payload_size_bytes",
    "payload_summary",
    "db_insert_status",
    "preauth_request_id",
    "preauth_log_id",
    "preauth_event_id",
    "http_status_returned",
    "final_status",
    "error_message",
    "processing_time_ms",
}


def mask_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 12:
        return "****"
    return f"{api_key[:8]}...{api_key[-4:]}"


def json_param(value):
    if value is None:
        return None
    return json.dumps(value)


async def create_webhook_delivery_log(
    *,
    provider: str,
    request_method: str,
    request_path: str,
    request_ip: str | None,
    user_agent: str | None,
    api_key_hint: str | None,
) -> str:
    delivery_id = str(uuid.uuid4())
    try:
        await pg_execute(
            """
            INSERT INTO webhook_delivery_logs (
                delivery_id,
                provider,
                request_method,
                request_path,
                request_ip,
                user_agent,
                api_key_hint
            )
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
            """,
            delivery_id,
            provider,
            request_method,
            request_path,
            request_ip,
            user_agent,
            api_key_hint,
        )
    except Exception:
        logger.exception("Failed to create webhook delivery log")
    return delivery_id


async def update_webhook_delivery_log(delivery_id: str | None, **fields):
    if not delivery_id:
        return

    safe_fields = {
        key: value
        for key, value in fields.items()
        if key in WEBHOOK_LOG_UPDATE_FIELDS
    }
    if not safe_fields:
        return

    assignments = []
    values = []
    for index, (key, value) in enumerate(safe_fields.items(), start=1):
        if key == "payload_summary":
            assignments.append(f"{key} = ${index}::jsonb")
            values.append(json_param(value))
        else:
            assignments.append(f"{key} = ${index}")
            values.append(value)

    delivery_id_param = len(values) + 1
    sql = f"""
        UPDATE webhook_delivery_logs
        SET {", ".join(assignments)}, updated_at = NOW()
        WHERE delivery_id = ${delivery_id_param}::uuid
    """

    try:
        await pg_execute(sql, *values, delivery_id)
    except Exception:
        logger.exception("Failed to update webhook delivery log")
