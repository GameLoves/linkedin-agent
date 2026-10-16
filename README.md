# LinkedIn Posting Agent

An assistant that writes LinkedIn posts in your voice as a software engineer and publishes them to your profile **after you approve them**.

It does two jobs:

1. **Hot-topic posts.** It reads what engineers are discussing right now (Hacker News top stories, plus optional web search), picks the subject that best fits your interests (AI, software engineering, computer science and so on), and drafts an opinionated, honest post.
2. **Your photos and videos.** You give it photos (up to 20) or one video, plus a few words about what happened. Claude looks at the images (or still frames from the video) and writes the post, including alt text for accessibility.

Every draft waits in a local queue. You review, edit, ask Claude to revise, approve, and either publish immediately or schedule it. Publishing uses LinkedIn's **official API** (OAuth 2.0 and the Posts API). There is no scraping or browser automation, so your account stays within LinkedIn's terms.

```
 Hacker News / web ─┐                 ┌─▶ review / edit / revise ─▶ approve (now or scheduled)
                    ├─▶ Claude ─▶ draft queue (SQLite)                        │
 photos / video ────┘   (writer)                                             ▼
   + your notes                                       scheduler / publish ─▶ LinkedIn Posts API
```

## Quick start

```bash
cd linkedin-agent
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env                                    # then add your keys
linkedin-agent doctor                                   # check the setup
linkedin-agent generate                                 # draft a hot-topic post
linkedin-agent media team.jpg demo.jpg --notes "Our team shipped the library system at Ghalib University"
linkedin-agent review                                   # approve, edit, schedule or discard
linkedin-agent login                                    # once LinkedIn credentials are in .env
linkedin-agent run-scheduler                            # posts approved drafts when they're due
```

`LINKEDIN_DRY_RUN=true` is the default, so nothing is posted until you switch it off. Full steps are in [docs/STARTUP_GUIDE.md](docs/STARTUP_GUIDE.md).

## Documentation

| Guide | What it covers |
|---|---|
| [docs/STARTUP_GUIDE.md](docs/STARTUP_GUIDE.md) | Requirements, Claude key, creating the LinkedIn developer app, first run, daily use, running on a schedule |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Every command, the review workflow, writing good notes, scheduling, examples |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Error table with causes, checks and fixes; logs and diagnostics |

## Project layout

```
src/linkedin_agent/
  cli.py          Command-line interface (thin layer)
  agent.py        Workflows: generate topic drafts, media drafts, revisions, schedule parsing
  writer.py       Claude prompts, structured JSON output, refusal handling
  trends.py       Hacker News trending stories (no key needed)
  media.py        Photo/video validation against LinkedIn limits; previews for Claude
  store.py        SQLite draft queue and approval state machine (versioned migrations)
  publisher.py    Publishes approved drafts; scheduler loop; dry run
  linkedin/
    oauth.py      OAuth 2.0 login, token storage (file mode 0600)
    client.py     Images, Videos (multipart) and Posts API calls with safe retries
    text.py       LinkedIn "little text" escaping and hashtag templates
tests/            66 unit tests; no network access needed
```

## Design decisions

- **Human approval is mandatory.** Only drafts in the `approved` state can be published, and approved text cannot be edited, so what you approved is exactly what goes out.
- **No duplicate posts.** Publishing claims a draft with an atomic database update, and creating the post is never retried after a server error (LinkedIn might already have created it). Such failures tell you to check your profile first.
- **Honest content.** The prompt forbids invented statistics, quotes and personal events, and news-based posts end with a `Source:` line. Headlines, web results and your notes are passed to Claude as untrusted data, so instructions hidden in a headline are ignored.
- **Model.** Claude Opus 5.5 (`claude-opus-5-5`) with adaptive thinking at `medium` effort, and the API's server-side refusal fallback enabled. Change `CLAUDE_MODEL` / `CLAUDE_EFFORT` in `.env` to trade quality for cost.
- **Correct LinkedIn formatting.** Post text is converted to LinkedIn's "little text" format. Unescaped characters such as `(`, `)`, `@` or `_` can otherwise silently truncate a post.

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

## Status and limitations

- Tested with mocked LinkedIn and Claude APIs. The first real post should be done with you watching; see "First real post" in the startup guide.
- LinkedIn member tokens last about 60 days. Run `linkedin-agent login` again when `doctor` warns you.
- `LINKEDIN_API_VERSION` (default `202609`) must be updated roughly once a year when LinkedIn retires old versions.
- Claude cannot watch video. For videos it sees up to 3 still frames (needs `ffmpeg`) plus your notes.
- Command-line interface only for now; a small web dashboard could be added on top of `agent.py` and `publisher.py`.
