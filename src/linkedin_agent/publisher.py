"""Publishing approved drafts to LinkedIn, now or on schedule.

The publisher is the only code path that writes to LinkedIn, and it only
accepts drafts a person has approved. In dry-run mode (the default until you
set LINKEDIN_DRY_RUN=false) it prints exactly what would be sent and leaves
the draft untouched.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .linkedin.client import LinkedInClient, LinkedInError, post_url
from .linkedin.oauth import AuthError, load_token
from .linkedin.text import build_commentary
from .store import Draft, DraftStore, utcnow

log = logging.getLogger(__name__)


@dataclass
class PublishResult:
    """Outcome of one publish attempt.

    Attributes:
        draft_id: Draft that was processed.
        ok: True when published (or previewed in dry-run mode).
        message: Human-readable outcome (URL on success).
        dry_run: True when nothing was sent to LinkedIn.
    """

    draft_id: int
    ok: bool
    message: str
    dry_run: bool = False


class Publisher:
    """Sends approved drafts to LinkedIn and records the result."""

    def __init__(
        self,
        settings: Settings,
        store: DraftStore,
        client_factory: Callable[[], LinkedInClient] | None = None,
    ):
        """
        Args:
            settings: Configuration (dry-run flag, token location).
            store: Draft queue.
            client_factory: Builds a LinkedInClient; defaults to one using the
                saved login token. Injected in tests.
        """
        self.settings = settings
        self.store = store
        self._client_factory = client_factory or (lambda: LinkedInClient(settings, load_token(settings)))

    def preview(self, draft: Draft) -> str:
        """Describe what would be sent, for dry runs."""
        media = ", ".join(f"{m.kind}:{m.path}" for m in draft.media) or "none"
        return (
            f"[DRY RUN] Draft {draft.id} would be posted (LINKEDIN_DRY_RUN=true).\n"
            f"Media: {media}\n--- commentary sent to LinkedIn ---\n"
            f"{build_commentary(draft.body, draft.hashtags)}\n-----------------------------------"
        )

    def publish(self, draft_id: int) -> PublishResult:
        """Publish one approved draft.

        The draft is claimed atomically (approved -> publishing) first, so a
        concurrent scheduler cannot post it a second time.
        """
        draft = self.store.get(draft_id)
        if draft.status != "approved":
            return PublishResult(draft_id, False, f"Draft {draft_id} is '{draft.status}'; approve it first.")
        if self.settings.linkedin_dry_run:
            return PublishResult(draft_id, True, self.preview(draft), dry_run=True)

        # Build the client before claiming so a missing login doesn't leave the draft stuck.
        try:
            client = self._client_factory()
        except AuthError as exc:
            return PublishResult(draft_id, False, str(exc))

        draft = self.store.claim_for_publishing(draft_id)
        try:
            images = [m for m in draft.media if m.kind == "image"]
            videos = [m for m in draft.media if m.kind == "video"]
            image_urns = [client.upload_image(m.path) for m in images]
            video_urn = client.upload_video(videos[0].path) if videos else None
            post_urn = client.create_post(
                build_commentary(draft.body, draft.hashtags),
                image_urns=image_urns,
                alt_texts=[m.alt_text for m in images],
                video_urn=video_urn,
            )
        except (LinkedInError, OSError, ValueError) as exc:
            self.store.mark_failed(draft_id, str(exc))
            log.error("Draft %s failed: %s", draft_id, exc)
            return PublishResult(draft_id, False, f"Publishing failed: {exc}")

        self.store.mark_published(draft_id, post_urn)
        return PublishResult(draft_id, True, f"Published: {post_url(post_urn)}")

    def publish_due(self) -> list[PublishResult]:
        """Publish every approved draft whose scheduled time has arrived."""
        return [self.publish(d.id) for d in self.store.due(utcnow())]

    def run_scheduler(self, interval_seconds: int = 60, *, once: bool = False, sleep=time.sleep) -> None:
        """Loop forever (or once), publishing due drafts every interval.

        Stop with Ctrl+C. Errors in one draft never stop the loop.
        """
        interval_seconds = max(15, interval_seconds)
        log.info("Scheduler started (checking every %ss, dry_run=%s)", interval_seconds, self.settings.linkedin_dry_run)
        previewed: set[int] = set()  # dry-run drafts stay approved; show each preview once
        while True:
            for result in self.publish_due():
                if result.dry_run:
                    if result.draft_id not in previewed:
                        previewed.add(result.draft_id)
                        log.info(result.message)
                    continue
                (log.info if result.ok else log.error)(result.message)
            if once:
                return
            sleep(interval_seconds)
