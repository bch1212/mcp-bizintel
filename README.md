# BizIntel MCP — Local Business Intelligence for AI Agents

> Real-time website audits, lead scoring, tech-stack detection, and local-business search — exposed as an MCP server. Built for AI agents doing sales outreach, competitor research, and prospecting at scale.

---

## MCPize Listing Copy

**Title:** BizIntel MCP — Real-Time Local Business Intelligence

**Subtitle:** Audit any website, score leads, find businesses with no booking system. Pay per call.

**Description (2 paragraphs):**

BizIntel is a paid MCP server that gives AI agents instant access to the kind of local-business intelligence sales teams used to pay analysts to gather. One call to `audit_website` returns a 0-100 score across SSL, mobile-readiness, page speed, contact form presence, and online booking — plus a tech-stack fingerprint (CMS, booking platform, email provider, analytics). Agents pointed at "find dentists in Austin with no booking system" get a ranked, contactable list in seconds, not hours.

The MCP exposes eight tools — `audit_website`, `bulk_audit`, `search_businesses`, `get_business_details`, `score_lead`, `find_no_website`, `find_no_booking`, `get_tech_stack` — backed by Yelp Fusion (or OSM/Overpass when no Yelp key is provided), an aiohttp scanner running 10 concurrent fetches, and a 24-hour SQLite cache so repeat calls don't burn quota. Drop it into Claude Desktop, Cursor, or any agent and your prospecting pipeline becomes one tool call wide.

---

## Pricing

| Tier | Price | Limits |
|------|-------|--------|
| **Free / Dev** | $0 | 20 calls / 24h |
| **Pro** | **$19/mo** | Unlimited |
| **Pay-as-you-go** | **$0.05/call** | No floor |

Upgrade: <https://mcpize.com/bizintel-mcp>

---

## Tools

| Tool | Args | Returns |
|------|------|---------|
| `audit_website` | `url` | Score 0-100, SSL, HTTPS redirect, viewport, load_time_ms, contact form, booking, tech_stack |
| `search_businesses` | `niche, city, state, limit` | List of normalized business records |
| `get_business_details` | `business_name, city` | Full record: phone, address, website, hours, rating, lat/lon |
| `bulk_audit` | `urls` (≤20) | Audit results sorted worst→best (best leads first) |
| `score_lead` | `business_name, city, niche` | Composite 0-100 lead score with breakdown |
| `find_no_website` | `niche, city, state, limit` | Hottest cold-outreach leads — ranked |
| `find_no_booking` | `niche, city, state, limit` | Have a site, no online booking — SaaS-pitch ready |
| `get_tech_stack` | `url` | CMS / booking / email / analytics fingerprint |

---

## Quickstart

### Add to Claude Desktop / Claude Code

```bash
claude mcp add bizintel-mcp --url https://mcp-bizintel.up.railway.app/mcp
```

Set your API key in the MCP config (header `X-API-Key`). The default dev key `bizintel-dev-key-001` is good for 20 calls per day.

### Direct HTTP

```bash
curl -X POST https://mcp-bizintel.up.railway.app/v1/find_no_booking \
  -H "X-API-Key: bizintel-dev-key-001" \
  -H "Content-Type: application/json" \
  -d '{"niche":"dentist","city":"Austin","state":"TX","limit":10}'
```

### Example agent prompt

> Find dentists in Austin with no booking system, audit the top 5, and write a one-line cold-email opener for each that references their actual tech stack.

---

## Local Dev

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in YELP_API_KEY (optional)
python -m uvicorn server:app --reload --port 8000
pytest -v
```

Without a Yelp key, search/details fall back to OSM (Nominatim + Overpass). Coverage is sparser but free.

---

## Deploy

`bash deploy.sh` from Brett's Mac — pulls secrets from the shared workspace `.deploy-secrets.env`, links/initializes the Railway project, sets env vars, and runs `railway up`. Pre-deploy pytest is part of the script so you can't ship a broken build.

---

## Architecture

```
server.py                FastAPI + fastmcp; HTTP at /v1/* and MCP at /mcp
tools/audit.py           Async aiohttp auditor (10 concurrent, 5s timeout)
tools/techstack.py       CMS / booking / email / analytics fingerprints
tools/search.py          Yelp Fusion primary, OSM Overpass fallback
tools/scoring.py         Composite lead score (0-100)
db/cache.py              SQLite cache + per-key call ledger
db/keys.py               Tier classification + 24h sliding window
nixpacks.toml            Railway build config
```

Caching: audits cached 6h, business details 24h, search results 12h. The cache is a single SQLite file with WAL mode — no Redis needed for the price point.

---

## Known Limitations

1. **Yelp doesn't expose external website URL** in `/businesses/search`. We return `yelp_url` as a stable handle; for a real domain, agents should chain `get_business_details` (returns hours/photos) and follow the Yelp page or use `find_no_website` (where OSM-tagged sites are surfaced).
2. **OSM coverage is uneven** — some niches map cleanly (`dentist`, `restaurant`); long-tail US small business categories (`pickleball coach`, `yacht detailer`) won't resolve.
3. **No headless rendering** — JS-heavy sites that gate content behind hydration won't expose contact/booking signals to the audit. This is intentional; we trade completeness for 5-second batched audits.
4. **Tech-stack detection is signature-based**, not Wappalyzer-grade. We catch the common 90% (WP, Wix, Shopify, Squarespace, Calendly, Mindbody, GA4, Klaviyo) — not obscure custom stacks.
5. **Rate-limit window is sliding 24h**, stored per-API-key in SQLite. Restart the container and the ledger persists; clear the DB to reset all dev quotas.

---

## License

Proprietary — © 2026.
