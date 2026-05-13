import aiomysql
from config.settings import settings

async def get_conn():
    return await aiomysql.connect(
        host=settings.aman_db_host,
        port=settings.aman_db_port,
        user=settings.aman_db_user,
        password=settings.aman_db_password,
        db=settings.aman_db_name,
        cursorclass=aiomysql.DictCursor
    )

async def query_one(sql: str, *args):
    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute(sql, args)
        return await cur.fetchone()

async def query_all(sql: str, *args):
    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute(sql, args)
        return await cur.fetchall()

async def execute(sql: str, *args):
    conn = await get_conn()
    async with conn.cursor() as cur:
        await cur.execute(sql, args)
        await conn.commit()
