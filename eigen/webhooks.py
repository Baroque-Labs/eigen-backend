"""Webhook ingestion. Each provider verifies its own signature and maps to
internal event kinds. Idempotent on (provider, provider_event_id).
"""
import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eigen import models
from eigen.config import settings
from eigen.db import get_db
from eigen.models import utcnow

log = logging.getLogger("eigen.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@dataclass
class NormalizedEvent:
    provider: str
    provider_event_id: str
    kind: str  # click | open | bounced | complained | delivered | unsubscribed
    provider_message_id: str | None
    recipient_email: str | None
    raw: dict


# ----- provider parsers ----------------------------------------------------

_RESEND_KIND_MAP = {
    "email.delivered": "delivered",
    "email.opened": "open",
    "email.clicked": "click",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.delivery_delayed": "delayed",
}


def parse_resend(payload: dict, headers: dict[str, str]) -> NormalizedEvent:
    """Resend uses Svix headers: svix-id, svix-timestamp, svix-signature.

    Caller has already verified the signature. We just map the payload here.
    """
    data = payload.get("data") or {}
    type_ = payload.get("type", "")
    kind = _RESEND_KIND_MAP.get(type_, type_)
    # Resend payload includes email_id (their message id) plus per-recipient data.
    provider_event_id = payload.get("id") or headers.get("svix-id") or f"resend-{data.get('email_id')}-{type_}"
    return NormalizedEvent(
        provider="resend",
        provider_event_id=provider_event_id,
        kind=kind,
        provider_message_id=data.get("email_id"),
        recipient_email=(data.get("to") or [None])[0] if isinstance(data.get("to"), list) else data.get("to"),
        raw=payload,
    )


def parse_fake(payload: dict, headers: dict[str, str]) -> NormalizedEvent:
    """FakeESP doesn't sign — tests post directly here."""
    return NormalizedEvent(
        provider="fake",
        provider_event_id=payload["event_id"],
        kind=payload["kind"],
        provider_message_id=payload.get("provider_message_id"),
        recipient_email=payload.get("to"),
        raw=payload,
    )


# ----- verification --------------------------------------------------------


def verify_resend(body: bytes, headers: dict[str, str]) -> None:
    secret = settings().resend_webhook_secret
    if not secret:
        raise HTTPException(500, "EIGEN_RESEND_WEBHOOK_SECRET not configured")
    try:
        from svix.webhooks import Webhook, WebhookVerificationError
    except ImportError as e:
        raise HTTPException(500, "svix not installed") from e
    try:
        Webhook(secret).verify(body, {k.lower(): v for k, v in headers.items()})
    except WebhookVerificationError as e:
        raise HTTPException(400, f"invalid webhook signature: {e}")


# ----- core handler --------------------------------------------------------


def _apply_event(db: Session, evt: NormalizedEvent) -> dict:
    """Find the Send, write Event (idempotently), update posterior + suppression."""
    send = None
    if evt.provider_message_id:
        send = (
            db.query(models.Send)
            .filter_by(provider=evt.provider, provider_message_id=evt.provider_message_id)
            .first()
        )

    # idempotency check
    existing = (
        db.query(models.Event)
        .filter_by(provider=evt.provider, provider_event_id=evt.provider_event_id)
        .first()
    )
    if existing:
        return {"ok": True, "duplicate": True, "send_id": existing.send_id}

    event = models.Event(
        send_id=send.id if send else 0,
        kind=evt.kind,
        provider=evt.provider,
        provider_event_id=evt.provider_event_id,
        raw=evt.raw,
    )
    db.add(event)

    if send and evt.kind == "click" and send.settled_at is None:
        from eigen.bandit import get_or_create_posterior

        v = db.get(models.Variant, send.variant_id)
        p = get_or_create_posterior(db, v, send.cohort)
        p.alpha += 1.0
        send.settled_at = utcnow()

    # Suppression — per-org. Resolve org via Send -> Campaign.
    if evt.kind in {"bounced", "complained", "unsubscribed"} and evt.recipient_email and send:
        campaign = db.get(models.Campaign, send.campaign_id)
        if campaign is not None:
            existing_supp = (
                db.query(models.Suppression)
                .filter_by(org_id=campaign.org_id, email=evt.recipient_email)
                .first()
            )
            if not existing_supp:
                db.add(
                    models.Suppression(
                        org_id=campaign.org_id,
                        email=evt.recipient_email,
                        reason=(
                            "bounce"
                            if evt.kind == "bounced"
                            else "complaint"
                            if evt.kind == "complained"
                            else "unsubscribe"
                        ),
                    )
                )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}
    return {"ok": True, "duplicate": False, "send_id": send.id if send else None}


@router.post("/resend")
async def webhook_resend(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    verify_resend(body, headers)
    payload = await request.json()
    evt = parse_resend(payload, headers)
    return _apply_event(db, evt)


@router.post("/fake")
async def webhook_fake(request: Request, db: Session = Depends(get_db)):
    """Test-only: no signature, no auth. Drop or guard in prod."""
    if settings().esp != "fake":
        raise HTTPException(403, "fake webhook only available when EIGEN_ESP=fake")
    payload = await request.json()
    evt = parse_fake(payload, dict(request.headers))
    return _apply_event(db, evt)
