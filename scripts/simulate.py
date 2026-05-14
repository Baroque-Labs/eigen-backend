"""Drive the eigen-backend through a simulated campaign.

Usage:
    # Terminal 1
    uvicorn eigen.main:app --reload

    # Terminal 2
    python scripts/simulate.py
"""
import os
import random
import sys

import httpx

BASE = os.environ.get("EIGEN_BASE", "http://127.0.0.1:8000")


def main():
    client = httpx.Client(base_url=BASE, timeout=30.0)

    n_variants = 4
    n_batches = 25
    payload = {
        "name": "Spring blast",
        "baseline": {"subject": "Big news inside", "true_ctr": 0.08},
        "n_variants": n_variants,
        "n_batches": n_batches,
        "emails": [f"u{i}@example.com" for i in range(5000)],
    }
    r = client.post("/campaigns", json=payload)
    r.raise_for_status()
    info = r.json()
    campaign_id = info["id"]
    batch_size = info["batch_size"]
    print(f"campaign={campaign_id} n_variants={n_variants} batch_size={batch_size}")

    rng = random.Random(42)
    baseline_ctr = payload["baseline"]["true_ctr"]

    def refresh_truth() -> dict[int, float]:
        raw = client.get(f"/campaigns/{campaign_id}/_truth").json()["true_ctrs"]
        return {int(k): float(v) for k, v in raw.items()}

    true_ctrs = refresh_truth()
    print("seed true CTRs:", true_ctrs)

    for i in range(n_batches):
        sends = client.post(f"/campaigns/{campaign_id}/tick").json()["sends"]
        if not sends:
            print("recipients exhausted")
            break
        clicked = 0
        for s in sends:
            ctr = true_ctrs.get(s["variant_id"], baseline_ctr)
            if rng.random() < ctr:
                client.post("/events", json={"send_id": s["send_id"], "kind": "click"})
                clicked += 1
        client.post(f"/campaigns/{campaign_id}/settle", params={"window_seconds": 0})

        if (i + 1) % 5 == 0:
            research = client.post(f"/campaigns/{campaign_id}/research").json()
            st = client.get(f"/campaigns/{campaign_id}/state").json()
            true_ctrs = refresh_truth()
            print(f"round {i+1} | clicked {clicked}/{len(sends)} | research={research}")
            for v in st["variants"]:
                print(
                    f"  v{v['id']:>3} status={v['status']:<6} samples={v['samples']:<7.1f} "
                    f"mean={v['mean']:.3f} P(best)={v['prob_best']:.3f} :: {v['subject']!r}"
                )
        else:
            print(f"round {i+1} | clicked {clicked}/{len(sends)}")

    final = client.get(f"/campaigns/{campaign_id}/state").json()
    print("\n=== final ===")
    print(f"total_sends={final['total_sends']} total_clicks={final['total_clicks']}")
    for v in final["variants"]:
        print(
            f"  v{v['id']:>3} status={v['status']:<6} samples={v['samples']:<7.1f} "
            f"mean={v['mean']:.3f} P(best)={v['prob_best']:.3f} :: {v['subject']!r}"
        )


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print("ERROR: backend not running. Start with: uvicorn eigen.main:app --reload", file=sys.stderr)
        sys.exit(1)
