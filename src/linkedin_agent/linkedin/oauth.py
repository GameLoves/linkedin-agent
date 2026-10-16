"""LinkedIn OAuth 2.0 (3-legged, authorization code) login.

Flow:
1. Open LinkedIn's consent page in the browser with a random `state`.
2. LinkedIn redirects to LINKEDIN_REDIRECT_URI (a tiny local web server
   started here) with a `code`.
3. The code is exchanged for an access token, and `/v2/userinfo` gives the
   member id used as the post author (`urn:li:person:{sub}`).
4. The token is saved to DATA_DIR/linkedin_token.json with owner-only
   permissions. Member tokens last about 60 days; then log in again.

Scopes: `openid profile` (from the "Sign In with LinkedIn using OpenID
Connect" product) and `w_member_social` (from "Share on LinkedIn").
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ..config import ConfigError, Settings

log = logging.getLogger(__name__)

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
SCOPES = ("openid", "profile", "w_member_social")
TIMEOUT_SECONDS = 30
# Warn this many days before the token expires.
EXPIRY_WARNING_DAYS = 7


class AuthError(RuntimeError):
    """Login failed, or no valid token is available."""


@dataclass
class LinkedInToken:
    """Saved credentials for posting as one member.

    Attributes:
        access_token: Bearer token (secret; never logged).
        expires_at: Unix time when the token stops working.
        person_urn: Author URN, e.g. "urn:li:person:abc123".
        name: Member's display name, for friendly messages.
        scope: Space-separated scopes LinkedIn granted.
    """

    access_token: str
    expires_at: int
    person_urn: str
    name: str = ""
    scope: str = ""

    @property
    def seconds_left(self) -> int:
        """Seconds until expiry (negative when already expired)."""
        return int(self.expires_at - time.time())

    def __repr__(self) -> str:  # keep the secret out of logs and tracebacks
        return f"LinkedInToken(person_urn={self.person_urn!r}, name={self.name!r}, expires_at={self.expires_at})"


def build_authorization_url(settings: Settings, state: str) -> str:
    """Return the LinkedIn consent-page URL for this app."""
    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "state": state,
        "scope": " ".join(SCOPES),
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(settings: Settings, code: str, session: requests.Session | None = None) -> dict:
    """Exchange an authorization code for an access token.

    Raises:
        AuthError: if LinkedIn rejects the code or app credentials.
    """
    session = session or requests.Session()
    resp = session.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.linkedin_redirect_uri,
            "client_id": settings.linkedin_client_id,
            "client_secret": settings.linkedin_client_secret,
        },
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        # LinkedIn returns {"error": ..., "error_description": ...}; never echo the secret.
        try:
            detail = resp.json().get("error_description") or resp.text[:300]
        except ValueError:
            detail = resp.text[:300]
        raise AuthError(f"Token exchange failed ({resp.status_code}): {detail}")
    return resp.json()


def fetch_member(access_token: str, session: requests.Session | None = None) -> tuple[str, str]:
    """Return (person_urn, display name) for the token's member.

    Raises:
        AuthError: if the token lacks the openid/profile scopes.
    """
    session = session or requests.Session()
    resp = session.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        raise AuthError(
            f"Could not read your LinkedIn profile ({resp.status_code}). Make sure the app has the "
            "'Sign In with LinkedIn using OpenID Connect' product."
        )
    data = resp.json()
    return f"urn:li:person:{data['sub']}", data.get("name", "")


def save_token(settings: Settings, token: LinkedInToken) -> None:
    """Write the token file with owner-only (0600) permissions."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.token_path
    # Create with restrictive permissions from the start (no window where others can read it).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(asdict(token), fh)
    try:
        os.chmod(path, 0o600)
    except OSError:  # e.g. some Windows file systems; best effort
        pass


