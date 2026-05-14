"""GET /campaigns list, bulk recipients, timeseries."""


def _create(client, name="x"):
    return client.post(
        "/campaigns",
        json={
            "name": name,
            "baseline": {"subject": "hi", "true_ctr": 0.05},
            "n_variants": 2,
            "n_batches": 2,
            "emails": ["a@example.com", "b@example.com"],
        },
    ).json()


def test_list_campaigns_for_org(client):
    _create(client, "campaign-1")
    _create(client, "campaign-2")
    listing = client.get("/campaigns").json()
    names = {c["name"] for c in listing["campaigns"]}
    assert names == {"campaign-1", "campaign-2"}
    for c in listing["campaigns"]:
        assert "status" in c and "total_sends" in c and "total_clicks" in c


def test_bulk_add_recipients(client):
    info = _create(client)
    cid = info["id"]
    r = client.post(
        f"/campaigns/{cid}/recipients",
        json={"emails": ["c@example.com", "d@example.com"]},
    )
    assert r.status_code == 200
    assert r.json()["added"] == 2

    # Add with cohort
    r2 = client.post(
        f"/campaigns/{cid}/recipients",
        json={"recipients": [{"email": "e@example.com", "cohort": "eu"}]},
    )
    assert r2.json()["added"] == 1


def test_timeseries_empty_then_with_sends(client, fake):
    info = _create(client)
    cid = info["id"]
    # Before any sends — empty points
    empty = client.get(f"/campaigns/{cid}/timeseries").json()
    assert empty["points"] == []

    client.post(f"/campaigns/{cid}/tick")
    with_sends = client.get(f"/campaigns/{cid}/timeseries").json()
    assert len(with_sends["points"]) >= 1
    assert with_sends["points"][0]["sends"] > 0
