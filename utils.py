"""
Dashboard utilities: cached provider, data fetching, Kelly math, news, filters.
"""

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import feedparser
import streamlit as st

from wrdata.providers.polymarket_provider import PolymarketProvider


# ==========================================================================
# Provider & Caching
# ==========================================================================


@st.cache_resource
def get_provider() -> PolymarketProvider:
    """Singleton PolymarketProvider."""
    return PolymarketProvider()


@st.cache_data(ttl=300)
def cached_fetch_events(
    active: Optional[bool] = None,
    closed: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
    tag_slug: Optional[str] = None,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    return get_provider().fetch_events(
        active=active, closed=closed, limit=limit, offset=offset,
        tag_slug=tag_slug, order=order, ascending=ascending,
    )


@st.cache_data(ttl=300)
def cached_fetch_markets(
    active: Optional[bool] = None,
    closed: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
    tag_slug: Optional[str] = None,
    order: Optional[str] = None,
    ascending: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    return get_provider().fetch_markets(
        active=active, closed=closed, limit=limit, offset=offset,
        tag_slug=tag_slug, order=order, ascending=ascending,
    )


@st.cache_data(ttl=600)
def cached_fetch_tags(limit: int = 100) -> List[Dict[str, Any]]:
    return get_provider().fetch_tags(limit=limit)


@st.cache_data(ttl=120)
def cached_fetch_market_history(
    market_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    fidelity: int = 60,
) -> Any:
    """Returns a Polars DataFrame (cached 2 min)."""
    return get_provider().fetch_market_history(
        market_id, start_date=start_date, end_date=end_date, fidelity=fidelity
    )


@st.cache_data(ttl=60)
def cached_fetch_orderbook(token_id: str) -> Dict[str, Any]:
    return get_provider().fetch_orderbook(token_id)


@st.cache_data(ttl=60)
def cached_fetch_trades(
    market: Optional[str] = None,
    event_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    return get_provider().fetch_trades(
        market=market, event_id=event_id, limit=limit, offset=offset
    )


@st.cache_data(ttl=300)
def cached_search_markets(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    return get_provider().search_markets(query, limit=limit)


@st.cache_data(ttl=60)
def cached_fetch_market(market_id: str) -> Dict[str, Any]:
    return get_provider().fetch_market(market_id)


# ==========================================================================
# News RSS
# ==========================================================================


@st.cache_data(ttl=300)
def fetch_news_rss(keywords: str, max_results: int = 20) -> List[Dict[str, str]]:
    """
    Fetch news from Google News RSS. No API key needed.

    Returns list of dicts with title, link, published.
    """
    query = quote_plus(keywords)
    url = (
        f"https://news.google.com/rss/search?q={query}"
        "&hl=en-US&gl=US&ceid=US:en"
    )
    feed = feedparser.parse(url)
    results: List[Dict[str, str]] = []
    for entry in feed.entries[:max_results]:
        published = entry.get("published", "")
        results.append(
            {
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": published,
                "source": entry.get("source", {}).get("title", ""),
            }
        )
    return results


# ==========================================================================
# Text Sanitization
# ==========================================================================

_MD_SPECIAL = re.compile(r'([\\`*_\{\}\[\]()#+\-.!|])')


def sanitize_md(s: str) -> str:
    """Escape Markdown special characters in untrusted text."""
    return _MD_SPECIAL.sub(r'\\\1', s)


# ==========================================================================
# Market Parsing & Filters
# ==========================================================================

# Common patterns for non-exclusive multi-outcome events
_CUMULATIVE_PATTERNS = re.compile(
    r"\b(or more|or fewer|or less|at least|no fewer|over|under|o\d|u\d)\b",
    re.IGNORECASE,
)
_THRESHOLD_PATTERNS = re.compile(
    r"\b(\d+\+|\d+\s*-\s*\d+|above|below|between|range)\b", re.IGNORECASE
)
_SPORTS_PROP_PATTERNS = re.compile(
    r"\b(passing yards|rushing yards|touchdowns|assists|rebounds|strikeouts|"
    r"points scored|total points|goals scored|home runs|RBIs|receptions|"
    r"saves|shots on goal)\b",
    re.IGNORECASE,
)


def is_cumulative(question: str) -> bool:
    return bool(_CUMULATIVE_PATTERNS.search(question))


def is_threshold(question: str) -> bool:
    return bool(_THRESHOLD_PATTERNS.search(question))


def is_sports_prop(question: str) -> bool:
    return bool(_SPORTS_PROP_PATTERNS.search(question))


def is_non_exclusive(question: str) -> bool:
    """True if market question suggests non-mutually-exclusive outcomes."""
    return is_cumulative(question) or is_threshold(question) or is_sports_prop(question)


def parse_market(m: Dict[str, Any]) -> Dict[str, Any]:
    """
    Standardize a Gamma market dict for dashboard use.

    Extracts commonly needed fields with safe defaults.
    """
    outcomes = m.get("outcomes", [])
    prices = m.get("outcomePrices", [])
    token_ids = m.get("clobTokenIds", [])

    yes_price = float(prices[0]) if len(prices) > 0 else None
    no_price = float(prices[1]) if len(prices) > 1 else None

    volume = 0.0
    for key in ("volume", "volumeNum"):
        val = m.get(key)
        if val is not None:
            try:
                volume = float(val)
                break
            except (ValueError, TypeError):
                pass

    liquidity = 0.0
    for key in ("liquidity", "liquidityNum"):
        val = m.get(key)
        if val is not None:
            try:
                liquidity = float(val)
                break
            except (ValueError, TypeError):
                pass

    end_date_str = m.get("endDate") or m.get("end_date_iso")
    end_date = None
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(
                end_date_str.replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            pass

    return {
        "condition_id": m.get("conditionId") or m.get("condition_id", ""),
        "question_id": m.get("questionId") or m.get("question_id", ""),
        "question": m.get("question", m.get("title", "")),
        "slug": m.get("slug", ""),
        "outcomes": outcomes,
        "outcome_prices": prices,
        "clob_token_ids": token_ids,
        "yes_price": yes_price,
        "no_price": no_price,
        "volume": volume,
        "liquidity": liquidity,
        "end_date": end_date,
        "active": m.get("active", False),
        "closed": m.get("closed", False),
        "group_item_title": m.get("groupItemTitle", ""),
        "event_slug": m.get("eventSlug", ""),
        "raw": m,
    }


def make_watchlist_entry(
    parsed_market: Dict[str, Any],
    alert_price: Optional[float] = None,
) -> Dict[str, Any]:
    """Create a consistently-schemed watchlist entry from a parsed market."""
    return {
        "id": str(uuid.uuid4()),
        "question": parsed_market.get("question", ""),
        "condition_id": parsed_market.get("condition_id", ""),
        "slug": parsed_market.get("slug", ""),
        "event_slug": parsed_market.get("event_slug", ""),
        "added_price": parsed_market.get("yes_price"),
        "alert_price": alert_price,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "clob_token_ids": parsed_market.get("clob_token_ids", []),
    }


# ==========================================================================
# Kelly Criterion
# ==========================================================================


def kelly_criterion(true_prob: float, market_price: float) -> float:
    """
    Full Kelly fraction: f* = (p*b - q) / b
    where b = (1/price) - 1 is the decimal odds minus 1,
    p = true_prob, q = 1 - p.

    Returns fraction of bankroll to bet (can be negative = don't bet).
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0
    b = (1.0 / market_price) - 1.0
    if b <= 0:
        return 0.0
    p = true_prob
    q = 1.0 - p
    f = (p * b - q) / b
    return max(f, 0.0)


def half_kelly(true_prob: float, market_price: float) -> float:
    return kelly_criterion(true_prob, market_price) * 0.5


def quarter_kelly(true_prob: float, market_price: float) -> float:
    return kelly_criterion(true_prob, market_price) * 0.25


def expected_value(true_prob: float, market_price: float) -> float:
    """EV of a $1 bet: p * payout - cost = p * (1/price) - 1."""
    if market_price <= 0:
        return 0.0
    return true_prob * (1.0 / market_price) - 1.0


# ==========================================================================
# Scoring
# ==========================================================================


def asymmetry_score(yes_price: float, volume: float, liquidity: float) -> float:
    """
    Score for top EV asymmetric bets.

    Favors markets in the 10-40% sweet spot with high volume & liquidity.
    """
    if yes_price <= 0 or yes_price >= 1:
        return 0.0

    # Sweet spot bonus: peaks at 25%, tapers to 0 at 5% and 45%
    sweet_spot = 0.0
    if 0.05 <= yes_price <= 0.45:
        sweet_spot = 1.0 - abs(yes_price - 0.25) / 0.20
        sweet_spot = max(sweet_spot, 0.0)

    # Payout multiplier (higher for lower prices)
    payout_bonus = math.log10(max(1.0 / yes_price, 1.0))

    vol_score = math.log10(max(volume, 1)) / 5.0
    liq_score = math.log10(max(liquidity, 1)) / 5.0

    return sweet_spot * 2.0 + payout_bonus + vol_score + liq_score


# ==========================================================================
# Formatters
# ==========================================================================


def fmt_pct(value: Optional[float], decimals: int = 1) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.{decimals}f}%"


def fmt_usd(value: Optional[float], decimals: int = 0) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.{decimals}f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.{decimals}f}K"
    return f"${value:,.{max(decimals, 2)}f}"


def polymarket_url(slug: str) -> str:
    if not slug:
        return ""
    return f"https://polymarket.com/event/{slug}"


def days_until(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - now
    return int(delta.total_seconds() / 86400)
