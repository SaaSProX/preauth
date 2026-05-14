# scripts/generate_api_key.py
import secrets
import asyncio
import asyncpg
from config.settings import settings

async def create_client(name: str):
    api_key = secrets.token_hex(32)
    conn = await asyncpg.connect(settings.our_db_url)
    await conn.execute(
        "INSERT INTO api_clients (client_name, api_key) VALUES ($1, $2)",
        name, api_key
    )
    await conn.close()
    print(f"Client: {name}")
    print(f"API Key: {api_key}")

asyncio.run(create_client("Aman HMO"))
