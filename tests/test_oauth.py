"""OAuth helpers: URL building, state checking, token storage and expiry."""

import os
import stat
import time
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import FakeResponse, FakeSession

from linkedin_agent.linkedin.oauth import (
    AuthError, LinkedInToken, build_authorization_url, complete_login, load_token, parse_callback, save_token,
)


def test_authorization_url_has_scopes_and_state(settings):
    q = parse_qs(urlparse(build_authorization_url(settings, "s1")).query)
    assert q["scope"] == ["openid profile w_member_social"]
    assert q["state"] == ["s1"] and q["response_type"] == ["code"]


def test_parse_callback_checks_state():
    assert parse_callback("http://localhost:8765/callback?code=abc&state=good", "good") == "abc"
    with pytest.raises(AuthError, match="state"):
        parse_callback("http://localhost:8765/callback?code=abc&state=evil", "good")
    with pytest.raises(AuthError, match="not completed"):
        parse_callback("?error=user_cancelled_login&error_description=cancelled&state=good", "good")


def test_token_saved_privately_and_loaded(settings):
    save_token(settings, LinkedInToken("secret-token", int(time.time()) + 3600, "urn:li:person:x"))
    if os.name != "nt":
        assert stat.S_IMODE(settings.token_path.stat().st_mode) == 0o600
    tok = load_token(settings)
    assert tok.person_urn == "urn:li:person:x"
    assert "secret-token" not in repr(tok)


def test_expired_token_rejected(settings):
    save_token(settings, LinkedInToken("t", int(time.time()) - 1, "urn:li:person:x"))
    with pytest.raises(AuthError, match="expired"):
        load_token(settings)


def test_missing_token_rejected(settings):
    with pytest.raises(AuthError, match="Not logged in"):
        load_token(settings)


def test_complete_login(settings):
    session = FakeSession([
        ("POST", "oauth/v2/accessToken", FakeResponse(200, {"access_token": "AT", "expires_in": 5184000, "scope": "openid,profile,w_member_social"})),
        ("GET", "/v2/userinfo", FakeResponse(200, {"sub": "782bbtaQ", "name": "Jane"})),
    ])
    tok = complete_login(settings, "code123", session)
    assert tok.person_urn == "urn:li:person:782bbtaQ" and load_token(settings).name == "Jane"
    assert session.calls[0][2]["data"]["code"] == "code123"


def test_token_exchange_error_is_readable(settings):
    session = FakeSession([("POST", "accessToken", FakeResponse(400, {"error": "invalid_grant", "error_description": "code expired"}))])
    with pytest.raises(AuthError, match="code expired"):
        complete_login(settings, "old", session)
