"""Search pipeline: providers -> merge/dedupe -> website enrichment -> Bolagsverket -> database."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session, sessionmaker

from . import orgnr as orgnr_mod
from .config import Settings
from .countries import get_country
from .enrichment import PoliteFetcher, enrich_website
from .leadutil import LeadData, dedupe_batch, refresh_derived, upsert_lead
from .models import Lead, Search, utcnow
from .providers.base import ProviderError, SearchCriteria
from .providers.bolagsverket import BolagsverketClient
from .providers.osm import OsmProvider

log = logging.getLogger(__name__)


def _progress(db: Session, search: Search, message: str, status: str | None = None) -> None:
    search.message = message
    if status:
        search.status = status
    db.commit()


def enrich_lead_from_website(lead: Lead, fetcher: PoliteFetcher) -> bool:
    """Fill email/phone/org number from the lead's website. Returns True if a field was filled."""
    if not lead.website:
        return False
    res = enrich_website(fetcher, lead.website)
    changed = False
    if res.email and not lead.email:
        lead.email, changed = res.email, True
    if res.phone and not lead.phone:
        lead.phone, changed = res.phone, True
    if res.org_number and not lead.org_number:
        lead.org_number = orgnr_mod.format_orgnr(res.org_number)
        personal = orgnr_mod.is_personal_number(res.org_number)
        if personal is not None and lead.is_sole_trader is None:
            lead.is_sole_trader = personal
        changed = True
    refresh_derived(lead)
    return changed


def enrich_lead_from_bolagsverket(lead: Lead, client: BolagsverketClient) -> bool:
    info = client.lookup(lead.org_number)
    if info is None:
        return False
    if info.legal_form:
        lead.legal_form = info.legal_form
    if info.legal_form or info.legal_form_code:
        lead.is_sole_trader = info.is_sole_trader
    if info.address and not lead.address:
        lead.address = info.address
    if info.city and not lead.city:
        lead.city = info.city
    if info.sni:
        tag = "SNI " + ", ".join(info.sni)
        if tag not in (lead.notes or ""):
            lead.notes = f"{lead.notes}\n{tag}".strip()
    refresh_derived(lead)
    return True


def run_search(
    session_factory: sessionmaker[Session],
    search_id: int,
    settings: Settings,
    osm: OsmProvider | None = None,
    fetcher: PoliteFetcher | None = None,
    bolagsverket: BolagsverketClient | None = None,
    should_enrich: bool = True,
) -> None:
    """Run one search job end to end. Never raises: failures are stored on the Search row."""
    with session_factory() as db:
        search = db.get(Search, search_id)
        if search is None:
            return
        try:
            _run(db, search, settings, osm, fetcher, bolagsverket, should_enrich)
        except ProviderError as exc:
            db.rollback()
            search = db.get(Search, search_id)
            search.finished_at = utcnow()
            _progress(db, search, f"Search failed: {exc}", "failed")
        except Exception as exc:  # noqa: BLE001 - background job: always record the failure
            log.exception("Search %s crashed", search_id)
            db.rollback()
            search = db.get(Search, search_id)
            search.finished_at = utcnow()
            _progress(db, search, f"Unexpected error: {type(exc).__name__}: {exc}", "failed")


def _run(db, search, settings, osm, fetcher, bolagsverket, should_enrich) -> None:
    country = get_country(search.country)
    criteria = SearchCriteria(
        country=country.code, niche=search.niche, city=search.city, limit=search.result_limit
    )
    _progress(db, search, "Searching OpenStreetMap…", "running")

    raw: list[LeadData] = []
    if "osm" in country.providers:
        osm = osm or OsmProvider(settings)
        raw.extend(osm.search(criteria))
    leads_data = dedupe_batch(raw)[: max(1, search.result_limit)]

    new_count = 0
    lead_ids: list[int] = []
    for data in leads_data:
        lead, created = upsert_lead(db, data, search_id=search.id)
        lead_ids.append(lead.id)
        new_count += int(created)
    search.found_count = len(lead_ids)
    _progress(db, search, f"Found {len(lead_ids)} companies ({new_count} new). Enriching…")

    notes: list[str] = []
    if should_enrich and lead_ids:
        fetcher = fetcher or PoliteFetcher(settings.user_agent, delay=settings.enrich_delay_seconds)
        done = 0
        for lead_id in lead_ids:
            lead = db.get(Lead, lead_id)
            if lead.website and not (lead.email and lead.phone and lead.org_number):
                try:
                    enrich_lead_from_website(lead, fetcher)
                except Exception:  # noqa: BLE001 - one bad site must not stop the job
                    log.exception("Website enrichment failed for lead %s", lead_id)
            done += 1
            if done % 5 == 0 or done == len(lead_ids):
                _progress(db, search, f"Enriched {done}/{len(lead_ids)} websites…")
        db.commit()

        if "bolagsverket" in country.enrichers:
            client = bolagsverket or BolagsverketClient(settings)
            if not client.available:
                notes.append(
                    "Bolagsverket skipped (set BOLAGSVERKET_CLIENT_ID/SECRET in .env to add legal form and sole-trader flags)"
                )
            else:
                ok = 0
                for lead_id in lead_ids:
                    lead = db.get(Lead, lead_id)
                    if lead.org_number:
                        try:
                            ok += int(enrich_lead_from_bolagsverket(lead, client))
                        except ProviderError as exc:
                            notes.append(f"Bolagsverket: {exc}")
                            break
                db.commit()
                notes.append(f"Bolagsverket matched {ok} companies")

    with_email = sum(1 for i in lead_ids if db.get(Lead, i).email)
    summary = f"Done: {len(lead_ids)} companies ({new_count} new), {with_email} with an email address."
    if notes:
        summary += " " + "; ".join(notes) + "."
    search.finished_at = utcnow()
    _progress(db, search, summary, "done")
