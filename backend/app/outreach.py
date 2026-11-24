"""Outreach: templates, mail merge, no-repeat rules, throttled sending over SMTP."""

from __future__ import annotations

import logging
import random
import re
import smtplib
import ssl
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .leadutil import FREE_MAIL_DOMAINS, email_domain
from .models import Campaign, DoNotContact, Lead, Message, utcnow

log = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
UNFILLED_RE = re.compile(r"\[(?:FYLL I|FILL IN)[^\]]*\]", re.I)

LEAD_PLACEHOLDERS = ["company", "city", "website", "industry", "lead_org_number"]
SENDER_PLACEHOLDERS = ["sender_name", "sender_email", "sender_phone", "company_name", "org_number"]
PLACEHOLDERS = LEAD_PLACEHOLDERS + SENDER_PLACEHOLDERS


class OutreachError(Exception):
    """A user-fixable problem with a campaign (bad template, missing settings, ...)."""


# --------------------------------------------------------------------------- templates
BUILTIN_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "interview_request": {
        "sv": {
            "subject": "15 minuter om hur {{company}} arbetar?",
            "body": (
                "Hej,\n\n"
                "Jag heter {{sender_name}} och är student. Via mitt bolag {{company_name}} bygger jag ett "
                "verktyg för advokatbyråer, och jag vill förstå hur ni arbetar i praktiken innan jag bygger "
                "något.\n\n"
                "Skulle ni kunna tänka er ett kort samtal på cirka 15 minuter, via telefon eller video, där "
                "jag får ställa några frågor om hur arbetet på {{company}} ser ut idag och vad som tar mest "
                "tid? Jag säljer ingenting – jag vill bara lära mig.\n\n"
                "Passar det? Svara gärna med en tid som fungerar för er, eller med en person hos er som är "
                "rätt att prata med.\n\n"
                "Tack på förhand!\n"
            ),
        },
        "en": {
            "subject": "15 minutes about how {{company}} works?",
            "body": (
                "Hello,\n\n"
                "My name is {{sender_name}} and I am a student. Through my company {{company_name}} I am "
                "building a tool for law firms, and I want to understand how you actually work before I "
                "build anything.\n\n"
                "Would you be open to a short 15-minute call, by phone or video, where I can ask a few "
                "questions about how work at {{company}} looks today and what takes the most time? I am not "
                "selling anything – I just want to learn.\n\n"
                "Does that work? Feel free to reply with a time that suits you, or with the right person at "
                "your firm to talk to.\n\n"
                "Thank you in advance!\n"
            ),
        },
    },
    "pilot_offer": {
        "sv": {
            "subject": "Pilot för advokatbyråer – är {{company}} intresserade?",
            "body": (
                "Hej,\n\n"
                "Jag heter {{sender_name}} och driver {{company_name}}. Vi utvecklar "
                "[FYLL I: kort beskrivning av verktyget och vilken tidsbesparing det ger] för "
                "advokatbyråer.\n\n"
                "Vi söker 3–5 pilotkunder. Som pilotkund får ni [FYLL I: villkor, t.ex. gratis eller "
                "rabatterad användning i X månader] mot att ni ger återkoppling varje vecka. Därefter är "
                "priset [FYLL I: pris] kr/månad som grundarkund.\n\n"
                "Vill ni se en kort demo på 15–20 minuter? Svara gärna med en tid som passar.\n\n"
                "Vänliga hälsningar\n"
            ),
        },
        "en": {
            "subject": "Pilot for law firms – is {{company}} interested?",
            "body": (
                "Hello,\n\n"
                "My name is {{sender_name}} and I run {{company_name}}. We are building "
                "[FILL IN: short description of the tool and the time it saves] for law firms.\n\n"
                "We are looking for 3–5 pilot customers. As a pilot customer you get [FILL IN: terms, e.g. "
                "free or discounted use for X months] in exchange for weekly feedback. After that the price "
                "is [FILL IN: price] SEK/month as a founding customer.\n\n"
                "Would you like a short 15–20 minute demo? Reply with a time that suits you.\n\n"
                "Kind regards\n"
            ),
        },
    },
}


