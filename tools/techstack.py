"""Tech stack fingerprinting — pure-string scan, no network."""

from __future__ import annotations

from typing import Any

CMS_SIGNATURES = {
    "WordPress": ("wp-content/", "/wp-includes/", "wp-json"),
    "Wix": ("static.wixstatic.com", "_wix_", "wix-code"),
    "Squarespace": ("squarespace.com", "static1.squarespace.com", "sqs_"),
    "Shopify": ("cdn.shopify.com", "shopify.com/s/", "/products.json"),
    "Webflow": ("webflow.com", "data-wf-page", "data-wf-site"),
    "Ghost": ("ghost-sdk", "ghost.io", "/ghost/api/"),
    "Drupal": ("drupal.org", "/sites/default/", "drupal-settings-json"),
    "Joomla": ("/components/com_", "joomla!"),
    "HubSpot": ("hs-scripts.com", "hubspot.com", "_hsq"),
}

BOOKING_SIGNATURES = {
    "Calendly": ("calendly.com", "calendly-badge"),
    "Acuity": ("acuityscheduling", "squarespacescheduling.com"),
    "Booksy": ("booksy.com", "booksy-widget"),
    "Vagaro": ("vagaro.com",),
    "Mindbody": ("mindbody", "healcode"),
    "Schedulista": ("schedulista.com",),
    "Square Appointments": ("squareup.com/appointments",),
    "Setmore": ("setmore.com",),
}

EMAIL_SIGNATURES = {
    "Mailchimp": ("mailchimp.com", "mc.us", "mc-embedded-subscribe"),
    "ConvertKit": ("convertkit.com", "ck.page"),
    "Klaviyo": ("klaviyo.com", "_klOnsite"),
    "ActiveCampaign": ("activecampaign.com", "trackcmp.net"),
    "HubSpot Forms": ("hsforms.com", "hsforms.net"),
    "SendGrid": ("sendgrid.net", "sengrid.com"),
}

ANALYTICS_SIGNATURES = {
    "Google Analytics": ("google-analytics.com", "gtag(", "googletagmanager.com"),
    "Facebook Pixel": ("connect.facebook.net/en_US/fbevents.js", "fbq("),
    "Hotjar": ("hotjar.com",),
    "Plausible": ("plausible.io",),
}


def _match(body_lower: str, sigs: dict[str, tuple[str, ...]]) -> str | None:
    for name, needles in sigs.items():
        for n in needles:
            if n.lower() in body_lower:
                return name
    return None


def _match_all(body_lower: str, sigs: dict[str, tuple[str, ...]]) -> list[str]:
    found: list[str] = []
    for name, needles in sigs.items():
        if any(n.lower() in body_lower for n in needles):
            found.append(name)
    return found


def detect_tech_stack(html: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Return cms, booking, email, analytics, server detected from HTML+headers."""
    if not html:
        html = ""
    body = html.lower()

    server = ""
    powered_by = ""
    if headers:
        # Headers may be dict-like with case-insensitive keys; normalize.
        norm = {k.lower(): v for k, v in headers.items()}
        server = norm.get("server", "")
        powered_by = norm.get("x-powered-by", "")

    return {
        "cms": _match(body, CMS_SIGNATURES) or "",
        "booking": _match(body, BOOKING_SIGNATURES) or "",
        "email_provider": _match(body, EMAIL_SIGNATURES) or "",
        "analytics": _match_all(body, ANALYTICS_SIGNATURES),
        "server": server,
        "x_powered_by": powered_by,
    }
