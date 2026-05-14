"""Scheduler tasks: periodic tick, settle, research across all active campaigns.

Runs inside the arq worker. Each task is best-effort and idempotent — a missed
run just means the next one picks up the slack. Tasks read EIGEN_SCHEDULER_ENABLED
at runtime so you can toggle without restarting.
"""
import logging
from datetime import timedelta

from sqlalchemy import select

from eigen import models
from eigen.bandit import get_or_create_posterior, sample_variant
from eigen.config import settings
from eigen.db import SessionLocal
from eigen.esp import get_dispatcher
from eigen.models import utcnow
from eigen.policy import run_research

log = logging.getLogger("eigen.scheduler")


def _enabled() -> bool:
    return bool(settings().scheduler_enabled)


def _tick_one(db, campaign: models.Campaign) -> int:
    if campaign.status != "running":
        return 0
    sent_ids = select(models.Send.recipient_id).where(models.Send.campaign_id == campaign.id)
    suppressed = select(models.Suppression.email).where(models.Suppression.org_id == campaign.org_id)
    batch = (
        db.query(models.Recipient)
        .filter(
            models.Recipient.campaign_id == campaign.id,
            ~models.Recipient.id.in_(sent_ids),
            ~models.Recipient.email.in_(suppressed),
        )
        .limit(campaign.batch_size)
        .all()
    )
    if not batch:
        return 0

    variants = db.query(models.Variant).filter_by(campaign_id=campaign.id).all()
    active = [v for v in variants if v.status == "active"]
    if not active:
        return 0

    dispatcher = get_dispatcher()
    n_sent = 0
    for r in batch:
        vid = sample_variant(db, active, r.cohort)
        variant = next(v for v in active if v.id == vid)
        s = models.Send(
            campaign_id=campaign.id, variant_id=vid, recipient_id=r.id, cohort=r.cohort
        )
        db.add(s)
        db.flush()
        result = dispatcher.send(
            to=r.email, subject=variant.subject, html=variant.body,
            headers={"X-Eigen-Send-Id": str(s.id)},
        )
        s.provider = result.provider
        s.provider_message_id = result.provider_message_id
        n_sent += 1
    db.commit()
    return n_sent


async def cron_tick_campaigns(ctx) -> dict:
    if not _enabled():
        return {"skipped": "scheduler disabled"}
    db = SessionLocal()
    try:
        results = {}
        for c in db.query(models.Campaign).all():
            n = _tick_one(db, c)
            if n:
                results[c.id] = n
        return {"ticked": results}
    finally:
        db.close()


async def cron_settle_campaigns(ctx) -> dict:
    if not _enabled():
        return {"skipped": "scheduler disabled"}
    window = settings().settle_window_seconds
    cutoff = utcnow() - timedelta(seconds=window)
    db = SessionLocal()
    try:
        unsettled = (
            db.query(models.Send)
            .filter(models.Send.settled_at.is_(None), models.Send.sent_at <= cutoff)
            .all()
        )
        for s in unsettled:
            v = db.get(models.Variant, s.variant_id)
            p = get_or_create_posterior(db, v, s.cohort)
            p.beta += 1.0
            s.settled_at = utcnow()
        db.commit()
        return {"settled": len(unsettled)}
    finally:
        db.close()


async def cron_research_campaigns(ctx) -> dict:
    if not _enabled():
        return {"skipped": "scheduler disabled"}
    db = SessionLocal()
    try:
        out = {}
        for c in db.query(models.Campaign).all():
            out[c.id] = run_research(db, c.id)
        return out
    finally:
        db.close()
