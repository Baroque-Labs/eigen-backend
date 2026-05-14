from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from eigen.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Campaign(Base):
    __tablename__ = "campaign"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # smoke-screen: hidden ground-truth CTR per variant_id for simulation
    true_ctrs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    variants: Mapped[list["Variant"]] = relationship(back_populates="campaign")
    recipients: Mapped[list["Recipient"]] = relationship(back_populates="campaign")


class Variant(Base):
    __tablename__ = "variant"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id"))
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(String(2000), default="")
    alpha: Mapped[float] = mapped_column(Float, default=1.0)  # successes + 1
    beta: Mapped[float] = mapped_column(Float, default=1.0)  # failures + 1
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|killed
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("variant.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="variants")


class Recipient(Base):
    __tablename__ = "recipient"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id"))
    email: Mapped[str] = mapped_column(String(320))

    campaign: Mapped[Campaign] = relationship(back_populates="recipients")


class Send(Base):
    __tablename__ = "send"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaign.id"))
    variant_id: Mapped[int] = mapped_column(ForeignKey("variant.id"))
    recipient_id: Mapped[int] = mapped_column(ForeignKey("recipient.id"))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    settled: Mapped[int] = mapped_column(Integer, default=0)  # 0=open, 1=closed


class Event(Base):
    __tablename__ = "event"
    id: Mapped[int] = mapped_column(primary_key=True)
    send_id: Mapped[int] = mapped_column(ForeignKey("send.id"))
    kind: Mapped[str] = mapped_column(String(20))  # "click"
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
