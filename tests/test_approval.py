"""Pending → approved/rejected lifecycle."""
import os


def _create(client):
    return client.post(
        "/campaigns",
        json={
            "name": "approve-test",
            "baseline": {"subject": "hi", "true_ctr": 0.05},
            "n_variants": 3,
            "batch_size": 100,
            "emails": [f"u{i}@example.com" for i in range(6)],
        },
    ).json()


def test_pending_when_auto_spawn_off(client, monkeypatch):
    monkeypatch.setenv("EIGEN_AUTO_SPAWN", "false")
    from eigen.config import settings
    settings.cache_clear()

    info = _create(client)
    cid = info["id"]
    # Force a research cycle by manipulating posteriors so a spawn happens.
    # Simpler: call /research a couple times — with no traffic, nothing kills, but spawn
    # also requires a "best" survivor. We'll instead inspect the initial spawn at create.
    pending = client.get(f"/campaigns/{cid}/pending").json()["variants"]
    # At creation we spawn n_variants - 1 = 2 children. They should all be pending.
    assert len(pending) >= 2

    # Approve one
    vid = pending[0]["id"]
    r = client.post(f"/campaigns/{cid}/variants/{vid}/approve").json()
    assert r["status"] == "active"

    # Reject another
    vid2 = pending[1]["id"]
    r = client.post(f"/campaigns/{cid}/variants/{vid2}/reject").json()
    assert r["status"] == "rejected"

    # Approving an already-active variant should 409
    r = client.post(f"/campaigns/{cid}/variants/{vid}/approve")
    assert r.status_code == 409

    settings.cache_clear()


def test_active_when_auto_spawn_on(client, monkeypatch):
    monkeypatch.setenv("EIGEN_AUTO_SPAWN", "true")
    from eigen.config import settings
    settings.cache_clear()

    info = _create(client)
    cid = info["id"]
    pending = client.get(f"/campaigns/{cid}/pending").json()["variants"]
    assert pending == []

    settings.cache_clear()
