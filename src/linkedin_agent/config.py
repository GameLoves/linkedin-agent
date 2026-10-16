"""Application configuration loaded from environment variables / `.env`.

All settings live in one immutable `Settings` object so every module reads
configuration the same way and tests can build one explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when a required setting is missing or invalid."""


# Placeholder values from .env.example; treated the same as "not set".
_PLACEHOLDERS = {"", "REPLACE_ME", "sk-ant-REPLACE_ME"}


def _bool(value: str | None, default: bool) -> bool:
    """Parse a boolean env value ("true"/"1"/"yes" are true).

    Args:
        value: Raw string from the environment, or None when unset.
        default: Returned when the variable is unset or blank.
    """
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Typed view of every configuration value the agent uses."""

    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"
    use_web_search: bool = False

    author_name: str = "A software engineer"
    author_role: str = "Software Engineer"
    author_style: str = "Practical, honest and friendly. Avoids hype."
    post_language: str = "English"
    topic_areas: tuple[str, ...] = (
        "artificial intelligence",
        "software engineering",
        "computer science",
    )

    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    linkedin_redirect_uri: str = "http://localhost:8765/callback"
    linkedin_api_version: str = "202609"
    linkedin_dry_run: bool = True

    data_dir: Path = field(default_factory=lambda: Path("./data"))
    timezone: str = "UTC"

    # ------------------------------------------------------------------
    # Derived paths
    # ------------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        """SQLite file holding the draft queue."""
        return self.data_dir / "agent.db"

    @property
    def token_path(self) -> Path:
        """JSON file holding the LinkedIn OAuth access token (mode 0600)."""
        return self.data_dir / "linkedin_token.json"

    @property
    def tz(self) -> ZoneInfo:
        """Time zone object for interpreting user-typed schedule times."""
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"TIMEZONE '{self.timezone}' is not a valid IANA zone name") from exc

    # ------------------------------------------------------------------
    # Validation helpers used right before a feature needs the values
    # ------------------------------------------------------------------
    def require_claude(self) -> None:
        """Ensure Claude credentials exist before drafting.

        Raises:
            ConfigError: if ANTHROPIC_API_KEY is missing.
        """
        if not self.anthropic_api_key:
            raise ConfigError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")

    def require_linkedin_app(self) -> None:
        """Ensure LinkedIn app credentials exist before OAuth login.

        Raises:
            ConfigError: if client id or secret is missing.
        """
        missing = [
            name
            for name, value in (
                ("LINKEDIN_CLIENT_ID", self.linkedin_client_id),
                ("LINKEDIN_CLIENT_SECRET", self.linkedin_client_secret),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"{', '.join(missing)} not set. Create a LinkedIn developer app "
                "(see docs/STARTUP_GUIDE.md) and add the values to .env."
            )


def _clean(value: str | None) -> str | None:
    """Return None for unset or placeholder values, else the stripped value."""
    if value is None:
        return None
    value = value.strip()
    return None if value in _PLACEHOLDERS else value


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    """Load settings from the process environment (and `.env` if present).

    Args:
        env_file: Optional explicit path to a dotenv file. When omitted,
            `.env` in the current directory is used if it exists. Real
            environment variables always win over the file.

    Returns:
        A populated, immutable Settings instance.
    """
    load_dotenv(env_file or Path.cwd() / ".env", override=False)
    env = os.environ

    # Effort is validated here so a typo fails fast instead of as an API 400.
    effort = (env.get("CLAUDE_EFFORT") or "medium").strip().lower()
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ConfigError("CLAUDE_EFFORT must be one of: low, medium, high, xhigh, max")

    topics = tuple(
        t.strip() for t in (env.get("TOPIC_AREAS") or "").split(",") if t.strip()
    ) or Settings.topic_areas

    return Settings(
        anthropic_api_key=_clean(env.get("ANTHROPIC_API_KEY")),
        claude_model=(env.get("CLAUDE_MODEL") or "claude-opus-5-5").strip(),
        claude_effort=effort,
        use_web_search=_bool(env.get("USE_WEB_SEARCH"), False),
        author_name=(env.get("AUTHOR_NAME") or Settings.author_name).strip(),
        author_role=(env.get("AUTHOR_ROLE") or Settings.author_role).strip(),
        author_style=(env.get("AUTHOR_STYLE") or Settings.author_style).strip(),
        post_language=(env.get("POST_LANGUAGE") or "English").strip(),
        topic_areas=topics,
        linkedin_client_id=_clean(env.get("LINKEDIN_CLIENT_ID")),
        linkedin_client_secret=_clean(env.get("LINKEDIN_CLIENT_SECRET")),
        linkedin_redirect_uri=(env.get("LINKEDIN_REDIRECT_URI") or Settings.linkedin_redirect_uri).strip(),
        linkedin_api_version=(env.get("LINKEDIN_API_VERSION") or "202609").strip(),
        # Dry run defaults to ON so nothing is published by accident.
        linkedin_dry_run=_bool(env.get("LINKEDIN_DRY_RUN"), True),
        data_dir=Path(env.get("DATA_DIR") or "./data").expanduser(),
        timezone=(env.get("TIMEZONE") or "UTC").strip(),
    )
