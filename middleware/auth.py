import asyncpg
from fastapi import Request, HTTPException
from config.settings import settings

async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    
    conn = await asyncpg.connect(settings.our_db_url)
    client = await conn.fetchrow(
        "SELECT * FROM api_clients WHERE api_key = $1 AND is_active = TRUE",
        api_key
    )
    await conn.close()
    
    if not client:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return client