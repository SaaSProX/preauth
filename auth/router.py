import json
import secrets
import asyncio

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from services.db import pg_execute, pg_query_all, pg_query_one
from services.invites import build_invite_link
from services.notifier import EmailDeliveryError, send_invite_email
from auth.utils import (
    hash_password, verify_password,
    generate_api_key, generate_session_token,
    verify_session_token
)

router = APIRouter(prefix="/auth")


# ─────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────

class RegisterPayload(BaseModel):
    invite_token: str
    email: str
    name: str
    password: str

class LoginPayload(BaseModel):
    email: str
    password: str

class TeamInvitePayload(BaseModel):
    email: str


def parse_json_field(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


# ─────────────────────────────────────────────
# Register (via invite link)
# ─────────────────────────────────────────────

@router.post("/register")
async def register(payload: RegisterPayload):
    # Validate invite
    invite = await pg_query_one(
        "SELECT * FROM invites WHERE token = $1 AND used = FALSE",
        payload.invite_token
    )
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid or expired invite token")

    if invite["email"] != payload.email:
        raise HTTPException(status_code=400, detail="Email does not match invite")

    # Check not already registered
    existing = await pg_query_one("SELECT id FROM clients WHERE email = $1", payload.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create client
    password_hash = hash_password(payload.password)
    await pg_execute(
        """
        INSERT INTO clients (org_id, name, email, password_hash, role)
        VALUES ($1, $2, $3, $4, $5)
        """,
        invite["org_id"], payload.name, payload.email, password_hash, invite["role"]
    )

    # Mark invite used
    await pg_execute("UPDATE invites SET used = TRUE WHERE token = $1", payload.invite_token)

    org = await pg_query_one("SELECT name FROM organizations WHERE id = $1", invite["org_id"])

    return {
        "message": "Registration successful",
        "org_name": org["name"] if org else None
    }


# ─────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────

@router.post("/login")
async def login(payload: LoginPayload):
    client = await pg_query_one(
        """
        SELECT clients.*, organizations.name AS org_name
        FROM clients
        LEFT JOIN organizations ON organizations.id = clients.org_id
        WHERE clients.email = $1
        """,
        payload.email
    )

    if not client or not verify_password(payload.password, client["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not client["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    token = generate_session_token(client["id"], client["email"], client["org_id"], client["role"])

    return {
        "token": token,
        "role": client["role"],
        "name": client["name"],
        "org_name": client["org_name"]
    }


# ─────────────────────────────────────────────
# Generate API key (user-owned)
# ─────────────────────────────────────────────

@router.get("/api-key")
async def get_user_api_key(claims: dict = Depends(verify_session_token)):
    api_client = await pg_query_one(
        """
        SELECT id, created_at
        FROM api_clients
        WHERE org_id = $1 AND user_id = $2 AND is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 1
        """,
        claims["org_id"], int(claims["sub"])
    )

    if not api_client:
        return {"has_api_key": False}

    return {
        "has_api_key": True,
        "masked_api_key": "*****",
        "created_at": api_client["created_at"],
    }


@router.post("/api-key/generate")
async def generate_user_api_key(claims: dict = Depends(verify_session_token)):
    org_id = claims["org_id"]
    user_id = int(claims["sub"])
    client = await pg_query_one(
        """
        SELECT clients.name, clients.email, organizations.name AS org_name
        FROM clients
        JOIN organizations ON organizations.id = clients.org_id
        WHERE clients.id = $1 AND clients.org_id = $2
          AND clients.is_active = TRUE AND organizations.is_active = TRUE
        """,
        user_id, org_id
    )
    if not client:
        raise HTTPException(status_code=404, detail="User or organization not found")

    await pg_execute(
        "DELETE FROM api_clients WHERE org_id = $1 AND user_id = $2 AND is_active = TRUE",
        org_id, user_id
    )

    api_key = generate_api_key()
    api_client = await pg_query_one(
        """
        INSERT INTO api_clients (org_id, user_id, client_name, api_key)
        VALUES ($1, $2, $3, $4)
        RETURNING created_at
        """,
        org_id, user_id, f"{client['name']} ({client['email']})", api_key
    )

    return {
        "message": "API key generated",
        "api_key": api_key,
        "created_at": api_client["created_at"],
        "note": "Save your API key — it won't be shown again"
    }


@router.delete("/api-key")
async def revoke_user_api_key(claims: dict = Depends(verify_session_token)):
    await pg_execute(
        "DELETE FROM api_clients WHERE org_id = $1 AND user_id = $2 AND is_active = TRUE",
        claims["org_id"], int(claims["sub"])
    )

    return {"message": "API key revoked"}


# ─────────────────────────────────────────────
# Incoming pre-auth payloads (admin only)
# ─────────────────────────────────────────────

@router.get("/preauth-payloads")
async def list_preauth_payloads(claims: dict = Depends(verify_session_token)):
    if claims["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view incoming payloads")

    rows = await pg_query_all(
        """
        SELECT request_id, patient_id, status, received_at, raw_payload, extracted_fields
        FROM preauth_logs
        WHERE org_id = $1
        ORDER BY received_at DESC
        LIMIT 20
        """,
        claims["org_id"]
    )

    return {
        "payloads": [
            {
                "request_id": row["request_id"],
                "patient_id": row["patient_id"],
                "status": row["status"],
                "received_at": row["received_at"],
                "raw_payload": parse_json_field(row["raw_payload"]) if row["raw_payload"] else None,
                "extracted_fields": parse_json_field(row["extracted_fields"]) if row["extracted_fields"] else None,
            }
            for row in rows
        ]
    }


# ─────────────────────────────────────────────
# Invite team member (admin only)
# ─────────────────────────────────────────────

@router.get("/team")
async def list_team_members(claims: dict = Depends(verify_session_token)):
    if claims["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view team members")

    rows = await pg_query_all(
        """
        SELECT name, email, role, created_at, 'active' AS status
        FROM clients
        WHERE org_id = $1 AND is_active = TRUE

        UNION ALL

        SELECT email AS name, email, role, created_at, 'pending' AS status
        FROM invites
        WHERE org_id = $1 AND used = FALSE

        ORDER BY created_at DESC
        """,
        claims["org_id"]
    )

    return {
        "members": [
            {
                "name": row["name"],
                "email": row["email"],
                "role": row["role"],
                "status": row["status"],
                "created_at": row["created_at"],
                "can_delete": row["email"] != claims["email"],
            }
            for row in rows
        ]
    }


@router.delete("/team-member/{email}")
async def delete_team_member(email: str, claims: dict = Depends(verify_session_token)):
    if claims["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete team members")

    if email == claims["email"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    deleted_invite = await pg_query_one(
        "DELETE FROM invites WHERE org_id = $1 AND email = $2 AND used = FALSE RETURNING id",
        claims["org_id"], email
    )
    if deleted_invite:
        return {"message": f"Pending invite deleted for {email}"}

    member = await pg_query_one(
        "SELECT id FROM clients WHERE org_id = $1 AND email = $2 AND is_active = TRUE",
        claims["org_id"], email
    )
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")

    await pg_execute(
        "DELETE FROM api_clients WHERE org_id = $1 AND user_id = $2",
        claims["org_id"], member["id"]
    )
    await pg_execute(
        "UPDATE clients SET is_active = FALSE WHERE id = $1",
        member["id"]
    )

    return {"message": f"Team member deleted: {email}"}


@router.post("/invite-member")
async def invite_member(request: Request, claims: dict = Depends(verify_session_token)):
    # Only admins can invite
    if claims["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can invite team members")

    try:
        body = await request.json()
        if isinstance(body, str):
            body = json.loads(body)
        payload = TeamInvitePayload(**body)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Request body must include an email")

    # Check not already invited or registered
    existing_invite = await pg_query_one("SELECT id FROM invites WHERE email = $1", payload.email)
    if existing_invite:
        raise HTTPException(status_code=400, detail="Invite already sent to this email")

    existing_client = await pg_query_one("SELECT id FROM clients WHERE email = $1", payload.email)
    if existing_client:
        raise HTTPException(status_code=400, detail="Email already registered")

    token = secrets.token_hex(16)

    await pg_execute(
        """
        INSERT INTO invites (org_id, email, token, role, invited_by)
        VALUES ($1, $2, $3, 'member', $4)
        """,
        claims["org_id"], payload.email, token, int(claims["sub"])
    )

    invite_link = build_invite_link(token, payload.email)
    inviter = await pg_query_one(
        """
        SELECT clients.name AS inviter_name, organizations.name AS org_name
        FROM clients
        JOIN organizations ON organizations.id = clients.org_id
        WHERE clients.id = $1 AND clients.org_id = $2
        """,
        int(claims["sub"]), claims["org_id"]
    )

    try:
        email_result = await asyncio.to_thread(
            send_invite_email,
            payload.email,
            invite_link,
            inviter["org_name"] if inviter else "your organization",
            inviter["inviter_name"] if inviter else None
        )
    except EmailDeliveryError as exc:
        return {
            "message": f"Invite created for {payload.email}, but email was not sent: {exc}",
            "email_sent": False,
        }

    return {
        "message": f"Invite email sent to {payload.email}",
        "email_sent": True,
        "email_id": email_result.get("id")
    }


# ─────────────────────────────────────────────
# Get current user info
# ─────────────────────────────────────────────

@router.get("/me")
async def me(claims: dict = Depends(verify_session_token)):
    client = await pg_query_one(
        """
        SELECT clients.id, clients.name, clients.email, clients.role, clients.created_at,
               organizations.name AS org_name
        FROM clients
        LEFT JOIN organizations ON organizations.id = clients.org_id
        WHERE clients.id = $1
        """,
        int(claims["sub"])
    )
    if not client:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(client)
