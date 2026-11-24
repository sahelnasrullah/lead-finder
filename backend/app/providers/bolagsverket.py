"""Bolagsverket "Värdefulla datamängder" API client (Sweden).

IMPORTANT: this API only looks up a company by organisation number. It cannot search by name,
city or industry. We therefore use it to *enrich* leads that already have an org number (for
example one found in the website footer, or imported from Excel): legal form, SNI codes, status
and registered address. Free OAuth2 client credentials are needed (register for access with
Bolagsverket under Zarin Tech AB and put them in .env).

The response parsing below follows the documented structure but is defensive, because it could
not be verified against the live API without credentials. See README.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from .. import orgnr as orgnr_mod
from ..config import Settings
from .base import ProviderError

log = logging.getLogger(__name__)

SOLE_TRADER_CODES = {"10"}  # juridisk form 10 = Enskild näringsidkare


@dataclass
class OrgInfo:
    org_number: str
    name: str = ""
    legal_form: str = ""
    legal_form_code: str = ""
    sni: list[str] = field(default_factory=list)
    address: str = ""
    city: str = ""
    status: str = ""

    @property
    def is_sole_trader(self) -> bool:
        return self.legal_form_code in SOLE_TRADER_CODES or "enskild" in self.legal_form.lower()


def _dig(obj, *path):
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def parse_organisation(payload: dict, org_number: str) -> OrgInfo | None:
    orgs = payload.get("organisationer") if isinstance(payload, dict) else None
    if not orgs:
        return None
    org = orgs[0]
    info = OrgInfo(org_number=org_number)

    names = _dig(org, "organisationsnamn", "organisationsnamnLista") or []
    if names and isinstance(names, list):
        info.name = str(names[0].get("namn", "")) if isinstance(names[0], dict) else ""

    form = org.get("juridiskForm") or {}
    info.legal_form = str(form.get("klartext", "") or "")
    info.legal_form_code = str(form.get("kod", "") or "")

    addr = _dig(org, "postadressOrganisation", "postadress") or {}
    info.address = " ".join(
        p for p in (str(addr.get("utdelningsadress", "") or ""), str(addr.get("postnummer", "") or "")) if p
    )
    info.city = str(addr.get("postort", "") or "")

    sni = _dig(org, "naringsgrenOrganisation", "sni") or []
    if isinstance(sni, list):
        info.sni = [str(s.get("kod", "")) for s in sni if isinstance(s, dict) and s.get("kod")]

    status = org.get("verksamOrganisation") or {}
    if isinstance(status, dict):
        info.status = str(status.get("kod", "") or "")
    return info


class BolagsverketClient:
    name = "Bolagsverket"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": settings.user_agent})
        self._token = ""
        self._token_expires = 0.0

    @property
    def available(self) -> bool:
        return self.settings.bolagsverket_configured

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires - 30:
            return self._token
        try:
            resp = self.client.post(
                self.settings.bolagsverket_token_url,
                data={"grant_type": "client_credentials", "scope": self.settings.bolagsverket_scope},
                auth=(self.settings.bolagsverket_client_id, self.settings.bolagsverket_client_secret),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Bolagsverket token request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ProviderError(
                f"Bolagsverket token request returned HTTP {resp.status_code}. "
                "Check BOLAGSVERKET_CLIENT_ID / BOLAGSVERKET_CLIENT_SECRET."
            )
        data = resp.json()
        self._token = data.get("access_token", "")
        self._token_expires = time.time() + float(data.get("expires_in", 3600))
        if not self._token:
            raise ProviderError("Bolagsverket token response had no access_token.")
        return self._token

    def lookup(self, org_number: str) -> OrgInfo | None:
        """Look up one organisation. Returns None if it is not found. Raises ProviderError on failures."""
        digits = orgnr_mod.normalize(org_number)
        if not digits:
            return None
        token = self._get_token()
        try:
            resp = self.client.post(
                f"{self.settings.bolagsverket_base_url}/organisationer",
                json={"identitetsbeteckning": digits},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Bolagsverket lookup failed: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise ProviderError(f"Bolagsverket lookup returned HTTP {resp.status_code}.")
        try:
            return parse_organisation(resp.json(), digits)
        except ValueError as exc:
            raise ProviderError("Bolagsverket returned invalid JSON.") from exc
