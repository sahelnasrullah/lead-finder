"""Polite website enrichment: find a general email, phone and org number on a company's site."""

from __future__ import annotations

import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from . import orgnr as orgnr_mod
from .leadutil import domain_of, valid_email

log = logging.getLogger(__name__)

MAX_BYTES = 1_500_000
TIMEOUT = 10.0
CONTACT_HINT = re.compile(r"(kontakt|contact|om-oss|om oss|about|kontakta)", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
TEL_FORMATTED = re.compile(r"(?<![\w/])(\+\d{1,3}[\s\-]?\(?\d{1,4}\)?(?:[\s\-]?\d{2,4}){2,4}|0\d{1,3}[\s\-/]\d{2,4}(?:[\s\-]?\d{2,4}){1,3})(?![\w])")

JUNK_EMAIL_PARTS = (
    "example.", "sentry", "wixpress", "yourdomain", "domain.com", "email.com", "your@", "name@",
    "user@", "test@", "@2x", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js",
)
NOREPLY = re.compile(r"(no-?reply|do-?not-?reply|mailer-daemon|postmaster|abuse@|privacy@|dpo@|gdpr@)", re.I)
GENERAL_PREFIXES = (
    "info", "kontakt", "contact", "mail", "hello", "hej", "office", "kansli", "reception",
    "admin", "post", "support", "order", "bokning", "boka", "firma",
)


@dataclass
class EnrichmentResult:
    email: str = ""
    phone: str = ""
    org_number: str = ""
    pages_fetched: int = 0
    notes: list[str] = field(default_factory=list)


def _is_safe_host(host: str) -> bool:
    if not host or host == "localhost":
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host  # a normal hostname
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def rank_emails(emails: list[str], site_domain: str) -> list[str]:
    """Order candidate emails: general same-domain first, then other same-domain, then the rest."""

    def score(e: str) -> tuple[int, int]:
        local, _, dom = e.partition("@")
        same = dom == site_domain or dom.endswith("." + site_domain) or site_domain.endswith("." + dom)
        general = local.split("+")[0] in GENERAL_PREFIXES
        return (0 if same else 1, 0 if general else 1)

    seen: list[str] = []
    for e in emails:
        if e not in seen:
            seen.append(e)
    return sorted(seen, key=score)  # sorted() is stable: keeps page order within a tier


def extract_emails(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            found.append(addr)
    text = soup.get_text(" ")
    text = re.sub(r"\s*[\[(]\s*(?:at|snabel-a)\s*[\])]\s*", "@", text, flags=re.I)
    found.extend(EMAIL_RE.findall(text))
    cleaned = []
    for e in found:
        e = e.strip().strip(".,;:()<>[]\"'").lower()
        if not valid_email(e) or NOREPLY.search(e) or any(j in e for j in JUNK_EMAIL_PARTS):
            continue
        cleaned.append(e)
    return cleaned


def extract_phone(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if a["href"].lower().startswith("tel:"):
            number = re.sub(r"[^\d+]", "", a["href"][4:])
            if 7 <= len(re.sub(r"\D", "", number)) <= 15:
                return number
    text = soup.get_text(" ")
    for m in TEL_FORMATTED.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if 8 <= len(digits) <= 13:
            return m.group(1).strip()
    return ""


def extract_org_number(html: str) -> str:
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    return orgnr_mod.find_in_text(text)


def contact_links(html: str, base_url: str, limit: int = 2) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_host = domain_of(base_url)
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        label = f"{a['href']} {a.get_text(' ', strip=True)}"
        if not CONTACT_HINT.search(label):
            continue
        url = urljoin(base_url, a["href"])
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or domain_of(url) != base_host:
            continue
        url = url.split("#")[0]
        if url not in links and url.rstrip("/") != base_url.rstrip("/"):
            links.append(url)
        if len(links) >= limit:
            break
    return links


class PoliteFetcher:
    """Fetches pages respecting robots.txt, with a per-host delay, timeout and size cap."""

    def __init__(
        self,
        user_agent: str,
        delay: float = 1.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.user_agent = user_agent
        self.delay = delay
        self.client = client or httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": user_agent}
        )
        self.sleep = sleep
        self.clock = clock
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last: dict[str, float] = {}

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp: RobotFileParser | None = RobotFileParser()
        try:
            resp = self.client.get(origin + "/robots.txt")
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            elif resp.status_code in (401, 403):
                rp.parse(["User-agent: *", "Disallow: /"])
            else:
                rp = None  # no robots.txt -> everything allowed
        except httpx.HTTPError:
            rp = None
        self._robots[origin] = rp
        return rp

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            remaining = self.delay - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last[host] = self.clock()

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return rp is None or rp.can_fetch(self.user_agent, url)

    def get(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not _is_safe_host(parsed.hostname or ""):
            return None
        if not self.allowed(url):
            log.info("robots.txt disallows %s", url)
            return None
        self._wait(parsed.netloc)
        try:
            resp = self.client.get(url)
        except httpx.HTTPError as exc:
            log.info("Fetch failed for %s: %s", url, exc)
            return None
        ctype = resp.headers.get("content-type", "")
        if resp.status_code != 200 or "html" not in ctype.lower():
            return None
        return resp.text[:MAX_BYTES]


def enrich_website(fetcher: PoliteFetcher, website: str) -> EnrichmentResult:
    """Visit the homepage and up to two contact-like pages; return what was found."""
    result = EnrichmentResult()
    if not website:
        return result
    home = fetcher.get(website)
    if home is None:
        result.notes.append("website not reachable or disallowed by robots.txt")
        return result
    result.pages_fetched = 1
    pages = [home]
    for link in contact_links(home, website):
        html = fetcher.get(link)
        if html:
            pages.append(html)
            result.pages_fetched += 1

    site_domain = domain_of(website)
    emails: list[str] = []
    for html in pages:
        emails.extend(extract_emails(html))
        if not result.phone:
            result.phone = extract_phone(html)
        if not result.org_number:
            result.org_number = extract_org_number(html)
    ranked = rank_emails(emails, site_domain)
    if ranked:
        result.email = ranked[0]
    return result
