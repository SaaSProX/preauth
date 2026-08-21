"""Tests for the shared Postgres connection pool in services/db.py.

Every query used to open a brand-new asyncpg connection (measured at 1-4s
per connect against the prod Supavisor pooler). These tests pin down the
pool lifecycle (init/close/lazy-fallback) and the acquire/release contract
around every query helper, using a mocked asyncpg so no real DB is needed.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.db as db


@pytest.fixture(autouse=True)
def reset_pool_state():
    """Every test starts and ends with a clean module-level pool singleton."""
    db._pg_pool = None
    yield
    db._pg_pool = None


def _fake_pool():
    pool = MagicMock()
    conn = MagicMock()
    pool.acquire = AsyncMock(return_value=conn)
    pool.release = AsyncMock()
    pool.close = AsyncMock()
    return pool, conn


class TestInitPgPool:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_creates_pool_with_expected_sizing(self):
        pool, _conn = _fake_pool()
        with patch("services.db.asyncpg.create_pool", new_callable=AsyncMock, return_value=pool) as create_pool:
            await db.init_pg_pool()
            assert db._pg_pool is pool
            _, kwargs = create_pool.call_args
            assert kwargs["min_size"] == 5
            assert kwargs["max_size"] == 30
            assert kwargs["statement_cache_size"] == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_idempotent_when_pool_already_exists(self):
        pool, _conn = _fake_pool()
        db._pg_pool = pool
        with patch("services.db.asyncpg.create_pool", new_callable=AsyncMock) as create_pool:
            await db.init_pg_pool()
            create_pool.assert_not_called()
            assert db._pg_pool is pool

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_concurrent_init_only_creates_pool_once(self):
        """A burst of concurrent requests hitting a cold instance should not
        race into creating multiple pools - the lock must serialize init."""
        pool, _conn = _fake_pool()
        with patch("services.db.asyncpg.create_pool", new_callable=AsyncMock, return_value=pool) as create_pool:
            await asyncio.gather(*[db.init_pg_pool() for _ in range(10)])
            assert create_pool.call_count == 1


class TestClosePgPool:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_closes_and_clears_pool(self):
        pool, _conn = _fake_pool()
        db._pg_pool = pool
        await db.close_pg_pool()
        pool.close.assert_awaited_once()
        assert db._pg_pool is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_noop_when_no_pool(self):
        db._pg_pool = None
        await db.close_pg_pool()  # should not raise
        assert db._pg_pool is None


class TestLazyFallback:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_pg_conn_initializes_pool_if_missing(self):
        """Scripts/tests that call the query helpers without going through
        the FastAPI startup lifecycle should still get a working pool."""
        pool, conn = _fake_pool()
        with patch("services.db.asyncpg.create_pool", new_callable=AsyncMock, return_value=pool):
            result = await db.get_pg_conn()
            assert result is conn
            pool.acquire.assert_awaited_once()


class TestQueryHelpersReleaseConnections:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pg_query_one_releases_connection_on_success(self):
        pool, conn = _fake_pool()
        conn.fetchrow = AsyncMock(return_value={"id": 1})
        db._pg_pool = pool

        result = await db.pg_query_one("SELECT 1")

        assert result == {"id": 1}
        conn.fetchrow.assert_awaited_once_with("SELECT 1")
        pool.release.assert_awaited_once_with(conn)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pg_query_all_releases_connection_on_success(self):
        pool, conn = _fake_pool()
        conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        db._pg_pool = pool

        result = await db.pg_query_all("SELECT * FROM t")

        assert result == [{"id": 1}, {"id": 2}]
        pool.release.assert_awaited_once_with(conn)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pg_execute_releases_connection_on_success(self):
        pool, conn = _fake_pool()
        conn.execute = AsyncMock(return_value="INSERT 0 1")
        db._pg_pool = pool

        result = await db.pg_execute("INSERT INTO t VALUES (1)")

        assert result == "INSERT 0 1"
        pool.release.assert_awaited_once_with(conn)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_connection_is_released_even_when_query_raises(self):
        """A connection must go back to the pool on failure too - otherwise
        a handful of failing requests would leak the pool empty and every
        later request would hang forever waiting for a free connection."""
        pool, conn = _fake_pool()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
        db._pg_pool = pool

        with pytest.raises(RuntimeError, match="boom"):
            await db.pg_query_one("SELECT 1")

        pool.release.assert_awaited_once_with(conn)
