import asyncio
import json
import re
from datetime import date
from anthropic import AsyncAnthropic
from config.settings import settings
from config.logging import get_logger, bind_contextvars
from config.sentry import set_sentry_context
from services.db import pg_execute, pg_query_one
from agent.knowledge_bases.registry import (
    DEFAULT_PLAN_CODE,
    get_knowledge_base,
    get_knowledge_base_by_plan_code,
    get_plan_limits,
    get_plan_limits_by_plan_code,
)
from agent.knowledge_bases.routing import resolve_plan_code, resolve_plan_context

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

logger = get_logger(__name__)

TODAY = date.today().isoformat()
ANTHROPIC_INPUT_USD_PER_1M = 3.0
ANTHROPIC_OUTPUT_USD_PER_1M = 15.0
ANTHROPIC_TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504, 529}
ANTHROPIC_RETRY_DELAYS_SECONDS = (1, 3, 6)


class AgentJSONParseError(ValueError):
    def __init__(self, message: str, raw_output: str, model_usage: dict | None = None):
        super().__init__(message)
        self.raw_output = raw_output
        self.model_usage = model_usage

# ---------------------------------------------------------------------------
# KNOWLEDGE BASE SELECTION
# ---------------------------------------------------------------------------
KNOWLEDGE_BASE = get_knowledge_base(DEFAULT_PLAN_CODE, today=TODAY)
PLAN_LIMITS = get_plan_limits(DEFAULT_PLAN_CODE)
KNOWLEDGE_BASE_BY_PLAN_CODE = get_knowledge_base_by_plan_code(today=TODAY)
PLAN_LIMITS_BY_PLAN_CODE = get_plan_limits_by_plan_code()


def _plan_code_for_request(pa: dict | None = None) -> str:
    return resolve_plan_code(pa)


def _knowledge_base_context_for_request(pa: dict | None = None) -> dict:
    return resolve_plan_context(pa)


def _knowledge_base_for_request(pa: dict) -> str:
    return get_knowledge_base(_plan_code_for_request(pa), today=TODAY)


def _plan_limits_for_request(pa: dict) -> dict:
    return get_plan_limits(_plan_code_for_request(pa))


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
CARE_TYPE_BUCKETS = {
    1: ("inpatient", "Inpatient Limit"),
    2: ("outpatient", "Outpatient Limit"),
    3: ("maternity", "Antenatal/Maternity"),
    4: ("dental", "Dental Care Limit"),
    5: ("optical_total", "Optical Total Limit"),
    6: ("outpatient", "Outpatient Limit"),
    7: ("wellness", "Wellness"),
}

CATEGORY_BUCKET_OVERRIDES = {
    5: ("dental", "Dental Care Limit"),
    6: ("optical_total", "Optical Total Limit"),
    8: ("wellness", "Wellness"),
}

AMAN_LIMIT_DEFINITION_BUCKETS = {
    # AMAN limit_definition_id values are tier-specific. Keep this map limited
    # to IDs we have observed consistently for core inpatient/outpatient rows;
    # other benefit rows can share amounts across unrelated buckets.
    1: "inpatient",
    2: "outpatient",
    6: "inpatient",
    7: "outpatient",
    11: "inpatient",
    12: "outpatient",
}


def _strip_json_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
    return text


def _extract_json_object(raw: str) -> str:
    text = _strip_json_fences(raw)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _parse_agent_json(raw: str) -> dict:
    text = _extract_json_object(raw)
    candidates = [
        text,
        re.sub(r",\s*([}\]])", r"\1", text),
    ]
    last_error = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
            if isinstance(parsed, dict):
                return parsed
            raise AgentJSONParseError("Agent response JSON was not an object", raw)
        except json.JSONDecodeError as exc:
            last_error = exc
    raise AgentJSONParseError(str(last_error), raw) from last_error


def _anthropic_usage_payload(response) -> dict:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_creation_input_tokens = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read_input_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    estimated_cost_usd = (
        (input_tokens / 1_000_000) * ANTHROPIC_INPUT_USD_PER_1M
        + (output_tokens / 1_000_000) * ANTHROPIC_OUTPUT_USD_PER_1M
    )
    return {
        "provider": "anthropic",
        "model": settings.anthropic_model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "pricing": {
            "input_usd_per_1m": ANTHROPIC_INPUT_USD_PER_1M,
            "output_usd_per_1m": ANTHROPIC_OUTPUT_USD_PER_1M,
        },
    }


def _combine_model_usage(*usages) -> dict | None:
    valid_usages = [usage for usage in usages if isinstance(usage, dict)]
    if not valid_usages:
        return None

    combined = dict(valid_usages[-1])
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "total_tokens",
    ):
        combined[key] = sum(int(usage.get(key) or 0) for usage in valid_usages)
    combined["estimated_cost_usd"] = sum(
        float(usage.get("estimated_cost_usd") or 0) for usage in valid_usages
    )
    combined["api_attempts"] = sum(int(usage.get("api_attempts") or 1) for usage in valid_usages)
    combined["api_retries"] = sum(int(usage.get("api_retries") or 0) for usage in valid_usages)
    combined["attempts"] = len(valid_usages)
    combined["attempt_usages"] = valid_usages
    return combined


def _anthropic_error_type(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("type") or "")
        return str(body.get("type") or "")
    return ""


def _is_transient_anthropic_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in ANTHROPIC_TRANSIENT_STATUS_CODES:
        return True

    error_type = _anthropic_error_type(exc).lower()
    if error_type in {"overloaded_error", "rate_limit_error", "api_error"}:
        return True

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "overloaded_error",
            "rate_limit_error",
            "temporarily unavailable",
            "timeout",
            "timed out",
        )
    )


async def _call_claude(system_prompt: str, user_message: str, *, max_tokens: int = 1000) -> dict:
    """Call Claude API and return parsed JSON response."""
    response = None
    attempt = 1
    for attempt, delay in enumerate((*ANTHROPIC_RETRY_DELAYS_SECONDS, None), start=1):
        try:
            response = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            break
        except Exception as exc:
            if delay is None or not _is_transient_anthropic_error(exc):
                raise
            logger.warning(
                "claude_transient_error",
                attempt=attempt,
                max_attempts=len(ANTHROPIC_RETRY_DELAYS_SECONDS) + 1,
                delay_seconds=delay,
                error=str(exc),
            )
            await asyncio.sleep(delay)

    if response is None:
        raise RuntimeError("Claude API did not return a response")

    raw = response.content[0].text.strip()
    usage_payload = _anthropic_usage_payload(response)
    usage_payload["api_attempts"] = attempt
    usage_payload["api_retries"] = attempt - 1
    try:
        parsed = _parse_agent_json(raw)
    except AgentJSONParseError as exc:
        exc.model_usage = usage_payload
        raise
    parsed["__model_usage"] = usage_payload
    return parsed


