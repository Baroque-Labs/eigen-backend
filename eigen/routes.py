import math
import random
from datetime import timedelta

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from eigen import models, schemas
from eigen.auth import hash_key, mint_key, require_org
from eigen.bandit import get_or_create_posterior, inherited_prior, prob_best, sample_variant, samples_observed
from eigen.generator import get_generator
from eigen.config import settings
from eigen.db import get_db
from eigen.esp import get_dispatcher
from eigen.models import utcnow
from eigen.policy import run_research

router = APIRouter()


@router.post("/campaigns")
def create_campaign(
    payload: schemas.CampaignIn,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    all_recipients = payload.all_recipients()
    if not all_recipients:
        raise HTTPException(400, "need at least one recipient")
    batch_size = max(1, math.ceil(len(all_recipients) / payload.n_batches))
    c = models.Campaign(
        org_id=org.id,
        name=payload.name,
        n_variants=payload.n_variants,
        n_batches=payload.n_batches,
        batch_size=batch_size,
        true_ctrs={},
    )
    db.add(c)
    db.flush()

    # Baseline
    baseline = models.Variant(campaign_id=c.id, subject=payload.baseline.subject, body=payload.baseline.body)
    db.add(baseline)
    db.flush()
    ctrs: dict[str, float] = {str(baseline.id): payload.baseline.true_ctr}

    # Spawn n_variants - 1 children from baseline with inherited (diffuse) prior.
    generator = get_generator()
    initial_status = "active" if settings().auto_spawn else "pending"
    history: list[str] = [baseline.subject]
    for _ in range(payload.n_variants - 1):
        a, b = inherited_prior(baseline.alpha, baseline.beta, pseudo_count=4.0)
        generated = generator.generate(
            parent_subject=baseline.subject, parent_body=baseline.body, history=history
        )
        history.append(generated.subject)
        child = models.Variant(
            campaign_id=c.id,
            subject=generated.subject,
            body=generated.body,
            parent_id=baseline.id,
            alpha=a,
            beta=b,
            status=initial_status,
        )
        db.add(child)
        db.flush()
        ctrs[str(child.id)] = max(
            0.0, min(1.0, payload.baseline.true_ctr + random.uniform(-0.02, 0.04))
        )

    c.true_ctrs = ctrs

    # Recipients
    for r in all_recipients:
        db.add(models.Recipient(campaign_id=c.id, email=r.email, cohort=r.cohort))

    db.commit()
    return {"id": c.id, "name": c.name, "batch_size": batch_size, "n_variants": payload.n_variants}


def _owned_campaign(db: Session, campaign_id: int, org: models.Org) -> models.Campaign:
    c = db.get(models.Campaign, campaign_id)
    if not c or c.org_id != org.id:
        raise HTTPException(404, "campaign not found")
    return c


_pool = None


async def _get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings().redis_url))
    return _pool


