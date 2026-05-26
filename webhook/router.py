import json
import logging
import time
import uuid
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from services.db import pg_query_one
from services.webhook_delivery import (
    create_webhook_delivery_log,
    mask_api_key,
    update_webhook_delivery_log,
)
from agent import agent

router = APIRouter()
logger = logging.getLogger(__name__)


def get_nested(payload: dict, path: str):
    value = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def first_value(payload: dict, paths: list[str]):
    for path in paths:
        value = get_nested(payload, path)
        if value not in (None, ""):
            return value
    return None


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def get_request_ip(request: Request):
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else None


def get_webhook_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key

    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()

    return None


async def authenticate_webhook_request(request: Request):
    api_key = get_webhook_api_key(request)
    if not api_key:
        return None, "missing_api_key", "Missing API key"

    client = await pg_query_one(
        "SELECT * FROM api_clients WHERE api_key = $1 AND is_active = TRUE",
        api_key,
    )
    if not client:
        return None, "invalid_api_key", "Invalid API key"

    return client, "auth_success", None


def summarize_payload(payload: dict):
    pa_items = payload.get("pa_items")
    return {
        "event_id": payload.get("event_id"),
        "event_type": payload.get("event_type"),
        "correlation_id": payload.get("correlation_id"),
        "checkin_id": get_nested(payload, "encounter.checkin_id"),
        "facility_name": get_nested(payload, "encounter.facility_name"),
        "insurance_no": get_nested(payload, "enrollee.insurance_no"),
        "policy_no": get_nested(payload, "policy.policy_no"),
        "plan_name": get_nested(payload, "policy.plan_name"),
        "item_count": len(pa_items) if isinstance(pa_items, list) else None,
    }


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None

    normalized = value.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            if fmt:
                return datetime.strptime(normalized, fmt).date()
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            continue
    return None


def calculate_age(date_of_birth):
    born = parse_date(date_of_birth)
    if not born:
        return None

    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def normalize_status(value):
    if isinstance(value, bool):
        return "active" if value else "inactive"
    if isinstance(value, int):
        return "active" if value == 1 else "inactive"
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "active", "enabled", "valid", "allowed"}:
            return "active"
        if cleaned in {"0", "inactive", "disabled", "expired", "rejected", "blocked"}:
            return "inactive"
    return value


def normalize_plan_tier(plan_name):
    if not isinstance(plan_name, str):
        return plan_name

    cleaned = plan_name.lower()
    if "platinum plus" in cleaned:
        return "Platinum Plus"
    if "platinum" in cleaned:
        return "Platinum"
    if "gold" in cleaned:
        return "Gold"
    if "silver" in cleaned:
        return "Silver"
    if "bronze" in cleaned:
        return "Bronze"
    return plan_name


def normalize_item_type(category_id):
    categories = {
        1: "medication",
        2: "consultation",
        3: "laboratory",
    }
    try:
        return categories.get(int(category_id), "service")
    except (TypeError, ValueError):
        return "service"


def normalize_items(raw_items):
    if not isinstance(raw_items, list):
        return raw_items

    normalized = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        quantity = item.get("quantity") or 1
        unit_cost = item.get("unit_cost")
        requested_cost = item.get("requested_cost")
        if requested_cost is None and unit_cost is not None:
            try:
                requested_cost = float(unit_cost) * float(quantity)
            except (TypeError, ValueError):
                requested_cost = unit_cost

        item_id = (
            item.get("claim_item_id")
            or item.get("facility_tariff_item_id")
            or item.get("id")
        )
        description = item.get("item_name") or item.get("description") or str(item_id)

        normalized.append({
            **item,
            "id": item_id,
            "type": item.get("type") or normalize_item_type(item.get("category_id")),
            "description": description,
            "name": description,
            "estimated_cost": requested_cost,
            "requested_cost": requested_cost,
            "quantity": quantity,
        })
    return normalized


def build_aman_eligibility(payload: dict):
    enrollee = payload.get("enrollee") if isinstance(payload.get("enrollee"), dict) else {}
    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
    consumption = payload.get("consumption") if isinstance(payload.get("consumption"), dict) else {}
    cycle = consumption.get("cycle") if isinstance(consumption.get("cycle"), dict) else {}

    enrollee_status = normalize_status(enrollee.get("status"))
    policy_status = normalize_status(policy.get("policy_status"))

    status = "active"
    if "inactive" in {enrollee_status, policy_status}:
        status = "inactive"

    return {
        "status": status,
        "enrollment_date": cycle.get("start"),
        "expiry_date": cycle.get("end"),
        "age": calculate_age(enrollee.get("date_of_birth")),
        "date_of_birth": enrollee.get("date_of_birth"),
        "relationship": enrollee.get("relationship"),
        "enrollee_status": enrollee_status,
        "policy_status": policy_status,
        "proposed_impact_status": get_nested(payload, "proposed_impact.status"),
    }


