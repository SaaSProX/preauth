from fastapi import APIRouter, BackgroundTasks, HTTPException
from models.schemas import WebhookPayload
from config.settings import settings
from agent import agent

router = APIRouter()

@router.post("/webhook/preauth")
async def receive_preauth(payload: WebhookPayload, background: BackgroundTasks):
    # Verify shared secret
    if payload.secret != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid secret")

    # Acknowledge immediately, process in background
    background.add_task(agent.run, payload.patient_id, payload.request_id)
    return {"status": "received"}
