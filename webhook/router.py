import json
import uuid
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from middleware.auth import verify_api_key
from services.db import pg_execute
from agent import agent

router = APIRouter()


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
    client=Depends(verify_api_key)
):
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook body must be a JSON object")

    extracted_fields = extract_preauth_fields(payload)
    request_id = extracted_fields["request_id"] or f"intake-{uuid.uuid4().hex}"
    patient_id = extracted_fields["patient_id"] or "unknown"
    missing_recommended_fields = [
        field for field in ["patient_id", "plan", "eligibility", "utilization", "items"]
        if not extracted_fields.get(field)
    ]

    # Save incoming webhook immediately
    await pg_execute(
        """
        INSERT INTO preauth_logs (org_id, request_id, patient_id, raw_payload, extracted_fields, status)
        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, 'pending')
        ON CONFLICT (request_id) DO UPDATE SET
            raw_payload = EXCLUDED.raw_payload,
            extracted_fields = EXCLUDED.extracted_fields,
            received_at = NOW(),
            status = 'pending'
        """,
        client["org_id"],
        str(request_id),
        str(patient_id),
        json.dumps(payload),
        json.dumps(extracted_fields)
    )

    # Kick off agent in background
    background.add_task(agent.run, patient_id, request_id)
    return {
        "status": "received",
        "request_id": str(request_id),
        "captured_fields": extracted_fields,
        "missing_recommended_fields": missing_recommended_fields,
    }
