"""
PA Decision Comparison Service (SAA-53)

Compares agent decisions with AMAN's actual item decisions.
Used for QA accuracy tracking and mismatch review.
"""

from datetime import datetime, timedelta
from typing import Optional
from services.db import pg_query_all, pg_query_one


# AMAN item status codes (confirmed by Sakeenah 2026-06-01)
AMAN_STATUS = {
    0: "pending",
    1: "approved",
    2: "queried",
    3: "rejected",
}

# Mismatch categories for classification
MISMATCH_CATEGORIES = [
    "coverage",       # Agent/AMAN disagree on coverage rules
    "amount",         # Amount approved differs
    "eligibility",    # Eligibility interpretation differs
    "limits",         # Limit calculation differs
    "agent_over",     # Agent approved, AMAN rejected
    "aman_over",      # Agent rejected, AMAN approved
    "data_gap",       # Missing data caused agent to fail
    "timing",         # Decision timing/sequencing issue
]


async def get_comparison_records(
    org_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
    checkin_id: Optional[str] = None,
) -> dict:
    """
    Compare agent decisions with AMAN decisions for scored PAs.
    
    Returns records where:
    - Agent made a decision (agent_result is not null)
    - AMAN has processed items (pa_items with status != 0)
    
    Each record includes:
    - checkin_id, request_id
    - Agent decision (APPROVE/DENY/ESCALATE)
    - AMAN item-level decisions
    - Match status and delta
    """
    
    if date_from is None:
        date_from = datetime.utcnow() - timedelta(days=7)
    if date_to is None:
        date_to = datetime.utcnow()
    
    # Build query for PAs with both agent and AMAN decisions
    query = """
    WITH latest_events AS (
        -- Get the latest event for each checkin_id
        SELECT DISTINCT ON (checkin_id)
            id,
            checkin_id,
            request_id,
            patient_id,
            facility_name,
            plan_name,
            raw_payload,
            occurred_at
        FROM preauth_events
        WHERE org_id = $1
        ORDER BY checkin_id, occurred_at DESC, id DESC
    ),
    agent_decisions AS (
        -- Get agent decisions from preauth_logs
        SELECT
            p.id as log_id,
            p.request_id,
            p.patient_id,
            p.decision,
            p.agent_result,
            p.raw_payload as log_payload,
            p.received_at,
            p.processed_at,
            p.callback_mode
        FROM preauth_logs p
        WHERE p.org_id = $1
          AND p.agent_result IS NOT NULL
          AND p.decision IS NOT NULL
    )
    SELECT
        le.checkin_id,
        le.request_id as event_request_id,
        ad.request_id as log_request_id,
        le.patient_id,
        le.facility_name,
        le.plan_name,
        le.occurred_at as aman_occurred_at,
        ad.decision as agent_decision,
        ad.agent_result,
        ad.received_at as agent_received_at,
        ad.processed_at as agent_processed_at,
        ad.callback_mode,
        le.raw_payload as aman_payload
    FROM latest_events le
    JOIN agent_decisions ad ON (
        -- Match by checkin_id embedded in request_id or direct match
        le.checkin_id = ad.request_id 
        OR le.checkin_id = (ad.log_payload->>'checkin_id')
        OR le.request_id = ad.request_id
    )
    WHERE le.occurred_at >= $2
      AND le.occurred_at <= $3
    """
    
    params = [org_id, date_from, date_to]
    
    if checkin_id:
        query += " AND le.checkin_id = $4"
        params.append(checkin_id)
    
    query += f" ORDER BY le.occurred_at DESC LIMIT {limit} OFFSET {offset}"
    
    rows = await pg_query_all(query, *params)
    
    # Process each row to compute comparison metrics
    records = []
    totals = {
        "total": 0,
        "scored": 0,
        "matched": 0,
        "mismatched": 0,
        "pending_aman": 0,
        "agent_skipped": 0,
    }
    
    for row in rows:
        record = await _process_comparison_row(row)
        records.append(record)
        
        totals["total"] += 1
        if record["aman_status"] == "pending":
            totals["pending_aman"] += 1
        elif record["scored"]:
            totals["scored"] += 1
            if record["match_status"] == "match":
                totals["matched"] += 1
            else:
                totals["mismatched"] += 1
    
    return {
        "params": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "limit": limit,
            "offset": offset,
            "checkin_id": checkin_id,
        },
        "totals": totals,
        "accuracy": (
            round(totals["matched"] / totals["scored"] * 100, 2)
            if totals["scored"] > 0 else None
        ),
        "records": records,
    }


