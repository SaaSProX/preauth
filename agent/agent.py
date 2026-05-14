import json
import anthropic
from agent.tools import TOOLS
from agent.prompts import SYSTEM_PROMPT
from services import db
from services import notifier

client = anthropic.Anthropic()

async def execute_tool(name: str, inputs: dict):
    # These queries will be updated once DB schema is confirmed
    if name == "get_preauth_request":
        return await db.query_one("SELECT * FROM preauth_requests WHERE id = %s", inputs["request_id"])

    elif name == "get_patient_eligibility":
        return await db.query_one("SELECT * FROM patients WHERE id = %s", inputs["patient_id"])

    elif name == "get_patient_plan":
        return await db.query_one("SELECT * FROM patient_plans WHERE patient_id = %s", inputs["patient_id"])

    elif name == "get_utilization":
        return await db.query_all("SELECT * FROM utilization WHERE patient_id = %s", inputs["patient_id"])

    elif name == "update_preauth_decision":
        await db.execute(
            "UPDATE preauth_requests SET decision = %s, decision_reason = %s WHERE id = %s",
            inputs["decision"], inputs["reason"], inputs["request_id"]
        )
        return {"status": "updated"}

    elif name == "send_notification":
        notifier.send_email(inputs["to"], inputs["subject"], inputs["body"])
        return {"status": "sent"}

async def run(patient_id: str, request_id: str):
    messages = [
        {
            "role": "user",
            "content": f"Process pre-authorization. Patient ID: {patient_id}, Request ID: {request_id}"
        }
    ]

    print(f"\n[Agent] Starting — patient: {patient_id}, request: {request_id}")

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            final = next((b.text for b in response.content if hasattr(b, "text")), "Done")
            print(f"[Agent] Finished: {final}")
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"[Agent] → {block.name}({block.input})")
                result = await execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str)
                })

        messages.append({"role": "user", "content": tool_results})