def list_templates() -> list[dict]:
    out = []
    for key, langs in BUILTIN_TEMPLATES.items():
        for lang, t in langs.items():
            out.append({"key": key, "language": lang, "subject": t["subject"], "body": t["body"]})
    return out


# --------------------------------------------------------------------------- rendering
def sender_context(settings: Settings) -> dict[str, str]:
    return {
        "sender_name": settings.sender_name,
        "sender_email": settings.smtp_from,
        "sender_phone": settings.sender_phone,
        "company_name": settings.company_name,
        "org_number": settings.company_org_number,
    }


def lead_context(lead: Lead) -> dict[str, str]:
    return {
        "company": lead.name,
        "city": lead.city,
        "website": lead.website,
        "industry": lead.industry,
        "lead_org_number": lead.org_number,
    }


def render(template: str, context: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in PLACEHOLDERS:
            raise OutreachError(f"Unknown placeholder {{{{{key}}}}}. Allowed: " + ", ".join(PLACEHOLDERS))
        return context.get(key, "")

    return PLACEHOLDER_RE.sub(sub, template)


def validate_template(subject: str, body: str) -> None:
    if not subject.strip():
        raise OutreachError("Subject is empty.")
    if not body.strip():
        raise OutreachError("Message body is empty.")
    for text in (subject, body):
        for m in PLACEHOLDER_RE.finditer(text):
            if m.group(1) not in PLACEHOLDERS:
                raise OutreachError(
                    f"Unknown placeholder {{{{{m.group(1)}}}}}. Allowed: " + ", ".join(PLACEHOLDERS)
                )
    unfilled = UNFILLED_RE.search(subject + "\n" + body)
    if unfilled:
        raise OutreachError(f"Replace the placeholder text in the message first: {unfilled.group(0)}")


def footer(settings: Settings, language: str) -> str:
    org = f", org.nr {settings.company_org_number}" if language == "sv" else f", org. no. {settings.company_org_number}"
    identity = f"{settings.company_name}{org if settings.company_org_number else ''}"
    contact = settings.smtp_from + (f" · {settings.sender_phone}" if settings.sender_phone else "")
    if language == "sv":
        optout = (
            'Vill du inte få fler mejl från mig? Svara bara "avregistrera" så tar jag bort dig från min '
            "lista direkt."
        )
    else:
        optout = (
            'If you would rather not hear from me again, just reply "unsubscribe" and I will remove you '
            "right away."
        )
    return f"\n--\n{settings.sender_name}\n{identity}\n{contact}\n\n{optout}\n"


def render_message(settings: Settings, subject: str, body: str, language: str, lead: Lead) -> tuple[str, str]:
    ctx = {**sender_context(settings), **lead_context(lead)}
    rendered_subject = " ".join(render(subject, ctx).split())
    rendered_body = render(body, ctx).rstrip() + "\n" + footer(settings, language)
    return rendered_subject, rendered_body


# --------------------------------------------------------------------------- planning
@dataclass
class PlannedMessage:
    lead_id: int
    company: str
    to_email: str
    subject: str
    body: str
    status: str  # pending | skipped
    skip_reason: str = ""


def _dnc_sets(db: Session) -> tuple[set[str], set[str]]:
    emails, domains = set(), set()
    for row in db.scalars(select(DoNotContact)):
        if row.email:
            emails.add(row.email.lower())
        if row.domain:
            domains.add(row.domain.lower())
    return emails, domains


def _previously_contacted(db: Session, exclude_campaign_id: int | None) -> set[str]:
    q = select(Message.to_email).where(Message.status == "sent")
    if exclude_campaign_id is not None:
        q = q.where(Message.campaign_id != exclude_campaign_id)
    return {e.lower() for e in db.scalars(q) if e}


def plan_messages(
    db: Session,
    settings: Settings,
    subject: str,
    body: str,
    language: str,
    lead_ids: list[int],
    include_sole_traders: bool = False,
    skip_previously_contacted: bool = True,
    exclude_campaign_id: int | None = None,
) -> list[PlannedMessage]:
    """Apply the template and the no-repeat rules to the selected leads (no database writes)."""
    validate_template(subject, body)
    leads = {l.id: l for l in db.scalars(select(Lead).where(Lead.id.in_(lead_ids)))} if lead_ids else {}
    dnc_emails, dnc_domains = _dnc_sets(db)
    already = _previously_contacted(db, exclude_campaign_id) if skip_previously_contacted else set()

    seen_emails: set[str] = set()
    seen_domains: set[str] = set()
    planned: list[PlannedMessage] = []
    for lead_id in sorted(set(lead_ids)):
        lead = leads.get(lead_id)
        if lead is None:
            continue
        email = (lead.email or "").strip().lower()
        domain = email_domain(email)
        shared = domain in FREE_MAIL_DOMAINS
        reason = ""
        if not email:
            reason = "no_email"
        elif email in dnc_emails or (domain in dnc_domains and not shared):
            reason = "do_not_contact"
        elif lead.is_sole_trader and not include_sole_traders:
            reason = "sole_trader"
        elif email in seen_emails:
            reason = "duplicate_email"
        elif domain in seen_domains and not shared:
            reason = "duplicate_domain"
        elif email in already:
            reason = "already_contacted"

        if reason:
            planned.append(PlannedMessage(lead.id, lead.name, email, "", "", "skipped", reason))
            continue
        seen_emails.add(email)
        if not shared:
            seen_domains.add(domain)
        subj, text = render_message(settings, subject, body, language, lead)
        planned.append(PlannedMessage(lead.id, lead.name, email, subj, text, "pending"))
    return planned


def create_campaign(
    db: Session,
    settings: Settings,
    name: str,
    subject: str,
    body: str,
    language: str,
    lead_ids: list[int],
    include_sole_traders: bool = False,
    skip_previously_contacted: bool = True,
) -> Campaign:
    if not lead_ids:
        raise OutreachError("Select at least one lead.")
    planned = plan_messages(
        db, settings, subject, body, language, lead_ids, include_sole_traders, skip_previously_contacted
    )
    campaign = Campaign(
        name=name.strip() or "Campaign",
        subject=subject,
        body=body,
        language=language,
        include_sole_traders=include_sole_traders,
        skip_previously_contacted=skip_previously_contacted,
    )
    db.add(campaign)
    db.flush()
    for p in planned:
        db.add(
            Message(
                campaign_id=campaign.id, lead_id=p.lead_id, to_email=p.to_email, subject=p.subject,
                body=p.body, status=p.status, skip_reason=p.skip_reason,
            )
        )
    db.commit()
    db.refresh(campaign)
    return campaign


# --------------------------------------------------------------------------- SMTP
Sender = Callable[[EmailMessage], None]


def build_email(settings: Settings, to_email: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((settings.sender_name, settings.smtp_from))
    msg["To"] = to_email  # always exactly one recipient
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.smtp_from.split("@")[-1])
    msg["Reply-To"] = settings.smtp_from
    msg["List-Unsubscribe"] = f"<mailto:{settings.smtp_from}?subject=unsubscribe>"
    msg.set_content(body)
    return msg


def smtp_sender(settings: Settings) -> Sender:
    def send(msg: EmailMessage) -> None:
        context = ssl.create_default_context()
        if settings.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        with server:
            if settings.smtp_security == "starttls":
                server.starttls(context=context)
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)

    return send


