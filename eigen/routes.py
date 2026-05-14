from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from eigen import models, schemas
from eigen.bandit import prob_best, sample_variant
from eigen.db import get_db
from eigen.dispatcher import dispatch
from eigen.policy import run_research

router = APIRouter()


@router.post("/campaigns")
def create_campaign(payload: schemas.CampaignIn, db: Session = Depends(get_db)):
    if not payload.variants:
        raise HTTPException(400, "need at least one variant")
    c = models.Campaign(name=payload.name, true_ctrs={})
    db.add(c)
    db.flush()
    ctrs = {}
    for v in payload.variants:
        var = models.Variant(campaign_id=c.id, subject=v.subject, body=v.body)
        db.add(var)
        db.flush()
        ctrs[str(var.id)] = v.true_ctr
    c.true_ctrs = ctrs
    db.commit()
    return {"id": c.id, "name": c.name}


@router.post("/campaigns/{campaign_id}/recipients")
def add_recipients(campaign_id: int, payload: schemas.RecipientsIn, db: Session = Depends(get_db)):
    c = db.get(models.Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign not found")
    for r in payload.recipients:
        db.add(models.Recipient(campaign_id=campaign_id, email=r.email))
    db.commit()
    return {"added": len(payload.recipients)}


@router.post("/campaigns/{campaign_id}/tick")
def tick(campaign_id: int, n: int = 100, db: Session = Depends(get_db)):
    """Pull a batch of recipients, Thompson-sample a variant per recipient, dispatch."""
    c = db.get(models.Campaign, campaign_id)
    if not c:
        raise HTTPException(404, "campaign not found")

    # Recipients that haven't been sent yet
    sent_sub = db.query(models.Send.recipient_id).filter_by(campaign_id=campaign_id).subquery()
    batch = (
        db.query(models.Recipient)
        .filter(models.Recipient.campaign_id == campaign_id, ~models.Recipient.id.in_(sent_sub))
        .limit(n)
        .all()
    )
    if not batch:
        return {"sends": [], "note": "no recipients remaining"}

    variants = db.query(models.Variant).filter_by(campaign_id=campaign_id).all()
    sends = []
    for r in batch:
        vid = sample_variant(variants)
        variant = next(v for v in variants if v.id == vid)
        s = models.Send(campaign_id=campaign_id, variant_id=vid, recipient_id=r.id)
        db.add(s)
        db.flush()
        dispatch(s.id, r.email, variant.subject, variant.body)
        sends.append({"send_id": s.id, "recipient": r.email, "variant_id": vid})
    db.commit()
    return {"sends": sends}


@router.post("/events")
def ingest_event(payload: schemas.EventIn, db: Session = Depends(get_db)):
    """Record an event and update posterior immediately (click => alpha += 1)."""
    s = db.get(models.Send, payload.send_id)
    if not s:
        raise HTTPException(404, "send not found")
    db.add(models.Event(send_id=s.id, kind=payload.kind))
    if payload.kind == "click" and not s.settled:
        v = db.get(models.Variant, s.variant_id)
        v.alpha += 1.0
        s.settled = 1
    db.commit()
    return {"ok": True}


@router.post("/campaigns/{campaign_id}/settle")
def settle(campaign_id: int, db: Session = Depends(get_db)):
    """Close out un-clicked sends as failures (beta += 1). Call after a 'window' elapses."""
    unsettled = db.query(models.Send).filter_by(campaign_id=campaign_id, settled=0).all()
    for s in unsettled:
        v = db.get(models.Variant, s.variant_id)
        v.beta += 1.0
        s.settled = 1
    db.commit()
    return {"settled": len(unsettled)}


@router.post("/campaigns/{campaign_id}/research")
def research(campaign_id: int, db: Session = Depends(get_db)):
    return run_research(db, campaign_id)


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
        n = int((v.alpha - 1) + (v.beta - 1))
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
        id=c.id, name=c.name, variants=out, total_sends=total_sends, total_clicks=total_clicks
    )