def build_aman_utilization(payload: dict):
    consumption = payload.get("consumption") if isinstance(payload.get("consumption"), dict) else {}
    enrollee_limits = consumption.get("enrollee_limits") or []
    policy_limits = consumption.get("policy_limits") or []

    return {
        "cycle": consumption.get("cycle") or {},
        "enrollee_limits": enrollee_limits,
        "policy_limits": policy_limits,
        "proposed_impact": payload.get("proposed_impact") or {},
        "utilization_data_missing": not bool(enrollee_limits or policy_limits),
    }


def extract_preauth_fields(payload: dict):
    plan_name = first_value(payload, [
        "plan",
        "plan_name",
        "planName",
        "plan.id",
        "coverage.plan",
        "policy.plan_name",
        "policy.insurance_package",
    ])
    raw_items = first_value(payload, [
        "items",
        "requested_items",
        "requestedItems",
        "services",
        "procedures",
        "line_items",
        "pa_items",
        "submission.items_added",
    ])
    items = normalize_items(raw_items)
    total_requested_cost = None
    if isinstance(items, list):
        total_requested_cost = sum(
            float(item.get("estimated_cost") or 0)
            for item in items
            if isinstance(item, dict)
        )

    return {
        "request_id": first_value(payload, [
            "request_id",
            "requestId",
            "id",
            "reference",
            "reference_id",
            "encounter.checkin_id",
            "event_id",
        ]),
        "patient_id": first_value(payload, [
            "patient_id",
            "patientId",
            "patient.id",
            "member_id",
            "memberId",
            "enrollee_id",
            "enrollee.insurance_no",
        ]),
        "plan": normalize_plan_tier(plan_name),
        "plan_name": plan_name,
        "eligibility": first_value(payload, [
            "eligibility",
            "eligibility_status",
            "eligibilityStatus",
            "eligible",
            "is_eligible",
        ]) or build_aman_eligibility(payload),
        "utilization": first_value(payload, [
            "utilization",
            "usage",
            "benefit_usage",
            "benefitUsage",
            "limits.utilization",
        ]) or build_aman_utilization(payload),
        "items": items,
        "requested_items": items,
        "total_requested_cost": total_requested_cost,
        "event_type": payload.get("event_type"),
        "event_id": payload.get("event_id"),
        "occurred_at": payload.get("occurred_at"),
        "checkin_id": get_nested(payload, "encounter.checkin_id"),
        "checkin_date": get_nested(payload, "encounter.checkin_date"),
        "checkin_type": get_nested(payload, "encounter.checkin_type"),
        "facility_id": get_nested(payload, "encounter.facility_id"),
        "facility": get_nested(payload, "encounter.facility_name"),
        "diagnosis": get_nested(payload, "encounter.diagnosis"),
        "care_category": get_nested(payload, "encounter.care_category"),
        "care_type": get_nested(payload, "encounter.care_type"),
        "item_counts": get_nested(payload, "encounter.item_counts"),
        "submitted_at": get_nested(payload, "submission.submitted_at"),
        "submitted_by": get_nested(payload, "submission.submitted_by"),
        "policy_no": get_nested(payload, "policy.policy_no"),
        "plan_id": get_nested(payload, "policy.plan_id"),
        "corporation_name": get_nested(payload, "policy.corporation_name"),
        "enforcement_mode": get_nested(payload, "policy.enforcement_mode"),
        "proposed_impact": payload.get("proposed_impact"),
        "meta": payload.get("meta"),
    }


