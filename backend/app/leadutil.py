"""Lead normalisation, deduplication and merge helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, fields
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import orgnr as orgnr_mod
from .models import Lead

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")

FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.se", "outlook.com", "outlook.se",
    "live.com", "live.se", "yahoo.com", "yahoo.se", "icloud.com", "me.com", "telia.com",
    "comhem.se", "bredband.net", "spray.se", "msn.com", "proton.me", "protonmail.com",
}


@dataclass
class LeadData:
    """Provider-neutral lead record."""

    name: str
    org_number: str = ""
    address: str = ""
    city: str = ""
    country: str = "SE"
    industry: str = ""
    website: str = ""
    phone: str = ""
    email: str = ""
    legal_form: str = ""
    is_sole_trader: bool | None = None
    source: str = ""
    notes: str = ""


LEAD_DATA_FIELDS = [f.name for f in fields(LeadData)]


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def name_key(name: str, city: str = "") -> str:
    """Normalised key for fuzzy-exact matching of company name + city."""
    n = strip_accents((name or "").lower())
    n = re.sub(r"\b(aktiebolag|ab|hb|kb|ltd|llc|as|oy|a/s)\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", "", n)
    c = re.sub(r"[^a-z0-9]+", "", strip_accents((city or "").lower()))
    return f"{n}|{c}"


def normalize_website(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return url


def domain_of(url_or_host: str) -> str:
    value = (url_or_host or "").strip().lower()
    if not value:
        return ""
    if "//" in value:
        value = urlparse(value).netloc
    value = value.split("@")[-1].split(":")[0]
    return value[4:] if value.startswith("www.") else value


def email_domain(email: str) -> str:
    return email.split("@", 1)[1].lower() if "@" in email else ""


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def apply_normalisation(data: LeadData) -> LeadData:
    data.name = " ".join((data.name or "").split())
    data.country = (data.country or "SE").upper()[:2]
    data.website = normalize_website(data.website)
    data.email = (data.email or "").strip().lower()
    if data.email and not valid_email(data.email):
        data.email = ""
    digits = orgnr_mod.normalize(data.org_number)
    data.org_number = orgnr_mod.format_orgnr(digits) if digits else ""
    if digits and data.is_sole_trader is None:
        personal = orgnr_mod.is_personal_number(digits)
        if personal is not None:
            data.is_sole_trader = personal
    return data


def find_existing(db: Session, data: LeadData) -> Lead | None:
    """Find a lead that is the same company by org number, website domain, or name+city."""
    if data.org_number:
        found = db.scalar(select(Lead).where(Lead.org_number == data.org_number))
        if found:
            return found
    domain = domain_of(data.website)
    if domain:
        found = db.scalar(select(Lead).where(Lead.domain == domain))
        if found:
            return found
    key = name_key(data.name, data.city)
    return db.scalar(select(Lead).where(Lead.name_key == key))


_MERGEABLE = [
    "org_number", "address", "city", "industry", "website", "phone", "email",
    "legal_form", "is_sole_trader", "source", "notes",
]


def _empty(value) -> bool:
    return value is None or value == ""


def merge_into(lead: Lead, data: LeadData, overwrite: bool = False) -> bool:
    """Merge LeadData into a Lead. Returns True if anything changed."""
    changed = False
    for field_name in _MERGEABLE:
        new = getattr(data, field_name)
        old = getattr(lead, field_name)
        if _empty(new):
            continue
        overwrite_here = overwrite
        if field_name == "source" and old:
            if new in old.split(", "):
                continue
            new = f"{old}, {new}"
            overwrite_here = True
        if _empty(old) or (overwrite_here and old != new):
            setattr(lead, field_name, new)
            changed = True
    refresh_derived(lead)
    return changed


def refresh_derived(lead: Lead) -> None:
    lead.domain = domain_of(lead.website)
    lead.name_key = name_key(lead.name, lead.city)
    lead.email_status = "found" if lead.email else "missing"


def upsert_lead(
    db: Session, data: LeadData, search_id: int | None = None, overwrite: bool = False
) -> tuple[Lead, bool]:
    """Insert or merge. Returns (lead, created)."""
    data = apply_normalisation(data)
    existing = find_existing(db, data)
    if existing:
        merge_into(existing, data, overwrite=overwrite)
        return existing, False
    lead = Lead(
        name=data.name,
        org_number=data.org_number,
        address=data.address,
        city=data.city,
        country=data.country,
        industry=data.industry,
        website=data.website,
        phone=data.phone,
        email=data.email,
        legal_form=data.legal_form,
        is_sole_trader=data.is_sole_trader,
        source=data.source,
        notes=data.notes,
        search_id=search_id,
    )
    refresh_derived(lead)
    db.add(lead)
    db.flush()
    return lead, True


def dedupe_batch(items: list[LeadData]) -> list[LeadData]:
    """Merge duplicates inside one provider batch (same domain or name+city)."""
    out: list[LeadData] = []
    index: dict[str, LeadData] = {}
    for item in items:
        item = apply_normalisation(item)
        if not item.name:
            continue
        keys = [f"n:{name_key(item.name, item.city)}"]
        dom = domain_of(item.website)
        if dom:
            keys.append(f"d:{dom}")
        if item.org_number:
            keys.append(f"o:{item.org_number}")
        target = next((index[k] for k in keys if k in index), None)
        if target is None:
            out.append(item)
            target = item
        else:
            for f in LEAD_DATA_FIELDS:
                if _empty(getattr(target, f)) and not _empty(getattr(item, f)):
                    setattr(target, f, getattr(item, f))
        for k in keys:
            index.setdefault(k, target)
    return out
