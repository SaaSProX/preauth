from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from services.db import pg_execute, pg_query_one
from auth.utils import (
    hash_password, verify_password,
    generate_api_key, generate_session_token,
    verify_session_token
)
import secrets

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

    return {"message": "Registration successful"}


# ─────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────

@router.post("/login")
async def login(payload: LoginPayload):
    client = await pg_query_one("SELECT * FROM clients WHERE email = $1", payload.email)

    if not client or not verify_password(payload.password, client["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not client["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    token = generate_session_token(client["id"], client["email"], client["org_id"], client["role"])

    return {
        "token": token,
        "role": client["role"],
        "name": client["name"]
    }


# ─────────────────────────────────────────────
# Generate API key (admin only)
# ─────────────────────────────────────────────

@router.post("/api-key/generate")
async def generate_org_api_key(claims: dict = Depends(verify_session_token)):
    if claims["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can generate API keys")

    org_id = claims["org_id"]
    existing_api_key = await pg_query_one(
        "SELECT id FROM api_clients WHERE org_id = $1 AND is_active = TRUE",
        org_id
    )
    if existing_api_key:
        raise HTTPException(status_code=409, detail="API key already generated for this organization")

    org = await pg_query_one(
        "SELECT name FROM organizations WHERE id = $1 AND is_active = TRUE",
        org_id
    )
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    api_key = generate_api_key()
    await pg_execute(
        "INSERT INTO api_clients (org_id, client_name, api_key) VALUES ($1, $2, $3)",
        org_id, org["name"], api_key
    )

    return {
        "message": "API key generated",
        "api_key": api_key,
        "note": "Save your API key — it won't be shown again"
    }


# ─────────────────────────────────────────────
# Invite team member (admin only)
# ─────────────────────────────────────────────

@router.post("/invite-member")
async def invite_member(payload: TeamInvitePayload, claims: dict = Depends(verify_session_token)):
    # Only admins can invite
    if claims["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can invite team members")

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

    invite_link = f"https://your-dashboard.com/register?token={token}&email={payload.email}"

    # TODO: send invite_link via email to payload.email
    print(f"[Invite] {payload.email} → {invite_link}")

    return {
        "message": f"Invite sent to {payload.email}",
        "invite_link": invite_link  # remove this in production, send via email only
    }


# ─────────────────────────────────────────────
# Get current user info
# ─────────────────────────────────────────────

@router.get("/me")
async def me(claims: dict = Depends(verify_session_token)):
    client = await pg_query_one(
        "SELECT id, name, email, role, created_at FROM clients WHERE id = $1",
        int(claims["sub"])
    )
    if not client:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(client)