def _parse_json_field(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _plan_tier(pa: dict) -> str | None:
    plan = str(pa.get("plan") or pa.get("plan_name") or "").lower()
    if "platinum plus" in plan:
        return "Platinum Plus"
    if "platinum" in plan:
        return "Platinum"
    if "gold" in plan:
        return "Gold"
    if "silver" in plan:
        return "Silver"
    if "bronze" in plan:
        return "Bronze"
    if "basic" in plan:
        return "Basic"
    return None


def _items(pa: dict) -> list[dict]:
    raw = _parse_json_field(pa.get("items") or pa.get("requested_items") or [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _items_added(pa: dict) -> list[dict]:
    raw = _parse_json_field(pa.get("items_added") or pa.get("submission_items_added") or [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _item_id(item: dict):
    return item.get("claim_item_id") or item.get("id") or item.get("facility_tariff_item_id")


def _item_name(item: dict) -> str:
    return str(item.get("name") or item.get("item_name") or item.get("description") or _item_id(item) or "Unknown item")


def _item_cost(item: dict) -> float:
    amount = _number(item.get("estimated_cost")) or _number(item.get("requested_cost")) or _number(item.get("cost")) or _number(item.get("amount"))
    if amount is not None:
        return amount

    unit_cost = _number(item.get("unit_cost"))
    quantity = _number(item.get("quantity")) or 1
    return float(unit_cost * quantity) if unit_cost is not None else 0.0


def _item_approved_cost(item: dict) -> float:
    amount = _number(item.get("approved_cost"))
    if amount is not None and amount > 0:
        return amount

    unit_cost = _number(item.get("unit_approved_cost"))
    quantity = _number(item.get("quantity")) or 1
    if unit_cost is not None and unit_cost > 0:
        return float(unit_cost * quantity)

    return _item_cost(item)


def _item_status(item: dict) -> str:
    value = item.get("item_status_label", item.get("status"))
    if isinstance(value, str):
        return value.strip().lower()
    labels = {0: "pending", 1: "approved", 2: "queried", 3: "rejected"}
    try:
        return labels.get(int(value), "unknown")
    except (TypeError, ValueError):
        return "unknown"


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


def _current_submission_items(pa: dict) -> list[dict]:
    """Return pending pa_items introduced by the current AMAN submission.

    AMAN sends the whole check-in snapshot on every event. Older approved or
    rejected lines are context; advisory decisions should be generated only for
    the current submission's pending lines.
    """
    items = _items(pa)
    pending = [item for item in items if _item_status(item) == "pending"]
    added = _items_added(pa)
    if not added:
        return pending

    matched: list[dict] = []
    used_indexes: set[int] = set()

    def add_match(index: int):
        if index in used_indexes:
            return
        used_indexes.add(index)
        matched.append(pending[index])

    for added_item in added:
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
                and item.get("claim_item_id") is not None
                and str(item.get("claim_item_id")) == str(added_id)
            ):
                add_match(index)
                break

    for added_item in added:
        # AMAN appends newly-added pa_items after existing lines in the
        # snapshot. If direct IDs are missing and two pending lines look
        # identical, prefer the latest matching line.
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

    return matched or pending


def _item_identity(item: dict) -> tuple:
    return (
        str(item.get("claim_item_id") or ""),
        str(item.get("facility_tariff_item_id") or ""),
        _item_name(item).lower(),
        round(_item_cost(item), 2),
        str(item.get("category_id") or ""),
        str(item.get("quantity") or 1),
    )


def _historical_items(pa: dict, current_items: list[dict]) -> list[dict]:
    current_identities = {_item_identity(item) for item in current_items}
    return [item for item in _items(pa) if _item_identity(item) not in current_identities]


def _pa_with_items(pa: dict, items: list[dict]) -> dict:
    scoped = dict(pa)
    scoped["items"] = items
    scoped["requested_items"] = items
    scoped["total_requested_cost"] = sum(_item_cost(item) for item in items)
    scoped["aman_prior_context"] = _aman_prior_context(pa, items)
    return scoped


def _aman_prior_context(pa: dict, current_items: list[dict] | None = None) -> dict:
    historical = _historical_items(pa, current_items or _current_submission_items(pa))
    approved_items = [item for item in historical if _item_status(item) == "approved"]
    rejected_items = [item for item in historical if _item_status(item) == "rejected"]
    approved_amount = sum(_item_approved_cost(item) for item in approved_items)
    rejected_requested_amount = sum(_item_cost(item) for item in rejected_items)
    return {
        "approved_count": len(approved_items),
        "approved_amount": approved_amount,
        "rejected_count": len(rejected_items),
        "rejected_requested_amount": rejected_requested_amount,
        "approved_items": [
            {
                "claim_item_id": _item_id(item),
                "item_name": _item_name(item),
                "approved_cost": _item_approved_cost(item),
                "requested_cost": _item_cost(item),
            }
            for item in approved_items
        ],
        "rejected_items": [
            {
                "claim_item_id": _item_id(item),
                "item_name": _item_name(item),
                "approved_cost": 0,
                "requested_cost": _item_cost(item),
            }
            for item in rejected_items
        ],
    }


def _item_concepts(item_or_name) -> set[str]:
    name = _item_name(item_or_name) if isinstance(item_or_name, dict) else str(item_or_name or "")
    text = name.lower()
    concepts: set[str] = set()
    if re.search(r"\b(hcv|hbsag|hepatitis|hep\s*[abc])\b", text):
        concepts.add("hepatitis")
    if re.search(r"\b(vit(?:amin)?\s*c|ascorbic)\b", text):
        concepts.add("vitamin_c")
    if re.search(r"\b(malaria|plasmodium|artemether|lumefantrine)\b", text):
        concepts.add("malaria")
    if re.search(r"\b(pregnan|delivery|antenatal|postnatal|obstetric|caesarean|cesarean)\b", text):
        concepts.add("maternity")
    if re.search(r"\b(cancer|chemo|radiotherapy|oncology)\b", text):
        concepts.add("cancer")
    if re.search(r"\b(dialysis|kidney|renal)\b", text):
        concepts.add("renal")
    if re.search(r"\b(hiv|aids|retroviral)\b", text):
        concepts.add("hiv")
    return concepts


def _item_tokens(item_or_name) -> set[str]:
    name = _item_name(item_or_name) if isinstance(item_or_name, dict) else str(item_or_name or "")
    stop = {
        "general", "initial", "consultation", "visit", "tablet", "capsule",
        "injection", "oral", "cream", "solution", "screening", "surface",
        "antigen", "human", "test", "per", "and", "with", "for", "the",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", name.lower())
        if len(token) > 2 and token not in stop
    }


def _items_related(left, right) -> bool:
    left_concepts = _item_concepts(left)
    right_concepts = _item_concepts(right)
    if left_concepts and right_concepts and left_concepts.intersection(right_concepts):
        return True

    left_tokens = _item_tokens(left)
    right_tokens = _item_tokens(right)
    overlap = left_tokens.intersection(right_tokens)
    return len(overlap) >= 2


def _related_aman_context(item: dict, prior_context: dict) -> dict:
    approved = [
        prior for prior in prior_context.get("approved_items") or []
        if _items_related(item, prior.get("item_name"))
    ]
    rejected = [
        prior for prior in prior_context.get("rejected_items") or []
        if _items_related(item, prior.get("item_name"))
    ]
    return {
        "approved_items": approved,
        "rejected_items": rejected,
        "approved_count": len(approved),
        "rejected_count": len(rejected),
    }


def _context_names(items: list[dict], limit: int = 3) -> str:
    names = [str(item.get("item_name") or item.get("name") or "").strip() for item in items if str(item.get("item_name") or item.get("name") or "").strip()]
    if not names:
        return "related item(s)"
    extra = len(names) - limit
    shown = ", ".join(names[:limit])
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def _all_limits(pa: dict) -> list[dict]:
    utilization = pa.get("utilization") if isinstance(pa.get("utilization"), dict) else {}
    limits = []
    for key in ("enrollee_limits", "policy_limits"):
        value = utilization.get(key) or []
        if isinstance(value, list):
            limits.extend(limit for limit in value if isinstance(limit, dict))
    return limits


def _limit_row_by_value(limits: list[dict], expected_value: float | None) -> dict | None:
    if expected_value is None:
        return None
    for limit in limits:
        actual = _number(limit.get("limit_value"))
        if actual is not None and abs(actual - float(expected_value)) < 0.01:
            return limit
    return None


def _limit_bucket_for_definition_id(value) -> str | None:
    try:
        definition_id = int(value)
    except (TypeError, ValueError):
        return None
    return AMAN_LIMIT_DEFINITION_BUCKETS.get(definition_id)


def _limit_row_by_bucket(limits: list[dict], bucket_key: str | None) -> dict | None:
    if not bucket_key:
        return None
    for limit in limits:
        if _limit_bucket_for_definition_id(limit.get("limit_definition_id")) == bucket_key:
            return limit
    return None


def _limit_row_for_bucket(
    limits: list[dict],
    bucket_key: str | None,
    expected_value: float | None,
) -> tuple[dict | None, str | None]:
    limit_row = _limit_row_by_bucket(limits, bucket_key)
    if limit_row:
        return limit_row, "limit_definition_id"

    limit_row = _limit_row_by_value(limits, expected_value)
    if limit_row:
        return limit_row, "limit_value"

    return None, None


def _coverage_key(value) -> str:
    return str(value or "").strip().lower()


def _coverage_by_item(agent2: dict, items: list[dict]) -> dict[str, dict]:
    results = agent2.get("item_results") if isinstance(agent2, dict) else None
    mapped: dict[str, dict] = {}
    if isinstance(results, list):
        for result in results:
            if not isinstance(result, dict):
                continue
            for key in (
                result.get("claim_item_id"),
                result.get("id"),
                result.get("item_id"),
                result.get("item_name"),
                result.get("name"),
            ):
                if key is not None:
                    mapped[_coverage_key(key)] = result

    denied_text = " | ".join(str(item) for item in (agent2.get("denied_items") or [])) if isinstance(agent2, dict) else ""
    for item in items:
        iid = _item_id(item)
        name = _item_name(item)
        result = mapped.get(_coverage_key(iid)) or mapped.get(_coverage_key(name))
        if result:
            mapped[_coverage_key(iid)] = result
            continue

        if denied_text and name.lower() in denied_text.lower():
            mapped[_coverage_key(iid)] = {
                "decision": "DENY",
                "reason": "Coverage denied by Agent 2.",
                "benefit_category": agent2.get("benefit_category"),
            }
        else:
            mapped[_coverage_key(iid)] = {
                "decision": "APPROVE" if agent2.get("pass") else "DENY",
                "reason": agent2.get("reason") or "Coverage result inherited from Agent 2.",
                "benefit_category": agent2.get("benefit_category"),
            }
    return mapped


def _item_bucket(pa: dict, item: dict, coverage: dict | None = None) -> tuple[str | None, str, str | None]:
    try:
        category_id = int(item.get("category_id"))
    except (TypeError, ValueError):
        category_id = None

    if category_id in CATEGORY_BUCKET_OVERRIDES:
        bucket_key, bucket_name = CATEGORY_BUCKET_OVERRIDES[category_id]
        return bucket_key, bucket_name, "item_category"

    benefit_category = str((coverage or {}).get("benefit_category") or "").lower()
    if "surgery" in benefit_category or "surgical" in benefit_category:
        return "surgical", "Surgical Care Limit", "coverage_category"
    if "cancer" in benefit_category:
        return "cancer", "Cancer Care Limit", "coverage_category"
    if "chronic" in benefit_category:
        return "chronic", "Chronic Disease Medication Limit", "coverage_category"
    if "dialysis" in benefit_category or "kidney" in benefit_category:
        return "dialysis", "Kidney Dialysis Limit", "coverage_category"
    if "neonatal" in benefit_category:
        return "neonatal", "Neonatal Care Limit", "coverage_category"

    try:
        care_type = int(pa.get("care_type"))
    except (TypeError, ValueError):
        care_type = None

    if care_type in CARE_TYPE_BUCKETS:
        bucket_key, bucket_name = CARE_TYPE_BUCKETS[care_type]
        return bucket_key, bucket_name, "care_type"

    return None, "Unknown Limit Bucket", None


def _coverage_decision(coverage: dict | None) -> str:
    if not isinstance(coverage, dict):
        return "ESCALATE"
    raw = str(coverage.get("decision") or coverage.get("coverage_decision") or "").upper()
    if raw in {"APPROVE", "COVERED", "PASS"}:
        return "APPROVE"
    if raw in {"DENY", "REJECT", "REJECTED", "DENIED", "NOT_COVERED", "NOT COVERED", "FAIL"}:
        return "DENY"
    if coverage.get("pass") is True:
        return "APPROVE"
    if coverage.get("pass") is False:
        return "DENY"
    return "ESCALATE"


async def _log_agent(request_id: str, agent_num: int, agent_name: str, result: dict):
    """Save individual agent result to agent_logs table and log to console."""
    model_usage = result.pop("__model_usage", None) if isinstance(result, dict) else None
    passed = result.get("pass", result.get("decision") == "APPROVE")
    status = "pass" if passed else "fail"
    logger.info(
        "agent_step_completed",
        agent_num=agent_num,
        agent_name=agent_name,
        status=status,
    )
    await pg_execute(
        """
        INSERT INTO agent_logs (
            request_id,
            agent_num,
            agent_name,
            status,
            result,
            model,
            input_tokens,
            output_tokens,
            total_tokens,
            estimated_cost_usd,
            model_usage,
            logged_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11::jsonb, NOW())
        """,
        str(request_id),
        agent_num,
        agent_name,
        status,
        json.dumps(result),
        model_usage.get("model") if model_usage else None,
        model_usage.get("input_tokens") if model_usage else None,
        model_usage.get("output_tokens") if model_usage else None,
        model_usage.get("total_tokens") if model_usage else None,
        model_usage.get("estimated_cost_usd") if model_usage else None,
        json.dumps(model_usage) if model_usage else None,
    )


# ---------------------------------------------------------------------------
# AGENT 1 — Eligibility
# ---------------------------------------------------------------------------
async def agent_eligibility(pa: dict) -> dict:
    system_prompt = """You are Agent 1 of a pre-authorization pipeline for Aman HMO.
Your ONLY job is to check member eligibility.
Return ONLY valid JSON. No markdown, no explanation outside the JSON."""

    user_message = f"""Check eligibility for this pre-authorization request.
Today's date is {TODAY}.

PA REQUEST:
{json.dumps(pa, indent=2)}

Rules:
1. If plan is exactly "Platinum Plus" → auto-pass, set is_platinum_plus: true
2. eligibility.status must be "active" (case-insensitive)
3. eligibility.enrollment_date must be on or before today ({TODAY})
4. eligibility.expiry_date must be strictly after today ({TODAY})
5. If member age is provided and exceeds 65 → fail (must be on Senior Citizens Plan)
6. Any failure → pass: false with a specific reason

Return ONLY this JSON:
{{
  "pass": true or false,
  "reason": "one sentence explanation",
  "is_platinum_plus": true or false,
  "checks": {{
    "status_active": true or false,
    "enrollment_valid": true or false,
    "not_expired": true or false,
    "age_ok": true or false or null
  }}
}}"""

    return await _call_claude(system_prompt, user_message)


# ---------------------------------------------------------------------------
# AGENT 2 — Plan & Coverage
# ---------------------------------------------------------------------------
async def agent_plan_coverage(pa: dict) -> dict:
    knowledge_base = _knowledge_base_for_request(pa)
    system_prompt = f"""You are Agent 2 of a pre-authorization pipeline for Aman HMO.
Your ONLY job is to check whether requested items are covered under the member's plan tier.
Use the knowledge base below. Return ONLY valid JSON. No markdown.

KNOWLEDGE BASE:
{knowledge_base}"""

    user_message = f"""Check plan coverage for this pre-authorization request.
Today's date is {TODAY}.

PA REQUEST:
{json.dumps(pa, indent=2)}

Check in this order:
1. EXCLUSION CHECK: Is any item in the exclusions list? → fail immediately if yes
2. WAITING PERIOD CHECK:
   - chronic_disease_waiting_cleared false + chronic disease item → fail
   - maternity_waiting_cleared false + pregnancy/delivery item → fail
   - surgical_waiting_cleared false + non-accidental surgery item → fail
3. PLAN COVERAGE CHECK: Is the item covered on this specific plan tier?
4. BENEFIT CATEGORY: Classify into the correct bucket

Production payload guidance:
- If items have pricing_source="tariff", treat them as recognized Aman tariff items.
- If proposed_impact.status is "allowed" and proposed_impact.violations is empty, do not deny solely because the exact tariff item name is not listed in the knowledge base.
- For basic outpatient consultation, routine laboratory tests, and routine medication on tariff, pass coverage unless there is a clear exclusion, waiting-period issue, or explicit violation.
- If coverage is uncertain, prefer pass with a note or escalate in the final decision rather than deterministic denial.
- If PA REQUEST.aman_prior_context shows AMAN already approved related care in the same check-in snapshot, use that as context. Do not blindly approve the new item, but if a broad exclusion conflicts with prior AMAN-approved related care, return ESCALATE for that item rather than a hard DENY.
- Distinguish investigations/screening from treatment. Example: approved hepatitis screening does not automatically cover hepatitis treatment/immunoglobulin, but it should raise context and may require human review.

Return ONLY this JSON:
{{
  "pass": true or false,
  "reason": "one sentence explanation",
  "benefit_category": "exact bucket name (e.g. Intermediate Surgery / Major Surgery / Minor Surgery / Inpatient / Outpatient / Dental / Optical / Cancer Care / Chronic Disease Medication / Physiotherapy / Psychiatric Care / Maternity / Neonatal / Kidney Dialysis / HIV/AIDS Care / CT/MRI Scan / Endoscopy / Immunization / Emergency)",
  "covered_items": ["item descriptions that are covered"],
  "denied_items": ["item description — reason for denial"],
  "item_results": [
    {{
      "claim_item_id": number or string or null,
      "item_name": "item name",
      "decision": "APPROVE" or "DENY" or "ESCALATE",
      "benefit_category": "benefit bucket for this specific item",
      "reason": "one sentence item-level reason",
      "exclusion_triggered": true or false,
      "waiting_period_issue": true or false,
      "plan_restriction": true or false
    }}
  ],
  "exclusion_triggered": true or false,
  "exclusion_detail": "which exclusion rule, or null",
  "waiting_period_issue": true or false,
  "waiting_period_detail": "which waiting period applies, or null",
  "plan_restriction": true or false,
  "plan_restriction_detail": "which plan restriction applies, or null"
}}"""

    coverage_max_tokens = 5000
    try:
        return await _call_claude(system_prompt, user_message, max_tokens=coverage_max_tokens)
    except AgentJSONParseError as first_error:
        retry_message = f"""{user_message}

The previous coverage-agent response failed JSON parsing with this error:
{str(first_error)}

Retry once. Return ONLY one complete strict JSON object matching the requested schema.
Do not use markdown fences. Do not include trailing commas. Close every string, object, and array.
If an item is uncertain, mark that item ESCALATE inside item_results instead of adding prose outside JSON."""
        try:
            retry_result = await _call_claude(
                system_prompt,
                retry_message,
                max_tokens=coverage_max_tokens,
            )
        except AgentJSONParseError as second_error:
            second_error.model_usage = _combine_model_usage(
                getattr(first_error, "model_usage", None),
                getattr(second_error, "model_usage", None),
            )
            second_error.first_parse_error = str(first_error)
            second_error.first_raw_output_preview = first_error.raw_output[:2000]
            raise

        retry_usage = retry_result.get("__model_usage")
        retry_result["__model_usage"] = (
            _combine_model_usage(getattr(first_error, "model_usage", None), retry_usage)
            or retry_usage
        )
        retry_result["parse_retry_attempted"] = True
        retry_result["first_parse_error"] = str(first_error)
        return retry_result


# ---------------------------------------------------------------------------
# AGENT 3 — Utilization & Limits
# ---------------------------------------------------------------------------
async def agent_utilization(pa: dict, benefit_category: str) -> dict:
    knowledge_base = _knowledge_base_for_request(pa)
    system_prompt = f"""You are Agent 3 of a pre-authorization pipeline for Aman HMO.
Your ONLY job is to check utilization limits and remaining balances.
Use the knowledge base below. Return ONLY valid JSON. No markdown.

KNOWLEDGE BASE:
{knowledge_base}"""

    user_message = f"""Check utilization and limits for this pre-authorization request.
Benefit category from Agent 2: {benefit_category}

PA REQUEST:
{json.dumps(pa, indent=2)}

Rules:
1. Identify the correct limit bucket based on benefit_category and plan tier
2. Use utilization data if present; if missing, use knowledge base limits and assume 0 used
3. For amount-based benefits, check: bucket_used + estimated_cost <= bucket_limit.
   For multi-item requests, estimated_cost means total_requested_cost if present, otherwise sum every item estimated_cost/requested_cost.
4. For frequency-based benefits such as CT/MRI scan counts, check: count_used + 1 <= count_limit.
   Do not compare estimated_cost against a frequency count.
4. Check: maximum_annual_benefit_used + estimated_cost <= maximum_annual_benefit_limit
5. Both checks must pass
6. If bucket_exceeded is false and annual_cap_exceeded is false, pass MUST be true.
7. Note: Surgical limit covers all surgery types (minor, intermediate, major)

Return ONLY this JSON:
{{
  "pass": true or false,
  "reason": "one sentence explanation",
  "bucket": "exact bucket name",
  "bucket_limit": number,
  "bucket_used": number,
  "estimated_cost": number,
  "bucket_remaining_before": number,
  "bucket_remaining_after": number,
  "annual_cap_limit": number,
  "annual_cap_used": number,
  "annual_cap_remaining_before": number,
  "annual_cap_remaining_after": number,
  "bucket_exceeded": true or false,
  "annual_cap_exceeded": true or false,
  "utilization_data_missing": true or false
}}"""

    result = await _call_claude(system_prompt, user_message)
    return _normalize_utilization_result(result)


def agent_item_utilization(pa: dict, agent2: dict) -> dict:
    items = _current_submission_items(pa)
    plan = _plan_tier(pa)
    plan_limits = _plan_limits_for_request(pa).get(plan or "")
    limits = _all_limits(pa)
    utilization_data_missing = not bool(limits)
    coverage_map = _coverage_by_item(agent2, items)
    prior_context = _aman_prior_context(pa, items)

    if not items:
        result = {
            "pass": None,
            "reason": "No current pending submission items were available for item-level utilization.",
            "item_decisions": [],
            "utilization_data_missing": True,
        }
        result["aman_prior_context"] = prior_context
        return result

    if not plan or not plan_limits:
        item_decisions = [
            _build_item_decision(
                item,
                "ESCALATE",
                "Unknown plan tier; cannot choose a benefit limit.",
                coverage_map.get(_coverage_key(_item_id(item))),
                bucket_key=None,
                bucket_name="Unknown Limit Bucket",
            )
            for item in items
        ]
        result = _summarize_item_utilization(item_decisions, "Unknown plan tier; cannot choose a benefit limit.")
        result["aman_prior_context"] = prior_context
        return result

    historical_used_by_bucket: dict[str, float] = {}
    for historical_item in _historical_items(pa, items):
        if _item_status(historical_item) != "approved":
            continue
        bucket_key, _bucket_name, _bucket_source = _item_bucket(pa, historical_item)
        if not bucket_key:
            continue
        historical_used_by_bucket[bucket_key] = (
            historical_used_by_bucket.get(bucket_key, 0.0)
            + _item_approved_cost(historical_item)
        )

    running_used_by_bucket: dict[str, float] = dict(historical_used_by_bucket) if utilization_data_missing else {}

    item_decisions = []
    for item in items:
        coverage = coverage_map.get(_coverage_key(_item_id(item)))
        coverage_decision = _coverage_decision(coverage)
        bucket_key, bucket_name, bucket_source = _item_bucket(pa, item, coverage)
        expected_limit = plan_limits.get(bucket_key) if bucket_key else None
        limit_row, limit_match_source = (
            _limit_row_for_bucket(limits, bucket_key, expected_limit)
            if limits else (None, None)
        )
        amount = _item_cost(item)
        source_status = _item_status(item)
        amount_to_count = _item_approved_cost(item) if source_status == "approved" else amount
        related_context = _related_aman_context(item, prior_context)

        if coverage_decision == "DENY":
            if related_context.get("approved_count"):
                context_label = _context_names(related_context.get("approved_items") or [])
                item_decisions.append(_build_item_decision(
                    item,
                    "ESCALATE",
                    (
                        f"Coverage rule suggests denial, but AMAN previously approved related care in this PA snapshot "
                        f"({context_label}); escalate for medical review instead of hard rejection."
                    ),
                    coverage,
                    bucket_key=bucket_key,
                    bucket_name=bucket_name,
                    bucket_source=bucket_source,
                    requested_cost=amount,
                    utilization_data_missing=utilization_data_missing,
                    utilization_source="aman_prior_context_conflict",
                    context_evidence=related_context,
                    context_override=True,
                ))
                continue

            item_decisions.append(_build_item_decision(
                item,
                "DENY",
                (coverage or {}).get("reason") or "Item is not covered under the plan.",
                coverage,
                bucket_key=bucket_key,
                bucket_name=bucket_name,
                bucket_source=bucket_source,
                requested_cost=amount,
                utilization_data_missing=utilization_data_missing,
                context_evidence=related_context if related_context.get("rejected_count") else None,
            ))
            continue

        if coverage_decision == "ESCALATE":
            item_decisions.append(_build_item_decision(
                item,
                "ESCALATE",
                (coverage or {}).get("reason") or "Coverage is uncertain for this item.",
                coverage,
                bucket_key=bucket_key,
                bucket_name=bucket_name,
                bucket_source=bucket_source,
                requested_cost=amount,
                utilization_data_missing=utilization_data_missing,
            ))
            continue

        if bucket_key == "wellness" and not limit_row:
            item_decisions.append(_build_item_decision(
                item,
                "ESCALATE",
                f"{bucket_name} has no deterministic limit mapping yet; AMAN limit_definition_id mapping is required.",
                coverage,
                bucket_key=bucket_key,
                bucket_name=bucket_name,
                bucket_source=bucket_source,
                requested_cost=amount,
                bucket_limit=expected_limit,
                utilization_data_missing=utilization_data_missing,
            ))
            continue

        if bucket_key == "maternity" and expected_limit is None and not limit_row:
            item_decisions.append(_build_item_decision(
                item,
                "ESCALATE",
                f"{bucket_name} has no deterministic limit mapping yet; AMAN limit_definition_id mapping is required.",
                coverage,
                bucket_key=bucket_key,
                bucket_name=bucket_name,
                bucket_source=bucket_source,
                requested_cost=amount,
                utilization_data_missing=utilization_data_missing,
            ))
            continue

        if expected_limit is None and not limit_row:
            item_decisions.append(_build_item_decision(
                item,
                "ESCALATE",
                f"{bucket_name} has no deterministic limit mapping for {plan}.",
                coverage,
                bucket_key=bucket_key,
                bucket_name=bucket_name,
                bucket_source=bucket_source,
                requested_cost=amount,
                utilization_data_missing=utilization_data_missing,
            ))
            continue

        aman_limit_value = _number(limit_row.get("limit_value")) if limit_row else None
        limit_value_mismatch = (
            limit_row is not None
            and expected_limit is not None
            and aman_limit_value is not None
            and not _close_amount(aman_limit_value, expected_limit)
        )
        used_knowledge_base_limit = not limit_row or limit_value_mismatch
        limit_value = (
            expected_limit
            if used_knowledge_base_limit and expected_limit is not None
            else aman_limit_value or expected_limit
        )
        base_used = _number(limit_row.get("consumed_value")) if limit_row else None
        if base_used is None:
            base_used = historical_used_by_bucket.get(bucket_key, 0.0)
        used_before = running_used_by_bucket.get(bucket_key, base_used)
        remaining_before = limit_value - used_before
        remaining_after = remaining_before - amount_to_count
        bucket_exceeded = remaining_after < -0.01
        utilization_unverified = utilization_data_missing or used_knowledge_base_limit

        decision = "DENY" if bucket_exceeded else "APPROVE"
        if used_knowledge_base_limit:
            if limit_value_mismatch:
                fallback_context = "AMAN consumption limit value did not match this routed knowledge-base bucket"
            elif limits:
                fallback_context = "AMAN consumption rows did not map to this routed knowledge-base bucket"
            else:
                fallback_context = "Consumption limits were not configured in the payload"
            reason = (
                f"{fallback_context}; approving this item would exceed the {bucket_name} fallback balance from known {plan} plan rules and available utilization context."
                if bucket_exceeded
                else f"{fallback_context}; item fits within the {bucket_name} fallback balance from known {plan} plan rules and available utilization context."
            )
        else:
            reason = (
                f"{bucket_name} remaining balance is insufficient for this item."
                if bucket_exceeded
                else f"Item fits within the {bucket_name} remaining balance."
            )
        if not bucket_exceeded:
            running_used_by_bucket[bucket_key] = used_before + amount_to_count

        item_decisions.append(_build_item_decision(
            item,
            decision,
            reason,
            coverage,
            bucket_key=bucket_key,
            bucket_name=bucket_name,
            bucket_source=bucket_source,
            requested_cost=amount,
            bucket_limit=limit_value,
            bucket_used_before=used_before,
            bucket_remaining_before=remaining_before,
            bucket_remaining_after=remaining_after,
            bucket_exceeded=bucket_exceeded,
            limit_definition_id=limit_row.get("limit_definition_id") if limit_row else None,
            utilization_data_missing=utilization_unverified,
            utilization_source=(
                f"aman_consumption_{limit_match_source}"
                if limit_row and limit_match_source and not used_knowledge_base_limit
                else (
                    f"aman_consumption_{limit_match_source}_with_knowledge_base_limit"
                    if limit_row and limit_match_source
                    else (
                        "fallback_knowledge_base_limit_unmatched_aman_consumption"
                        if limits
                        else "fallback_knowledge_base_limit_missing_aman_consumption"
                    )
                )
            ),
        ))

    result = _summarize_item_utilization(item_decisions)
    result["aman_prior_context"] = prior_context
    return result


def _build_item_decision(
    item: dict,
    decision: str,
    reason: str,
    coverage: dict | None,
    *,
    bucket_key: str | None,
    bucket_name: str,
    bucket_source: str | None = None,
    requested_cost: float | None = None,
    bucket_limit=None,
    bucket_used_before=None,
    bucket_remaining_before=None,
    bucket_remaining_after=None,
    bucket_exceeded: bool | None = None,
    limit_definition_id=None,
    utilization_data_missing: bool = False,
    utilization_source: str | None = None,
    context_evidence: dict | None = None,
    context_override: bool = False,
) -> dict:
    amount = _item_cost(item) if requested_cost is None else requested_cost
    approved_cost = amount if decision == "APPROVE" else 0
    coverage_reason = (coverage or {}).get("reason")
    reviewer_reason = _reviewer_item_reason(
        decision,
        coverage_reason=coverage_reason,
        utilization_reason=reason,
    )
    return {
        "claim_item_id": _item_id(item),
        "facility_tariff_item_id": item.get("facility_tariff_item_id"),
        "item_name": _item_name(item),
        "category_id": item.get("category_id"),
        "category_label": item.get("category_label"),
        "quantity": item.get("quantity") or 1,
        "unit_cost": item.get("unit_cost"),
        "requested_cost": amount,
        "recommended_approved_cost": approved_cost,
        "source_item_status": _item_status(item),
        "decision": decision,
        "recommendation": "approve" if decision == "APPROVE" else "reject" if decision == "DENY" else "review",
        "reason": reviewer_reason,
        "utilization_reason": reason,
        "coverage_decision": _coverage_decision(coverage),
        "coverage_reason": coverage_reason,
        "benefit_category": (coverage or {}).get("benefit_category"),
        "bucket_key": bucket_key,
        "bucket": bucket_name,
        "bucket_source": bucket_source,
        "limit_definition_id": limit_definition_id,
        "utilization_data_missing": utilization_data_missing,
        "utilization_source": utilization_source,
        "context_evidence": context_evidence,
        "context_override": context_override,
        "bucket_limit": bucket_limit,
        "bucket_used_before": bucket_used_before,
        "bucket_remaining_before": bucket_remaining_before,
        "bucket_remaining_after": bucket_remaining_after,
        "bucket_exceeded": bucket_exceeded,
    }


def _reviewer_item_reason(
    decision: str,
    *,
    coverage_reason: str | None,
    utilization_reason: str | None,
) -> str:
    """Create the line-level rationale shown to AMAN reviewers."""
    coverage_text = str(coverage_reason or "").strip()
    utilization_text = str(utilization_reason or "").strip()
    decision_text = str(decision or "").upper()

    if decision_text == "APPROVE":
        parts = []
        if coverage_text:
            parts.append(coverage_text)
        if utilization_text and utilization_text != coverage_text:
            parts.append(f"Utilization check: {utilization_text}")
        return " ".join(parts) or "Approved based on eligibility, coverage, and utilization checks."

    return utilization_text or coverage_text or "Requires manual review based on eligibility, coverage, or utilization checks."


def _summarize_item_utilization(item_decisions: list[dict], fallback_reason: str | None = None) -> dict:
    approved = [item for item in item_decisions if item.get("decision") == "APPROVE"]
    denied = [item for item in item_decisions if item.get("decision") == "DENY"]
    escalated = [item for item in item_decisions if item.get("decision") == "ESCALATE"]
    approved_amount = sum(_number(item.get("recommended_approved_cost")) or 0 for item in approved)
    requested_amount = sum(_number(item.get("requested_cost")) or 0 for item in item_decisions)

    if escalated:
        passed = None
        reason = fallback_reason or "One or more items require human review."
    elif denied:
        passed = False
        reason = "One or more items failed coverage or utilization checks."
    else:
        passed = True
        reason = "All items passed coverage and utilization checks."

    return {
        "pass": passed,
        "reason": reason,
        "mode": "item_level",
        "item_decisions": item_decisions,
        "approved_item_count": len(approved),
        "denied_item_count": len(denied),
        "escalated_item_count": len(escalated),
        "total_item_count": len(item_decisions),
        "requested_amount": requested_amount,
        "approved_amount": approved_amount,
        "denied_amount": sum(_number(item.get("requested_cost")) or 0 for item in denied),
        "escalated_amount": sum(_number(item.get("requested_cost")) or 0 for item in escalated),
        "utilization_data_missing": any(item.get("utilization_data_missing") for item in item_decisions),
    }


def _global_item_decisions(pa: dict, decision: str, reason: str) -> dict:
    current_items = _current_submission_items(pa)
    item_decisions = [
        _build_item_decision(
            item,
            decision,
            reason,
            {"decision": decision, "reason": reason},
            bucket_key=None,
            bucket_name="Not evaluated",
            requested_cost=_item_cost(item),
        )
        for item in current_items
    ]
    result = _summarize_item_utilization(item_decisions, reason)
    result["aman_prior_context"] = _aman_prior_context(pa, current_items)
    return result


def _build_final_decision_from_items(pa: dict, agent1: dict, agent2: dict, agent3: dict) -> dict:
    item_decisions = agent3.get("item_decisions") or []
    approved = [item for item in item_decisions if item.get("decision") == "APPROVE"]
    denied = [item for item in item_decisions if item.get("decision") == "DENY"]
    escalated = [item for item in item_decisions if item.get("decision") == "ESCALATE"]
    approved_amount = sum(_number(item.get("recommended_approved_cost")) or 0 for item in approved)

    if not agent1.get("pass"):
        decision = "DENY"
        confidence = "HIGH"
        denial_reason = agent1.get("reason")
        escalation_reason = None
        reasoning = agent1.get("reason") or "Member failed eligibility checks."
    elif not item_decisions:
        decision = "ESCALATE"
        confidence = "LOW"
        denial_reason = None
        escalation_reason = "No current pending submitted line items were available for advisory decisioning."
        reasoning = "No current pending line items were available to advise on."
    elif escalated:
        decision = "ESCALATE"
        confidence = "MEDIUM"
        denial_reason = None
        escalation_reason = "One or more line items require human review."
        reasoning = "Some line items require human review before a final PA recommendation can be trusted."
    elif denied and approved:
        decision = "ESCALATE"
        confidence = "MEDIUM"
        denial_reason = None
        escalation_reason = "Mixed item-level recommendations."
        reasoning = "The PA has mixed item-level results; approved and rejected lines should be reviewed per item."
    elif denied:
        decision = "DENY"
        confidence = "HIGH"
        denial_reason = "All requested line items failed coverage or utilization checks."
        escalation_reason = None
        reasoning = "All requested line items failed coverage or utilization checks."
    else:
        decision = "APPROVE"
        confidence = "HIGH"
        denial_reason = None
        escalation_reason = None
        reasoning = "All requested line items passed eligibility, coverage, and utilization checks."

    return {
        "decision": decision,
        "pa_decision": "PARTIAL_APPROVE" if approved and (denied or escalated) else decision,
        "confidence": confidence,
        "amount_approved": approved_amount,
        "denial_reason": denial_reason,
        "escalation_reason": escalation_reason,
        "reasoning": reasoning,
        "flags": _item_decision_flags(item_decisions),
        "no_preauth_required": bool(agent1.get("is_platinum_plus")),
        "agent_summary": {
            "agent1_pass": agent1.get("pass"),
            "agent2_pass": agent2.get("pass"),
            "agent3_pass": agent3.get("pass"),
        },
        "knowledge_base": _knowledge_base_context_for_request(pa),
        "item_summary": {
            "approved": len(approved),
            "denied": len(denied),
            "escalated": len(escalated),
            "total": len(item_decisions),
            "requested_amount": agent3.get("requested_amount"),
            "approved_amount": approved_amount,
            "denied_amount": agent3.get("denied_amount"),
            "escalated_amount": agent3.get("escalated_amount"),
        },
        "aman_prior_context": agent3.get("aman_prior_context") or {},
    }


def _item_decision_flags(item_decisions: list[dict]) -> list[str]:
    flags = []
    if any(item.get("decision") == "DENY" for item in item_decisions):
        flags.append("One or more items rejected")
    if any(item.get("decision") == "ESCALATE" for item in item_decisions):
        flags.append("One or more items need review")
    if any(item.get("bucket_exceeded") for item in item_decisions):
        flags.append("Benefit limit exceeded")
    if any(item.get("source_item_status") == "pending" for item in item_decisions):
        flags.append("Pending source items present")
    if any(item.get("context_override") for item in item_decisions):
        flags.append("Prior AMAN context changed item recommendation")
    return flags


# ---------------------------------------------------------------------------
# AGENT 4 — Final Decision
# ---------------------------------------------------------------------------
async def agent_final_decision(pa: dict, agent1: dict, agent2: dict, agent3: dict) -> dict:
    system_prompt = """You are Agent 4 of a pre-authorization pipeline for Aman HMO.
Aggregate findings from Agents 1, 2, and 3 and produce the final decision.
Return ONLY valid JSON. No markdown."""

    user_message = f"""Make the final pre-authorization decision.

ORIGINAL PA REQUEST:
{json.dumps(pa, indent=2)}

AGENT 1 — ELIGIBILITY:
{json.dumps(agent1, indent=2)}

AGENT 2 — PLAN & COVERAGE:
{json.dumps(agent2, indent=2)}

AGENT 3 — UTILIZATION:
{json.dumps(agent3, indent=2)}

Decision rules:
- APPROVE: all 3 agents passed
- DENY: any agent failed with a clear deterministic reason
- ESCALATE: incomplete data, conflicting results, edge case, or low confidence
- Platinum Plus (agent1.is_platinum_plus = true) → always APPROVE

Return ONLY this JSON:
{{
  "decision": "APPROVE" or "DENY" or "ESCALATE",
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "amount_approved": number or null,
  "denial_reason": "specific reason or null",
  "escalation_reason": "specific reason or null",
  "reasoning": "one clear sentence summarizing the decision",
  "flags": ["notable flags or empty array"],
  "no_preauth_required": true or false,
  "agent_summary": {{
    "agent1_pass": true or false,
    "agent2_pass": true or false,
    "agent3_pass": true or false
  }}
}}"""

    return await _call_claude(system_prompt, user_message)


# ---------------------------------------------------------------------------
# ORCHESTRATOR
# ---------------------------------------------------------------------------
async def run(patient_id: str, request_id: str):
    # Bind context for all subsequent logs in this request
    bind_contextvars(request_id=request_id, patient_id=patient_id)
    set_sentry_context(request_id=request_id)
    
    logger.info("agent_started")

    row = await pg_query_one(
        "SELECT extracted_fields FROM preauth_logs WHERE request_id = $1",
        str(request_id)
    )
    if not row:
        logger.error("agent_no_record_found")
        return

    pa = _parse_json_field(row["extracted_fields"])
    decision_pa = _pa_with_items(pa, _current_submission_items(pa))

    try:
        # ── Agent 1: Eligibility ──────────────────────────────────────────
        await pg_execute(
            "UPDATE preauth_logs SET status = 'processing', agent_step = 'eligibility' WHERE request_id = $1",
            str(request_id)
        )
        result_1 = await agent_eligibility(pa)
        await _log_agent(request_id, 1, "Eligibility", result_1)

        if not result_1.get("pass"):
            result_3 = _global_item_decisions(pa, "DENY", result_1.get("reason") or "Failed eligibility check")
            final_result = _build_final_decision_from_items(decision_pa, result_1, {"pass": None}, result_3)
            await _log_agent(request_id, 3, "Item Utilization & Limits", result_3)
            await _log_agent(request_id, 4, "Final Decision", final_result)
            await _save_decision(request_id, final_result.get("decision", "DENY"), {
                "agent1": result_1, "agent2": None, "agent3": None, "agent4": None,
                **final_result,
                "agent3": result_3,
                "agent4": final_result,
                "item_decisions": result_3.get("item_decisions", []),
            })
            return

        if result_1.get("is_platinum_plus"):
            logger.info("agent_platinum_plus_express", auto_approve=True)
            result_3 = _global_item_decisions(pa, "APPROVE", "Platinum Plus express card — no pre-authorization required.")
            final_result = _build_final_decision_from_items(decision_pa, result_1, {"pass": True}, result_3)
            final_result["flags"] = ["platinum_plus_express_card"]
            final_result["no_preauth_required"] = True
            await _log_agent(request_id, 3, "Item Utilization & Limits", result_3)
            await _log_agent(request_id, 4, "Final Decision", final_result)
            await _save_decision(request_id, "APPROVE", {
                "agent1": result_1, "agent2": None, "agent3": result_3, "agent4": final_result,
                **final_result,
                "item_decisions": result_3.get("item_decisions", []),
            })
            return

        # ── Agent 2: Plan & Coverage ──────────────────────────────────────
        await pg_execute(
            "UPDATE preauth_logs SET agent_step = 'coverage' WHERE request_id = $1",
            str(request_id)
        )
        try:
            result_2 = await agent_plan_coverage(decision_pa)
        except AgentJSONParseError as e:
            logger.warning(
                "[Agent] Agent 2 JSON parse failed request_id=%s raw=%s",
                request_id,
                e.raw_output[:1000],
            )
            result_2 = {
                "pass": None,
                "reason": "Coverage agent returned malformed JSON; routing all items to human review.",
                "benefit_category": "Coverage review required",
                "covered_items": [],
                "denied_items": [],
                "item_results": [
                    {
                        "claim_item_id": _item_id(item),
                        "item_name": _item_name(item),
                        "decision": "ESCALATE",
                        "benefit_category": "Coverage review required",
                        "reason": "Coverage agent parse failed before this item could be evaluated.",
                        "exclusion_triggered": False,
                        "waiting_period_issue": False,
                        "plan_restriction": False,
                    }
                    for item in _items(decision_pa)
                ],
                "exclusion_triggered": False,
                "exclusion_detail": None,
                "waiting_period_issue": False,
                "waiting_period_detail": None,
                "plan_restriction": False,
                "plan_restriction_detail": None,
                "parse_failed": True,
                "parse_error": str(e),
                "raw_output_preview": e.raw_output[:2000],
                "first_parse_error": getattr(e, "first_parse_error", None),
                "first_raw_output_preview": getattr(e, "first_raw_output_preview", None),
                "__model_usage": getattr(e, "model_usage", None),
            }
        await _log_agent(request_id, 2, "Plan & Coverage", result_2)

        # ── Agent 3: Utilization & Limits ─────────────────────────────────
        await pg_execute(
            "UPDATE preauth_logs SET agent_step = 'utilization' WHERE request_id = $1",
            str(request_id)
        )
        result_3 = agent_item_utilization(pa, result_2)
        await _log_agent(request_id, 3, "Item Utilization & Limits", result_3)

        # ── Agent 4: Final Decision ───────────────────────────────────────
        await pg_execute(
            "UPDATE preauth_logs SET agent_step = 'decision' WHERE request_id = $1",
            str(request_id)
        )
        result_4 = _build_final_decision_from_items(decision_pa, result_1, result_2, result_3)
        await _log_agent(request_id, 4, "Final Decision", result_4)

        await _save_decision(request_id, result_4.get("decision", "ESCALATE"), {
            "agent1": result_1, "agent2": result_2, "agent3": result_3, "agent4": result_4,
            **result_4,
            "item_decisions": result_3.get("item_decisions", []),
        })

    except Exception as e:
        logger.exception("agent_error", error=str(e))
        await pg_execute(
            "UPDATE preauth_logs SET status = 'error', error_message = $2 WHERE request_id = $1",
            str(request_id), str(e)
        )


async def _save_decision(request_id: str, decision: str, result: dict):
    status = decision.lower()
    await pg_execute(
        """
        UPDATE preauth_logs
        SET status       = $2,
            agent_step   = 'completed',
            decision     = $3,
            agent_result = $4::jsonb,
            processed_at = NOW()
        WHERE request_id = $1
        """,
        str(request_id), status, decision, json.dumps(result)
    )
    logger.info("agent_completed", decision=decision)

    # Send the decision back to Aman (advisory callback — integration direction (ii)).
    # Imported lazily to avoid any startup-time coupling.
    try:
        from services.aman_callback import send_decision_to_aman
        await send_decision_to_aman(str(request_id))
    except Exception:
        logger.exception("agent_callback_failed")


def _get_estimated_cost(pa: dict) -> float | None:
    if not isinstance(pa, dict):
        return None

    total_requested_cost = pa.get("total_requested_cost")
    if isinstance(total_requested_cost, (int, float)):
        return total_requested_cost

    items = _parse_json_field(pa.get("items") or [])
    if isinstance(items, list) and items:
        total = 0
        has_cost = False
        for item in items:
            if not isinstance(item, dict):
                continue
            cost = item.get("estimated_cost") or item.get("requested_cost") or item.get("amount")
            try:
                total += float(cost)
                has_cost = True
            except (TypeError, ValueError):
                continue
        if has_cost:
            return total
    return None


def _normalize_utilization_result(result: dict) -> dict:
    normalized = dict(result)
    bucket_exceeded = normalized.get("bucket_exceeded") is True
    annual_cap_exceeded = normalized.get("annual_cap_exceeded") is True

    if bucket_exceeded or annual_cap_exceeded:
        normalized["pass"] = False
        if not normalized.get("reason"):
            normalized["reason"] = "Requested service exceeds the identified benefit or annual cap limit."
        return normalized

    if normalized.get("pass") is False:
        normalized["pass"] = True
        normalized["reason"] = "Utilization is within the identified benefit bucket and annual cap limits."
        normalized["normalization_note"] = (
            "Corrected contradictory utilization output where no exceeded limit was reported."
        )

    return normalized
