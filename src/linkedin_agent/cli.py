"""Command-line interface: `linkedin-agent <command>`.

Run `linkedin-agent --help` for the command list. Each command is a thin
wrapper around Agent / Publisher / DraftStore; errors are shown as short
messages (exit code 1) instead of stack traces. Use --verbose for details.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime

from . import __version__
from .agent import Agent, parse_schedule
from .config import ConfigError, Settings, load_settings
from .linkedin.client import post_url
from .linkedin.oauth import AuthError, interactive_login, load_token
from .media import MediaError
from .publisher import Publisher
from .store import Draft, DraftStore, InvalidTransition
from .writer import WriterError

log = logging.getLogger("linkedin_agent")

# Errors that represent a user-fixable problem; shown without a traceback.
USER_ERRORS = (ConfigError, AuthError, MediaError, WriterError, InvalidTransition, KeyError, ValueError)


# ----------------------------------------------------------------------
# Display helpers
# ----------------------------------------------------------------------
def _local(dt: datetime | None, settings: Settings) -> str:
    """Format a stored UTC time in the user's time zone."""
    return dt.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M") if dt else "-"


def print_draft(d: Draft, settings: Settings) -> None:
    """Print a draft with its metadata, as the reader would see the text."""
    print("=" * 72)
    print(f"Draft #{d.id}  [{d.status}]  mode={d.mode}  topic={d.topic or '-'}")
    if d.scheduled_at:
        print(f"Scheduled: {_local(d.scheduled_at, settings)} ({settings.timezone})")
    if d.media:
        for m in d.media:
            print(f"Media: {m.kind} {m.path}" + (f"  alt: {m.alt_text}" if m.alt_text else ""))
    if d.post_urn:
        print(f"Post: {post_url(d.post_urn)}")
    if d.error:
        print(f"Last error: {d.error}")
    print("-" * 72)
    print(d.full_text())
    print(f"({len(d.full_text())} characters)")
    print("=" * 72)


def _edit_in_editor(text: str) -> str:
    """Open $EDITOR (or nano/notepad) on `text` and return the edited result."""
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else shutil.which("nano") or "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        path = fh.name
    try:
        subprocess.run([editor, path], check=True)
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def _split_hashtags(line: str) -> tuple[str, list[str]]:
    """Separate a trailing line of #hashtags (from an edited post) from the body."""
    lines = line.rstrip().splitlines()
    if lines and lines[-1].strip() and all(w.startswith("#") for w in lines[-1].split()):
        return "\n".join(lines[:-1]).rstrip(), [w.lstrip("#") for w in lines[-1].split()]
    return line.rstrip(), []


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def cmd_doctor(args, settings: Settings, store: DraftStore) -> int:
    """Check configuration and dependencies; prints a readiness report."""
    checks = [
        ("Claude API key set", bool(settings.anthropic_api_key)),
        ("LinkedIn app id/secret set", bool(settings.linkedin_client_id and settings.linkedin_client_secret)),
        ("ffmpeg installed (video previews)", shutil.which("ffmpeg") is not None),
    ]
    try:
        token = load_token(settings)
        checks.append((f"LinkedIn login ({token.name or token.person_urn}, {token.seconds_left // 86400} days left)", True))
    except AuthError as exc:
        checks.append((f"LinkedIn login: {exc}", False))
    print(f"linkedin-agent {__version__}")
    print(f"Database: {settings.db_path}")
    print(f"Model: {settings.claude_model} (effort {settings.claude_effort})   LinkedIn API version: {settings.linkedin_api_version}")
    print(f"Dry run: {'ON (nothing is posted)' if settings.linkedin_dry_run else 'OFF (approved posts go live)'}")
    for label, ok in checks:
        print(f"  [{'OK' if ok else '--'}] {label}")
    return 0


def cmd_login(args, settings: Settings, store: DraftStore) -> int:
    """Log in to LinkedIn with OAuth and save the token."""
    token = interactive_login(settings, open_browser=not args.no_browser, manual=args.manual)
    print(f"Logged in as {token.name or token.person_urn}. Token valid for {token.seconds_left // 86400} days.")
    return 0