# --------------------------------------------------------------------------- sending
_running: set[int] = set()
_running_lock = threading.Lock()


def is_running(campaign_id: int) -> bool:
    with _running_lock:
        return campaign_id in _running


def sent_today(db: Session) -> int:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count()).select_from(Message).where(Message.status == "sent", Message.sent_at >= start)
    ) or 0


def retry_failed(db: Session, campaign_id: int) -> int:
    msgs = db.scalars(select(Message).where(Message.campaign_id == campaign_id, Message.status == "failed")).all()
    for m in msgs:
        m.status, m.error = "pending", ""
    db.commit()
    return len(msgs)


def stop_campaign(db: Session, campaign_id: int) -> None:
    campaign = db.get(Campaign, campaign_id)
    if campaign and campaign.status == "sending":
        campaign.status = "stopped"
        campaign.note = "Stopped by user."
        db.commit()


def _sleep_interruptible(seconds: float, should_stop: Callable[[], bool], sleep: Callable[[float], None]) -> None:
    remaining = seconds
    while remaining > 0 and not should_stop():
        step = min(1.0, remaining)
        sleep(step)
        remaining -= step


def check_ready_to_send(settings: Settings) -> None:
    missing = settings.missing_for_sending()
    if missing:
        raise OutreachError("Cannot send real emails yet. Set in backend/.env: " + ", ".join(missing))


