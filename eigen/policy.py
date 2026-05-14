"""Generation policy: when to kill, when to spawn.

Rules:
- Lifetime leader (the active variant with highest posterior mean) is immune from kill.
- Kill if P(best) < 0.05 and samples >= 200.
- After kill pass, spawn children from leader until active count == campaign.n_variants.
- New variants inherit a weak prior (pseudo-count 4) from the parent's posterior mean.
"""
import random

from sqlalchemy.orm import Session

from eigen import models
from eigen.bandit import inherited_prior, prob_best
from eigen.generator import get_generator

KILL_PROB_BEST = 0.05
KILL_MIN_SAMPLES = 200


def _samples(v: models.Variant) -> float:
    return (v.alpha - 1) + (v.beta - 1)


def _mean(v: models.Variant) -> float:
    return v.alpha / (v.alpha + v.beta)


def _spawn_child(db: Session, campaign: models.Campaign, parent: models.Variant) -> models.Variant:
    a, b = inherited_prior(parent.alpha, parent.beta, pseudo_count=4.0)
    history = [v.subject for v in db.query(models.Variant).filter_by(campaign_id=campaign.id).all()]
    generated = get_generator().generate(
        parent_subject=parent.subject, parent_body=parent.body, history=history
    )
    new = models.Variant(
        campaign_id=campaign.id,
        subject=generated.subject,
        body=generated.body,
        parent_id=parent.id,
        alpha=a,
        beta=b,
        status="active",
    )
    db.add(new)
    db.flush()
    # smoke-screen: inherited ground-truth CTR with small jitter
    ctrs = dict(campaign.true_ctrs or {})
    parent_ctr = ctrs.get(str(parent.id), 0.05)
    ctrs[str(new.id)] = max(0.0, min(1.0, parent_ctr + random.uniform(-0.02, 0.04)))
    campaign.true_ctrs = ctrs
    return new


def run_research(db: Session, campaign_id: int) -> dict:
    campaign = db.get(models.Campaign, campaign_id)
    if not campaign:
        return {"killed": [], "spawned": []}

    variants = db.query(models.Variant).filter_by(campaign_id=campaign_id).all()
    active = [v for v in variants if v.status == "active"]
    killed: list[int] = []

    # Identify lifetime leader (active variant with highest mean among those with enough samples).
    eligible = [v for v in active if _samples(v) >= KILL_MIN_SAMPLES]
    leader = max(eligible, key=_mean) if eligible else None

    if len(active) >= 2:
        pb = prob_best(active)
        for v in active:
            if leader and v.id == leader.id:
                continue
            if _samples(v) >= KILL_MIN_SAMPLES and pb.get(v.id, 1.0) < KILL_PROB_BEST:
                v.status = "killed"
                killed.append(v.id)

    # Refill toward n_variants from current best survivor.
    spawned: list[int] = []
    survivors = [v for v in db.query(models.Variant).filter_by(campaign_id=campaign_id, status="active").all()]
    if survivors:
        seed = max(survivors, key=_mean)
        while len(survivors) < campaign.n_variants:
            child = _spawn_child(db, campaign, seed)
            spawned.append(child.id)
            survivors.append(child)

    db.commit()
    return {"killed": killed, "spawned": spawned}
