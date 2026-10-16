"""Publisher: approval gate, dry run, success and failure bookkeeping."""

from datetime import timedelta

from linkedin_agent.linkedin.client import LinkedInError
from linkedin_agent.publisher import Publisher
from linkedin_agent.store import MediaItem, utcnow


class StubClient:
    """Records calls instead of talking to LinkedIn."""

    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.posts = []
        self.images = []

    def upload_image(self, path):
        self.images.append(path)
        return f"urn:li:image:{len(self.images)}"

    def upload_video(self, path):
        return "urn:li:video:1"

    def create_post(self, commentary, **kw):
        if self.fail_with:
            raise self.fail_with
        self.posts.append((commentary, kw))
        return "urn:li:share:42"


def test_refuses_unapproved(settings, store):
    d = store.add(mode="topic", body="x")
    stub = StubClient()
    result = Publisher(settings, store, lambda: stub).publish(d.id)
    assert not result.ok and stub.posts == []


def test_publishes_with_images_and_alt_text(settings, store):
    d = store.add(mode="media", body="Demo (day)", hashtags=["AI"], media=[MediaItem("/a.jpg", "image", "team"), MediaItem("/b.jpg", "image", "")])
    store.approve(d.id)
    stub = StubClient()
    result = Publisher(settings, store, lambda: stub).publish(d.id)
    assert result.ok and "urn:li:share:42" in result.message
    commentary, kw = stub.posts[0]
    assert commentary.startswith("Demo \\(day\\)")
    assert kw["image_urns"] == ["urn:li:image:1", "urn:li:image:2"] and kw["alt_texts"] == ["team", ""]
    assert store.get(d.id).status == "published"


def test_failure_is_recorded(settings, store):
    d = store.add(mode="topic", body="x")
    store.approve(d.id)
    result = Publisher(settings, store, lambda: StubClient(LinkedInError("nope", 403))).publish(d.id)
    saved = store.get(d.id)
    assert not result.ok and saved.status == "failed" and "nope" in saved.error


def test_dry_run_sends_nothing(settings, store):
    from dataclasses import replace

    d = store.add(mode="topic", body="x")
    store.approve(d.id)
    stub = StubClient()
    result = Publisher(replace(settings, linkedin_dry_run=True), store, lambda: stub).publish(d.id)
    assert result.ok and result.dry_run and stub.posts == []
    assert store.get(d.id).status == "approved"


def test_scheduler_publishes_only_due(settings, store):
    due = store.add(mode="topic", body="due")
    later = store.add(mode="topic", body="later")
    store.approve(due.id)
    store.approve(later.id, utcnow() + timedelta(hours=1))
    stub = StubClient()
    Publisher(settings, store, lambda: stub).run_scheduler(once=True)
    assert store.get(due.id).status == "published"
    assert store.get(later.id).status == "approved"


def test_missing_login_does_not_strand_draft(settings, store):
    from linkedin_agent.linkedin.oauth import AuthError

    d = store.add(mode="topic", body="x")
    store.approve(d.id)

    def no_login():
        raise AuthError("Not logged in")

    result = Publisher(settings, store, no_login).publish(d.id)
    assert not result.ok and store.get(d.id).status == "approved"
