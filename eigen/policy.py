"""Generation policy: when to kill, when to spawn."""
from sqlalchemy.orm import Session

from eigen import models
from eigen.bandit import is_stabilized, prob_best

KILL_PROB_BEST = 0.05
KILL_MIN_SAMPLES = 100


def run_research(db: Session, campaign_id: int) -> dict:
    """Kill underperformers and (if everyone has stabilized) spawn a new variant from the leader."""
    variants = db.query(models.Variant).filter_by(campaign_id=campaign_id).all()
    active = [v for v in variants if v.status == "active"]
    killed = []
    spawned = None

    if len(active) >= 2:
        pb = prob_best(active)
        for v in active:
            n = (v.alpha - 1) + (v.beta - 1)
            if n >= KILL_MIN_SAMPLES and pb.get(v.id, 1.0) < KILL_PROB_BEST:
                v.status = "killed"
                killed.append(v.id)

    survivors = [v for v in variants if v.status == "active"]
    if survivors and all(is_stabilized(v) for v in survivors):
        leader = max(survivors, key=lambda v: v.alpha / (v.alpha + v.beta))
        new = models.Variant(
            campaign_id=campaign_id,
            subject=f"{leader.subject} (variant)",
            body=leader.body,
            parent_id=leader.id,
        )
        db.add(new)
        db.flush()
        # smoke-screen: inherit ground-truth CTR with a small random perturbation
        campaign = db.get(models.Campaign, campaign_id)
        if campaign:
            ctrs = dict(campaign.true_ctrs or {})
            parent_ctr = ctrs.get(str(leader.id), 0.05)
            import random
            ctrs[str(new.id)] = max(0.0, min(1.0, parent_ctr + random.uniform(-0.02, 0.04)))
            campaign.true_ctrs = ctrs
        spawned = new.id

    db.commit()
    return {"killed": killed, "spawned": spawned}
