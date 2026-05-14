import math
import random
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from eigen import models, schemas
from eigen.bandit import inherited_prior, prob_best, sample_variant
from eigen.db import get_db
from eigen.esp import get_dispatcher
from eigen.models import utcnow
from eigen.policy import run_research

router = APIRouter()


@router.post("/campaigns")
def create_campaign(payload: schemas.CampaignIn, db: Session = Depends(get_db)):
    if not payload.emails:
        raise HTTPException(400, "need at least one recipient")
    batch_size = max(1, math.ceil(len(payload.emails) / payload.n_batches))
    c = models.Campaign(
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
    for _ in range(payload.n_variants - 1):
        a, b = inherited_prior(baseline.alpha, baseline.beta, pseudo_count=4.0)
        child = models.Variant(
            campaign_id=c.id,
            subject=f"{baseline.subject} (variant)",
            body=baseline.body,
            parent_id=baseline.id,
            alpha=a,
            beta=b,
        )
        db.add(child)
        db.flush()
        ctrs[str(child.id)] = max(
            0.0, min(1.0, payload.baseline.true_ctr + random.uniform(-0.02, 0.04))
        )

    c.true_ctrs = ctrs

    # Recipients
    for email in payload.emails:
        db.add(models.Recipient(campaign_id=c.id, email=email))

    db.commit()
    return {"id": c.id, "name": c.name, "batch_size": batch_size, "n_variants": payload.n_variants}


@router.post("/campaigns/{campaign_id}/tick")
def tick(campaign_id: int, db: Session = Depends(get_db)):
    """Pull next batch_size recipients, Thompson-sample a variant each, dispatch."""
    c = db.get(models.Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign not found")

    sent_ids = select(models.Send.recipient_id).where(models.Send.campaign_id == campaign_id)
    suppressed = select(models.Suppression.email)
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
    dispatcher = get_dispatcher()
    sends = []
    for r in batch:
        vid = sample_variant(variants)
        variant = next(v for v in variants if v.id == vid)
        s = models.Send(campaign_id=campaign_id, variant_id=vid, recipient_id=r.id)
        db.add(s)
        db.flush()
        result = dispatcher.send(
            to=r.email,
            subject=variant.subject,
            html=variant.body,
            headers={"X-Eigen-Send-Id": str(s.id)},
        )
        s.provider = result.provider
        s.provider_message_id = result.provider_message_id
        sends.append({"send_id": s.id, "recipient": r.email, "variant_id": vid})
    db.commit()
    return {"sends": sends}


@router.post("/events")
def ingest_event(payload: schemas.EventIn, db: Session = Depends(get_db)):
    s = db.get(models.Send, payload.send_id)
    if not s:
        raise HTTPException(404, "send not found")
    db.add(models.Event(send_id=s.id, kind=payload.kind))
    if payload.kind == "click" and s.settled_at is None:
        v = db.get(models.Variant, s.variant_id)
        v.alpha += 1.0
        s.settled_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post("/campaigns/{campaign_id}/settle")
def settle(campaign_id: int, window_seconds: int = 0, db: Session = Depends(get_db)):
    """Settle sends older than `window_seconds` as failures (β += 1).

    window_seconds=0 means 'settle everything outstanding now' (useful for testing).
    """
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
        v.beta += 1.0
        s.settled_at = utcnow()
    db.commit()
    return {"settled": len(unsettled)}


@router.post("/campaigns/{campaign_id}/research")
def research(campaign_id: int, db: Session = Depends(get_db)):
    return run_research(db, campaign_id)


@router.get("/campaigns/{campaign_id}/_truth")
def truth(campaign_id: int, db: Session = Depends(get_db)):
    """SMOKE-SCREEN ONLY: ground-truth CTRs used by the simulator."""
    c = db.get(models.Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign not found")
    return {"true_ctrs": c.true_ctrs}


@router.get("/campaigns/{campaign_id}/state", response_model=schemas.CampaignState)
def state(campaign_id: int, db: Session = Depends(get_db)):
    c = db.get(models.Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign not found")
    variants = db.query(models.Variant).filter_by(campaign_id=campaign_id).all()
    active = [v for v in variants if v.status == "active"]
    pb = prob_best(active) if active else {}
    out = []
    for v in variants:
        n = (v.alpha - 1) + (v.beta - 1)
        out.append(
            schemas.VariantOut(
                id=v.id,
                subject=v.subject,
                status=v.status,
                alpha=v.alpha,
                beta=v.beta,
                mean=v.alpha / (v.alpha + v.beta),
                samples=n,
                prob_best=pb.get(v.id, 0.0),
                parent_id=v.parent_id,
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
        n_variants=c.n_variants,
        n_batches=c.n_batches,
        batch_size=c.batch_size,
        variants=out,
        total_sends=total_sends,
        total_clicks=total_clicks,
    )
