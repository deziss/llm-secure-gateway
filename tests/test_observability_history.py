"""Comprehensive tests for multi-scale observability historical timeseries and dashboard APIs.

Tests:
- LiveStats minute-bucket accumulation and extraction (get_minute_rollups, drain_minute_rollups).
- Atomic UPSERT into model_telemetry_rollups and retention cleanup (flush_telemetry_rollups, prune_old_telemetry_rollups).
- GET /admin/observability/history for all timeframes: 1s, 1m, 1h, 6h, 12h, 24h.
- GET /admin/dashboard/traffic-24h: 24-hour hourly bucketing, totals, filters.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select

from llm_gateway.auth.models import Role
from llm_gateway.auth.users import current_active_user
from llm_gateway.database import get_session
from llm_gateway.models import ModelTelemetryRollup
from llm_gateway.observability import api
from llm_gateway.observability import store as store_mod
from llm_gateway.observability.collect import poller
from llm_gateway.observability.store import LiveStats
from llm_gateway.routers import dashboard


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def clean_live_store():
    store_mod.get_live_stats().drain_minute_rollups()
    yield
    store_mod.get_live_stats().drain_minute_rollups()


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


def _build_test_app(db_sess: AsyncSession, role=Role.ADMIN):
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(dashboard.router)

    user = SimpleNamespace(id="test-user", role=role, is_superuser=(role == Role.ADMIN))
    app.dependency_overrides[current_active_user] = lambda: user

    async def _override_session():
        yield db_sess

    app.dependency_overrides[get_session] = _override_session
    return app


def _client(app: FastAPI):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------- #
# 1. LiveStats minute rollups accumulation & draining
# --------------------------------------------------------------------------- #
def test_livestats_minute_bucket_accumulation():
    st = LiveStats()
    key = ("srv-1", "vllm", "qwen3.8")
    now_dt = datetime(2026, 11, 5, 12, 0, 15)

    # Dispatch two requests in the same minute
    st.dispatch(key, now=now_dt)
    st.dispatch(key, now=now_dt)

    # First token and finish one successfully
    st.first_token(key)
    st.finish(
        key,
        was_generating=True,
        success=True,
        output_tokens=100,
        prompt_tokens=50,
        total_tokens=150,
        decode_seconds=2.0,
        ttft=0.25,
        itl_ms=20.0,
        now=now_dt,
    )

    # Finish second as an error
    st.finish(
        key,
        was_generating=False,
        success=False,
        output_tokens=0,
        prompt_tokens=20,
        now=now_dt,
    )

    # Sample concurrency
    st.sample_concurrency(now=now_dt)

    # Record server scrape
    st.record_server_scrape(
        server="srv-1",
        server_type="vllm",
        model="qwen3.8",
        server_rps=1.5,
        server_tok_s=50.0,
        server_running=2,
        server_waiting=1,
        kv_cache_pct=45.0,
        now=now_dt,
    )

    # Non-destructive inspect
    peek = st.get_minute_rollups()
    assert len(peek) == 1
    r = peek[0]
    assert r["bucket_time"] == datetime(2026, 11, 5, 12, 0, 0)
    assert r["server"] == "srv-1"
    assert r["model"] == "qwen3.8"
    assert r["requests"] == 2
    assert r["errors"] == 1
    assert r["prompt_tokens"] == 70
    assert r["completion_tokens"] == 100
    assert r["total_tokens"] == 170
    assert r["ttft_count"] == 1
    assert r["ttft_sum_ms"] == pytest.approx(250.0)
    assert r["ttft_p50_ms"] == pytest.approx(250.0)
    assert r["decode_n"] == 1
    assert r["decode_tps_sum"] == pytest.approx(50.0)
    assert r["itl_count"] == 1
    assert r["itl_sum_ms"] == pytest.approx(20.0)
    assert r["samples_count"] == 1
    assert r["server_scrape_count"] == 1
    assert r["server_rps_sum"] == pytest.approx(1.5)
    assert r["kv_cache_pct_sum"] == pytest.approx(45.0)

    # Destructive drain clears in-memory minute buckets
    drained = st.drain_minute_rollups()
    assert len(drained) == 1
    assert st.drain_minute_rollups() == []


# --------------------------------------------------------------------------- #
# 2. Flush to DB (atomic UPSERT) and retention cleanup
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_flush_telemetry_rollups_upsert_and_prune(db_session: AsyncSession, monkeypatch):
    store = LiveStats()
    monkeypatch.setattr(store_mod, "get_live_stats", lambda: store)
    monkeypatch.setattr(store_mod, "_LIVE", store)

    key = ("srv-upsert", "vllm", "qwen3.8")
    now_dt = datetime(2026, 11, 5, 12, 0, 10)

    # Dispatch & finish 1 request
    store.dispatch(key, now=now_dt)
    store.first_token(key)
    store.finish(
        key,
        was_generating=True,
        success=True,
        output_tokens=60,
        prompt_tokens=40,
        total_tokens=100,
        decode_seconds=1.5,
        ttft=0.2,
        now=now_dt,
    )

    # First flush
    count1 = await poller.flush_telemetry_rollups(db_session)
    assert count1 == 1

    rows = (await db_session.execute(select(ModelTelemetryRollup))).scalars().all()
    assert len(rows) == 1
    assert rows[0].requests == 1
    assert rows[0].total_tokens == 100
    assert rows[0].ttft_sum_ms == pytest.approx(200.0)

    # Dispatch 2 more requests in the same minute and flush again
    store.dispatch(key, now=now_dt)
    store.first_token(key)
    store.finish(
        key,
        was_generating=True,
        success=True,
        output_tokens=40,
        prompt_tokens=30,
        total_tokens=70,
        decode_seconds=1.0,
        ttft=0.1,
        now=now_dt,
    )

    count2 = await poller.flush_telemetry_rollups(db_session)
    assert count2 == 1

    # Conflict on (bucket_time, server, model) must update, not insert duplicate
    db_session.expire_all()
    rows = (await db_session.execute(select(ModelTelemetryRollup))).scalars().all()
    assert len(rows) == 1
    assert rows[0].requests == 2
    assert rows[0].prompt_tokens == 70
    assert rows[0].completion_tokens == 100
    assert rows[0].total_tokens == 170
    assert rows[0].ttft_count == 2
    assert rows[0].ttft_sum_ms == pytest.approx(300.0)

    # Test retention pruning (older than 30 days)
    old_time = _now() - timedelta(days=35)
    db_session.add(
        ModelTelemetryRollup(
            bucket_time=old_time,
            server="srv-old",
            server_type="vllm",
            model="qwen3.8",
            requests=10,
        )
    )
    await db_session.commit()

    pruned = await poller.prune_old_telemetry_rollups(db_session, days=30)
    assert pruned == 1

    # Verify old row is deleted, current row remains
    all_rows = (await db_session.execute(select(ModelTelemetryRollup))).scalars().all()
    assert len(all_rows) == 1
    assert all_rows[0].server == "srv-upsert"


# --------------------------------------------------------------------------- #
# 3. GET /admin/observability/history for all timeframes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_history_all_timeframes(db_session: AsyncSession, monkeypatch):
    store = LiveStats()
    monkeypatch.setattr(store_mod, "get_live_stats", lambda: store)
    monkeypatch.setattr(store_mod, "_LIVE", store)

    now = _now().replace(second=0, microsecond=0)

    # Seed rollups over the last 24 hours (1 rollup per hour)
    for h in range(24):
        t = now - timedelta(hours=23 - h)
        db_session.add(
            ModelTelemetryRollup(
                bucket_time=t,
                server="srv-a",
                server_type="vllm",
                model="qwen3.8-flash-next",
                requests=10 + h,
                errors=1 if h % 5 == 0 else 0,
                prompt_tokens=100 * (10 + h),
                completion_tokens=50 * (10 + h),
                total_tokens=150 * (10 + h),
                ttft_sum_ms=2000.0,
                ttft_count=10 + h,
                ttft_p50_ms=120.0,
                ttft_p90_ms=250.0,
                itl_sum_ms=150.0,
                itl_count=10 + h,
                decode_tps_sum=500.0,
                decode_n=10 + h,
                waiting_sum=2,
                generating_sum=5,
                samples_count=10,
                server_rps_sum=12.0,
                server_tok_s_sum=450.0,
                server_running_sum=30,
                server_waiting_sum=5,
                kv_cache_pct_sum=350.0,
                server_scrape_count=10,
            )
        )
    await db_session.commit()

    app = _build_test_app(db_session, role=Role.ADMIN)

    async with _client(app) as client:
        # A. 1s live history
        key = ("srv-a", "vllm", "qwen3.8-flash-next")
        store.dispatch(key)
        store.first_token(key)
        store.finish(
            key,
            was_generating=True,
            success=True,
            output_tokens=25,
            prompt_tokens=10,
            total_tokens=35,
            decode_seconds=0.5,
            ttft=0.15,
        )

        r_1s = await client.get("/admin/observability/history?timeframe=1s")
        assert r_1s.status_code == 200
        d_1s = r_1s.json()
        assert d_1s["timeframe"] == "1s"
        assert d_1s["bucket_interval_s"] == 1
        assert len(d_1s["models"]) >= 1
        m1s = next(m for m in d_1s["models"] if m["model"] == "qwen3.8-flash-next")
        assert m1s["total_requests"] >= 1
        assert len(m1s["series"]) == 60
        assert "avg_rps" in m1s["summary"]
        assert "peak_rps" in m1s["summary"]
        assert "tok_s_decode" in m1s["summary"]
        assert "tok_s_throughput" in m1s["summary"]

        # B. 1m timeframe
        r_1m = await client.get("/admin/observability/history?timeframe=1m")
        assert r_1m.status_code == 200
        d_1m = r_1m.json()
        assert d_1m["timeframe"] == "1m"
        assert d_1m["bucket_interval_s"] == 60
        assert "models" in d_1m

        # C. 1h timeframe
        r_1h = await client.get("/admin/observability/history?timeframe=1h")
        assert r_1h.status_code == 200
        d_1h = r_1h.json()
        assert d_1h["timeframe"] == "1h"
        assert d_1h["bucket_interval_s"] == 60
        m1h = next(m for m in d_1h["models"] if m["model"] == "qwen3.8-flash-next")
        assert len(m1h["series"]) == 60
        assert m1h["summary"]["total_requests"] > 0
        assert m1h["summary"]["total_tokens"] > 0
        assert m1h["summary"]["ttft_p50_ms"] is not None

        # D. 6h timeframe
        r_6h = await client.get("/admin/observability/history?timeframe=6h")
        assert r_6h.status_code == 200
        d_6h = r_6h.json()
        assert d_6h["timeframe"] == "6h"
        assert d_6h["bucket_interval_s"] == 300
        m6h = next(m for m in d_6h["models"] if m["model"] == "qwen3.8-flash-next")
        assert len(m6h["series"]) == 72

        # E. 12h timeframe
        r_12h = await client.get("/admin/observability/history?timeframe=12h")
        assert r_12h.status_code == 200
        d_12h = r_12h.json()
        assert d_12h["timeframe"] == "12h"
        assert d_12h["bucket_interval_s"] == 600
        m12h = next(m for m in d_12h["models"] if m["model"] == "qwen3.8-flash-next")
        assert len(m12h["series"]) == 72

        # F. 24h timeframe
        r_24h = await client.get("/admin/observability/history?timeframe=24h")
        assert r_24h.status_code == 200
        d_24h = r_24h.json()
        assert d_24h["timeframe"] == "24h"
        assert d_24h["bucket_interval_s"] == 1200
        m24h = next(m for m in d_24h["models"] if m["model"] == "qwen3.8-flash-next")
        assert len(m24h["series"]) == 72
        assert m24h["summary"]["total_requests"] > 200
        assert m24h["summary"]["error_rate_pct"] > 0.0
        assert m24h["summary"]["kv_cache_pct_avg"] == pytest.approx(35.0)

        # G. Filtering by server / model
        r_filtered = await client.get("/admin/observability/history?timeframe=24h&server=srv-a&model=qwen3.8-flash-next")
        assert r_filtered.status_code == 200
        for m in r_filtered.json()["models"]:
            assert m["server"] == "srv-a"
            assert m["model"] == "qwen3.8-flash-next"

        r_empty = await client.get("/admin/observability/history?timeframe=24h&server=nonexistent")
        assert r_empty.status_code == 200
        assert len(r_empty.json()["models"]) == 0


# --------------------------------------------------------------------------- #
# 4. GET /admin/dashboard/traffic-24h
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dashboard_traffic_24h(db_session: AsyncSession):
    now = _now().replace(minute=0, second=0, microsecond=0)

    # Insert sample traffic for 3 different hours
    db_session.add(
        ModelTelemetryRollup(
            bucket_time=now - timedelta(hours=5),
            server="srv-main",
            server_type="vllm",
            model="qwen3.8",
            requests=50,
            errors=2,
            total_tokens=10000,
            ttft_sum_ms=5000.0,
            ttft_count=50,
        )
    )
    db_session.add(
        ModelTelemetryRollup(
            bucket_time=now - timedelta(hours=2),
            server="srv-main",
            server_type="vllm",
            model="qwen3.8",
            requests=30,
            errors=0,
            total_tokens=6000,
            ttft_sum_ms=3000.0,
            ttft_count=30,
        )
    )
    await db_session.commit()

    app = _build_test_app(db_session, role=Role.ADMIN)

    async with _client(app) as client:
        res = await client.get("/admin/dashboard/traffic-24h")
        assert res.status_code == 200
        data = res.json()

        assert "start_time" in data
        assert "end_time" in data
        assert data["total_requests"] == 80
        assert data["total_tokens"] == 16000
        assert data["total_errors"] == 2
        assert data["avg_ttft_ms"] == pytest.approx(100.0)

        assert len(data["hourly"]) == 24
        # Verify hours with traffic have non-zero requests
        non_zero = [h for h in data["hourly"] if h["requests"] > 0]
        assert len(non_zero) == 2
        assert sum(h["requests"] for h in data["hourly"]) == 80
        assert sum(h["total_tokens"] for h in data["hourly"]) == 16000

        # Filter by server
        res_filtered = await client.get("/admin/dashboard/traffic-24h?server=srv-main")
        assert res_filtered.status_code == 200
        assert res_filtered.json()["total_requests"] == 80

        res_miss = await client.get("/admin/dashboard/traffic-24h?server=other")
        assert res_miss.status_code == 200
        assert res_miss.json()["total_requests"] == 0


# --------------------------------------------------------------------------- #
# 5. Auth gate: regular user denied
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_observability_history_auth_guard(db_session: AsyncSession):
    app = _build_test_app(db_session, role=Role.VIEWER)

    async with _client(app) as client:
        r1 = await client.get("/admin/observability/history?timeframe=1h")
        assert r1.status_code == 403

        r2 = await client.get("/admin/dashboard/traffic-24h")
        assert r2.status_code == 403


# --------------------------------------------------------------------------- #
# 6. PostgreSQL integration test (real database in Docker environment)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_postgres_flush_and_history_e2e(monkeypatch):
    import os
    pg_url = os.getenv("PG_TEST_URL", "postgresql+asyncpg://gateway:password@postgres:5432/gateway_db")
    try:
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        pg_engine = create_async_engine(pg_url)
        async with pg_engine.connect() as conn:
            pass
    except Exception:
        pytest.skip("Skipping PostgreSQL test: postgres container not directly reachable with test settings")
        return

    from llm_gateway.database import get_session

    store = LiveStats()
    monkeypatch.setattr(store_mod, "get_live_stats", lambda: store)
    monkeypatch.setattr(store_mod, "_LIVE", store)

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0, tzinfo=None)
    key = ("pg-test-srv", "vllm", "qwen3.8-flash-next")

    # Record traffic in LiveStats
    store.dispatch(key, now=now)
    store.first_token(key)
    store.finish(
        key,
        was_generating=True,
        success=True,
        output_tokens=100,
        prompt_tokens=50,
        total_tokens=150,
        decode_seconds=2.0,
        ttft=0.3,
        now=now,
    )

    async with AsyncSession(pg_engine, expire_on_commit=False) as sess:
        flushed = await poller.flush_telemetry_rollups(sess)
        assert flushed >= 1

        app = FastAPI()
        app.include_router(api.router)
        app.include_router(dashboard.router)
        user = SimpleNamespace(id="admin-user", role=Role.ADMIN, is_superuser=True)
        app.dependency_overrides[current_active_user] = lambda: user

        async def _sess():
            yield sess

        app.dependency_overrides[get_session] = _sess

        try:
            async with _client(app) as client:
                res = await client.get("/admin/observability/history?timeframe=1h&server=pg-test-srv")
                assert res.status_code == 200
                data = res.json()
                assert data["timeframe"] == "1h"
                matched = [m for m in data["models"] if m["server"] == "pg-test-srv"]
                assert len(matched) == 1
                assert matched[0]["total_requests"] >= 1
                assert matched[0]["total_tokens"] >= 150

                dash_res = await client.get("/admin/dashboard/traffic-24h?server=pg-test-srv")
                assert dash_res.status_code == 200
                dash_data = dash_res.json()
                assert dash_data["total_requests"] >= 1
                assert dash_data["total_tokens"] >= 150
        finally:
            from sqlalchemy import delete
            await sess.execute(
                delete(ModelTelemetryRollup).where(ModelTelemetryRollup.server == "pg-test-srv")
            )
            await sess.commit()

    await pg_engine.dispose()

