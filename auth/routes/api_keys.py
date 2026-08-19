"""API key management routes (SAA-81).

Extracted from auth/router.py.
"""

from fastapi import APIRouter, Depends, HTTPException

from auth.schemas import GenerateKeyPayload
from auth.utils import verify_session_token, generate_api_key
from services.db import pg_execute, pg_query_all, pg_query_one

router = APIRouter()


def _mask_key(k: str) -> str:
    if not k or len(k) < 4:
        return "••••"
    return "••••" + k[-4:]

@router.get("/api-key")
async def list_user_api_keys(claims: dict = Depends(verify_session_token)):
    last_used_column = await pg_query_one(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'api_clients'
              AND column_name = 'last_used_at'
        ) AS exists
        """
    )
    last_used_expr = (
        "last_used_at"
        if last_used_column and last_used_column["exists"]
        else "NULL::timestamptz AS last_used_at"
    )
    rows = await pg_query_all(
        f"""
        SELECT id, client_name, api_key, created_at, {last_used_expr}
        FROM api_clients
        WHERE org_id = $1 AND user_id = $2 AND is_active = TRUE
        ORDER BY created_at DESC
        """,
        claims["org_id"], int(claims["sub"])
    )
    return {
        "keys": [
            {
                "id": r["id"],
                "name": r["client_name"],
                "masked_api_key": _mask_key(r["api_key"]),
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
            }
            for r in rows
        ]
    }

@router.post("/api-key/generate")
async def generate_user_api_key(payload: GenerateKeyPayload, claims: dict = Depends(verify_session_token)):
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can generate API keys")
    org_id = claims["org_id"]
    user_id = int(claims["sub"])
    client = await pg_query_one(
        """
        SELECT clients.name, clients.email
        FROM clients
        JOIN organizations ON organizations.id = clients.org_id
        WHERE clients.id = $1 AND clients.org_id = $2
          AND clients.is_active = TRUE AND organizations.is_active = TRUE
        """,
        user_id, org_id
    )
    if not client:
        raise HTTPException(status_code=404, detail="User or organization not found")

    name = ((payload.name or "").strip()) or f"{client['name']} ({client['email']})"

    api_key = generate_api_key()
    row = await pg_query_one(
        """
        INSERT INTO api_clients (org_id, user_id, client_name, api_key)
        VALUES ($1, $2, $3, $4)
        RETURNING id, client_name, created_at
        """,
        org_id, user_id, name, api_key
    )

    return {
        "message": "API key generated",
        "id": row["id"],
        "name": row["client_name"],
        "api_key": api_key,
        "masked_api_key": _mask_key(api_key),
        "created_at": row["created_at"],
        "note": "Save your API key — it won't be shown again",
    }

@router.delete("/api-key/{key_id}")
async def revoke_user_api_key(key_id: int, claims: dict = Depends(verify_session_token)):
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can revoke API keys")
    # Revoke any key in the caller's org (not just keys the caller created
    # themselves). Org admins manage org-level credentials. The previous
    # `AND user_id = $3` clause made revocation impossible for keys issued
    # by a different admin.
    await pg_execute(
        "DELETE FROM api_clients WHERE id = $1 AND org_id = $2",
        key_id, claims["org_id"]
    )
    return {"message": "API key revoked"}
