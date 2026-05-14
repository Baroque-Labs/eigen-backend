"""Create a demo campaign with the sample recipient list, then run one tick.

Usage:
    make seed                                # campaign name defaults to "Demo"
    EIGEN_SEED_NAME="Spring blast" make seed

Requires the backend to be running on EIGEN_SEED_BASE (default http://localhost:8000)
and a master key in EIGEN_API_KEYS that you can pass as EIGEN_SEED_KEY.
"""
import csv
import os
import sys
from pathlib import Path

import httpx

BASE = os.environ.get("EIGEN_SEED_BASE", "http://localhost:8000")
KEY = os.environ.get("EIGEN_SEED_KEY", "ek_master_dev_dont_use_in_prod")
NAME = os.environ.get("EIGEN_SEED_NAME", "Demo")
CSV_PATH = Path(__file__).parent / "sample_recipients.csv"


def load_recipients() -> list[dict]:
    with CSV_PATH.open() as f:
        return [{"email": r["email"], "cohort": r["cohort"]} for r in csv.DictReader(f)]


def main() -> int:
    recipients = load_recipients()
    client = httpx.Client(base_url=BASE, timeout=15.0)
    client.headers["Authorization"] = f"Bearer {KEY}"

    payload = {
        "name": NAME,
        "baseline": {
            "subject": "Hello {{first_name}} — quick question",
            "body": "<p>Hey {{first_name}},</p><p>We just shipped something I think you'll like. Take a look:</p><p><a href='https://example.com'>See what's new</a></p>",
            "true_ctr": 0.08,
        },
        "n_variants": 4,
        "n_batches": 5,
        "recipients": recipients,
    }

    try:
        r = client.post("/admin/orgs", json={"name": f"seed-{NAME}"})
        if r.status_code == 200:
            org_id = r.json()["id"]
            print(f"created org #{org_id} (seed-{NAME})")
            mint = client.post("/admin/keys", json={"org_id": org_id, "label": "seed"})
            mint.raise_for_status()
            org_key = mint.json()["api_key"]
            client.headers["Authorization"] = f"Bearer {org_key}"
            print(f"minted scoped key for seed org")
    except httpx.HTTPError as e:
        print(f"warning: could not create dedicated seed org ({e}); using master key directly", file=sys.stderr)

    r = client.post("/campaigns", json=payload)
    if r.status_code != 200:
        print(f"failed to create campaign: {r.status_code} {r.text}", file=sys.stderr)
        return 1
    info = r.json()
    print(f"created campaign #{info['id']} '{info['name']}' (batch_size={info['batch_size']})")

    # Tick a couple times so the inbox immediately has rows to play with.
    for i in range(2):
        t = client.post(f"/campaigns/{info['id']}/tick")
        if t.status_code == 200:
            n = len(t.json().get("sends", []))
            print(f"tick {i+1}: dispatched {n} emails")

    print()
    print(f"  → open the inbox: {BASE}/_inbox")
    print(f"  → see posteriors: http://localhost:3000/campaigns/{info['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
