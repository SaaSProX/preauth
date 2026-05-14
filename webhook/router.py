from fastapi import APIRouter, BackgroundTasks, Depends
from middleware.auth import verify_api_key
from models.schemas import WebhookPayload
from services.db import pg_execute
from agent import agent

router = APIRouter()

@router.post("/webhook/preauth")
async def receive_preauth(
    payload: WebhookPayload,
    background: BackgroundTasks,
    client=Depends(verify_api_key)
):
    # Save incoming webhook immediately
    await pg_execute(
        """
        INSERT INTO preauth_logs (request_id, patient_id, status)
        VALUES ($1, $2, 'pending')
        ON CONFLICT (request_id) DO NOTHING
        """,
        payload.request_id, payload.patient_id
    )

    # Kick off agent in background
    # background.add_task(agent.run, payload.patient_id, payload.request_id)
    return {"status": "received"}