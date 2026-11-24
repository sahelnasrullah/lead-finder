"""HTTP API (all routes live under /api)."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import outreach
from .config import Settings
from .countries import COUNTRIES, get_country
from .excel_io import ExcelImportError, export_leads, import_leads
from .leadutil import domain_of, normalize_website, refresh_derived, valid_email
from .models import Campaign, DoNotContact, Lead, Message, Search
from .niches import NICHES, random_niche
from .pipeline import run_search
from .schemas import (
    CampaignCreate, CampaignDetail, CampaignOut, DncCreate, DncOut, LeadOut, LeadUpdate,
    PreviewRequest, SearchCreate, SearchOut, SendRequest,
)
from . import orgnr as orgnr_mod

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- dependencies
def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


# --------------------------------------------------------------------------- meta
@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/meta")
def meta(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return {
        "countries": [
            {"code": c.code, "name": c.name, "language": c.language, "compliance_note": c.compliance_note}
            for c in COUNTRIES.values()
        ],
        "niches": [{"key": n.key, "label": n.label} for n in NICHES],
        "placeholders": outreach.PLACEHOLDERS,
        "sender": {
            "name": settings.sender_name,
            "email": settings.smtp_from,
            "company": settings.company_name,
            "org_number": settings.company_org_number,
        },
        "smtp_configured": settings.smtp_configured,
        "missing_for_sending": settings.missing_for_sending(),
        "bolagsverket_configured": settings.bolagsverket_configured,
        "daily_cap": settings.daily_cap,
        "send_delay": [settings.send_delay_min, settings.send_delay_max],
        "sent_today": outreach.sent_today(db),
    }


@router.get("/niches/random")
def get_random_niche():
    n = random_niche()
    return {"key": n.key, "label": n.label}


# --------------------------------------------------------------------------- searches
@router.post("/searches", response_model=SearchOut, status_code=202)
def start_search(body: SearchCreate, request: Request, db: Session = Depends(get_db)):
    search = Search(
        country=body.country, niche=body.niche.strip(), city=body.city.strip(), result_limit=body.limit
    )
    db.add(search)
    db.commit()
    db.refresh(search)
    state = request.app.state
    state.spawn(
        run_search, state.session_factory, search.id, state.settings,
        state.osm, state.fetcher, state.bolagsverket,
    )
    db.refresh(search)
    return search


@router.get("/searches", response_model=list[SearchOut])
def list_searches(db: Session = Depends(get_db)):
    return db.scalars(select(Search).order_by(Search.id.desc()).limit(50)).all()


@router.get("/searches/{search_id}", response_model=SearchOut)
def get_search(search_id: int, db: Session = Depends(get_db)):
    search = db.get(Search, search_id)
    if not search:
        raise HTTPException(404, "Search not found")
    return search


# --------------------------------------------------------------------------- leads
def _lead_query(
    q: str | None, status: str | None, search_id: int | None, has_email: bool | None, country: str | None
):
    stmt = select(Lead)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            Lead.name.ilike(like) | Lead.city.ilike(like) | Lead.email.ilike(like) | Lead.website.ilike(like)
        )
    if status:
        stmt = stmt.where(Lead.status == status)
    if search_id is not None:
        stmt = stmt.where(Lead.search_id == search_id)
    if has_email is True:
        stmt = stmt.where(Lead.email != "")
    elif has_email is False:
        stmt = stmt.where(Lead.email == "")
    if country:
        stmt = stmt.where(Lead.country == country.upper())
    return stmt


@router.get("/leads", response_model=list[LeadOut])
def list_leads(
    q: str | None = None,
    status: str | None = None,
    search_id: int | None = None,
    has_email: bool | None = None,
    country: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    stmt = _lead_query(q, status, search_id, has_email, country).order_by(Lead.id.desc()).limit(limit)
    return db.scalars(stmt).all()


@router.get("/leads/export")
def export(
    ids: str | None = None,
    search_id: int | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Lead).order_by(Lead.name)
    if ids:
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "ids must be comma-separated integers") from None
        stmt = stmt.where(Lead.id.in_(id_list))
    if search_id is not None:
        stmt = stmt.where(Lead.search_id == search_id)
    leads = db.scalars(stmt).all()
    country = leads[0].country if leads else "SE"
    data = export_leads(list(leads), country)
    filename = f"leads-{datetime.now():%Y%m%d-%H%M}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/leads/import")
async def import_(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    try:
        report = import_leads(db, content)
    except ExcelImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "created": report.created, "updated": report.updated,
        "skipped": report.skipped, "errors": report.errors,
    }


@router.post("/leads/delete")
def delete_leads(body: dict, db: Session = Depends(get_db)):
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(400, "ids must be a list of integers")
    for lead in db.scalars(select(Lead).where(Lead.id.in_(ids))):
        db.delete(lead)
    db.commit()
    return {"deleted": len(ids)}


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def update_lead(lead_id: int, body: LeadUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    changes = body.model_dump(exclude_unset=True)
    if "email" in changes:
        email = (changes["email"] or "").strip().lower()
        if email and not valid_email(email):
            raise HTTPException(422, "Invalid email address")
        changes["email"] = email
    if "website" in changes:
        changes["website"] = normalize_website(changes["website"] or "")
    if "org_number" in changes:
        digits = orgnr_mod.normalize(changes["org_number"] or "")
        changes["org_number"] = orgnr_mod.format_orgnr(digits) if digits else ""
    for key, value in changes.items():
        if key == "name" and value is None:
            continue
        setattr(lead, key, value)
    refresh_derived(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.delete("/leads/{lead_id}", status_code=204)
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    db.delete(lead)
    db.commit()


# --------------------------------------------------------------------------- campaigns
def _counts(db: Session, campaign_id: int) -> dict[str, int]:
    rows = db.execute(
        select(Message.status, func.count()).where(Message.campaign_id == campaign_id).group_by(Message.status)
    ).all()
    counts = {"pending": 0, "sent": 0, "failed": 0, "skipped": 0}
    counts.update({status: n for status, n in rows})
    return counts


def _campaign_out(db: Session, campaign: Campaign) -> dict:
    data = CampaignOut.model_validate(campaign).model_dump()
    data["counts"] = _counts(db, campaign.id)
    data["running"] = outreach.is_running(campaign.id)
    return data


@router.get("/templates")
def templates():
    return outreach.list_templates()


@router.post("/campaigns/preview")
def preview(body: PreviewRequest, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    try:
        planned = outreach.plan_messages(
            db, settings, body.subject, body.body, body.language, body.lead_ids,
            body.include_sole_traders, body.skip_previously_contacted,
        )
    except outreach.OutreachError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [p.__dict__ for p in planned]


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
def create_campaign(body: CampaignCreate, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    try:
        campaign = outreach.create_campaign(
            db, settings, body.name, body.subject, body.body, body.language, body.lead_ids,
            body.include_sole_traders, body.skip_previously_contacted,
        )
    except outreach.OutreachError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _campaign_out(db, campaign)


@router.get("/campaigns", response_model=list[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    campaigns = db.scalars(select(Campaign).order_by(Campaign.id.desc())).all()
    return [_campaign_out(db, c) for c in campaigns]


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
def get_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    data = _campaign_out(db, campaign)
    data["messages"] = list(campaign.messages)
    names = {l.id: l.name for l in db.scalars(select(Lead).where(Lead.id.in_([m.lead_id for m in campaign.messages])))}
    data["company_names"] = names
    return data


@router.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if outreach.is_running(campaign_id):
        raise HTTPException(409, "Stop the campaign before deleting it")
    db.delete(campaign)
    db.commit()


@router.post("/campaigns/{campaign_id}/send")
def send_campaign(
    campaign_id: int,
    body: SendRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if body.dry_run:
        return outreach.dry_run(db, campaign_id)
    if outreach.is_running(campaign_id):
        raise HTTPException(409, "This campaign is already sending")
    try:
        outreach.check_ready_to_send(settings)
    except outreach.OutreachError as exc:
        raise HTTPException(400, str(exc)) from exc
    state = request.app.state

    def job():
        try:
            outreach.run_send(state.session_factory, campaign_id, settings, sender=state.sender, sleep=state.sleep)
        except Exception:  # noqa: BLE001 - background thread: log, do not crash
            log.exception("Sending campaign %s crashed", campaign_id)

    state.spawn(job)
    return {"started": True}


@router.post("/campaigns/{campaign_id}/stop")
def stop_campaign(campaign_id: int, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    outreach.stop_campaign(db, campaign_id)
    return {"stopped": True}


@router.post("/campaigns/{campaign_id}/retry-failed")
def retry_failed(campaign_id: int, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    return {"requeued": outreach.retry_failed(db, campaign_id)}


# --------------------------------------------------------------------------- do-not-contact
@router.get("/do-not-contact", response_model=list[DncOut])
def list_dnc(db: Session = Depends(get_db)):
    return db.scalars(select(DoNotContact).order_by(DoNotContact.id.desc())).all()


@router.post("/do-not-contact", response_model=DncOut, status_code=201)
def add_dnc(body: DncCreate, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    domain = domain_of(body.domain) if body.domain.strip() else ""
    if not email and not domain:
        raise HTTPException(422, "Provide an email address or a domain")
    if email and not valid_email(email):
        raise HTTPException(422, "Invalid email address")
    existing = db.scalar(select(DoNotContact).where(DoNotContact.email == email, DoNotContact.domain == domain))
    if existing:
        return existing
    row = DoNotContact(email=email, domain=domain, reason=body.reason.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/do-not-contact/{row_id}", status_code=204)
def remove_dnc(row_id: int, db: Session = Depends(get_db)):
    row = db.get(DoNotContact, row_id)
    if not row:
        raise HTTPException(404, "Entry not found")
    db.delete(row)
    db.commit()
