import io

import pytest
from openpyxl import Workbook, load_workbook

from app.db import Base, make_engine, make_session_factory
from app.excel_io import COLUMNS, ExcelImportError, export_leads, import_leads
from app.leadutil import LeadData, upsert_lead
from app.models import Lead


def _seed(db):
    upsert_lead(db, LeadData(name="Nilsson & Co AB", org_number="556016-0680", city="Göteborg",
                             website="https://nilsson.example", email="info@nilsson.example",
                             phone="031 123 456", industry="Law firms", source="OpenStreetMap"))
    upsert_lead(db, LeadData(name="Berg Juristbyrå", city="Malmö", website="https://berg.example"))
    db.commit()


def test_default_columns_present():
    headers = [h for h, _ in COLUMNS]
    for h in ["Name", "Org number", "Address", "City", "Industry", "Website", "Phone", "Email", "Source"]:
        assert h in headers
    assert "Notes" in headers and "Status" in headers and "LinkedIn contacted (manual)" in headers


def test_export_has_leads_and_info_sheet(db):
    _seed(db)
    data = export_leads(list(db.query(Lead).order_by(Lead.id)), "SE")
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Leads", "Info"]
    rows = list(wb["Leads"].iter_rows(values_only=True))
    assert rows[0][0] == "Name" and len(rows) == 3
    assert rows[1][0] == "Nilsson & Co AB"
    assert wb["Leads"].auto_filter.ref


def test_round_trip_is_stable(db):
    _seed(db)
    lead = db.query(Lead).first()
    lead.notes, lead.status, lead.linkedin_contacted = "Called\nSecond line", "interested", True
    db.commit()
    data = export_leads(list(db.query(Lead).order_by(Lead.id)))

    fresh_engine = make_engine("sqlite://")
    Base.metadata.create_all(fresh_engine)
    with make_session_factory(fresh_engine)() as fresh:
        report = import_leads(fresh, data)
        assert (report.created, report.updated, report.errors) == (2, 0, [])
        first = fresh.query(Lead).filter_by(name="Nilsson & Co AB").one()
        assert first.notes == "Called\nSecond line" and first.status == "interested"
        assert first.linkedin_contacted is True and first.org_number == "556016-0680"
        again = import_leads(fresh, data)  # re-import must not duplicate
        assert (again.created, again.updated) == (0, 2)
        assert fresh.query(Lead).count() == 2


def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_reports_bad_rows(db):
    data = _xlsx([
        ["Name", "Email", "Status"],
        ["Good AB", "good@good.example", "contacted"],
        ["", "x@y.example", ""],
        ["Bad Email AB", "not-an-email", "weird"],
        [None, None, None],
    ])
    report = import_leads(db, data)
    assert report.created == 2 and report.skipped == 1
    messages = " | ".join(e["message"] for e in report.errors)
    assert "Missing name" in messages and "Invalid email" in messages and "Unknown status" in messages
    assert db.query(Lead).filter_by(name="Bad Email AB").one().email == ""


def test_import_rejects_garbage_and_missing_name_column(db):
    with pytest.raises(ExcelImportError):
        import_leads(db, b"this is not an excel file")
    with pytest.raises(ExcelImportError):
        import_leads(db, _xlsx([["Email"], ["a@b.example"]]))
