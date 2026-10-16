"""Trending-topic sources.

The default source is the public Hacker News API (no key needed), which is a
good signal for what software engineers are discussing right now. Claude then
picks the item that best fits your topic areas. If the network is down the
agent still works: `fetch_trends` returns an empty list and Claude chooses an
evergreen topic instead.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

HN_BASE = "https://hacker-news.firebaseio.com/v0"
TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class TrendItem:
    """One candidate story.

    Attributes:
        title: Headline as published.
        url: Link to the article (or the HN discussion for text posts).
        score: Upvotes, used as a popularity signal.
        comments: Number of comments, a discussion-intensity signal.
    """

    title: str
    url: str
    score: int = 0
    comments: int = 0


def _fetch_item(session: requests.Session, item_id: int) -> TrendItem | None:
    """Fetch one HN item; returns None for deleted/non-story items or errors."""
    try:
        resp = session.get(f"{HN_BASE}/item/{item_id}.json", timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json() or {}
    except (requests.RequestException, ValueError) as exc:
        log.debug("Skipping HN item %s: %s", item_id, exc)
        return None
    if data.get("type") != "story" or data.get("dead") or data.get("deleted") or not data.get("title"):
        return None
    return TrendItem(
        title=data["title"],
        url=data.get("url") or f"https://news.ycombinator.com/item?id={item_id}",
        score=int(data.get("score") or 0),
        comments=int(data.get("descendants") or 0),
    )


def fetch_trends(limit: int = 30, session: requests.Session | None = None) -> list[TrendItem]:
    """Return up to `limit` current top Hacker News stories, most popular first.

    Network failures are logged and produce an empty list instead of an
    exception, so drafting can continue without trend data.

    Args:
        limit: Maximum number of stories to fetch (bounded to 1..60).
        session: Optional requests session (injected in tests).
    """
    limit = max(1, min(limit, 60))
    session = session or requests.Session()
    try:
        resp = session.get(f"{HN_BASE}/topstories.json", timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        ids = list(resp.json() or [])[:limit]
    except (requests.RequestException, ValueError) as exc:
        log.warning("Could not reach Hacker News (%s); continuing without trends.", exc)
        return []

    # Items are independent, so fetch them concurrently with a small pool.
    with ThreadPoolExecutor(max_workers=8) as pool:
        items = [i for i in pool.map(lambda i: _fetch_item(session, i), ids) if i]
    return sorted(items, key=lambda t: (t.score + 2 * t.comments), reverse=True)
