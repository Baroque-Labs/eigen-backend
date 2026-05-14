"""Create a demo campaign with the sample recipient list, then run one tick.

Usage:
    make seed                                # campaign in the dashboard's org
    EIGEN_SEED_NAME="Spring blast" make seed
    EIGEN_SEED_TARGET="dev workspace" make seed   # which dashboard org to seed under

The seed lands inside whatever backend org the dashboard's `EIGEN_SEED_TARGET`
workspace currently routes to, so the campaign is immediately visible in the
dashboard's campaign list. Looks up the per-org API key by reading the
frontend Drizzle table directly (eigen_fe.api_keys).

If FRONTEND_DATABASE_URL isn't set, falls back to the master key, which makes
the campaign owned by the backend's "default" org — useful for CLI-only use,
but won't show in the dashboard.
"""
import csv
import os
import sys
from pathlib import Path

import httpx

BASE = os.environ.get("EIGEN_SEED_BASE", "http://localhost:8000")
NAME = os.environ.get("EIGEN_SEED_NAME", "Demo")
TARGET_WORKSPACE = os.environ.get("EIGEN_SEED_TARGET", "dev workspace")
FRONTEND_DB_URL = os.environ.get(
    "FRONTEND_DATABASE_URL",
    # Default matches eigen/.env on this machine. Override via env in CI/prod.
    "postgresql://postgres:REDACTED@yamanote.proxy.rlwy.net:30752/eigen_fe",
)
MASTER_KEY = os.environ.get("EIGEN_SEED_KEY", "ek_master_dev_dont_use_in_prod")
CSV_PATH = Path(__file__).parent / "sample_recipients.csv"


def load_recipients() -> list[dict]:
    with CSV_PATH.open() as f:
        return [{"email": r["email"], "cohort": r["cohort"]} for r in csv.DictReader(f)]


def lookup_dashboard_key(workspace_name: str) -> str | None:
    """Read the dashboard's stored backend API key for a given workspace.

    Returns None if the frontend DB isn't reachable or no row matches.
    """
    try:
        import psycopg  # type: ignore
    except ImportError:
        return None
    try:
        with psycopg.connect(FRONTEND_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ak.raw_key
                    FROM api_keys ak
                    JOIN organizations o ON o.id = ak.org_id
                    WHERE o.name = %s
                    ORDER BY ak.created_at DESC
                    LIMIT 1
                    """,
                    (workspace_name,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        print(f"  (couldn't read frontend DB: {e})", file=sys.stderr)
        return None


def main() -> int:
    recipients = load_recipients()
    client = httpx.Client(base_url=BASE, timeout=15.0)

    key = lookup_dashboard_key(TARGET_WORKSPACE)
    if key:
        print(f"using dashboard org's key ({TARGET_WORKSPACE!r})")
    else:
        print(
            f"no key found for workspace {TARGET_WORKSPACE!r}; falling back to master key. "
            f"Open the dashboard at /campaigns at least once to provision a key, then re-run."
        )
        key = MASTER_KEY
    client.headers["Authorization"] = f"Bearer {key}"

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
