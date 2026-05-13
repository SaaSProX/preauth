# Tool definitions for the Claude agent
# Queries will be filled in once Aman DB schema is received

TOOLS = [
    {
        "name": "get_preauth_request",
        "description": "Fetch full pre-authorization request details from the database",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"}
            },
            "required": ["request_id"]
        }
    },
    {
        "name": "get_patient_eligibility",
        "description": "Check if a patient is active and eligible for coverage",
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"}
            },
            "required": ["patient_id"]
        }
    },
    {
        "name": "get_patient_plan",
        "description": "Get patient insurance plan details and coverage tier",
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"}
            },
            "required": ["patient_id"]
        }
    },
    {
        "name": "get_utilization",
        "description": "Get patient current utilization against plan limits for the active period",
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"}
            },
            "required": ["patient_id"]
        }
    },
    {
        "name": "update_preauth_decision",
        "description": "Write the final decision back to the database",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["approved", "denied", "escalated"]},
                "reason": {"type": "string"}
            },
            "required": ["request_id", "decision", "reason"]
        }
    },
    {
        "name": "send_notification",
        "description": "Send email notification to provider or patient about the decision",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"}
            },
            "required": ["to", "subject", "body"]
        }
    }
]
