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
                "n_batches": 2,
                "emails": [f"{name}{i}@example.com" for i in range(4)],
            },
        ).raise_for_status()

    result = asyncio.run(cron_tick_campaigns(ctx={}))
    assert "ticked" in result
    assert len(result["ticked"]) == 2

    # Now settle should close them out (window_seconds=0 path doesn't apply, but
    # settle window defaults to 24h — so newly-sent records won't settle yet).
    settle_result = asyncio.run(cron_settle_campaigns(ctx={}))
    assert settle_result["settled"] == 0  # window hasn't elapsed

    settings.cache_clear()
