from pydantic import BaseModel

class WebhookPayload(BaseModel):
    request_id: str
    patient_id: str
