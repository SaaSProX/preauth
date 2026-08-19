"""Corporation-to-plan-code routing for Aman HMO knowledge bases."""

import re

from .registry import DEFAULT_PLAN_CODE, get_plan_codes, normalize_plan_code


ORGANIZATION_PLAN_CODE_MAP = {
    "Africado Consulting": "S001",
    "Aman HMO": "C001",
    "Asshaam Global Construction Ltd": "C001",
    "Century Mining Company": "C002",
    "Chicken Foods Enterprise": "S001",
    "Citizens Engineering Ltd": "S001",
    "Dantata and Sawoe": "C001",
    "Datharm": "S001",
    "Edin & People Limited": "C001",
    "Eggcorn Digital": "S001",
    "Ethica Capital": "S001",
    "Fantel Nigeria Limited": "C000",
    "Flexisaf": "C001",
    "Jaiz Bank Outsourced Staff": "C040",
    "Jaiz Bank Plc": "C004",
    "MILESTONE GLOBAL BANK": "C004",
    "Murty Consulting": "C000",
    "National Mosque": "C000",
    "Naztech Solution Limited": "C000",
    "Neelds Facility Management": "C001",
    "Neelds Realty Limited": "C001",
    "Nexim Bank": "C001",
    "Onyx Investment Advisory Ltd": "C000",
    "Outsource Global Limited": "C022",
    "Phase 3 Telecoms": "C001",
    "Quantum MFB Limited": "C001",
    "Salam Takaful Insurance Ltd": "S001",
    "Sapphire Platfroms": "R001",
    "Sapphire Platforms": "R001",
    "Shamsa Resources and Services Limited": "C001",
    "Skywise Group": "S001",
    "Spectafex Security Company": "C002",
    "STERON GROUP": "C001",
    "Summit Bank": "C001",
    "Symverge Limited": "S001",
    "Transadvisory Legal": "S001",
    "Trust Television": "S001",
    "UMZA Aviation Limited": "C001",
    "Vento Furniture": "C024",
    "Wacop Hotel and Conference Center": "S001",
    "Wacop Hotel and Conference Centre": "S001",
    "Yaz Global": "C001",
    "Zavati Group Limited": "S001",
    "Kaduna Electric": "C030",
    "Ajibola Bashiru Legal": "S001",
}

DIRECT_PLAN_CODE_KEYS = (
    "plan",
    "plan_name",
    "plan_code",
    "plan_id",
    "policy_plan_code",
    "corporate_plan_code",
    "benefit_plan_code",
)

CORPORATION_NAME_KEYS = (
    "corporation_name",
    "corporate_name",
    "organization_name",
    "organisation_name",
    "company_name",
)


def normalize_organization_name(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_NORMALIZED_ORGANIZATION_PLAN_CODE_MAP = {
    normalize_organization_name(name): normalize_plan_code(plan_code)
    for name, plan_code in ORGANIZATION_PLAN_CODE_MAP.items()
}


def _nested_value(payload: dict | None, *path: str):
    value = payload or {}
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first_value(payload: dict | None, keys: tuple[str, ...]):
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _registered_plan_codes() -> set[str]:
    return set(get_plan_codes())


def _registered_plan_code_from_value(value, registered: set[str]) -> str | None:
    code = normalize_plan_code(value)
    if code in registered and code != DEFAULT_PLAN_CODE:
        return code

    text = str(value or "").upper()
    for candidate in sorted(registered - {DEFAULT_PLAN_CODE}, key=len, reverse=True):
        if re.search(rf"\b{re.escape(candidate)}\b", text):
            return candidate
    return None


def _direct_plan_code(payload: dict | None) -> str | None:
    values = [
        _first_value(payload, DIRECT_PLAN_CODE_KEYS),
        _nested_value(payload, "policy", "plan_code"),
        _nested_value(payload, "policy", "plan_id"),
        _nested_value(payload, "policy", "plan_name"),
        _nested_value(payload, "policy", "insurance_package"),
        _nested_value(payload, "policy", "corporate_plan_code"),
        _nested_value(payload, "policy", "benefit_plan_code"),
    ]
    registered = _registered_plan_codes()
    for value in values:
        code = _registered_plan_code_from_value(value, registered)
        if code:
            return code
    return None


def _corporation_name(payload: dict | None) -> str | None:
    return (
        _first_value(payload, CORPORATION_NAME_KEYS)
        or _nested_value(payload, "policy", "corporation_name")
        or _nested_value(payload, "policy", "corporate_name")
    )


def resolve_plan_context(payload: dict | None) -> dict:
    registered = _registered_plan_codes()
    direct_code = _direct_plan_code(payload)
    corporation_name = _corporation_name(payload)
    normalized_corporation = normalize_organization_name(corporation_name)
    mapped_code = _NORMALIZED_ORGANIZATION_PLAN_CODE_MAP.get(normalized_corporation)

    if direct_code:
        return {
            "plan_code": direct_code,
            "source": "payload_plan_code",
            "corporation_name": corporation_name,
            "matched_corporation": None,
            "mapped_plan_code": mapped_code,
            "fallback_reason": None,
        }

    if mapped_code and mapped_code in registered:
        matched_corporation = next(
            (
                name
                for name in ORGANIZATION_PLAN_CODE_MAP
                if normalize_organization_name(name) == normalized_corporation
            ),
            corporation_name,
        )
        return {
            "plan_code": mapped_code,
            "source": "corporation_name",
            "corporation_name": corporation_name,
            "matched_corporation": matched_corporation,
            "mapped_plan_code": mapped_code,
            "fallback_reason": None,
        }

    fallback_reason = "corporation_not_mapped"
    if mapped_code and mapped_code not in registered:
        fallback_reason = f"mapped_plan_code_not_registered:{mapped_code}"

    return {
        "plan_code": DEFAULT_PLAN_CODE,
        "source": "default",
        "corporation_name": corporation_name,
        "matched_corporation": None,
        "mapped_plan_code": mapped_code,
        "fallback_reason": fallback_reason,
    }


def resolve_plan_code(payload: dict | None) -> str:
    return str(resolve_plan_context(payload)["plan_code"])
