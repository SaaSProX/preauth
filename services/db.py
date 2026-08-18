import asyncio

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
#
# Every query used to open + close a brand-new asyncpg connection (fresh
# TCP + TLS + Supavisor auth handshake each time — measured at 1-4s per
# connect against the prod pooler). Under concurrent requests that cost
# multiplies and can dominate wall-clock time even though the queries
# themselves run in well under a second. A shared pool amortizes that
# handshake across requests instead of paying it on every query.

_pg_pool: asyncpg.Pool | None = None
_pg_pool_lock = asyncio.Lock()


async def init_pg_pool() -> None:
    """Create the shared pool. Call once from app startup."""
    global _pg_pool
    if _pg_pool is not None:
        return
    async with _pg_pool_lock:
        if _pg_pool is None:
            _pg_pool = await asyncpg.create_pool(
                settings.our_db_url,
                # Supabase's transaction pooler sits behind PgBouncer. Disabling
                # asyncpg's statement cache keeps the same code safe on both
                # direct Postgres and PgBouncer-backed production URLs.
                statement_cache_size=0,
                # Vercel Fluid Compute keeps one shared process handling all
                # concurrent requests, so this is the *only* pool, not one per
                # instance. Sized against Supabase's max_connections=60 with
                # headroom for Supavisor's own overhead and other consumers.
                min_size=5,
                max_size=30,
            )


async def close_pg_pool() -> None:
    """Close the shared pool. Call once from app shutdown."""
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None


async def _get_pool() -> asyncpg.Pool:
    if _pg_pool is None:
        # Defensive fallback for scripts/tests that use these helpers without
        # going through the FastAPI startup lifecycle.
        await init_pg_pool()
    return _pg_pool


async def get_pg_conn():
    pool = await _get_pool()
    return await pool.acquire()


async def release_pg_conn(conn) -> None:
    pool = await _get_pool()
    await pool.release(conn)


async def pg_query_one(sql: str, *args):
    conn = await get_pg_conn()
    try:
        return await conn.fetchrow(sql, *args)
    finally:
        await release_pg_conn(conn)

async def pg_query_all(sql: str, *args):
    conn = await get_pg_conn()
    try:
        return await conn.fetch(sql, *args)
    finally:
        await release_pg_conn(conn)

async def pg_execute(sql: str, *args):
    conn = await get_pg_conn()
    try:
        return await conn.execute(sql, *args)
    finally:
        await release_pg_conn(conn)
