import json


def normalize_json_value(value, max_depth: int = 3):
    """Unwrap JSON values that were serialized before reaching a JSONB column."""
    current = value
    for _ in range(max_depth):
        if not isinstance(current, str):
            return current

        stripped = current.strip()
        if not stripped:
            return current

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return current

        if parsed == current:
            return current
        current = parsed

    return current


def parse_json_field(value):
    return normalize_json_value(value)


def json_param(value):
    if value is None:
        return None
    return json.dumps(normalize_json_value(value))
