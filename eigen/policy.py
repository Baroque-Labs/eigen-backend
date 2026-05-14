"""Generation policy: when to kill, when to spawn, when to stop.

Operates per-cohort: kill/spawn decisions look at each cohort's posteriors
separately. A variant is killed only when it loses across ALL cohorts that
have enough samples to judge (so a niche winner survives).

Stopping rule: campaign is marked stopped when there's a clear cross-cohort
leader (P(best) > stop_prob_best in every cohort that has min samples).
"""
import random

from sqlalchemy.orm import Session

from eigen import models
from eigen.bandit import (
    get_or_create_posterior,
    inherited_prior,
    prob_best,
    samples_observed,
)
from eigen.config import settings
from eigen.generator import get_generator
from eigen.models import utcnow

KILL_PROB_BEST = 0.05
KILL_MIN_SAMPLES = 200


def _cohorts_in_campaign(db: Session, campaign_id: int) -> list[str]:
    rows = (
        db.query(models.Posterior.cohort)
        .join(models.Variant, models.Variant.id == models.Posterior.variant_id)
        .filter(models.Variant.campaign_id == campaign_id)
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows}) or ["default"]


def _mean_in_cohort(db: Session, v: models.Variant, cohort: str) -> float:
    p = get_or_create_posterior(db, v, cohort)
    return p.alpha / (p.alpha + p.beta)


def _spawn_child(db: Session, campaign: models.Campaign, parent: models.Variant) -> models.Variant:
    a, b = inherited_prior(parent.alpha, parent.beta, pseudo_count=4.0)
    history = [v.subject for v in db.query(models.Variant).filter_by(campaign_id=campaign.id).all()]
    generated = get_generator().generate(
        parent_subject=parent.subject, parent_body=parent.body, history=history
    )
    initial_status = "active" if settings().auto_spawn else "pending"
    new = models.Variant(
        campaign_id=campaign.id,
        subject=generated.subject,
        body=generated.body,
        parent_id=parent.id,
        alpha=a,
        beta=b,
        status=initial_status,
    )
    db.add(new)
    db.flush()
    ctrs = dict(campaign.true_ctrs or {})
    parent_ctr = ctrs.get(str(parent.id), 0.05)
    ctrs[str(new.id)] = max(0.0, min(1.0, parent_ctr + random.uniform(-0.02, 0.04)))
    campaign.true_ctrs = ctrs
    return new


def _check_stopping(db: Session, campaign: models.Campaign, cohorts: list[str]) -> str | None:
    """Returns a reason string if the campaign should stop, else None.

    Stop when there's a leader with P(best) > threshold in every cohort that
    has enough samples. Cohorts with too few samples to judge don't block.
    """
    threshold = settings().stop_prob_best
    active = [
        v
        for v in db.query(models.Variant).filter_by(campaign_id=campaign.id, status="active").all()
    ]
    if len(active) < 2:
        return None  # bandit isn't meaningful with one arm

    eligible_cohorts = []
    leader_per_cohort: dict[str, int] = {}
    for co in cohorts:
        if not any(
            samples_observed(get_or_create_posterior(db, v, co).alpha,
                             get_or_create_posterior(db, v, co).beta) >= KILL_MIN_SAMPLES
            for v in active
        ):
            continue
        pb = prob_best(db, active, co)
        eligible_cohorts.append(co)
        winner = max(pb.items(), key=lambda kv: kv[1])
        if winner[1] < threshold:
            return None
        leader_per_cohort[co] = winner[0]

    if not eligible_cohorts:
        return None

    leaders = set(leader_per_cohort.values())
    if len(leaders) == 1:
        return f"converged: P(best)>={threshold} in all {len(eligible_cohorts)} cohort(s)"
    return f"converged per-cohort to {len(leaders)} distinct leaders"


def run_research(db: Session, campaign_id: int) -> dict:
    campaign = db.get(models.Campaign, campaign_id)
    if not campaign or campaign.status != "running":
        return {"killed": [], "spawned": [], "stopped": False}

    variants = db.query(models.Variant).filter_by(campaign_id=campaign_id).all()
    active = [v for v in variants if v.status == "active"]
    cohorts = _cohorts_in_campaign(db, campaign_id)
    killed: list[int] = []

    if len(active) >= 2:
        # Kill any non-leader that loses across ALL eligible cohorts.
        for v in active:
            kill_votes = 0
            eligible_cohorts = 0
            for co in cohorts:
                eligible = [
                    a
                    for a in active
                    if samples_observed(get_or_create_posterior(db, a, co).alpha,
                                        get_or_create_posterior(db, a, co).beta)
                    >= KILL_MIN_SAMPLES
                ]
                if v not in eligible:
                    continue
                eligible_cohorts += 1
                # protect this cohort's lifetime leader
                leader = max(eligible, key=lambda x: _mean_in_cohort(db, x, co))
                if v.id == leader.id:
                    continue
                pb = prob_best(db, active, co)
                if pb.get(v.id, 1.0) < KILL_PROB_BEST:
                    kill_votes += 1
            if eligible_cohorts > 0 and kill_votes == eligible_cohorts:
                v.status = "killed"
                killed.append(v.id)

    # Refill toward n_variants from default-cohort posterior leader.
    spawned: list[int] = []
    current = [
        v
        for v in db.query(models.Variant).filter_by(campaign_id=campaign_id).all()
        if v.status in ("active", "pending")
    ]
    active = [v for v in current if v.status == "active"]
    if active:
        seed = max(active, key=lambda x: _mean_in_cohort(db, x, cohorts[0]))
        while len(current) < campaign.n_variants:
            child = _spawn_child(db, campaign, seed)
            spawned.append(child.id)
            current.append(child)

    # Check stopping
    stop_reason = _check_stopping(db, campaign, cohorts)
    if stop_reason:
        campaign.status = "stopped"
        campaign.stopped_at = utcnow()
        campaign.stopped_reason = stop_reason

    db.commit()
    return {
        "killed": killed,
        "spawned": spawned,
        "stopped": campaign.status == "stopped",
        "stopped_reason": campaign.stopped_reason,
    }
