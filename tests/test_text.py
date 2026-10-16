"""LinkedIn little-text escaping (unescaped characters can truncate posts)."""

import pytest

from linkedin_agent.linkedin.text import RESERVED, build_commentary, escape_little_text, hashtag_template


def test_every_reserved_character_is_escaped():
    escaped = escape_little_text("".join(sorted(RESERVED)))
    assert escaped == "".join("\\" + c for c in sorted(RESERVED))


def test_plain_text_unchanged():
    assert escape_little_text("Hello, world! 1-2. Salam") == "Hello, world! 1-2. Salam"


def test_build_commentary_with_hashtags():
    out = build_commentary("Use f(x) & @team", ["AI", "Dev"])
    assert out == "Use f\\(x\\) & \\@team\n\n{hashtag|\\#|AI} {hashtag|\\#|Dev}"


def test_build_commentary_without_hashtags():
    assert build_commentary("text\n", []) == "text"


@pytest.mark.parametrize("bad", ["two words", "a}b", ""])
def test_hashtag_rejects_invalid(bad):
    with pytest.raises(ValueError):
        hashtag_template(bad)
