"""Async send mode test — requires Redis at EIGEN_REDIS_URL. Skipped if unreachable."""
import asyncio
import os

import pytest
from arq import create_pool
from arq.connections import RedisSettings

REDIS_URL = os.environ.get("EIGEN_REDIS_URL", "redis://localhost:6379/0")


async def _redis_alive() -> bool:
    try:
        pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
        await pool.aclose() if hasattr(pool, "aclose") else None
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def redis_available():
    return asyncio.run(_redis_alive())


def test_async_tick_enqueues_jobs(client, fake, monkeypatch, redis_available):
    if not redis_available:
        pytest.skip("redis not available")

    monkeypatch.setenv("EIGEN_SEND_MODE", "async")
    from eigen.config import settings
    settings.cache_clear()

    info = client.post(
        "/campaigns",
        json={
            "name": "async-test",
            "baseline": {"subject": "hi", "true_ctr": 0.05},
            "n_variants": 2,
            "n_batches": 2,
            "emails": [f"a{i}@example.com" for i in range(4)],
        },
    ).json()
    cid = info["id"]
    r = client.post(f"/campaigns/{cid}/tick").json()
    assert r["mode"] == "async"
    # In async mode, fake dispatcher hasn't run yet — sends are queued in arq
    assert len(fake.sends) == 0
    # The Send rows exist but have no provider_message_id yet
    st = client.get(f"/campaigns/{cid}/state").json()
    assert st["total_sends"] == 2

    settings.cache_clear()
