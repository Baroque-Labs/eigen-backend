"""Per-campaign scheduling.

Each campaign has its own cadence_minutes (sim time) and a calendar
(weekdays + hours, in the campaign's tz). The cron jobs fire often (every
10 wall-seconds) and consult each campaign individually:

  wall_seconds_between_ticks = cadence_minutes * 60 / EIGEN_TIME_SCALE
  → tick if (now - last_tick_at) >= wall_seconds_between_ticks
            AND calendar permits at the campaign's local time

EIGEN_TIME_SCALE compresses wall-clock time so testing doesn't take hours.
1.0 = real time. 60.0 = 1 wall-clock second = 1 sim minute.
"""
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

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


def _calendar_permits(campaign: models.Campaign, now_utc) -> bool:
    cal = campaign.calendar or {}
    weekdays = cal.get("weekdays") or []
    hours = cal.get("hours") or []
    if not weekdays and not hours:
        return True
    try:
        tz = ZoneInfo(campaign.timezone or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    local = now_utc.astimezone(tz)
    if weekdays and local.isoweekday() not in weekdays:
        return False
    if hours and local.hour not in hours:
        return False
    return True


def _due_to_tick(campaign: models.Campaign, now_utc) -> bool:
    if campaign.status != "running":
        return False
    if not _calendar_permits(campaign, now_utc):
        return False
    if campaign.last_tick_at is None:
        return True
    wall_seconds_between = (campaign.cadence_minutes * 60.0) / max(settings().time_scale, 0.001)
    return (now_utc - campaign.last_tick_at).total_seconds() >= wall_seconds_between


def _tick_one(db, campaign: models.Campaign) -> int:
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
            to=r.email,
            subject=variant.subject,
            html=variant.body,
            headers={
                "X-Eigen-Send-Id": str(s.id),
                "X-Eigen-Campaign-Id": str(campaign.id),
                "X-Eigen-Variant-Id": str(vid),
                "X-Eigen-Org-Id": str(campaign.org_id),
                "X-Eigen-Cohort": r.cohort,
                "X-Eigen-True-Ctr": str((campaign.true_ctrs or {}).get(str(vid), 0.05)),
            },
        )
        s.provider = result.provider
        s.provider_message_id = result.provider_message_id
        n_sent += 1
    campaign.last_tick_at = utcnow()
    db.commit()
    return n_sent


async def cron_tick_campaigns(ctx) -> dict:
    if not _enabled():
        return {"skipped": "scheduler disabled"}
    db = SessionLocal()
    try:
        now = utcnow()
        results = {}
        for c in db.query(models.Campaign).filter_by(status="running").all():
            if not _due_to_tick(c, now):
                continue
            n = _tick_one(db, c)
            if n:
                results[c.id] = n
        return {"ticked": results}
    finally:
        db.close()


async def cron_settle_campaigns(ctx) -> dict:
    if not _enabled():
        return {"skipped": "scheduler disabled"}
    db = SessionLocal()
    try:
        results = {}
        scale = max(settings().time_scale, 0.001)
        for c in db.query(models.Campaign).all():
            wall_window = c.settle_window_seconds / scale
            cutoff = utcnow() - timedelta(seconds=wall_window)
            unsettled = (
                db.query(models.Send)
                .filter(
                    models.Send.campaign_id == c.id,
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
            if unsettled:
                results[c.id] = len(unsettled)
        db.commit()
        return {"settled": results}
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
