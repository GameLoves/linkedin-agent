"""Agent workflows (with a fake Claude) and schedule parsing."""

from datetime import datetime, timezone

import pytest
from conftest import FakeClaude, post_json

from linkedin_agent.agent import Agent, parse_schedule
from linkedin_agent.trends import TrendItem
from linkedin_agent.writer import PostWriter

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20


def test_generate_batch_avoids_repeating_topics(settings, store):
    fake = FakeClaude([post_json(topic="Rust in the kernel"), post_json(topic="Vector databases")])
    agent = Agent(settings, store, PostWriter(settings, client=fake), trend_fetcher=lambda: [TrendItem("t", "u")])
    drafts = agent.generate_topic_drafts(count=2)
    assert [d.topic for d in drafts] == ["Vector databases", "Rust in the kernel"][::-1]
    assert "Rust in the kernel" in fake.requests[1]["messages"][0]["content"]
    assert all(d.status == "draft" for d in drafts)


def test_media_draft_uses_user_alt_text_first(settings, store, tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(PNG)
    b.write_bytes(PNG)
    fake = FakeClaude([post_json(alt_texts=["claude a", "claude b"])])
    agent = Agent(settings, store, PostWriter(settings, client=fake))
    d = agent.create_media_draft([str(a), str(b)], "Hackathon at Ghalib University", ["my alt"])
    assert [m.alt_text for m in d.media] == ["my alt", "claude b"]


def test_media_draft_requires_notes(settings, store):
    with pytest.raises(ValueError):
        Agent(settings, store, PostWriter(settings, client=FakeClaude([]))).create_media_draft(["x.png"], "  ")


def test_parse_schedule_uses_configured_timezone(settings):
    now = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
    when = parse_schedule("2026-10-20 09:30", settings, now=now)
    assert when.astimezone(timezone.utc).strftime("%H:%M") == "05:00"  # Kabul is UTC+4:30


@pytest.mark.parametrize("text", ["tomorrow", "2026-10-20", "2020-01-01 10:00"])
def test_parse_schedule_rejects_bad_input(settings, text):
    with pytest.raises(ValueError):
        parse_schedule(text, settings, now=datetime(2026, 10, 16, tzinfo=timezone.utc))
