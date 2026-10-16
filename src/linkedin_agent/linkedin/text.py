"""Conversion of plain post text into LinkedIn's "little text" format.

The Posts API `commentary` field is not plain text: characters such as
( ) [ ] { } < > @ # * _ ~ | \\ are reserved and, if left unescaped, can make
LinkedIn silently cut the post short. Every reserved character is escaped
with a backslash, and hashtags are emitted with LinkedIn's hashtag template
so they stay clickable.

Reference: LinkedIn "little Text Format" documentation (checked 2026-10).
"""

from __future__ import annotations

import re

# The 15 reserved characters listed in the little text grammar.
RESERVED = set("|{}@[]()<>#\\*_~")

_HASHTAG_VALUE = re.compile(r"^[0-9A-Za-z؀-ۿ]+$")


def escape_little_text(text: str) -> str:
    """Escape every reserved character with a backslash.

    Example: the text  Use f(x) #now  becomes  Use f\\(x\\) \\#now
    """
    return "".join("\\" + ch if ch in RESERVED else ch for ch in text)


def hashtag_template(tag: str) -> str:
    """Return LinkedIn's clickable hashtag template for one tag.

    Raises:
        ValueError: if the tag contains characters other than letters/digits.
    """
    tag = tag.lstrip("#")
    if not _HASHTAG_VALUE.match(tag):
        raise ValueError(f"Invalid hashtag: {tag!r}")
    return "{hashtag|\\#|" + tag + "}"


def build_commentary(body: str, hashtags: list[str]) -> str:
    """Build the final `commentary` value: escaped body plus hashtag line."""
    escaped = escape_little_text(body.rstrip())
    if not hashtags:
        return escaped
    return escaped + "\n\n" + " ".join(hashtag_template(t) for t in hashtags)
