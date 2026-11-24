"""Excel export and import of leads (round-trip safe)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from .countries import get_country
from .leadutil import LeadData, apply_normalisation, upsert_lead, valid_email
from .models import LEAD_STATUSES, Lead

# (header, attribute) in export order. The first nine are the default columns.
COLUMNS: list[tuple[str, str]] = [
    ("Name", "name"),
    ("Org number", "org_number"),
    ("Address", "address"),
    ("City", "city"),
    ("Industry", "industry"),
    ("Website", "website"),
    ("Phone", "phone"),
    ("Email", "email"),
    ("Source", "source"),
    ("Country", "country"),
    ("Legal form", "legal_form"),
    ("Sole trader", "is_sole_trader"),
    ("Status", "status"),
    ("LinkedIn contacted (manual)", "linkedin_contacted"),
    ("Notes", "notes"),
]
MAX_IMPORT_ROWS = 5000
MAX_IMPORT_BYTES = 10 * 1024 * 1024


class ExcelImportError(Exception):
    """The uploaded file cannot be read as a lead spreadsheet."""


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[dict] = field(default_factory=list)


def _cell_value(lead: Lead, attr: str):
    value = getattr(lead, attr)
    if attr == "is_sole_trader":
        return "" if value is None else ("Yes" if value else "No")
    if attr == "linkedin_contacted":
        return "Yes" if value else "No"
    return value or ""


def export_leads(leads: list[Lead], country_code: str = "SE") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append([h for h, _ in COLUMNS])
    header_fill = PatternFill("solid", fgColor="1F3A5F")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
    for lead in leads:
        ws.append([_cell_value(lead, attr) for _, attr in COLUMNS])
    for idx, (header, attr) in enumerate(COLUMNS, start=1):
        longest = max([len(header)] + [len(str(_cell_value(l, attr))) for l in leads[:300]])
        ws.column_dimensions[get_column_letter(idx)].width = min(max(12, longest + 2), 60)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(ws.max_row, 1)}"

    info = wb.create_sheet("Info")
    country = get_country(country_code)
    rows = [
        ("Generated", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Leads", len(leads)),
        ("Source", "Lead Finder (OpenStreetMap, company websites, Bolagsverket where configured)"),
        ("Compliance", country.compliance_note),
        ("Personal data", "Collect company-level data by default. Do not contact do-not-contact entries. "
                          "Emails to sole traders may count as emails to private persons."),
    ]
    for r in rows:
        info.append(list(r))
    info.column_dimensions["A"].width = 16
    info.column_dimensions["B"].width = 110
    for row in info.iter_rows():
        row[0].font = Font(bold=True)
        row[1].alignment = Alignment(wrap_text=True, vertical="top")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _yes_no(value) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lower() in ("yes", "y", "true", "1", "ja", "x")


def import_leads(db: Session, content: bytes) -> ImportReport:
    if len(content) > MAX_IMPORT_BYTES:
        raise ExcelImportError("File is too large (max 10 MB).")
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types for bad files
        raise ExcelImportError("Could not read the file. Upload an .xlsx file exported from Lead Finder.") from exc
    ws = wb["Leads"] if "Leads" in wb.sheetnames else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        raise ExcelImportError("The spreadsheet is empty.") from None

    by_header = {h.lower(): attr for h, attr in COLUMNS}
    by_header.update({"company": "name", "organisation number": "org_number", "e-mail": "email",
                      "org.nr": "org_number", "linkedin contacted": "linkedin_contacted"})
    col_attr: dict[int, str] = {}
    for idx, h in enumerate(header_row):
        if h is None:
            continue
        attr = by_header.get(str(h).strip().lower())
        if attr:
            col_attr[idx] = attr
    if "name" not in col_attr.values():
        raise ExcelImportError("Missing required column 'Name'.")

    report = ImportReport()
    for row_no, row in enumerate(rows, start=2):
        if row_no - 1 > MAX_IMPORT_ROWS:
            report.errors.append({"row": row_no, "message": f"Stopped after {MAX_IMPORT_ROWS} rows."})
            break
        values = {attr: row[idx] if idx < len(row) else None for idx, attr in col_attr.items()}
        if all(v is None or str(v).strip() == "" for v in values.values()):
            continue
        name = str(values.get("name") or "").strip()
        if not name:
            report.skipped += 1
            report.errors.append({"row": row_no, "message": "Missing name"})
            continue
        email = str(values.get("email") or "").strip()
        if email and not valid_email(email):
            report.errors.append({"row": row_no, "message": f"Invalid email '{email}' ignored"})
            email = ""
        status = str(values.get("status") or "").strip().lower().replace(" ", "_")
        if status and status not in LEAD_STATUSES:
            report.errors.append({"row": row_no, "message": f"Unknown status '{status}' ignored"})
            status = ""

        def s(attr: str) -> str:
            v = values.get(attr)
            return "" if v is None else str(v).strip()

        data = LeadData(
            name=name, org_number=s("org_number"), address=s("address"), city=s("city"),
            country=(s("country") or "SE")[:2], industry=s("industry"), website=s("website"),
            phone=s("phone"), email=email, legal_form=s("legal_form"),
            is_sole_trader=_yes_no(values.get("is_sole_trader")), source=s("source") or "Excel import",
            notes=s("notes"),
        )
        data = apply_normalisation(data)
        lead, created = upsert_lead(db, data, overwrite=True)
        if status:
            lead.status = status
        li = _yes_no(values.get("linkedin_contacted"))
        if li is not None:
            lead.linkedin_contacted = li
        if created:
            report.created += 1
        else:
            report.updated += 1
    db.commit()
    return report