@router.post("/webhook/preauth")
async def receive_preauth(
    request: Request,
    background: BackgroundTasks,
):
    started_at = time.perf_counter()
    api_key = get_webhook_api_key(request)
    delivery_id = await create_webhook_delivery_log(
        provider="aman",
        request_method=request.method,
        request_path=str(request.url.path),
        request_ip=get_request_ip(request),
        user_agent=request.headers.get("User-Agent"),
        api_key_hint=mask_api_key(api_key),
    )

    try:
        try:
            client, auth_status, auth_error = await authenticate_webhook_request(request)
        except Exception as exc:
            await update_webhook_delivery_log(
                delivery_id,
                auth_status="auth_error",
                final_status="auth_error",
                http_status_returned=500,
                error_message=str(exc),
                processing_time_ms=elapsed_ms(started_at),
            )
            logger.exception("Webhook auth check failed")
            raise HTTPException(status_code=500, detail="Webhook authentication failed")

        if not client:
            await update_webhook_delivery_log(
                delivery_id,
                auth_status=auth_status,
                final_status=auth_status,
                http_status_returned=401,
                error_message=auth_error,
                processing_time_ms=elapsed_ms(started_at),
            )
            raise HTTPException(status_code=401, detail=auth_error)

        await update_webhook_delivery_log(
            delivery_id,
            org_id=client["org_id"],
            api_client_id=client["id"],
            auth_status=auth_status,
        )

        body = await request.body()
        payload_size_bytes = len(body)
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await update_webhook_delivery_log(
                delivery_id,
                payload_received=payload_size_bytes > 0,
                payload_valid=False,
                payload_status="invalid_json",
                payload_size_bytes=payload_size_bytes,
                final_status="invalid_payload",
                http_status_returned=400,
                error_message="Webhook body must be valid JSON",
                processing_time_ms=elapsed_ms(started_at),
            )
            raise HTTPException(status_code=400, detail="Webhook body must be valid JSON")

        if not isinstance(payload, dict):
            await update_webhook_delivery_log(
                delivery_id,
                payload_received=True,
                payload_valid=False,
                payload_status="not_json_object",
                payload_size_bytes=payload_size_bytes,
                final_status="invalid_payload",
                http_status_returned=400,
                error_message="Webhook body must be a JSON object",
                processing_time_ms=elapsed_ms(started_at),
            )
            raise HTTPException(status_code=400, detail="Webhook body must be a JSON object")

        payload_summary = summarize_payload(payload)
        await update_webhook_delivery_log(
            delivery_id,
            event_id=payload.get("event_id"),
            event_type=payload.get("event_type"),
            correlation_id=payload.get("correlation_id"),
            checkin_id=get_nested(payload, "encounter.checkin_id"),
            facility_name=payload_summary.get("facility_name"),
            insurance_no=payload_summary.get("insurance_no"),
            policy_no=payload_summary.get("policy_no"),
            plan_name=payload_summary.get("plan_name"),
            payload_received=True,
            payload_valid=True,
            payload_status="payload_valid",
            payload_size_bytes=payload_size_bytes,
            payload_summary=payload_summary,
        )

        extracted_fields = extract_preauth_fields(payload)
        request_id = extracted_fields["request_id"] or f"intake-{uuid.uuid4().hex}"
        patient_id = extracted_fields["patient_id"] or "unknown"
        missing_recommended_fields = [
            field for field in ["patient_id", "plan", "eligibility", "utilization", "items"]
            if not extracted_fields.get(field)
        ]

        # Save incoming webhook immediately
        try:
            preauth_row = await pg_query_one(
                """
                INSERT INTO preauth_logs (org_id, request_id, patient_id, raw_payload, extracted_fields, status)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, 'pending')
                ON CONFLICT (request_id) DO UPDATE SET
                    raw_payload = EXCLUDED.raw_payload,
                    extracted_fields = EXCLUDED.extracted_fields,
                    received_at = NOW(),
                    status = 'pending'
                RETURNING id
                """,
                client["org_id"],
                str(request_id),
                str(patient_id),
                json.dumps(payload),
                json.dumps(extracted_fields)
            )
        except Exception as exc:
            await update_webhook_delivery_log(
                delivery_id,
                db_insert_status="db_insert_failed",
                preauth_request_id=str(request_id),
                final_status="db_insert_failed",
                http_status_returned=500,
                error_message=str(exc),
                processing_time_ms=elapsed_ms(started_at),
            )
            logger.exception("Failed to persist preauth webhook payload")
            raise HTTPException(status_code=500, detail="Failed to persist webhook payload")

        await update_webhook_delivery_log(
            delivery_id,
            db_insert_status="db_upsert_success",
            preauth_request_id=str(request_id),
            preauth_log_id=preauth_row["id"] if preauth_row else None,
            final_status="accepted",
            http_status_returned=200,
            processing_time_ms=elapsed_ms(started_at),
        )

        # Kick off agent in background
        # background.add_task(agent.run, str(patient_id), str(request_id))
        return {
            "status": "received",
            "request_id": str(request_id),
            "captured_fields": extracted_fields,
            "missing_recommended_fields": missing_recommended_fields,
        }
    except HTTPException:
        raise
    except Exception as exc:
        await update_webhook_delivery_log(
            delivery_id,
            final_status="failed",
            http_status_returned=500,
            error_message=str(exc),
            processing_time_ms=elapsed_ms(started_at),
        )
        logger.exception("Unhandled webhook processing error")
        raise
