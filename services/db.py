import aiomysql
import asyncpg
from config.settings import settings

# ─────────────────────────────────────────────
# Aman HMO DB (MySQL) — read patient/plan data
# ─────────────────────────────────────────────

async def aman_query_one(sql: str, *args):
    conn = await aiomysql.connect(
        host=settings.aman_db_host,
        port=settings.aman_db_port,
        user=settings.aman_db_user,
        password=settings.aman_db_password,
        db=settings.aman_db_name,
        cursorclass=aiomysql.DictCursor
    )
    async with conn.cursor() as cur:
        await cur.execute(sql, args)
        return await cur.fetchone()

async def aman_query_all(sql: str, *args):
    conn = await aiomysql.connect(
        host=settings.aman_db_host,
        port=settings.aman_db_port,
        user=settings.aman_db_user,
        password=settings.aman_db_password,
        db=settings.aman_db_name,
        cursorclass=aiomysql.DictCursor
    )
    async with conn.cursor() as cur:
        await cur.execute(sql, args)
        return await cur.fetchall()

async def aman_execute(sql: str, *args):
    conn = await aiomysql.connect(
        host=settings.aman_db_host,
        port=settings.aman_db_port,
        user=settings.aman_db_user,
        password=settings.aman_db_password,
        db=settings.aman_db_name,
        cursorclass=aiomysql.DictCursor
    )
    async with conn.cursor() as cur:
        await cur.execute(sql, args)
        await conn.commit()


# ─────────────────────────────────────────────
# Our DB (PostgreSQL) — store logs for dashboard
# ─────────────────────────────────────────────

async def get_pg_conn():
    # Supabase's transaction pooler sits behind PgBouncer. Disabling asyncpg's
    # statement cache keeps the same code safe on both direct Postgres and
    # PgBouncer-backed production URLs.
    return await asyncpg.connect(settings.our_db_url, statement_cache_size=0)

async def pg_query_one(sql: str, *args):
    conn = await get_pg_conn()
    try:
        return await conn.fetchrow(sql, *args)
    finally:
        await conn.close()

async def pg_query_all(sql: str, *args):
    conn = await get_pg_conn()
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()

async def pg_execute(sql: str, *args):
    conn = await get_pg_conn()
    try:
        return await conn.execute(sql, *args)
    finally:
        await conn.close()
