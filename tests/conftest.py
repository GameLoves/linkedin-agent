"""Shared pytest fixtures: isolated settings/store and fake HTTP/Claude clients.

No test touches the network, the real LinkedIn API or the Claude API.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from linkedin_agent.config import Settings
from linkedin_agent.linkedin.oauth import LinkedInToken, save_token
from linkedin_agent.store import DraftStore


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at a temporary data folder, dry run OFF."""
    return Settings(
        anthropic_api_key="test-key",
        linkedin_client_id="cid",
        linkedin_client_secret="secret",
        linkedin_dry_run=False,
        data_dir=tmp_path / "data",
        timezone="Asia/Kabul",
    )


@pytest.fixture
def store(settings: Settings) -> DraftStore:
    """Fresh, migrated draft database."""
    return DraftStore(settings.db_path)


@pytest.fixture
def token(settings: Settings) -> LinkedInToken:
    """A valid saved LinkedIn token."""
    tok = LinkedInToken(access_token="tok", expires_at=int(time.time()) + 50 * 86400, person_urn="urn:li:person:abc", name="Test User", scope="openid profile w_member_social")
    save_token(settings, tok)
    return tok


@dataclass
class FakeResponse:
    """Minimal stand-in for requests.Response."""

    status_code: int = 200
    payload: object = None
    headers: dict = field(default_factory=dict)

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload

    @property
    def text(self) -> str:
        return json.dumps(self.payload) if self.payload is not None else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    """Records requests and replays queued responses matched by URL substring."""

    def __init__(self, routes: list[tuple[str, str, FakeResponse]]):
        # routes: (METHOD, url-substring, response); each route used once, in order.
        self.routes = list(routes)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for i, (m, frag, resp) in enumerate(self.routes):
            if m == method and frag in url:
                self.routes.pop(i)
                return resp
        raise AssertionError(f"Unexpected request {method} {url}")

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


class FakeClaude:
    """Fake Anthropic client returning queued JSON answers."""

    def __init__(self, answers: list[str], stop_reason: str = "end_turn"):
        self.answers = list(answers)
        self.stop_reason = stop_reason
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        text = self.answers.pop(0)
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)])


def post_json(**overrides) -> str:
    """Build a valid Claude answer, with optional field overrides."""
    data = {
        "topic": "Code review habits",
        "body": "Small pull requests get better reviews.\n\nWhat is your limit?",
        "hashtags": ["CodeReview", "#SoftwareEngineering", "codereview"],
        "source_url": "",
        "alt_texts": [],
    }
    data.update(overrides)
    return json.dumps(data)