def cmd_generate(args, settings: Settings, store: DraftStore) -> int:
    """Draft hot-topic posts."""
    print("Finding trending topics and writing with Claude...")
    for d in Agent(settings, store).generate_topic_drafts(args.count, args.topic):
        print_draft(d, settings)
    print("Review with: linkedin-agent review")
    return 0


def cmd_media(args, settings: Settings, store: DraftStore) -> int:
    """Draft a post about your photos or video."""
    print("Looking at your media and writing with Claude...")
    d = Agent(settings, store).create_media_draft(args.files, args.notes, args.alt)
    print_draft(d, settings)
    print(f"Approve with: linkedin-agent approve {d.id}   (or: linkedin-agent review)")
    return 0


def cmd_list(args, settings: Settings, store: DraftStore) -> int:
    """List drafts in a compact table."""
    drafts = store.list_drafts(status=args.status, limit=args.limit)
    if not drafts:
        print("No drafts.")
        return 0
    print(f"{'ID':>4}  {'STATUS':<10}  {'MODE':<5}  {'SCHEDULED':<16}  TOPIC")
    for d in drafts:
        print(f"{d.id:>4}  {d.status:<10}  {d.mode:<5}  {_local(d.scheduled_at, settings):<16}  {textwrap.shorten(d.topic or d.body, 40)}")
    return 0


def cmd_show(args, settings: Settings, store: DraftStore) -> int:
    """Show one draft in full."""
    print_draft(store.get(args.id), settings)
    return 0


def cmd_approve(args, settings: Settings, store: DraftStore) -> int:
    """Approve a draft for posting now or at a scheduled time."""
    when = parse_schedule(args.at, settings) if args.at else None
    d = store.approve(args.id, when)
    if when:
        print(f"Draft #{d.id} approved for {_local(when, settings)} ({settings.timezone}).")
    else:
        print(f"Draft #{d.id} approved. Publish now with: linkedin-agent publish {d.id}  (or let the scheduler post it)")
    return 0


def cmd_simple_transition(args, settings: Settings, store: DraftStore) -> int:
    """unapprove / discard / retry."""
    if args.command == "unapprove":
        d = store.unapprove(args.id)
    elif args.command == "discard":
        d = store.discard(args.id)
    else:
        d = store.retry(args.id, force=args.force)
    print(f"Draft #{d.id} is now '{d.status}'.")
    return 0


def cmd_edit(args, settings: Settings, store: DraftStore) -> int:
    """Edit a draft's text in your editor."""
    d = store.get(args.id)
    body, tags = _split_hashtags(_edit_in_editor(d.full_text()))
    d = store.update_content(d.id, body=body, hashtags=tags)
    print_draft(d, settings)
    return 0


def cmd_revise(args, settings: Settings, store: DraftStore) -> int:
    """Ask Claude to rewrite a draft with your feedback."""
    print_draft(Agent(settings, store).revise_draft(args.id, args.feedback), settings)
    return 0


def cmd_publish(args, settings: Settings, store: DraftStore) -> int:
    """Publish one approved draft immediately."""
    result = Publisher(settings, store).publish(args.id)
    print(result.message)
    return 0 if result.ok else 1


def cmd_scheduler(args, settings: Settings, store: DraftStore) -> int:
    """Run the scheduler loop."""
    print(f"Scheduler running every {args.interval}s. Press Ctrl+C to stop.")
    try:
        Publisher(settings, store).run_scheduler(args.interval, once=args.once)
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
    return 0


