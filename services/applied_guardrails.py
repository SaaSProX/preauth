"""
Applied Mode Guardrails Service (SAA-61)

Determines whether a PA decision can be applied (enforced) vs advisory-only.
All decisions outside guardrails remain advisory regardless of applied_mode_enabled.
"""

from typing import Optional
from dataclasses import dataclass
from config.settings import settings


@dataclass
class GuardrailResult:
    """Result of guardrail check."""
    can_apply: bool
    mode: str  # 'applied' | 'advisory'
    reason: Optional[str] = None
    violations: list = None
    
    def __post_init__(self):
        if self.violations is None:
            self.violations = []


def check_guardrails(
    agent_decision: str,
    agent_confidence: str,
    agent_amount: Optional[float],
    total_requested: float,
    items: list,
    care_type: Optional[int],
    plan_name: Optional[str],
    utilization_pct: Optional[float] = None,
    eligibility_complete: bool = True,
) -> GuardrailResult:
    """
    Check if a PA decision can be applied or must remain advisory.
    
    Args:
        agent_decision: APPROVE, DENY, or ESCALATE
        agent_confidence: HIGH, MEDIUM, or LOW
        agent_amount: Amount approved by agent (for approvals)
        total_requested: Total requested amount for PA
        items: List of line items with category_id, requested_cost
        care_type: AMAN care type ID (1-7)
        plan_name: Plan name from payload
        utilization_pct: Enrollee's utilization as decimal (0.0-1.0)
        eligibility_complete: Whether eligibility data is complete
    
    Returns:
        GuardrailResult with can_apply, mode, reason, and violations
    """
    
    violations = []
    
    # Check 0: Is applied mode even enabled?
    if not settings.applied_mode_enabled:
        return GuardrailResult(
            can_apply=False,
            mode="advisory",
            reason="Applied mode is disabled globally",
            violations=["applied_mode_disabled"],
        )
    
    # Check 1: Confidence requirement
    if settings.applied_require_high_confidence:
        if (agent_confidence or "").upper() != "HIGH":
            violations.append(f"confidence_not_high:{agent_confidence}")
    
    # Check 2: Escalations are always advisory
    if (agent_decision or "").upper() == "ESCALATE":
        violations.append("decision_is_escalate")
    
    # Check 3: Eligibility completeness
    if not eligibility_complete:
        violations.append("eligibility_incomplete")
    
    # Check 4: Care type denylist
    denylist_caretypes = _parse_int_list(settings.applied_caretype_denylist)
    if care_type and care_type in denylist_caretypes:
        violations.append(f"caretype_denied:{care_type}")
    
    # Check 5: Plan denylist
    denylist_plans = [p.strip().lower() for p in settings.applied_plan_denylist.split(",") if p.strip()]
    if plan_name and plan_name.lower().strip() in denylist_plans:
        violations.append(f"plan_denied:{plan_name}")
    
    # Check 6: Utilization threshold
    if utilization_pct is not None and utilization_pct > settings.applied_utilization_threshold:
        violations.append(f"utilization_high:{utilization_pct:.0%}")
    
    # Check 7: Amount thresholds
    if total_requested > settings.applied_max_pa_amount:
        violations.append(f"pa_amount_exceeded:{total_requested}>{settings.applied_max_pa_amount}")
    
    # Check 8: Per-item thresholds and category checks
    allowlist_cats = _parse_int_list(settings.applied_category_allowlist)
    denylist_cats = _parse_int_list(settings.applied_category_denylist)
    
    for item in items:
        item_cost = float(item.get("requested_cost") or item.get("cost") or item.get("estimated_cost") or 0)
        item_cat = item.get("category_id")
        item_name = item.get("item_name") or item.get("name") or "unknown"
        
        # Item amount check
        if item_cost > settings.applied_max_item_amount:
            violations.append(f"item_amount_exceeded:{item_name}:{item_cost}>{settings.applied_max_item_amount}")
        
        # Category denylist check
        if item_cat and item_cat in denylist_cats:
            violations.append(f"item_category_denied:{item_name}:cat{item_cat}")
        
        # Category allowlist check (if not in allowlist, it's advisory)
        if item_cat and allowlist_cats and item_cat not in allowlist_cats:
            violations.append(f"item_category_not_allowed:{item_name}:cat{item_cat}")
        
        # Keyword checks for high-risk items
        if _has_surgery_keyword(item_name):
            violations.append(f"item_surgery_keyword:{item_name}")
    
    # Determine result
    if violations:
        return GuardrailResult(
            can_apply=False,
            mode="advisory",
            reason=f"Guardrail violations: {len(violations)}",
            violations=violations,
        )
    
    return GuardrailResult(
        can_apply=True,
        mode="applied",
        reason="All guardrails passed",
        violations=[],
    )


def _parse_int_list(s: str) -> list:
    """Parse comma-separated string to list of ints."""
    if not s:
        return []
    result = []
    for part in s.split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part))
    return result


def _has_surgery_keyword(item_name: str) -> bool:
    """Check if item name contains surgery-related keywords."""
    if not item_name:
        return False
    
    keywords = [
        "surgery", "surgical", "operation", "incision", "excision",
        "amputation", "transplant", "resection", "biopsy",
        "catheter", "dialysis", "chemotherapy", "radiotherapy",
        "admission", "ward", "bed", "icu", "intensive care",
        "c-section", "caesarean", "cesarean", "delivery", "labor", "labour",
    ]
    
    name_lower = item_name.lower()
    return any(kw in name_lower for kw in keywords)


def get_guardrail_config() -> dict:
    """Return current guardrail configuration for dashboard display."""
    return {
        "applied_mode_enabled": settings.applied_mode_enabled,
        "thresholds": {
            "max_item_amount": settings.applied_max_item_amount,
            "max_pa_amount": settings.applied_max_pa_amount,
            "utilization_threshold": settings.applied_utilization_threshold,
        },
        "allowlists": {
            "categories": _parse_int_list(settings.applied_category_allowlist),
        },
        "denylists": {
            "categories": _parse_int_list(settings.applied_category_denylist),
            "care_types": _parse_int_list(settings.applied_caretype_denylist),
            "plans": [p.strip() for p in settings.applied_plan_denylist.split(",") if p.strip()],
        },
        "require_high_confidence": settings.applied_require_high_confidence,
    }
