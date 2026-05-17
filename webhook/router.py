import json
import uuid

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


def extract_preauth_fields(payload: dict):
    return {
        "request_id": first_value(payload, ["request_id", "requestId", "id", "reference", "reference_id"]),
        "patient_id": first_value(payload, ["patient_id", "patientId", "patient.id", "member_id", "memberId", "enrollee_id"]),
        "plan": first_value(payload, ["plan", "plan_name", "planName", "plan.id", "coverage.plan"]),
        "eligibility": first_value(payload, ["eligibility", "eligibility_status", "eligibilityStatus", "eligible", "is_eligible"]),
        "utilization": first_value(payload, ["utilization", "usage", "benefit_usage", "benefitUsage", "limits.utilization"]),
        "items": first_value(payload, ["items", "requested_items", "requestedItems", "services", "procedures", "line_items"]),
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
