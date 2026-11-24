# Lead Finder

Find companies in a niche, collect their contact details, export them to Excel, and send one personal
email per company from one place. Built for Zarin Tech AB (first use: interviewing 20–30 law firms in Sweden).

- **Backend:** Python, FastAPI, SQLite (`backend/`)
- **Frontend:** React, Vite, TypeScript (`frontend/`)
- **Free data sources only:** OpenStreetMap (Overpass + Nominatim), the free Bolagsverket API, and polite
  scraping of each company's own website for a general email, phone number and org number.
- **Not included on purpose:** LinkedIn scraping or automation (send LinkedIn messages manually and tick
  "LinkedIn (manual)" on the lead), Google Places, allabolag/Proff scraping.

## Quick start

```bash
./run.sh          # creates the venv, installs dependencies, starts backend (8000) and frontend (5173)
```

Then open http://localhost:5173. Edit `backend/.env` (created from `backend/.env.example` on first run).

Manual start:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app_factory --factory --port 8000
cd frontend && npm install && npm run dev
```

Tests: `cd backend && .venv/bin/python -m pytest` and `cd frontend && npm run lint && npm run build`.

## How to use it

1. **Find leads:** pick a country (Sweden by default), a niche (type anything, pick from the list, or press
   **Random**), an optional city and a max number of results. Searches take one to two minutes because the free
   map servers are slow. Results are cached for 24 hours.
2. **Leads:** edit emails, phones, status and notes in the table. Tick **LinkedIn (manual)** when you have
   messaged someone yourself. **Export Excel** downloads the (selected) leads. **Import Excel** reads the same
   format back (re-importing updates existing leads; it never duplicates them).
3. **Outreach:** choose a template (interview request or pilot offer, Swedish or English), edit the text, press
   **Preview** to see exactly who gets what and who is skipped and why, then **Create campaign**. Use **Dry run**
   to check without sending, then **Send now**. Emails go out one at a time, about 30–60 seconds apart, up to the
   daily cap. Keep the backend running until it finishes. Press **Send now** again later to continue after a pause.
4. **Do not contact:** add anyone who replies "unsubscribe" (or a whole domain). They are never emailed, even by
   campaigns created earlier.

### No-repeat rules

- One email per company (the first found address; general addresses like `info@` are preferred).
- Skipped: leads without email, do-not-contact entries, sole traders (unless you include them), a second
  contact at the same domain, a repeated address, and addresses already emailed in an earlier campaign (can be
  switched off for e.g. the later pilot offer).
- Messages that already went out are never sent again, so pressing **Send** twice sends nothing new.
- Each email is sent individually to exactly one recipient (never CC/BCC).

## Configuration (`backend/.env`)

See `backend/.env.example`. Real sending stays disabled until `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` and
`COMPANY_ORG_NUMBER` are set (the org number appears in every email footer). Preview and dry run always work.
Never commit `.env`.

### Sending from your company mailbox (SPF / DKIM / DMARC checklist)

Use a real mailbox on your company domain (for example `ali.nasrullah@zarintech.se`) from a provider such as
Google Workspace, Microsoft 365 or Zoho. Before the first campaign:

- [ ] **SPF:** a TXT record on the domain that authorises your mail provider (the provider documents the exact value).
- [ ] **DKIM:** enable DKIM signing in the provider and publish the DKIM record it gives you.
- [ ] **DMARC:** add a TXT record at `_dmarc.yourdomain`, for example `v=DMARC1; p=none; rua=mailto:you@yourdomain`
      (start with `p=none`, tighten later).
- [ ] Check all three with a tool such as mail-tester.com or your provider's admin console.
- [ ] Use the mailbox normally for a week or two first. A new domain that suddenly sends 30 cold emails looks suspicious.
- [ ] Send a test campaign to your own addresses (Gmail and Outlook) and check that it does not land in spam.
- [ ] Keep volume low (the default cap is 30/day) and personalise the text. If you later send hundreds, use a separate
      domain for cold outreach so your main domain's reputation is protected.

SMTP settings: use `SMTP_SECURITY=starttls` with port 587, or `ssl` with port 465. With Google Workspace or
Microsoft 365 you normally need an app password (or SMTP AUTH enabled for the mailbox).

### Bolagsverket (Sweden, optional)

Register for free OAuth2 client credentials for Bolagsverket's "Värdefulla datamängder" API (as Zarin Tech AB)
and set `BOLAGSVERKET_CLIENT_ID` / `BOLAGSVERKET_CLIENT_SECRET`.

**Important:** this API only looks up one company by *organisation number*. It cannot search by name, city or
industry. So it is used to **enrich** leads that already have an org number (found in the website footer, or
imported from Excel): legal form, SNI codes and whether it is a sole trader. Without credentials the app still
works: it flags sole traders from the org number itself (personnummer-style numbers) and shows "unknown" otherwise.
The response parsing could not be verified against the live API without credentials, so check the first results.
If the free weekly bulk file from Bolagsverket is wanted later, it could be added as another provider to
find org numbers by company name.

## Compliance notes (general information, not legal advice)

- Collect company-level data. A general address such as `info@firm.se` is much safer than a named person's address.
- In Sweden, marketing email to companies is generally allowed, but email to **sole traders** (enskilda firmor) may
  count as email to private persons and then needs consent. They are flagged and excluded by default.
- Every email carries your identity (name, company, org number, contact) and a simple opt-out line. Honour
  opt-outs immediately via the Do-not-contact tab.
- The interview template says honestly that you are a student building a tool through Zarin Tech AB. Keep that true.
- Respect website terms: the website enrichment obeys `robots.txt`, waits between requests, and uses a clear user agent.
- Other countries have different rules (see the note shown under the country selector). Check before sending there.

## Data quality limits

OpenStreetMap coverage is partial, so a niche search finds only the companies someone has mapped (often 30–70% of
the real number, and some are mis-tagged or have outdated websites). Emails come from the company's own website
and are not verified. Always skim the list before sending.

## Project layout

```
backend/app/        api.py (routes), pipeline.py (search job), providers/ (osm, bolagsverket),
                    enrichment.py (website scraping), excel_io.py, outreach.py (templates, rules, SMTP),
                    niches.py, countries.py, models.py
backend/tests/      pytest suite (no live network; SMTP is tested against a local stub server)
frontend/src/       App.tsx + components/ (Search, Leads, Outreach, DoNotContact)
```

Adding a country: add an entry to `backend/app/countries.py` (any ISO code already works with OpenStreetMap only).
Adding a niche: add a `Niche` to `backend/app/niches.py` (OSM tags, SNI codes, aliases). Unknown keywords fall back
to a generic name/tag search.
