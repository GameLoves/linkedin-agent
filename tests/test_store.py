"""Draft queue: state machine, scheduling and double-publish protection."""

from datetime import timedelta

import pytest

from linkedin_agent.store import DraftStore, InvalidTransition, MediaItem, utcnow


def test_add_and_get_roundtrip(store):
    d = store.add(mode="media", body="  Hello  ", hashtags=["AI"], media=[MediaItem("/x.jpg", "image", "alt")])
    got = store.get(d.id)
    assert got.body == "Hello"
    assert got.status == "draft"
    assert got.media[0].alt_text == "alt"
    assert got.full_text() == "Hello\n\n#AI"


@pytest.mark.parametrize("kwargs", [{"mode": "bad", "body": "x"}, {"mode": "topic", "body": "   "}])
def test_add_rejects_invalid(store, kwargs):
    with pytest.raises(ValueError):
        store.add(**kwargs)


def test_get_missing_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.get(999)


def test_happy_path_transitions(store):
    d = store.add(mode="topic", body="x")
    store.approve(d.id)
    store.claim_for_publishing(d.id)
    done = store.mark_published(d.id, "urn:li:share:1")
    assert done.status == "published" and done.post_urn == "urn:li:share:1" and done.attempts == 1


def test_claim_only_once(store):
    """Two schedulers racing: the second claim must fail (no duplicate post)."""
    d = store.add(mode="topic", body="x")
    store.approve(d.id)
    store.claim_for_publishing(d.id)
    with pytest.raises(InvalidTransition):
        store.claim_for_publishing(d.id)


def test_cannot_edit_after_approval(store):
    d = store.add(mode="topic", body="x")
    store.approve(d.id)
    with pytest.raises(InvalidTransition):
        store.update_content(d.id, body="changed")


def test_cannot_publish_unapproved(store):
    d = store.add(mode="topic", body="x")
    with pytest.raises(InvalidTransition):
        store.claim_for_publishing(d.id)


def test_retry_requires_force_for_stuck_publishing(store):
    d = store.add(mode="topic", body="x")
    store.approve(d.id)
    store.claim_for_publishing(d.id)
    with pytest.raises(InvalidTransition):
        store.retry(d.id)
    assert store.retry(d.id, force=True).status == "approved"


def test_due_respects_schedule(store):
    now = utcnow()
    a = store.add(mode="topic", body="now")
    b = store.add(mode="topic", body="later")
    c = store.add(mode="topic", body="not approved")
    store.approve(a.id)
    store.approve(b.id, now + timedelta(hours=2))
    assert [d.id for d in store.due(now)] == [a.id]
    assert {d.id for d in store.due(now + timedelta(hours=3))} == {a.id, b.id}
    assert c.id not in {d.id for d in store.due(now + timedelta(days=1))}


def test_approve_rejects_naive_datetime(store):
    from datetime import datetime

    d = store.add(mode="topic", body="x")
    with pytest.raises(ValueError):
        store.approve(d.id, datetime(2030, 1, 1, 9, 0))


def test_migration_is_idempotent(settings):
    DraftStore(settings.db_path).add(mode="topic", body="keep me")
    again = DraftStore(settings.db_path)  # re-open runs migrations again
    assert again.list_drafts()[0].body == "keep me"


def test_list_filter_and_pagination(store):
    for i in range(5):
        store.add(mode="topic", body=f"b{i}")
    store.discard(1)
    assert len(store.list_drafts(status="draft")) == 4
    assert [d.id for d in store.list_drafts(limit=2)] == [5, 4]
    with pytest.raises(ValueError):
        store.list_drafts(status="nope")
