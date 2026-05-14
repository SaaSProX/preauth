import bcrypt
import jwt
import secrets
from datetime import datetime, timedelta
from fastapi import Request, HTTPException
from config.settings import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def generate_api_key() -> str:
    return secrets.token_hex(32)


def generate_session_token(client_id: int, email: str, org_id: int, role: str) -> str:
    return jwt.encode(
        {
            "sub": str(client_id),
            "email": email,
            "org_id": org_id,
            "role": role,
            "exp": datetime.utcnow() + timedelta(days=7)
        },
        settings.jwt_secret,
        algorithm="HS256"
    )


def verify_session_token(request: Request) -> dict:
    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")

    token = auth_header.split(" ")[1]

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
