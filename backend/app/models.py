"""Database models: Lead, Search, Campaign, Message, DoNotContact."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

LEAD_STATUSES = ["new", "contacted", "replied", "interested", "not_interested", "customer"]
MESSAGE_STATUSES = ["pending", "sent", "failed", "skipped"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Search(Base):
    __tablename__ = "searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(2), default="SE")
    niche: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(200), default="")
    result_limit: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|done|failed
    message: Mapped[str] = mapped_column(Text, default="")
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    org_number: Mapped[str] = mapped_column(String(20), default="", index=True)
    address: Mapped[str] = mapped_column(String(300), default="")
    city: Mapped[str] = mapped_column(String(200), default="")
    country: Mapped[str] = mapped_column(String(2), default="SE")
    industry: Mapped[str] = mapped_column(String(200), default="")
    website: Mapped[str] = mapped_column(String(500), default="")
    domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    phone: Mapped[str] = mapped_column(String(60), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    email_status: Mapped[str] = mapped_column(String(20), default="missing")  # found|missing
    legal_form: Mapped[str] = mapped_column(String(100), default="")
    is_sole_trader: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None = unknown
    source: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(30), default="new")
    notes: Mapped[str] = mapped_column(Text, default="")
    linkedin_contacted: Mapped[bool] = mapped_column(Boolean, default=False)
    name_key: Mapped[str] = mapped_column(String(400), default="", index=True)
    search_id: Mapped[int | None] = mapped_column(ForeignKey("searches.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(5), default="sv")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|sending|paused|done|stopped
    note: Mapped[str] = mapped_column(Text, default="")
    include_sole_traders: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_previously_contacted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("campaign_id", "lead_id", name="uq_campaign_lead"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    to_email: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    skip_reason: Mapped[str] = mapped_column(String(100), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="messages")
    lead: Mapped[Lead] = relationship()


class DoNotContact(Base):
    __tablename__ = "do_not_contact"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), default="", index=True)
    domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
