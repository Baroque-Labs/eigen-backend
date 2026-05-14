"""End-to-end: create campaign, tick, fire webhook clicks via FakeESP, watch posteriors update."""
import uuid


def _create_campaign(client, n_recipients=20):
    payload = {
        "name": "test",
        "baseline": {"subject": "Hello", "true_ctr": 0.1},
        "n_variants": 2,
        "n_batches": 2,
        "emails": [f"u{i}@example.com" for i in range(n_recipients)],
    }
    r = client.post("/campaigns", json=payload)
    r.raise_for_status()
    return r.json()


def test_full_loop_with_fake_esp(client, fake):
    info = _create_campaign(client)
    cid = info["id"]

    r = client.post(f"/campaigns/{cid}/tick")
    sends = r.json()["sends"]
    assert len(sends) > 0
    assert len(fake.sends) == len(sends)

    # Fire a click webhook for the first send
    first = fake.sends[0]
    evt = {
        "event_id": str(uuid.uuid4()),
        "kind": "click",
        "provider_message_id": first.provider_message_id,
        "to": first.to,
    }
    r = client.post("/webhooks/fake", json=evt)
    assert r.json() == {"ok": True, "duplicate": False, "send_id": sends[0]["send_id"]}

    # State should reflect 1 click
    st = client.get(f"/campaigns/{cid}/state").json()
    assert st["total_clicks"] == 1


def test_webhook_idempotency(client, fake):
    info = _create_campaign(client)
    cid = info["id"]
    sends = client.post(f"/campaigns/{cid}/tick").json()["sends"]
    first = fake.sends[0]

    evt = {"event_id": "dedupe-me", "kind": "click", "provider_message_id": first.provider_message_id, "to": first.to}
    r1 = client.post("/webhooks/fake", json=evt)
    r2 = client.post("/webhooks/fake", json=evt)
    r3 = client.post("/webhooks/fake", json=evt)
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True
    assert r3.json()["duplicate"] is True

    st = client.get(f"/campaigns/{cid}/state").json()
    assert st["total_clicks"] == 1  # not 3


def test_suppression_blocks_subsequent_sends(client, fake):
    info = _create_campaign(client, n_recipients=4)
    cid = info["id"]

    first = client.post(f"/campaigns/{cid}/tick").json()["sends"]
    assert len(first) == 2  # batch_size = ceil(4/2)
    bounced_email = fake.sends[0].to

    # Bounce one of them
    client.post(
        "/webhooks/fake",
        json={
            "event_id": "bounce-1",
            "kind": "bounced",
            "provider_message_id": fake.sends[0].provider_message_id,
            "to": bounced_email,
        },
    ).raise_for_status()

    # Next tick should skip the bounced recipient
    second = client.post(f"/campaigns/{cid}/tick").json()["sends"]
    new_recipients = {s["recipient"] for s in second}
    assert bounced_email not in new_recipients
