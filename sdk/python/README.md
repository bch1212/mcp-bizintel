# bizintel-mcp — Python client for BizIntel MCP

Real-time local business intelligence for AI agents — website audits, lead scoring, tech-stack detection, and business search. Pay per call.

## Install

```bash
pip install bizintel-mcp
```

## Quickstart

```python
from bizintel_mcp import BizIntelClient

# Default uses the free dev key (20 calls/24h). Pro keys: https://mcpize.com/bizintel-mcp
with BizIntelClient(api_key="bizintel-dev-key-001") as bi:
    audit = bi.audit_website("https://acme.dental")
    print(audit["score"], audit["tech_stack"])

    leads = bi.find_no_booking(niche="dentist", city="Austin", state="TX", limit=10)
    for biz in leads["businesses"]:
        print(biz["name"], biz["lead_score"])
```

## Native MCP transport

For Claude Desktop / Cursor / any MCP client:

```bash
claude mcp add bizintel-mcp --url https://mcp-bizintel-production.up.railway.app/mcp \
  --header "X-API-Key: bizintel-dev-key-001"
```

## Tools

| Method | Purpose |
|--------|---------|
| `audit_website(url)` | Score 0-100 across SSL/mobile/speed/contact/booking |
| `search_businesses(niche, city, state, limit)` | Yelp + OSM-backed search |
| `get_business_details(business_name, city)` | Phone/address/hours/rating |
| `bulk_audit(urls)` | Up to 20 sites concurrently |
| `score_lead(business_name, city, niche)` | Composite 0-100 |
| `find_no_website(niche, city, ...)` | Hottest cold-outreach leads |
| `find_no_booking(niche, city, ...)` | SaaS-pitch-ready leads |
| `get_tech_stack(url)` | CMS / booking / email / analytics fingerprint |

## License

MIT
