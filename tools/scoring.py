"""Composite lead scoring + hot-lead filtering."""

from __future__ import annotations

from typing import Any

from .audit import audit_one


def lead_score(business: dict[str, Any], audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Composite 0-100 score combining audit signal + business signal.

    Hotter lead = higher score. Two intuitions:
      • A bad website (low audit score) on a busy real business = hot pitch.
      • No website at all = hottest possible.
    """
    breakdown: dict[str, int] = {}

    # 1) Website opportunity — INVERSE of audit score, capped at 50.
    if not business.get("website"):
        breakdown["no_website"] = 50
    elif audit is not None:
        # Lower audit_score = more opportunity = higher lead score chunk.
        audit_score = int(audit.get("score") or 0)
        breakdown["website_opportunity"] = max(0, 50 - int(audit_score * 0.5))
    else:
        breakdown["website_opportunity"] = 25

    # 2) Booking gap — they get appointments but don't have online booking.
    has_booking = bool((audit or {}).get("has_booking"))
    if business.get("phone") and not has_booking:
        breakdown["booking_gap"] = 15
    else:
        breakdown["booking_gap"] = 0

    # 3) Demand signal — Yelp ratings/review count = active, real revenue.
    rating = business.get("rating")
    reviews = business.get("review_count") or 0
    demand = 0
    if rating:
        try:
            demand += int(min(20, float(rating) * 4))
        except Exception:
            pass
    if reviews:
        try:
            demand += int(min(15, (float(reviews) ** 0.5)))
        except Exception:
            pass
    breakdown["demand"] = min(35, demand)

    total = min(100, sum(breakdown.values()))
    return {"score": total, "breakdown": breakdown}


async def score_business(
    business: dict[str, Any],
) -> dict[str, Any]:
    """Run audit on the business website (if any) then compose lead score."""
    audit: dict[str, Any] | None = None
    website = business.get("website") or ""
    if website:
        audit = await audit_one(website)
    composite = lead_score(business, audit)
    return {
        "business": business,
        "audit": audit,
        "lead_score": composite["score"],
        "score_breakdown": composite["breakdown"],
    }


def is_hot_no_website(business: dict[str, Any]) -> bool:
    return not bool(business.get("website"))


def is_hot_no_booking(business: dict[str, Any], audit: dict[str, Any] | None) -> bool:
    if not business.get("website"):
        return False  # Different bucket — these are no-website leads.
    if audit is None:
        return False
    return not bool(audit.get("has_booking"))
