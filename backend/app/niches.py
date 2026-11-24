"""Niche configuration: keyword -> OpenStreetMap tags / SNI codes, plus the Random list."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Niche:
    key: str
    label: str
    osm_filters: tuple[str, ...]  # Overpass tag filters, e.g. '["office"="lawyer"]'
    sni_codes: tuple[str, ...] = ()
    aliases: tuple[str, ...] = field(default_factory=tuple)
    in_random: bool = True


NICHES: list[Niche] = [
    Niche("law_firms", "Law firms", ('["office"="lawyer"]',), ("69.101",),
          ("law firm", "law firms", "lawyer", "lawyers", "attorney", "advokat", "advokatbyrå",
           "advokatbyra", "juristbyrå", "juristbyra", "advokatfirma")),
    Niche("accountants", "Accounting firms", ('["office"="accountant"]',), ("69.201", "69.202"),
          ("accountant", "accountants", "accounting firm", "accounting", "redovisningsbyrå",
           "redovisningsbyra", "revisor", "revisionsbyrå", "bokföring")),
    Niche("tax_advisors", "Tax advisors", ('["office"="tax_advisor"]',), ("69.203",),
          ("tax advisor", "tax advisors", "skatterådgivare", "skattekonsult")),
    Niche("notaries", "Notaries", ('["office"="notary"]',), (), ("notary", "notaries", "notarie"), False),
    Niche("real_estate_agents", "Real estate agents", ('["office"="estate_agent"]',), ("68.31",),
          ("real estate agent", "real estate agents", "estate agent", "mäklare", "maklare",
           "fastighetsmäklare", "fastighetsmaklare", "mäklarbyrå")),
    Niche("architects", "Architects", ('["office"="architect"]',), ("71.111",),
          ("architect", "architects", "arkitekt", "arkitektbyrå", "arkitektkontor")),
    Niche("engineering_consultants", "Engineering consultancies", ('["office"="engineer"]',), ("71.12",),
          ("engineer", "engineering", "engineering consultancy", "ingenjör", "konsult", "teknikkonsult")),
    Niche("insurance_agents", "Insurance agents and brokers", ('["office"="insurance"]',), ("66.22",),
          ("insurance", "insurance broker", "insurance agent", "försäkring", "forsakring",
           "försäkringsmäklare", "försäkringsförmedlare")),
    Niche("dentists", "Dentists", ('["amenity"="dentist"]', '["healthcare"="dentist"]'), ("86.230",),
          ("dentist", "dentists", "tandläkare", "tandlakare", "tandvård", "tandvard")),
    Niche("physiotherapists", "Physiotherapists", ('["healthcare"="physiotherapist"]',), ("86.906",),
          ("physiotherapist", "physiotherapy", "physio", "sjukgymnast", "fysioterapeut")),
    Niche("veterinarians", "Veterinary clinics", ('["amenity"="veterinary"]',), ("75.00",),
          ("veterinary", "veterinarian", "vet", "veterinär", "veterinar", "djurklinik")),
    Niche("driving_schools", "Driving schools", ('["amenity"="driving_school"]',), ("85.53",),
          ("driving school", "driving schools", "trafikskola", "körskola", "korskola")),
    Niche("it_consultants", "IT consultancies", ('["office"="it"]',), ("62.020",),
          ("it consultant", "it consultancy", "it consulting", "software", "it-konsult", "itkonsult")),
    Niche("electricians", "Electricians", ('["craft"="electrician"]',), ("43.21",),
          ("electrician", "electricians", "elektriker", "elinstallation")),
    Niche("opticians", "Opticians", ('["shop"="optician"]',), ("47.782",),
          ("optician", "opticians", "optiker")),
    Niche("hairdressers", "Hairdressers", ('["shop"="hairdresser"]',), ("96.021",),
          ("hairdresser", "hairdressers", "frisör", "frisor", "frisörsalong"), False),
]


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def find_niche(keyword: str) -> Niche | None:
    """Match a free-text keyword to a configured niche (exact alias first, then substring)."""
    kw = _norm(keyword)
    if not kw:
        return None
    for n in NICHES:
        if kw == n.key or kw == _norm(n.label) or kw in n.aliases:
            return n
    for n in NICHES:
        for alias in (*n.aliases, _norm(n.label)):
            if len(kw) >= 4 and (kw in alias or alias in kw):
                return n
    return None


def random_niche(rng: random.Random | None = None) -> Niche:
    pool = [n for n in NICHES if n.in_random]
    return (rng or random).choice(pool)


_SAFE_KEYWORD = re.compile(r"[^\w\s\-&.åäöÅÄÖéèüÜ]", re.UNICODE)


def sanitize_keyword(keyword: str) -> str:
    """Strip characters that could break an Overpass query string."""
    return _SAFE_KEYWORD.sub("", keyword or "").strip()[:80]
