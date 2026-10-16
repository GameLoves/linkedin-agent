"""SQLite-backed draft queue.

Every post the agent writes is stored here first. A draft only reaches
LinkedIn after a person approves it, and the state machine below is
enforced in SQL (conditional UPDATEs) so that two running schedulers can
never publish the same draft twice.

State machine::

    draft ──approve──▶ approved ──claim──▶ publishing ──▶ published
      ▲                  │   ▲                   │
      └────unapprove─────┘   └──────retry────── failed
    draft / approved / failed ──discard──▶ discarded

All timestamps are stored as UTC ISO-8601 strings.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# Allowed values, mirrored by CHECK constraints in the schema.
STATUSES = ("draft", "approved", "publishing", "published", "failed", "discarded")
MODES = ("topic", "media")

# Ordered list of migrations. Index + 1 == schema version (PRAGMA user_version).
# Append new migrations; never edit an existing one after release.
_MIGRATIONS: list[str] = [
    """
    CREATE TABLE drafts (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        mode          TEXT    NOT NULL CHECK (mode IN ('topic', 'media')),
        topic         TEXT,
        source_url    TEXT,
        notes         TEXT,
        body          TEXT    NOT NULL CHECK (length(body) > 0),
        hashtags      TEXT    NOT NULL DEFAULT '[]',
        media         TEXT    NOT NULL DEFAULT '[]',
        status        TEXT    NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','approved','publishing','published','failed','discarded')),
        scheduled_at  TEXT,
        created_at    TEXT    NOT NULL,
        updated_at    TEXT    NOT NULL,
        published_at  TEXT,
        post_urn      TEXT,
        error         TEXT,
        attempts      INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_drafts_status_schedule ON drafts (status, scheduled_at);
    """,
]


def utcnow() -> datetime:
    """Current time as an aware UTC datetime (second precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime | None) -> str | None:
    """Serialize an aware datetime to a UTC ISO string for storage.

    Raises:
        ValueError: if `dt` is naive, because its zone would be ambiguous.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def from_iso(value: str | None) -> datetime | None:
    """Parse a stored UTC ISO string back into an aware datetime."""
    return datetime.fromisoformat(value) if value else None


@dataclass
class MediaItem:
    """A local photo or video attached to a draft.

    Attributes:
        path: Absolute path to the file on disk.
        kind: "image" or "video".
        alt_text: Accessibility description sent to LinkedIn (images).
    """

    path: str
    kind: str
    alt_text: str = ""


@dataclass
class Draft:
    """One LinkedIn post candidate and its lifecycle state."""

    id: int
    mode: str
    body: str
    status: str
    created_at: datetime
    updated_at: datetime
    topic: str | None = None
    source_url: str | None = None
    notes: str | None = None
    hashtags: list[str] = field(default_factory=list)
    media: list[MediaItem] = field(default_factory=list)
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    post_urn: str | None = None
    error: str | None = None
    attempts: int = 0

    def full_text(self) -> str:
        """Post body followed by the hashtag line, as a reader will see it."""
        tags = " ".join(f"#{t}" for t in self.hashtags)
        return f"{self.body.rstrip()}\n\n{tags}" if tags else self.body.rstrip()


class InvalidTransition(RuntimeError):
    """Raised when a draft is not in a state that allows the requested action."""


class DraftStore:
    """Data-access object for the drafts table.

    Opens a short-lived connection per operation; SQLite handles locking.
    """

    def __init__(self, db_path: Path):
        """Create the store and apply any pending schema migrations.

        Args:
            db_path: Location of the SQLite file (parent folder is created).
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    # ------------------------------------------------------------------
    # Connection and migrations
    # ------------------------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside a transaction (commit or roll back)."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commits on success, rolls back on exception
                yield conn
        finally:
            conn.close()

    def _migrate(self) -> None:
        """Apply migrations newer than the database's user_version."""
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")  # better concurrent readers
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for version, script in enumerate(_MIGRATIONS[current:], start=current + 1):
                conn.executescript(script)
                conn.execute(f"PRAGMA user_version = {version}")

    # ------------------------------------------------------------------
    # Row mapping
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_draft(row: sqlite3.Row) -> Draft:
        """Convert a database row into a Draft object."""
        return Draft(
            id=row["id"],
            mode=row["mode"],
            body=row["body"],
            status=row["status"],
            created_at=from_iso(row["created_at"]),
            updated_at=from_iso(row["updated_at"]),
            topic=row["topic"],
            source_url=row["source_url"],
            notes=row["notes"],
            hashtags=json.loads(row["hashtags"]),
            media=[MediaItem(**m) for m in json.loads(row["media"])],
            scheduled_at=from_iso(row["scheduled_at"]),
            published_at=from_iso(row["published_at"]),
            post_urn=row["post_urn"],
            error=row["error"],
            attempts=row["attempts"],
        )

    # ------------------------------------------------------------------
    # Create / read
    # ------------------------------------------------------------------
    def add(
        self,
        *,
        mode: str,
        body: str,
        hashtags: list[str] | None = None,
        media: list[MediaItem] | None = None,
        topic: str | None = None,
        source_url: str | None = None,
        notes: str | None = None,
    ) -> Draft:
        """Insert a new draft in the `draft` state and return it.

        Raises:
            ValueError: for an unknown mode or an empty body.
        """
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if not body or not body.strip():
            raise ValueError("body must not be empty")
        now = to_iso(utcnow())
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO drafts (mode, topic, source_url, notes, body, hashtags,
                                       media, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (
                    mode,
                    topic,
                    source_url,
                    notes,
                    body.strip(),
                    json.dumps(hashtags or []),
                    json.dumps([m.__dict__ for m in media or []]),
                    now,
                    now,
                ),
            )
            new_id = cur.lastrowid
        return self.get(new_id)

    def get(self, draft_id: int) -> Draft:
        """Fetch one draft by id.

        Raises:
            KeyError: if no draft has that id.
        """
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError(f"No draft with id {draft_id}")
        return self._row_to_draft(row)

    def list_drafts(self, status: str | None = None, limit: int = 50, offset: int = 0) -> list[Draft]:
        """List drafts newest first, optionally filtered by status (paginated)."""
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        sql = "SELECT * FROM drafts"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [max(1, min(limit, 500)), max(0, offset)]
        with self._connect() as conn:
            return [self._row_to_draft(r) for r in conn.execute(sql, params)]

    def due(self, now: datetime | None = None) -> list[Draft]:
        """Approved drafts whose scheduled time has arrived (or is unset), oldest first."""
        now_iso = to_iso(now or utcnow())
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM drafts
                   WHERE status = 'approved' AND (scheduled_at IS NULL OR scheduled_at <= ?)
                   ORDER BY COALESCE(scheduled_at, created_at), id""",
                (now_iso,),
            ).fetchall()
        return [self._row_to_draft(r) for r in rows]

    # ------------------------------------------------------------------
    # State transitions. Each uses a conditional UPDATE so the check and
    # the change happen atomically inside SQLite.
    # ------------------------------------------------------------------
    def _transition(self, draft_id: int, allowed_from: tuple[str, ...], sets: dict) -> Draft:
        """Apply `sets` only if the draft's current status is in `allowed_from`.

        Raises:
            KeyError: unknown draft id.
            InvalidTransition: draft exists but is in another state.
        """
        sets = {**sets, "updated_at": to_iso(utcnow())}
        assignments = ", ".join(f"{col} = ?" for col in sets)  # column names are internal constants
        placeholders = ", ".join("?" for _ in allowed_from)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE drafts SET {assignments} WHERE id = ? AND status IN ({placeholders})",
                (*sets.values(), draft_id, *allowed_from),
            )
            changed = cur.rowcount
        if changed == 0:
            current = self.get(draft_id)  # raises KeyError if missing
            raise InvalidTransition(
                f"Draft {draft_id} is '{current.status}'; expected one of {allowed_from}"
            )
        return self.get(draft_id)

    def update_content(
        self, draft_id: int, *, body: str | None = None, hashtags: list[str] | None = None
    ) -> Draft:
        """Edit text or hashtags. Only drafts not yet approved can be edited,
        so the approved text is exactly what gets published."""
        sets: dict = {}
        if body is not None:
            if not body.strip():
                raise ValueError("body must not be empty")
            sets["body"] = body.strip()
        if hashtags is not None:
            sets["hashtags"] = json.dumps(hashtags)
        if not sets:
            return self.get(draft_id)
        return self._transition(draft_id, ("draft",), sets)

    def approve(self, draft_id: int, scheduled_at: datetime | None = None) -> Draft:
        """Mark a draft approved, optionally for a future time (None = next scheduler run)."""
        return self._transition(
            draft_id, ("draft",), {"status": "approved", "scheduled_at": to_iso(scheduled_at), "error": None}
        )

    def unapprove(self, draft_id: int) -> Draft:
        """Move an approved (not yet publishing) draft back to editable draft."""
        return self._transition(draft_id, ("approved",), {"status": "draft", "scheduled_at": None})

    def discard(self, draft_id: int) -> Draft:
        """Soft-delete a draft; the row is kept for history."""
        return self._transition(draft_id, ("draft", "approved", "failed"), {"status": "discarded"})

    def claim_for_publishing(self, draft_id: int) -> Draft:
        """Atomically move approved -> publishing. Only one caller can win.

        The winner is decided by the UPDATE's row count, not by re-reading
        the status, because a losing caller would also read 'publishing'.
        """
        with self._connect() as conn:
            claimed = conn.execute(
                """UPDATE drafts SET status = 'publishing', attempts = attempts + 1, updated_at = ?
                   WHERE id = ? AND status = 'approved'""",
                (to_iso(utcnow()), draft_id),
            ).rowcount
        draft = self.get(draft_id)
        if claimed != 1:
            raise InvalidTransition(f"Draft {draft_id} is '{draft.status}', not 'approved'")
        return draft

    def mark_published(self, draft_id: int, post_urn: str) -> Draft:
        """Record a successful publish and LinkedIn's post URN."""
        return self._transition(
            draft_id,
            ("publishing",),
            {"status": "published", "post_urn": post_urn, "published_at": to_iso(utcnow()), "error": None},
        )

    def mark_failed(self, draft_id: int, error: str) -> Draft:
        """Record a failed publish; the draft can be retried later."""
        return self._transition(draft_id, ("publishing",), {"status": "failed", "error": error[:2000]})

    def retry(self, draft_id: int, *, force: bool = False) -> Draft:
        """Return a failed draft to the approved queue.

        Args:
            force: Also recover a draft stuck in 'publishing' (e.g. after a
                crash). Check LinkedIn first so you don't post it twice.
        """
        allowed = ("failed", "publishing") if force else ("failed",)
        return self._transition(draft_id, allowed, {"status": "approved", "scheduled_at": None})
