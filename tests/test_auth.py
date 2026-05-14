"""Auth: dev mode (open), env master key, and API-key-issued tenancy isolation."""
from eigen import models
from eigen.auth import hash_key, mint_key
from eigen.db import SessionLocal


def _make_campaign(client, headers=None, name="x"):
    return client.post(
        "/campaigns",
        headers=headers or {},
        json={
            "name": name,
            "baseline": {"subject": "hi"},
            "n_variants": 2,
            "n_batches": 2,
            "emails": ["a@example.com", "b@example.com"],
        },
    )


def test_dev_mode_open(client):
    r = _make_campaign(client)
    assert r.status_code == 200


def test_two_orgs_cannot_see_each_other(client):
    db = SessionLocal()
    try:
        org_a = models.Org(name="orgA")
        org_b = models.Org(name="orgB")
        db.add_all([org_a, org_b])
        db.flush()
        key_a = mint_key()
        key_b = mint_key()
        db.add(models.ApiKey(org_id=org_a.id, key_hash=hash_key(key_a), label="a"))
        db.add(models.ApiKey(org_id=org_b.id, key_hash=hash_key(key_b), label="b"))
        db.commit()
    finally:
        db.close()

    ra = _make_campaign(client, headers={"Authorization": f"Bearer {key_a}"}, name="A1")
    assert ra.status_code == 200
    cid = ra.json()["id"]

    # Org B can't see Org A's campaign
    rb = client.get(f"/campaigns/{cid}/state", headers={"Authorization": f"Bearer {key_b}"})
    assert rb.status_code == 404

    # No key at all is rejected once keys exist
    r_noauth = client.get(f"/campaigns/{cid}/state")
    assert r_noauth.status_code == 401

    # Bad key
    r_bad = client.get(f"/campaigns/{cid}/state", headers={"Authorization": "Bearer ek_nope"})
    assert r_bad.status_code == 401