def load_token(settings: Settings) -> LinkedInToken:
    """Load the saved token and make sure it is still valid.

    Raises:
        AuthError: if there is no token or it has expired.
    """
    path = settings.token_path
    if not path.exists():
        raise AuthError("Not logged in to LinkedIn. Run: linkedin-agent login")
    try:
        token = LinkedInToken(**json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as exc:
        raise AuthError(f"Token file {path} is damaged. Run: linkedin-agent login") from exc
    if token.seconds_left <= 0:
        raise AuthError("Your LinkedIn login has expired (tokens last about 60 days). Run: linkedin-agent login")
    if token.seconds_left < EXPIRY_WARNING_DAYS * 86400:
        log.warning("LinkedIn login expires in %d day(s). Run 'linkedin-agent login' soon.", token.seconds_left // 86400)
    return token


def complete_login(settings: Settings, code: str, session: requests.Session | None = None) -> LinkedInToken:
    """Exchange the code, identify the member and save the token."""
    data = exchange_code(settings, code, session)
    person_urn, name = fetch_member(data["access_token"], session)
    token = LinkedInToken(
        access_token=data["access_token"],
        expires_at=int(time.time()) + int(data.get("expires_in", 0)),
        person_urn=person_urn,
        name=name,
        scope=data.get("scope", ""),
    )
    if "w_member_social" not in token.scope.replace(",", " ").split():
        log.warning("LinkedIn did not grant w_member_social; posting will fail. Add the 'Share on LinkedIn' product.")
    save_token(settings, token)
    return token


def parse_callback(url_or_query: str, expected_state: str) -> str:
    """Extract the authorization code from a redirect URL, checking `state`.

    Raises:
        AuthError: if LinkedIn returned an error or the state does not match
            (which would indicate a forged or stale redirect).
    """
    query = parse_qs(urlparse(url_or_query).query or url_or_query.lstrip("?"))
    if "error" in query:
        raise AuthError(f"LinkedIn login was not completed: {query.get('error_description', query['error'])[0]}")
    if query.get("state", [None])[0] != expected_state:
        raise AuthError("Login response did not match this session (state mismatch). Please try again.")
    code = query.get("code", [None])[0]
    if not code:
        raise AuthError("No authorization code in the redirect URL")
    return code


def interactive_login(settings: Settings, *, open_browser: bool = True, manual: bool = False, timeout: int = 300) -> LinkedInToken:
    """Run the full browser login.

    Args:
        open_browser: Open the consent page automatically.
        manual: Do not start a local server; ask the user to paste the URL
            they were redirected to (useful on remote/headless machines).
        timeout: Seconds to wait for the browser redirect.
    """
    settings.require_linkedin_app()
    state = secrets.token_urlsafe(24)
    url = build_authorization_url(settings, state)
    print("\nOpen this address to allow the agent to post for you:\n\n  " + url + "\n")

    if manual:
        pasted = input("After approving, paste the full address your browser was sent to:\n> ").strip()
        return complete_login(settings, parse_callback(pasted, state))

    redirect = urlparse(settings.linkedin_redirect_uri)
    if redirect.hostname not in {"localhost", "127.0.0.1"}:
        raise ConfigError("Automatic login needs a localhost LINKEDIN_REDIRECT_URI; use --manual otherwise.")

    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        """Receives the single OAuth redirect from the browser."""

        def do_GET(self):  # noqa: N802 (stdlib naming)
            if urlparse(self.path).path != (redirect.path or "/"):
                self.send_response(404)
                self.end_headers()
                return
            try:
                result["code"] = parse_callback(self.path, state)
                message = "LinkedIn login complete. You can close this tab."
                self.send_response(200)
            except AuthError as exc:
                result["error"] = str(exc)
                message = "Login failed: " + str(exc)
                self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(message.encode("utf-8"))

        def log_message(self, *args):  # silence default request logging (it contains the code)
            pass

    # Bind to loopback only so nothing else on the network can reach it.
    server = HTTPServer(("127.0.0.1", redirect.port or 80), Handler)
    server.timeout = 1
    thread = threading.Thread(target=lambda: _serve_until(server, result, timeout), daemon=True)
    thread.start()
    if open_browser:
        webbrowser.open(url)
    thread.join()
    server.server_close()

    if "error" in result:
        raise AuthError(result["error"])
    if "code" not in result:
        raise AuthError(f"No response from LinkedIn within {timeout} seconds. Run login again.")
    return complete_login(settings, result["code"])


def _serve_until(server: HTTPServer, result: dict, timeout: int) -> None:
    """Handle requests until a code/error arrives or the timeout passes."""
    deadline = time.time() + timeout
    while time.time() < deadline and not result:
        server.handle_request()
