"""
Mismatch Review Workflow Service (SAA-54)

Allows admins to classify agent/AMAN disagreements by cause,
capture reviewer notes, and track fix status.
"""

from datetime import datetime, timezone
from typing import Optional
from services.db import pg_query_all, pg_query_one, pg_execute


# Mismatch cause categories
CAUSE_CATEGORIES = [
    "rule_gap",           # Agent missing a plan rule
    "missing_data",       # Payload missing required data
    "plan_ambiguity",     # Plan rules unclear/ambiguous
    "pricing",            # Cost/pricing discrepancy
    "clinical",           # Clinical judgment needed
    "aman_override",      # AMAN made exception/override
    "other",              # Uncategorized
]

# Fix statuses
FIX_STATUSES = [
    "open",               # Not yet reviewed
    "in_progress",        # Being worked on
    "fixed",              # Rule/code updated
    "accepted_difference",# Known acceptable difference
    "wont_fix",           # Won't be addressed
]


async def create_review(
    org_id: int,
    request_id: str,
    checkin_id: Optional[str],
    reviewer_id: int,
    reviewer_email: str,
    mismatch_type: str,
    cause_category: str,
    agent_decision: Optional[str] = None,
    agent_amount: Optional[float] = None,
    aman_decision: Optional[str] = None,
    aman_amount: Optional[float] = None,
    notes: Optional[str] = None,
    follow_up_action: Optional[str] = None,
) -> dict:
    """Create a new mismatch review record."""
    
    if cause_category not in CAUSE_CATEGORIES:
        raise ValueError(f"Invalid cause_category. Must be one of: {CAUSE_CATEGORIES}")
    
    amount_delta = None
    if agent_amount is not None and aman_amount is not None:
        amount_delta = abs(agent_amount - aman_amount)
    
    row = await pg_query_one(
        """
        INSERT INTO mismatch_reviews (
            org_id, request_id, checkin_id, reviewer_id, reviewer_email,
            mismatch_type, cause_category,
            agent_decision, agent_amount, aman_decision, aman_amount, amount_delta,
            notes, follow_up_action, fix_status
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 'open'
        )
        RETURNING id, created_at
        """,
        org_id, request_id, checkin_id, reviewer_id, reviewer_email,
        mismatch_type, cause_category,
        agent_decision, agent_amount, aman_decision, aman_amount, amount_delta,
        notes, follow_up_action
    )
    
    return {
        "id": row["id"],
        "request_id": request_id,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "status": "created",
    }


async def update_review(
    review_id: int,
    org_id: int,
    cause_category: Optional[str] = None,
    notes: Optional[str] = None,
    follow_up_action: Optional[str] = None,
    fix_status: Optional[str] = None,
) -> dict:
    """Update an existing mismatch review."""
    
    if cause_category and cause_category not in CAUSE_CATEGORIES:
        raise ValueError(f"Invalid cause_category. Must be one of: {CAUSE_CATEGORIES}")
    
    if fix_status and fix_status not in FIX_STATUSES:
        raise ValueError(f"Invalid fix_status. Must be one of: {FIX_STATUSES}")
    
    # Build dynamic update
    updates = ["updated_at = NOW()"]
    params = []
    param_idx = 1
    
    if cause_category:
        updates.append(f"cause_category = ${param_idx}")
        params.append(cause_category)
        param_idx += 1
    
    if notes is not None:
        updates.append(f"notes = ${param_idx}")
        params.append(notes)
        param_idx += 1
    
    if follow_up_action is not None:
        updates.append(f"follow_up_action = ${param_idx}")
        params.append(follow_up_action)
        param_idx += 1
    
    if fix_status:
        updates.append(f"fix_status = ${param_idx}")
        params.append(fix_status)
        param_idx += 1
        
        if fix_status in ("fixed", "accepted_difference", "wont_fix"):
            updates.append("resolved_at = NOW()")
    
    params.append(review_id)
    params.append(org_id)
    
    query = f"""
        UPDATE mismatch_reviews
        SET {', '.join(updates)}
        WHERE id = ${param_idx} AND org_id = ${param_idx + 1}
        RETURNING id, fix_status, updated_at, resolved_at
    """
    
    row = await pg_query_one(query, *params)
    
    if not row:
        raise ValueError("Review not found or access denied")
    
    return {
        "id": row["id"],
        "fix_status": row["fix_status"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
    }


async def get_reviews(
    org_id: int,
    fix_status: Optional[str] = None,
    cause_category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Get mismatch reviews with optional filters."""
    
    conditions = ["org_id = $1"]
    params = [org_id]
    param_idx = 2
    
    if fix_status:
        conditions.append(f"fix_status = ${param_idx}")
        params.append(fix_status)
        param_idx += 1
    
    if cause_category:
        conditions.append(f"cause_category = ${param_idx}")
        params.append(cause_category)
        param_idx += 1
    
    where_clause = " AND ".join(conditions)
    
    # Get total count
    count_row = await pg_query_one(
        f"SELECT COUNT(*) as total FROM mismatch_reviews WHERE {where_clause}",
        *params
    )
    total = count_row["total"] if count_row else 0
    
    # Get records
    rows = await pg_query_all(
        f"""
        SELECT 
            id, request_id, checkin_id, reviewer_email,
            mismatch_type, cause_category,
            agent_decision, agent_amount, aman_decision, aman_amount, amount_delta,
            notes, follow_up_action, fix_status,
            created_at, updated_at, resolved_at
        FROM mismatch_reviews
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT {limit} OFFSET {offset}
        """,
        *params
    )
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "reviews": [
            {
                "id": r["id"],
                "request_id": r["request_id"],
                "checkin_id": r["checkin_id"],
                "reviewer_email": r["reviewer_email"],
                "mismatch_type": r["mismatch_type"],
                "cause_category": r["cause_category"],
                "agent_decision": r["agent_decision"],
                "agent_amount": float(r["agent_amount"]) if r["agent_amount"] else None,
                "aman_decision": r["aman_decision"],
                "aman_amount": float(r["aman_amount"]) if r["aman_amount"] else None,
                "amount_delta": float(r["amount_delta"]) if r["amount_delta"] else None,
                "notes": r["notes"],
                "follow_up_action": r["follow_up_action"],
                "fix_status": r["fix_status"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
            }
            for r in rows
        ],
    }


async def get_review_summary(org_id: int) -> dict:
    """Get summary stats for weekly reporting."""
    
    rows = await pg_query_all(
        """
        SELECT 
            cause_category,
            fix_status,
            COUNT(*) as count
        FROM mismatch_reviews
        WHERE org_id = $1
        GROUP BY cause_category, fix_status
        """,
        org_id
    )
    
    by_cause = {}
    by_status = {}
    total = 0
    
    for r in rows:
        cause = r["cause_category"] or "uncategorized"
        status = r["fix_status"] or "open"
        count = r["count"]
        total += count
        
        by_cause[cause] = by_cause.get(cause, 0) + count
        by_status[status] = by_status.get(status, 0) + count
    
    return {
        "total": total,
        "by_cause": [
            {"category": k, "count": v}
            for k, v in sorted(by_cause.items(), key=lambda x: -x[1])
        ],
        "by_status": [
            {"status": k, "count": v}
            for k, v in sorted(by_status.items(), key=lambda x: -x[1])
        ],
        "open_count": by_status.get("open", 0) + by_status.get("in_progress", 0),
        "resolved_count": (
            by_status.get("fixed", 0) + 
            by_status.get("accepted_difference", 0) + 
            by_status.get("wont_fix", 0)
        ),
    }
