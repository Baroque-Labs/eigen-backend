"""Scheduler tasks operate over all campaigns."""
import asyncio

from eigen.config import settings
from eigen.db import SessionLocal
from eigen.scheduler import cron_settle_campaigns, cron_tick_campaigns


def test_scheduler_disabled_by_default(client):
    result = asyncio.run(cron_tick_campaigns(ctx={}))
    assert result == {"skipped": "scheduler disabled"}


def test_scheduler_ticks_all_campaigns(client, monkeypatch):
    monkeypatch.setenv("EIGEN_SCHEDULER_ENABLED", "true")
    settings.cache_clear()

    # Two campaigns
    for name in ("A", "B"):
        client.post(
            "/campaigns",
            json={
                "name": name,
                "baseline": {"subject": f"hi {name}", "true_ctr": 0.05},
                "n_variants": 2,
                "batch_size": 100,
                "emails": [f"{name}{i}@example.com" for i in range(4)],
            },
        ).raise_for_status()

    result = asyncio.run(cron_tick_campaigns(ctx={}))
    assert "ticked" in result
    # With cadence_minutes=60 (default) and last_tick_at=None, both fire on
    # the first sweep. Subsequent sweeps within wall_seconds_between_ticks
    # would be no-ops.
    assert len(result["ticked"]) == 2

    # Settle with default 24h sim-window: nothing settles yet.
    settle_result = asyncio.run(cron_settle_campaigns(ctx={}))
    assert settle_result["settled"] == {}

    settings.cache_clear()
