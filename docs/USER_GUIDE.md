# User Guide

Version 0.1.0. How to use the LinkedIn posting agent day to day.

## Roles

The agent has one user: **you, the author**. You own the Claude key, the LinkedIn app and the LinkedIn login, and only you approve posts. Claude only drafts; it can never publish on its own.

## How a post moves through the agent

| Status | Meaning | What you can do |
|---|---|---|
| `draft` | Written by Claude, waiting for you | edit, revise, approve, schedule, discard |
| `approved` | You accepted it; posts now or at its scheduled time | publish, unapprove, discard |
| `publishing` | Being uploaded right now | wait |
| `published` | Live on LinkedIn; the post link is saved | view with `show` |
| `failed` | LinkedIn rejected it; the error is saved | fix the cause, then `retry` |
| `discarded` | You threw it away (kept for history) | nothing |

## Commands

| Command | What it does | Example |
|---|---|---|
| `doctor` | Shows configuration, login status and dry-run mode | `linkedin-agent doctor` |
| `login` | Connects your LinkedIn account | `linkedin-agent login` |
| `generate` | Drafts posts about trending tech topics | `linkedin-agent generate -n 3` |
| `generate --topic` | Drafts a post on a subject you choose | `linkedin-agent generate -t "Why we moved from REST to gRPC"` |
| `media` | Drafts a post about your photos or video | see below |
| `review` | Walks through new drafts one by one | `linkedin-agent review` |
| `list` | Lists drafts (filter with `-s`) | `linkedin-agent list -s approved` |
| `show` | Shows one draft in full | `linkedin-agent show 4` |
| `edit` | Opens a draft in your text editor | `linkedin-agent edit 4` |
| `revise` | Asks Claude to rewrite with your feedback | `linkedin-agent revise 4 --feedback "shorter, add a personal lesson"` |
| `approve` | Approves for now, or schedules | `linkedin-agent approve 4 --at "2026-10-20 09:30"` |
| `unapprove` | Takes an approved draft back to draft | `linkedin-agent unapprove 4` |
| `publish` | Publishes an approved draft immediately | `linkedin-agent publish 4` |
| `run-scheduler` | Publishes approved drafts when they are due | `linkedin-agent run-scheduler` |
| `retry` | Re-queues a failed draft | `linkedin-agent retry 4` |
| `discard` | Throws a draft away | `linkedin-agent discard 4` |

Add `-v` before the command (for example `linkedin-agent -v publish 4`) for detailed logs.

## Workflow 1: hot-topic posts

```bash
linkedin-agent generate -n 3
linkedin-agent review
```

The agent fetches the current Hacker News top stories and asks Claude to pick the one most relevant to your `TOPIC_AREAS` that will start a good discussion. It avoids subjects you posted recently. Posts based on a story end with a `Source:` line.

For fresher signals beyond Hacker News, set `USE_WEB_SEARCH=true` in `.env`. Claude then searches the web for this week's discussions first. This uses Claude's web search tool, which must be enabled for your Anthropic organization and costs a little extra per search.

## Workflow 2: your photos or video

```bash
linkedin-agent media stage.jpg team.jpg certificate.png \
  --notes "Presented our AI-based attendance system at Ghalib University tech day. 40 students attended. Built with Django and PostgreSQL." \
  --alt "Me presenting on stage" --alt "Our five-person team"
```

- **Photos:** JPG, PNG or GIF, 1 to 20 per post.
- **Video:** one MP4, 75 KB to 500 MB, 3 seconds to 30 minutes.
- Photos and video cannot be mixed in one post (a LinkedIn rule).
- `--alt` is optional and repeats in file order. Missing alt texts are written by Claude.

**Writing good notes.** Claude only states facts it can see or that you wrote, so the notes carry the facts: what happened, where, who was involved (only names you are happy to publish), what you built or learned, and any numbers. Two or three sentences are enough.

## Reviewing drafts

`linkedin-agent review` shows each new draft with its character count, then:

| Key | Action |
|---|---|
| `a` | Approve; posted by `publish` or at the next scheduler run |
| `s` | Schedule; you type a time like `2026-10-20 09:30` in your `TIMEZONE` |
| `e` | Edit in your text editor. The last line of `#hashtags` becomes the hashtag list |
| `r` | Revise; type what should change and Claude rewrites it |
| `d` | Discard |
| `n` | Skip for now |
| `q` | Quit |

Approved drafts cannot be edited. Use `unapprove` first; this guarantees the published text is exactly what you approved.

## Validation messages you may see

| Message | Meaning |
|---|---|
| `The scheduled time must be in the future` | The time you typed has passed in your `TIMEZONE` |
| `Use the format "YYYY-MM-DD HH:MM"` | Type the time like `2026-10-20 09:30` |
| `A LinkedIn post can contain photos or one video, not both` | Split into two posts |
| `... does not match its .png extension` | The file was renamed; export it again in the real format |
| `Draft 4 is 'approved'; expected one of ('draft',)` | The action needs a different status; see the table above |
| `Please describe the photos/video in a few words (--notes)` | Notes are required for media posts |

## Tips for a strong LinkedIn presence

- Post 2 to 4 times a week. Schedule them for mornings in your audience's time zone.
- Mix topic posts (thought leadership) with media posts about your real work. Real projects build the most trust.
- Reply to comments yourself during the first hour after a post goes live.
- Use `revise` freely ("more personal", "less formal", "add a code example in words") until it sounds like you.

## Reports, import and export

There is no built-in reporting yet. Your full history lives in `data/agent.db` (SQLite). For example, to export everything to CSV:

```bash
sqlite3 -header -csv data/agent.db "SELECT id,status,topic,published_at,post_urn FROM drafts" > posts.csv
```