async def _process_comparison_row(row: dict) -> dict:
    """Process a single row to extract comparison metrics."""
    
    aman_payload = row.get("aman_payload") or {}
    agent_result = row.get("agent_result") or {}
    
    # Extract AMAN item decisions
    pa_items = aman_payload.get("pa_items") or []
    aman_items = []
    aman_approved = 0
    aman_rejected = 0
    aman_pending = 0
    aman_queried = 0
    aman_total_approved = 0.0
    aman_total_rejected = 0.0
    
    for item in pa_items:
        status_code = item.get("status")
        if isinstance(status_code, str):
            status_code = {"pending": 0, "approved": 1, "queried": 2, "rejected": 3}.get(status_code.lower(), 0)
        
        status = AMAN_STATUS.get(status_code, "unknown")
        cost = float(item.get("requested_cost") or item.get("cost") or 0)
        
        aman_items.append({
            "claim_item_id": item.get("claim_item_id"),
            "item_name": item.get("item_name"),
            "status": status,
            "status_code": status_code,
            "requested_cost": cost,
            "approved_cost": float(item.get("approved_cost") or 0),
        })
        
        if status == "approved":
            aman_approved += 1
            aman_total_approved += float(item.get("approved_cost") or cost)
        elif status == "rejected":
            aman_rejected += 1
            aman_total_rejected += cost
        elif status == "pending":
            aman_pending += 1
        elif status == "queried":
            aman_queried += 1
    
    # Determine AMAN's overall decision
    if aman_pending > 0 and aman_approved == 0 and aman_rejected == 0:
        aman_overall = "pending"
    elif aman_rejected > 0 and aman_approved == 0:
        aman_overall = "rejected"
    elif aman_approved > 0 and aman_rejected == 0:
        aman_overall = "approved"
    elif aman_approved > 0 and aman_rejected > 0:
        aman_overall = "partial"
    else:
        aman_overall = "pending"
    
    # Extract agent decision
    agent_decision = (row.get("agent_decision") or "").upper()
    agent_amount = agent_result.get("amount_approved")
    
    # Determine match status
    scored = aman_overall != "pending"
    match_status = "pending"
    mismatch_category = None
    
    if scored:
        # Compare decisions
        agent_approve = agent_decision == "APPROVE"
        aman_approve = aman_overall in ("approved", "partial")
        
        if agent_approve == aman_approve:
            # Decisions align - check amounts
            if agent_amount is not None and aman_total_approved > 0:
                amount_delta = abs(float(agent_amount) - aman_total_approved)
                tolerance = max(aman_total_approved * 0.05, 1000)  # 5% or ₦1000
                if amount_delta <= tolerance:
                    match_status = "match"
                else:
                    match_status = "amount_mismatch"
                    mismatch_category = "amount"
            else:
                match_status = "match"
        else:
            match_status = "decision_mismatch"
            if agent_approve and not aman_approve:
                mismatch_category = "agent_over"
            else:
                mismatch_category = "aman_over"
    
    return {
        "checkin_id": row.get("checkin_id"),
        "request_id": row.get("event_request_id") or row.get("log_request_id"),
        "patient_id": row.get("patient_id"),
        "facility_name": row.get("facility_name"),
        "plan_name": row.get("plan_name"),
        
        # Agent side
        "agent_decision": agent_decision,
        "agent_amount_approved": agent_amount,
        "agent_confidence": agent_result.get("confidence"),
        "agent_reasoning": agent_result.get("reasoning"),
        "agent_received_at": row.get("agent_received_at"),
        "agent_processed_at": row.get("agent_processed_at"),
        "callback_mode": row.get("callback_mode"),
        
        # AMAN side
        "aman_status": aman_overall,
        "aman_occurred_at": row.get("aman_occurred_at"),
        "aman_items": aman_items,
        "aman_item_counts": {
            "approved": aman_approved,
            "rejected": aman_rejected,
            "pending": aman_pending,
            "queried": aman_queried,
            "total": len(pa_items),
        },
        "aman_total_approved": aman_total_approved,
        "aman_total_rejected": aman_total_rejected,
        
        # Comparison
        "scored": scored,
        "match_status": match_status,
        "mismatch_category": mismatch_category,
        "amount_delta": (
            abs(float(agent_amount or 0) - aman_total_approved)
            if agent_amount is not None else None
        ),
    }


async def get_mismatch_summary(
    org_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Get summary of mismatches by category for reporting."""
    
    result = await get_comparison_records(
        org_id=org_id,
        date_from=date_from,
        date_to=date_to,
        limit=1000,
    )
    
    categories = {cat: 0 for cat in MISMATCH_CATEGORIES}
    
    for record in result["records"]:
        if record["mismatch_category"]:
            cat = record["mismatch_category"]
            if cat in categories:
                categories[cat] += 1
    
    return {
        "params": result["params"],
        "totals": result["totals"],
        "accuracy": result["accuracy"],
        "mismatch_breakdown": [
            {"category": cat, "count": count}
            for cat, count in sorted(categories.items(), key=lambda x: -x[1])
            if count > 0
        ],
    }


async def score_single_pa(org_id: int, checkin_id: str) -> dict:
    """Score a single PA by checkin_id."""
    
    result = await get_comparison_records(
        org_id=org_id,
        checkin_id=checkin_id,
        limit=1,
    )
    
    if result["records"]:
        return {
            "found": True,
            "record": result["records"][0],
        }
    
    return {
        "found": False,
        "checkin_id": checkin_id,
        "message": "No matching PA with agent decision found",
    }