def cmd_review(args, settings: Settings, store: DraftStore) -> int:
    """Interactive approval loop over drafts waiting for review."""
    pending = list(reversed(store.list_drafts(status="draft", limit=100)))  # oldest first
    if not pending:
        print("Nothing to review. Create drafts with 'generate' or 'media'.")
        return 0
    agent = Agent(settings, store)
    menu = "[a]pprove now  [s]chedule  [e]dit  [r]evise with Claude  [d]iscard  [n]ext  [q]uit"
    for d in pending:
        while True:
            print_draft(d, settings)
            choice = input(menu + "\n> ").strip().lower()[:1]
            try:
                if choice == "a":
                    store.approve(d.id)
                    print(f"Approved #{d.id}.")
                    break
                if choice == "s":
                    when = parse_schedule(input('When? "YYYY-MM-DD HH:MM" > '), settings)
                    store.approve(d.id, when)
                    print(f"Scheduled #{d.id} for {_local(when, settings)}.")
                    break
                if choice == "e":
                    body, tags = _split_hashtags(_edit_in_editor(d.full_text()))
                    d = store.update_content(d.id, body=body, hashtags=tags)
                elif choice == "r":
                    d = agent.revise_draft(d.id, input("What should change? > "))
                elif choice == "d":
                    store.discard(d.id)
                    print(f"Discarded #{d.id}.")
                    break
                elif choice == "n":
                    break
                elif choice == "q":
                    return 0
            except USER_ERRORS as exc:
                print(f"Error: {exc}")
    print("Review finished. Approved drafts are posted by 'publish <id>' or 'run-scheduler'.")
    return 0


# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Define all sub-commands and their options."""
    p = argparse.ArgumentParser(prog="linkedin-agent", description="Draft LinkedIn posts with Claude; publish after your approval.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="show debug logs and tracebacks")
    p.add_argument("--env-file", help="path to a .env file (default: ./.env)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check configuration and login").set_defaults(func=cmd_doctor)

    s = sub.add_parser("login", help="log in to LinkedIn (OAuth)")
    s.add_argument("--manual", action="store_true", help="paste the redirect URL instead of using a local server")
    s.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("generate", help="draft posts about trending tech topics")
    s.add_argument("-n", "--count", type=int, default=1, help="number of drafts (1-5)")
    s.add_argument("-t", "--topic", help="write about this subject instead of picking a trend")
    s.set_defaults(func=cmd_generate)

    s = sub.add_parser("media", help="draft a post about your photos or video")
    s.add_argument("files", nargs="+", help="JPG/PNG/GIF photos (up to 20) or one MP4 video")
    s.add_argument("--notes", required=True, help="a few words about what happened")
    s.add_argument("--alt", action="append", help="alt text per photo (repeat in file order)")
    s.set_defaults(func=cmd_media)

    s = sub.add_parser("list", help="list drafts")
    s.add_argument("-s", "--status", choices=["draft", "approved", "publishing", "published", "failed", "discarded"])
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list)

    for name, func, help_text in (
        ("show", cmd_show, "show a draft"),
        ("edit", cmd_edit, "edit a draft in your text editor"),
        ("unapprove", cmd_simple_transition, "move an approved draft back to draft"),
        ("discard", cmd_simple_transition, "discard a draft"),
        ("publish", cmd_publish, "publish an approved draft now"),
    ):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("id", type=int)
        s.set_defaults(func=func)

    s = sub.add_parser("approve", help="approve a draft (optionally scheduled)")
    s.add_argument("id", type=int)
    s.add_argument("--at", help='schedule time "YYYY-MM-DD HH:MM" in TIMEZONE')
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("revise", help="ask Claude to rewrite a draft")
    s.add_argument("id", type=int)
    s.add_argument("--feedback", required=True, help='e.g. "shorter, add a personal example"')
    s.set_defaults(func=cmd_revise)

    s = sub.add_parser("retry", help="re-queue a failed draft")
    s.add_argument("id", type=int)
    s.add_argument("--force", action="store_true", help="also recover a draft stuck in 'publishing' (check LinkedIn first)")
    s.set_defaults(func=cmd_simple_transition)

    sub.add_parser("review", help="interactively approve, edit or discard new drafts").set_defaults(func=cmd_review)

    s = sub.add_parser("run-scheduler", help="publish approved drafts when they are due")
    s.add_argument("--interval", type=int, default=60, help="seconds between checks (min 15)")
    s.add_argument("--once", action="store_true", help="check once and exit (for cron / Task Scheduler)")
    s.set_defaults(func=cmd_scheduler)
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Keep third-party HTTP logs quiet unless debugging (they can include URLs with codes).
    for noisy in ("urllib3", "anthropic", "httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    try:
        settings = load_settings(args.env_file)
        store = DraftStore(settings.db_path)
        return args.func(args, settings, store)
    except USER_ERRORS as exc:
        if args.verbose:
            raise
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        print(f"Error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
