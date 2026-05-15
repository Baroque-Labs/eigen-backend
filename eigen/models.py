from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from eigen.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Org(Base):
    __tablename__ = "org"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_key"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("org.id"))
    key_hash: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Campaign(Base):
    __tablename__ = "campaign"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("org.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | stopped
    n_variants: Mapped[int] = mapped_column(Integer, default=4)
    # Number of recipients per batch. Explicit (not derived from n_batches).
    batch_size: Mapped[int] = mapped_column(Integer, default=100)
    # How often the scheduler should fire a new batch (sim minutes).
    cadence_minutes: Mapped[int] = mapped_column(Integer, default=60)
    # Calendar restricts when the scheduler dispatches. JSON shape:
    #   {"weekdays": [1,2,3,4,5], "hours": [9,10,...,17]}
    # weekday 1=Mon...7=Sun (ISO). Empty list / missing key = any.
    calendar: Mapped[dict] = mapped_column(JSON, default=dict)
    # IANA tz name applied when evaluating the calendar.
    timezone: Mapped[str] = mapped_column(String(60), default="UTC")
    # Per-campaign settle window (sim seconds). Sends older than this
    # without a click get β bumped.
    settle_window_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    # smoke-screen: hidden ground-truth CTR per variant_id for simulation
    true_ctrs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Last time the scheduler dispatched a batch for this campaign.
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    variants: Mapped[list["Variant"]] = relationship(back_populates="campaign")
    recipients: Mapped[list["Recipient"]] = relationship(back_populates="campaign")


class Variant(Base):
    __tablename__ = "variant"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id"))
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(String(2000), default="")
    # Inherited-prior seed values used when a Posterior row is first created
    # for this (variant, cohort) pair. NOT the live posterior.
    alpha: Mapped[float] = mapped_column(Float, default=1.0)
    beta: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|killed|pending|rejected
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("variant.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="variants")


class Posterior(Base):
    """(variant, cohort) -> alpha, beta. The live posterior the sampler reads."""
    __tablename__ = "posterior"
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variant.id"), index=True)
    cohort: Mapped[str] = mapped_column(String(80), default="default", index=True)
    alpha: Mapped[float] = mapped_column(Float, default=1.0)
    beta: Mapped[float] = mapped_column(Float, default=1.0)

    __table_args__ = (UniqueConstraint("variant_id", "cohort", name="uq_posterior_variant_cohort"),)


class Recipient(Base):
    __tablename__ = "recipient"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id"))
    email: Mapped[str] = mapped_column(String(320))
    cohort: Mapped[str] = mapped_column(String(80), default="default", index=True)

    campaign: Mapped[Campaign] = relationship(back_populates="recipients")


class Send(Base):
    __tablename__ = "send"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id"))
    variant_id: Mapped[int] = mapped_column(ForeignKey("variant.id"))
    recipient_id: Mapped[int] = mapped_column(ForeignKey("recipient.id"))
    cohort: Mapped[str] = mapped_column(String(80), default="default", index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)


class Event(Base):
    __tablename__ = "event"
    id: Mapped[int] = mapped_column(primary_key=True)
    send_id: Mapped[int] = mapped_column(ForeignKey("send.id"))
    kind: Mapped[str] = mapped_column(String(40))  # click | open | bounced | complained | delivered
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Provider event id for idempotency. NULL for events we synthesized ourselves.
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_event_provider_event"),)


class Decision(Base):
    """Audit log of every kill/spawn/stop the policy made."""
    __tablename__ = "decision"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # kill | spawn | stop
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variant.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Suppression(Base):
    __tablename__ = "suppression"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("org.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    reason: Mapped[str] = mapped_column(String(40))  # bounce | complaint | unsubscribe
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_suppression_org_email"),)