@router.post("/campaigns/{campaign_id}/tick")
async def tick(
    campaign_id: int,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    """Pull next batch_size recipients, Thompson-sample a variant each, dispatch.

    In sync mode dispatches inline. In async mode enqueues to arq and returns immediately
    (Send rows are persisted with no provider_message_id; the worker fills them in).
    """
    c = _owned_campaign(db, campaign_id, org)
    if c.status != "running":
        return {"sends": [], "note": f"campaign is {c.status}"}

    sent_ids = select(models.Send.recipient_id).where(models.Send.campaign_id == campaign_id)
    suppressed = select(models.Suppression.email).where(models.Suppression.org_id == org.id)
    batch = (
        db.query(models.Recipient)
        .filter(
            models.Recipient.campaign_id == campaign_id,
            ~models.Recipient.id.in_(sent_ids),
            ~models.Recipient.email.in_(suppressed),
        )
        .limit(c.batch_size)
        .all()
    )
    if not batch:
        return {"sends": [], "note": "no recipients remaining"}

    variants = db.query(models.Variant).filter_by(campaign_id=campaign_id).all()
    active = [v for v in variants if v.status == "active"]
    if not active:
        return {"sends": [], "note": "no active variants"}

    mode = settings().send_mode
    dispatcher = get_dispatcher() if mode == "sync" else None
    pool = await _get_pool() if mode == "async" else None

    sends = []
    pending_jobs: list[tuple[int, str, str, str]] = []
    for r in batch:
        vid = sample_variant(db, active, r.cohort)
        variant = next(v for v in active if v.id == vid)
        s = models.Send(
            campaign_id=campaign_id, variant_id=vid, recipient_id=r.id, cohort=r.cohort
        )
        db.add(s)
        db.flush()
        if mode == "sync":
            result = dispatcher.send(
                to=r.email,
                subject=variant.subject,
                html=variant.body,
                headers={"X-Eigen-Send-Id": str(s.id)},
            )
            s.provider = result.provider
            s.provider_message_id = result.provider_message_id
        else:
            pending_jobs.append((s.id, r.email, variant.subject, variant.body))
        sends.append(
            {"send_id": s.id, "recipient": r.email, "variant_id": vid, "cohort": r.cohort}
        )
    db.commit()

    if mode == "async" and pool is not None:
        for args in pending_jobs:
            await pool.enqueue_job("dispatch_send", *args)

    return {"sends": sends, "mode": mode}


@router.post("/events")
def ingest_event(payload: schemas.EventIn, db: Session = Depends(get_db)):
    s = db.get(models.Send, payload.send_id)
    if not s:
        raise HTTPException(404, "send not found")
    db.add(models.Event(send_id=s.id, kind=payload.kind))
    if payload.kind == "click" and s.settled_at is None:
        v = db.get(models.Variant, s.variant_id)
        p = get_or_create_posterior(db, v, s.cohort)
        p.alpha += 1.0
        s.settled_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post("/campaigns/{campaign_id}/settle")
def settle(
    campaign_id: int,
    window_seconds: int = 0,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    """Settle sends older than `window_seconds` as failures (β += 1).

    window_seconds=0 means 'settle everything outstanding now' (useful for testing).
    """
    _owned_campaign(db, campaign_id, org)
    cutoff = utcnow() - timedelta(seconds=window_seconds)
    unsettled = (
        db.query(models.Send)
        .filter(
            models.Send.campaign_id == campaign_id,
            models.Send.settled_at.is_(None),
            models.Send.sent_at <= cutoff,
        )
        .all()
    )
    for s in unsettled:
        v = db.get(models.Variant, s.variant_id)
        p = get_or_create_posterior(db, v, s.cohort)
        p.beta += 1.0
        s.settled_at = utcnow()
    db.commit()
    return {"settled": len(unsettled)}


@router.get("/campaigns/{campaign_id}/pending")
def pending_variants(
    campaign_id: int,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    _owned_campaign(db, campaign_id, org)
    pending = db.query(models.Variant).filter_by(campaign_id=campaign_id, status="pending").all()
    return {
        "variants": [
            {"id": v.id, "subject": v.subject, "body": v.body, "parent_id": v.parent_id}
            for v in pending
        ]
    }


@router.post("/campaigns/{campaign_id}/variants/{variant_id}/approve")
def approve_variant(
    campaign_id: int,
    variant_id: int,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    _owned_campaign(db, campaign_id, org)
    v = db.get(models.Variant, variant_id)
    if not v or v.campaign_id != campaign_id:
        raise HTTPException(404, "variant not found")
    if v.status != "pending":
        raise HTTPException(409, f"variant is {v.status}, not pending")
    v.status = "active"
    db.commit()
    return {"id": v.id, "status": "active"}


@router.post("/campaigns/{campaign_id}/variants/{variant_id}/reject")
def reject_variant(
    campaign_id: int,
    variant_id: int,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    _owned_campaign(db, campaign_id, org)
    v = db.get(models.Variant, variant_id)
    if not v or v.campaign_id != campaign_id:
        raise HTTPException(404, "variant not found")
    if v.status != "pending":
        raise HTTPException(409, f"variant is {v.status}, not pending")
    v.status = "rejected"
    db.commit()
    return {"id": v.id, "status": "rejected"}


@router.post("/campaigns/{campaign_id}/research")
def research(
    campaign_id: int,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    _owned_campaign(db, campaign_id, org)
    return run_research(db, campaign_id)


@router.get("/campaigns/{campaign_id}/decisions")
def list_decisions(
    campaign_id: int,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    _owned_campaign(db, campaign_id, org)
    rows = (
        db.query(models.Decision)
        .filter_by(campaign_id=campaign_id)
        .order_by(models.Decision.at.asc())
        .all()
    )
    return {
        "decisions": [
            {
                "id": d.id,
                "kind": d.kind,
                "variant_id": d.variant_id,
                "reason": d.reason,
                "snapshot": d.snapshot,
                "at": d.at.isoformat(),
            }
            for d in rows
        ]
    }


@router.get("/campaigns/{campaign_id}/_truth")
def truth(
    campaign_id: int,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    """SMOKE-SCREEN ONLY: ground-truth CTRs used by the simulator."""
    c = _owned_campaign(db, campaign_id, org)
    return {"true_ctrs": c.true_ctrs}


@router.get("/campaigns/{campaign_id}/state", response_model=schemas.CampaignState)
def state(
    campaign_id: int,
    db: Session = Depends(get_db),
    org: models.Org = Depends(require_org),
):
    c = _owned_campaign(db, campaign_id, org)
    variants = db.query(models.Variant).filter_by(campaign_id=campaign_id).all()
    active = [v for v in variants if v.status == "active"]

    # All cohorts that have any posterior in this campaign
    variant_ids = [v.id for v in variants]
    cohort_rows = (
        db.query(models.Posterior.cohort)
        .filter(models.Posterior.variant_id.in_(variant_ids))
        .distinct()
        .all()
    )
    cohorts = sorted({r[0] for r in cohort_rows}) or ["default"]

    pb_by_cohort = {co: prob_best(db, active, co) for co in cohorts} if active else {}

    out = []
    for v in variants:
        per_cohort: list[schemas.CohortPosterior] = []
        for co in cohorts:
            p = get_or_create_posterior(db, v, co)
            mean = p.alpha / (p.alpha + p.beta)
            per_cohort.append(
                schemas.CohortPosterior(
                    cohort=co,
                    alpha=p.alpha,
                    beta=p.beta,
                    mean=mean,
                    samples=samples_observed(p.alpha, p.beta),
                    prob_best=pb_by_cohort.get(co, {}).get(v.id, 0.0),
                )
            )
        out.append(
            schemas.VariantOut(
                id=v.id,
                subject=v.subject,
                status=v.status,
                parent_id=v.parent_id,
                cohorts=per_cohort,
            )
        )
    total_sends = db.query(models.Send).filter_by(campaign_id=campaign_id).count()
    total_clicks = (
        db.query(models.Event)
        .join(models.Send, models.Event.send_id == models.Send.id)
        .filter(models.Send.campaign_id == campaign_id, models.Event.kind == "click")
        .count()
    )
    return schemas.CampaignState(
        id=c.id,
        name=c.name,
        status=c.status,
        n_variants=c.n_variants,
        n_batches=c.n_batches,
        batch_size=c.batch_size,
        variants=out,
        total_sends=total_sends,
        total_clicks=total_clicks,
        stopped_reason=c.stopped_reason,
    )
