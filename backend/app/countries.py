"""Per-country configuration: language, providers, and a short compliance note.

The compliance notes are general orientation only, not legal advice.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    language: str  # template language: "sv" or "en"
    providers: tuple[str, ...] = ("osm",)
    enrichers: tuple[str, ...] = ()
    compliance_note: str = ""


_GENERIC_NOTE = (
    "Check the local rules for B2B email marketing and data protection (GDPR applies in the EU/EEA/UK) "
    "before sending. Always identify yourself and offer a simple way to opt out."
)

COUNTRIES: dict[str, Country] = {
    "SE": Country(
        "SE", "Sweden", "sv", ("osm",), ("bolagsverket",),
        "Sweden: marketing email to companies is generally allowed, but emails to sole traders "
        "(enskilda firmor) may count as emails to private persons and then need consent. Identify the "
        "sender (company name and org number) and give an easy opt-out. Collect company-level data "
        "by default (GDPR). This is general information, not legal advice.",
    ),
    "NO": Country("NO", "Norway", "en", ("osm",), (),
                  "Norway: GDPR applies through the EEA agreement and Norwegian marketing law is strict about "
                  "unsolicited electronic marketing to individuals. Check the rules for B2B before sending."),
    "DK": Country("DK", "Denmark", "en", ("osm",), (),
                  "Denmark: GDPR applies, and Danish marketing law has specific rules for electronic "
                  "marketing. Check the rules for B2B before sending."),
    "FI": Country("FI", "Finland", "en", ("osm",), (),
                  "Finland: GDPR applies, and electronic direct marketing rules differ for companies and "
                  "individuals. Check the rules for B2B before sending."),
    "GB": Country("GB", "United Kingdom", "en", ("osm",), (),
                  "UK: UK GDPR and PECR apply. Corporate subscribers can generally be emailed, but sole "
                  "traders and some partnerships are treated as individuals."),
    "DE": Country("DE", "Germany", "en", ("osm",), (),
                  "Germany: unsolicited marketing email is tightly restricted (UWG), including for B2B. "
                  "Get legal advice before sending cold email."),
}


def get_country(code: str) -> Country:
    """Known country config, or a generic OSM-only fallback for any other ISO code."""
    code = (code or "SE").upper()
    if code in COUNTRIES:
        return COUNTRIES[code]
    return Country(code, code, "en", ("osm",), (), _GENERIC_NOTE)
