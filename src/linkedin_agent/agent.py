"""High-level workflows that combine trends, media, Claude and the queue.

The CLI (and any future web UI) calls these functions; they hold the
business rules so the interface layer stays thin.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .config import Settings
from .media import build_previews, validate_media
from .store import Draft, DraftStore, utcnow
from .trends import fetch_trends
from .writer import PostWriter

log = logging.getLogger(__name__)


class Agent:
    """Creates and revises drafts. Never publishes; see `Publisher` for that."""

    def __init__(self, settings: Settings, store: DraftStore, writer: PostWriter | None = None, trend_fetcher=fetch_trends):
        """
        Args:
            settings: Loaded configuration.
            store: Draft queue.
            writer: Claude writer (built lazily so commands that don't draft
                don't need an API key).
            trend_fetcher: Callable returning TrendItems (injected in tests).
        """
        self.settings = settings
        self.store = store
        self._writer = writer
        self._trend_fetcher = trend_fetcher

    @property
    def writer(self) -> PostWriter:
        """The Claude writer, created on first use."""
        if self._writer is None:
            self._writer = PostWriter(self.settings)
        return self._writer

    def _recent_topics(self) -> list[str]:
        """Topics of recent non-discarded drafts, so new posts don't repeat them."""
        return [d.topic for d in self.store.list_drafts(limit=30) if d.topic and d.status != "discarded"]

    def generate_topic_drafts(self, count: int = 1, topic_hint: str | None = None) -> list[Draft]:
        """Write `count` hot-topic drafts (1-5) and save them for review.

        Each new draft's topic is added to the avoid-list so a batch covers
        different subjects.
        """
        count = max(1, min(count, 5))
        writer = self.writer  # fail fast on a missing API key before any network work
        trends = [] if topic_hint else self._trend_fetcher()
        research = None
        if self.settings.use_web_search and not topic_hint:
            research = writer.research_with_web_search()
        recent = self._recent_topics()
        drafts: list[Draft] = []
        for _ in range(count):
            post = writer.write_topic_post(trends, topic_hint=topic_hint, web_research=research, recent_topics=recent)
            draft = self.store.add(
                mode="topic",
                body=post.body,
                hashtags=post.hashtags,
                topic=post.topic,
                source_url=post.source_url or None,
                notes=topic_hint,
            )
            recent.insert(0, post.topic)
            drafts.append(draft)
        return drafts

    def create_media_draft(self, paths: list[str], notes: str, alt_texts: list[str] | None = None) -> Draft:
        """Write a draft about the user's photos or video and save it.

        Alt text the user typed wins; otherwise Claude's descriptions are used.

        Raises:
            MediaError: if the files cannot be posted to LinkedIn.
            ValueError: if the notes are empty.
        """
        if not notes or not notes.strip():
            raise ValueError("Please describe the photos/video in a few words (--notes).")
        items = validate_media(paths, alt_texts)
        is_video = items[0].kind == "video"
        photo_count = 0 if is_video else len(items)
        post = self.writer.write_media_post(build_previews(items), notes.strip(), photo_count=photo_count, is_video=is_video)
        if not is_video:
            for item, generated in zip(items, post.alt_texts):
                item.alt_text = item.alt_text or generated
        return self.store.add(
            mode="media", body=post.body, hashtags=post.hashtags, media=items, topic=post.topic, notes=notes.strip()
        )

    def revise_draft(self, draft_id: int, feedback: str) -> Draft:
        """Ask Claude to rewrite a draft according to feedback."""
        draft = self.store.get(draft_id)
        post = self.writer.revise(draft.body, draft.hashtags, feedback)
        return self.store.update_content(draft_id, body=post.body, hashtags=post.hashtags)


def parse_schedule(text: str, settings: Settings, now: datetime | None = None) -> datetime:
    """Parse "YYYY-MM-DD HH:MM" in the configured time zone.

    Raises:
        ValueError: on bad format or a time that is not in the future.
    """
    try:
        local = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ValueError('Use the format "YYYY-MM-DD HH:MM", e.g. "2026-10-20 09:30"') from exc
    when = local.replace(tzinfo=settings.tz)
    if when <= (now or utcnow()):
        raise ValueError("The scheduled time must be in the future")
    return when
