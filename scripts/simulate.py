"""Drive the eigen-backend through a simulated campaign.

Usage:
    # Terminal 1
    uvicorn eigen.main:app --reload

    # Terminal 2
    python scripts/simulate.py
"""
import random
import sys
import time

import httpx

import os
BASE = os.environ.get("EIGEN_BASE", "http://127.0.0.1:8000")


def main():
    client = httpx.Client(base_url=BASE, timeout=30.0)

    # 1. Create campaign with 3 variants of varying true CTR
    payload = {
        "name": "Spring blast",
        "variants": [
            {"subject": "Hello there", "true_ctr": 0.04},
            {"subject": "Big news inside", "true_ctr": 0.09},
            {"subject": "[urgent] read this", "true_ctr": 0.06},
        ],
    }
    r = client.post("/campaigns", json=payload)
    r.raise_for_status()
    campaign_id = r.json()["id"]
    print(f"campaign={campaign_id}")

    # 2. Load 5000 recipients
    chunk = [{"email": f"u{i}@example.com"} for i in range(5000)]
    client.post(f"/campaigns/{campaign_id}/recipients", json={"recipients": chunk}).raise_for_status()

    # 3. Pull ground-truth CTRs (read state isn't enough — we exposed via state? no, we have to peek db)
    #    Cheat: re-fetch campaign config via direct sqlite? Cleaner: pass them through state.
    #    For prototype, just re-derive from `state` + know our payload ordering.
    state = client.get(f"/campaigns/{campaign_id}/state").json()
    variant_ids = [v["id"] for v in state["variants"]]
    true_ctrs = {variant_ids[i]: payload["variants"][i]["true_ctr"] for i in range(len(variant_ids))}
    print("true CTRs:", true_ctrs)

    # 4. Run ticks. After each tick, simulate clicks per true CTR, settle the rest.
    rng = random.Random(42)
    batch = 200
    rounds = 25
    for i in range(rounds):
        sends = client.post(f"/campaigns/{campaign_id}/tick", params={"n": batch}).json()["sends"]
        if not sends:
            print("recipients exhausted")
            break
        clicked = 0
        for s in sends:
            ctr = true_ctrs.get(s["variant_id"], 0.05)
            if rng.random() < ctr:
                client.post("/events", json={"send_id": s["send_id"], "kind": "click"})
                clicked += 1
        # close out the batch (failures get beta += 1)
        client.post(f"/campaigns/{campaign_id}/settle")

        # every 5 rounds, run research
        if (i + 1) % 5 == 0:
            research = client.post(f"/campaigns/{campaign_id}/research").json()
            st = client.get(f"/campaigns/{campaign_id}/state").json()
            # If research spawned a new variant, learn its ground truth from server-side perturbation.
            # We expose true_ctrs via the campaign — but state doesn't carry them. For sim we'll
            # query a fresh state and treat any variant we don't know as the parent's CTR + jitter.
            for v in st["variants"]:
                if v["id"] not in true_ctrs:
                    parent_ctr = true_ctrs.get(v["parent_id"], 0.05) if v["parent_id"] else 0.05
                    true_ctrs[v["id"]] = max(0.0, min(1.0, parent_ctr + rng.uniform(-0.02, 0.04)))
            print(f"round {i+1} | clicked {clicked}/{len(sends)} | research={research}")
            for v in st["variants"]:
                print(
                    f"  v{v['id']:>3} status={v['status']:<6} samples={v['samples']:<5} "
                    f"mean={v['mean']:.3f} P(best)={v['prob_best']:.3f} :: {v['subject']!r}"
                )
        else:
            print(f"round {i+1} | clicked {clicked}/{len(sends)}")
        time.sleep(0.05)

    # final dump
    final = client.get(f"/campaigns/{campaign_id}/state").json()
    print("\n=== final ===")
    print(f"total_sends={final['total_sends']} total_clicks={final['total_clicks']}")
    for v in final["variants"]:
        print(
            f"  v{v['id']:>3} status={v['status']:<6} samples={v['samples']:<5} "
            f"mean={v['mean']:.3f} P(best)={v['prob_best']:.3f} :: {v['subject']!r}"
        )


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print("ERROR: backend not running. Start with: uvicorn eigen.main:app --reload", file=sys.stderr)
        sys.exit(1)
