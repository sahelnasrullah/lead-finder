import httpx

from app.enrichment import (
    PoliteFetcher, contact_links, enrich_website, extract_emails, extract_org_number,
    extract_phone, rank_emails,
)

HOME = """<html><body>
<a href="/kontakt">Kontakta oss</a><a href="/blogg">Blogg</a><a href="https://other.example/kontakt">x</a>
<footer>Nilsson &amp; Co AB. Org.nr: 556016-0680</footer></body></html>"""
CONTACT = """<html><body>
<a href="mailto:anna.berg@nilsson.example?subject=hej">Anna</a>
<p>Mejla oss: info@nilsson.example eller noreply@nilsson.example</p>
<img src="logo@2x.png"> <p>Ring 031-123 45 67</p>
<a href="tel:+46311234567">Ring</a></body></html>"""


def test_extract_emails_filters_junk():
    emails = extract_emails(CONTACT + "<p>you@example.com sentry@wixpress.com</p>")
    assert "info@nilsson.example" in emails and "anna.berg@nilsson.example" in emails
    assert not any("noreply" in e or "example.com" in e or "wixpress" in e or "2x" in e for e in emails)


def test_extract_emails_handles_at_obfuscation():
    assert extract_emails("<p>info [at] firma.se</p>") == ["info@firma.se"]


def test_rank_prefers_general_same_domain():
    ranked = rank_emails(["anna@other.example", "anna.berg@firm.se", "info@firm.se"], "firm.se")
    assert ranked[0] == "info@firm.se" and ranked[-1] == "anna@other.example"


def test_extract_phone_prefers_tel_links_then_text():
    assert extract_phone(CONTACT) == "+46311234567"
    assert extract_phone("<p>Telefon: 031-123 45 67</p>").startswith("031")
    assert extract_phone("<p>Order 2024-11-05 nr 123456</p>") == ""


def test_extract_org_number():
    assert extract_org_number(HOME) == "5560160680"


def test_contact_links_stay_on_site():
    links = contact_links(HOME, "https://nilsson.example/")
    assert links == ["https://nilsson.example/kontakt"]


def make_fetcher(handler, delay=0.0):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return PoliteFetcher("TestBot", delay=delay, client=client, sleep=lambda s: None)


def site_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /privat\n")
    html = {"/": HOME, "/kontakt": CONTACT}.get(path)
    if html is None:
        return httpx.Response(404)
    return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})


def test_enrich_website_end_to_end():
    res = enrich_website(make_fetcher(site_handler), "https://nilsson.example/")
    assert res.email == "info@nilsson.example"
    assert res.phone == "+46311234567"
    assert res.org_number == "5560160680"
    assert res.pages_fetched == 2


def test_robots_txt_disallow_is_respected():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        raise AssertionError("page must not be fetched")

    res = enrich_website(make_fetcher(handler), "https://blocked.example/")
    assert res.email == "" and res.pages_fetched == 0 and res.notes


def test_unreachable_site_does_not_raise():
    def handler(request):
        raise httpx.ConnectError("down")

    res = enrich_website(make_fetcher(handler), "https://down.example/")
    assert res.email == "" and res.pages_fetched == 0


def test_private_hosts_are_not_fetched():
    called = []
    fetcher = make_fetcher(lambda r: called.append(r) or httpx.Response(200))
    assert fetcher.get("http://127.0.0.1/") is None
    assert fetcher.get("http://localhost/") is None
    assert fetcher.get("http://192.168.1.5/") is None
    assert not called


def test_delay_between_requests_to_same_host():
    sleeps = []
    client = httpx.Client(transport=httpx.MockTransport(site_handler))
    now = [0.0]
    fetcher = PoliteFetcher("TestBot", delay=2.0, client=client, sleep=sleeps.append, clock=lambda: now[0])
    fetcher.get("https://nilsson.example/")
    fetcher.get("https://nilsson.example/kontakt")
    assert sleeps and sleeps[0] == 2.0
