# Startup Guide

Version 0.1.0. This guide takes you from nothing to a first real LinkedIn post, then explains daily use and running the agent on a schedule.

## 1. Requirements

| Item | Version / detail | Needed for |
|---|---|---|
| Operating system | Windows 10/11, macOS 13+, or Linux (Ubuntu 22.04+ tested family) | All |
| Python | 3.10 or newer (developed on 3.13) | All |
| pip | Bundled with Python | Installing |
| ffmpeg | Any recent build, on your `PATH` (optional) | Letting Claude see video frames and very large photos |
| Internet access | `api.anthropic.com`, `hacker-news.firebaseio.com`, `www.linkedin.com`, `api.linkedin.com` | Drafting and posting |
| Claude API key | From https://console.anthropic.com | Drafting |
| LinkedIn developer app | Free, created by you (step 4) | Posting |

No database server is required. The agent uses a local SQLite file in `DATA_DIR` (default `./data`). No ports are opened except **8765 on 127.0.0.1 for about a minute during `login`**.

Python packages (installed automatically): `anthropic` 1.15+, `requests` 2.32+, `python-dotenv` 1.0+; `pytest` 8+ for tests.

## 2. First installation

Run these in a terminal inside the `linkedin-agent` folder.

**Linux / macOS (bash/zsh)**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

**Windows (PowerShell)**
```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

Expected result: `linkedin-agent --version` prints `linkedin-agent 0.1.0`.

Installing ffmpeg (optional): `sudo apt install ffmpeg` (Ubuntu), `brew install ffmpeg` (macOS), or `winget install Gyan.FFmpeg` (Windows).

## 3. Add your Claude API key

1. Open https://console.anthropic.com, go to **Settings > API keys**, and create a key.
2. In `.env`, set `ANTHROPIC_API_KEY=sk-ant-...`.
3. Adjust your voice: `AUTHOR_NAME`, `AUTHOR_ROLE`, `AUTHOR_STYLE`, `TOPIC_AREAS`, `POST_LANGUAGE`, and `TIMEZONE` (for example `Asia/Kabul`).

At this point you can already draft posts:

```bash
linkedin-agent doctor      # "[OK] Claude API key set"
linkedin-agent generate    # prints a draft
```

## 4. Create the LinkedIn developer app (one time, about 10 minutes)

The agent posts through LinkedIn's official API, which requires an app that you own.

1. Go to https://www.linkedin.com/developers/apps and click **Create app**.
2. Fill in the app name (for example "My Post Agent"), a **LinkedIn Page** (LinkedIn requires one; your company page or a small page you create works), and a logo. Accept the terms.
3. Open the **Products** tab and request both:
   - **Share on LinkedIn** (grants `w_member_social`, needed to post).
   - **Sign In with LinkedIn using OpenID Connect** (grants `openid profile`, needed to know who you are).
   Both are self-serve and are normally approved immediately.
4. Open the **Auth** tab:
   - Copy **Client ID** and **Primary Client Secret** into `.env` as `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET`.
   - Under **Authorized redirect URLs for your app**, add exactly `http://localhost:8765/callback` (must match `LINKEDIN_REDIRECT_URI`).
5. Keep the client secret private. It must never be committed or shared; `.env` is already in `.gitignore`.

## 5. Log in to LinkedIn

```bash
linkedin-agent login
```

Your browser opens LinkedIn's consent page. Click **Allow**. The terminal prints `Logged in as <your name>. Token valid for 59 days.` The token is saved to `data/linkedin_token.json`, readable only by your user account.

On a remote server without a browser, use `linkedin-agent login --manual`: open the printed address on any computer, approve, then paste the address you were redirected to (the page itself will fail to load, which is expected).

## 6. First real post (do this with care)

1. Check `linkedin-agent doctor` shows `[OK]` for the key, the app and the login.
2. Create and approve a draft: `linkedin-agent generate`, then `linkedin-agent review` and press `a`.
3. With dry run still on, run `linkedin-agent publish <id>` and read the exact text that would be sent.
4. In `.env`, set `LINKEDIN_DRY_RUN=false`.
5. Run `linkedin-agent publish <id>`. Expected output: `Published: https://www.linkedin.com/feed/update/urn:li:share:...`.
6. Open the link and confirm the post looks right.

## 7. Daily use

```bash
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
linkedin-agent generate -n 3       # three topic ideas
linkedin-agent media photo1.jpg photo2.jpg --notes "What happened"
linkedin-agent review              # approve now / schedule / edit / revise / discard
```

Approved drafts are posted either by `linkedin-agent publish <id>` or by the scheduler.

## 8. Running the scheduler

**Option A: keep it running** in a terminal (or a `tmux`/`screen` session):
```bash
linkedin-agent run-scheduler --interval 60
```
Stop with `Ctrl+C`. Restart with the same command; nothing is lost because the queue is on disk.

**Option B: cron (Linux/macOS)**, every 10 minutes:
```cron
*/10 * * * * cd /path/to/linkedin-agent && .venv/bin/linkedin-agent run-scheduler --once >> data/scheduler.log 2>&1
```

**Option C: Windows Task Scheduler.** Create a task that runs every 10 minutes:
- Program: `C:\path\to\linkedin-agent\.venv\Scripts\linkedin-agent.exe`
- Arguments: `run-scheduler --once`
- Start in: `C:\path\to\linkedin-agent`

Optional: generate drafts automatically every morning by scheduling `linkedin-agent generate -n 2` the same way. They still wait for your approval.

**Readiness check:** `linkedin-agent doctor` and `linkedin-agent list -s approved`.

## 9. Production notes

- Keep `DATA_DIR` on a disk that is backed up. Back up with the scheduler stopped: copy `data/agent.db` (and `agent.db-wal` if present). Restore by copying the file back. The token file does not need a backup; just log in again.
- Database schema changes are applied automatically at start-up (versioned migrations in `store.py`). Back up `agent.db` before upgrading the agent.
- Run only one scheduler per database. Two would not double-post (claims are atomic), but one is simpler to monitor.
- Every 60 days LinkedIn tokens expire; `doctor` and the logs warn 7 days ahead.
- About once a year update `LINKEDIN_API_VERSION` to a current `YYYYMM` when LinkedIn announces a sunset.

## 10. Shutdown and uninstall

Stop the scheduler with `Ctrl+C` or by disabling the cron/Task Scheduler entry. To revoke the agent's LinkedIn access, go to LinkedIn **Settings > Data privacy > Permitted services** and remove the app, then delete `data/linkedin_token.json`.