def dry_run(db: Session, campaign_id: int) -> dict:
    """Report what a real send would do right now, without sending or changing anything."""
    pending = db.scalars(
        select(Message).where(Message.campaign_id == campaign_id, Message.status == "pending")
    ).all()
    return {
        "dry_run": True,
        "would_send": [{"to": m.to_email, "subject": m.subject} for m in pending],
        "count": len(pending),
    }


def run_send(
    session_factory: sessionmaker[Session],
    campaign_id: int,
    settings: Settings,
    sender: Sender | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> dict:
    """Send all pending messages of a campaign one by one, throttled. Safe to call again later."""
    with _running_lock:
        if campaign_id in _running:
            raise OutreachError("This campaign is already sending.")
        _running.add(campaign_id)
    counts = {"sent": 0, "failed": 0, "skipped": 0}
    try:
        sender = sender or smtp_sender(settings)
        rng = rng or random.Random()
        with session_factory() as db:
            campaign = db.get(Campaign, campaign_id)
            if campaign is None:
                raise OutreachError("Campaign not found.")
            campaign.status, campaign.note = "sending", ""
            db.commit()

            def stopped() -> bool:
                db.refresh(campaign)
                return campaign.status == "stopped"

            ids = list(
                db.scalars(
                    select(Message.id).where(Message.campaign_id == campaign_id, Message.status == "pending")
                    .order_by(Message.id)
                )
            )
            for index, message_id in enumerate(ids):
                if stopped():
                    break
                if sent_today(db) >= settings.daily_cap:
                    campaign.status = "paused"
                    campaign.note = f"Daily cap of {settings.daily_cap} emails reached. Press Send again tomorrow."
                    db.commit()
                    return counts
                msg = db.get(Message, message_id)
                if msg is None or msg.status != "pending":
                    continue
                dnc_emails, dnc_domains = _dnc_sets(db)
                dom = email_domain(msg.to_email)
                if msg.to_email.lower() in dnc_emails or (dom in dnc_domains and dom not in FREE_MAIL_DOMAINS):
                    msg.status, msg.skip_reason = "skipped", "do_not_contact"
                    counts["skipped"] += 1
                    db.commit()
                    continue
                try:
                    sender(build_email(settings, msg.to_email, msg.subject, msg.body))
                except Exception as exc:  # noqa: BLE001 - record any SMTP/network failure per message
                    msg.status = "failed"
                    msg.error = f"{type(exc).__name__}: {exc}"[:500]
                    counts["failed"] += 1
                    log.warning("Send failed for message %s: %s", message_id, type(exc).__name__)
                else:
                    msg.status, msg.sent_at, msg.error = "sent", utcnow(), ""
                    counts["sent"] += 1
                    lead = db.get(Lead, msg.lead_id)
                    if lead and lead.status == "new":
                        lead.status = "contacted"
                db.commit()
                if index < len(ids) - 1:
                    delay = rng.uniform(settings.send_delay_min, settings.send_delay_max)
                    _sleep_interruptible(delay, stopped, sleep)

            db.refresh(campaign)
            if campaign.status != "stopped":
                left = db.scalar(
                    select(func.count()).select_from(Message).where(
                        Message.campaign_id == campaign_id, Message.status == "pending"
                    )
                )
                campaign.status = "done" if not left else "paused"
                db.commit()
        return counts
    finally:
        with _running_lock:
            _running.discard(campaign_id)
