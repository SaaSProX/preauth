"""Registry for Aman HMO plan knowledge bases."""

from . import c000, c001, c002, c004, c022, c024, c030, general, r001, s001

DEFAULT_PLAN_CODE = "GENERAL"

_PLAN_MODULES = {
    DEFAULT_PLAN_CODE: general,
    "C000": c000,
    "C001": c001,
    "C002": c002,
    "C004": c004,
    "C022": c022,
    "C024": c024,
    "C030": c030,
    "R001": r001,
    "S001": s001,
}


def normalize_plan_code(plan_code: str | None) -> str:
    code = str(plan_code or DEFAULT_PLAN_CODE).strip().upper()
    return code or DEFAULT_PLAN_CODE


def get_plan_codes() -> tuple[str, ...]:
    return tuple(_PLAN_MODULES.keys())


def get_knowledge_base(plan_code: str | None = None, *, today: str) -> str:
    module = _PLAN_MODULES.get(normalize_plan_code(plan_code), general)
    return module.build_knowledge_base(today)


def get_plan_limits(plan_code: str | None = None) -> dict:
    module = _PLAN_MODULES.get(normalize_plan_code(plan_code), general)
    return module.PLAN_LIMITS


def get_knowledge_base_by_plan_code(*, today: str) -> dict[str, str]:
    return {code: module.build_knowledge_base(today) for code, module in _PLAN_MODULES.items()}


def get_plan_limits_by_plan_code() -> dict[str, dict]:
    return {code: module.PLAN_LIMITS for code, module in _PLAN_MODULES.items()}
