# Troubleshooting

Version 0.1.0. Start with `linkedin-agent doctor`; it checks most of the items below. For detail, put `-v` before any command (`linkedin-agent -v publish 4`).

**Observed vs anticipated.** Rows marked *observed* were reproduced while building and testing the agent. All other rows are anticipated from LinkedIn's and Anthropic's documented behavior.

## Error table

| Symptom or message | Likely cause | Diagnostic check | Resolution | Confirm recovery |
|---|---|---|---|---|
| `ANTHROPIC_API_KEY is not set` *(observed)* | Key missing from `.env`, or the command runs from another folder | `linkedin-agent doctor` shows `[--] Claude API key set` | Add the key to `.env` in the folder you run from, or pass `--env-file /path/.env` | `doctor` shows `[OK]` |
| `Claude rejected the API key` | Wrong, revoked or mistyped key | Check the key exists in console.anthropic.com | Create a new key and update `.env` | `linkedin-agent generate` produces a draft |
| `Claude API rate limit reached` | Too many requests, or a low usage tier | Anthropic console, Usage page | Wait a minute; generate fewer drafts at once | Command succeeds on retry |
| `Could not reach the Claude API` | No internet, proxy or firewall | `curl -I https://api.anthropic.com` | Fix the network/proxy (`HTTPS_PROXY`) | `generate` works |
| `Claude declined to write this post` | A safety classifier refused the topic or notes | Read your `--topic` / `--notes` | Rephrase the topic or notes | New draft is created |
| `Could not reach Hacker News ... continuing without trends` *(observed)* | Hacker News blocked or offline | `curl -I https://hacker-news.firebaseio.com/v0/topstories.json` | Nothing required; Claude writes an evergreen topic. Or use `--topic`, or `USE_WEB_SEARCH=true` | Warning disappears when reachable |
| `CLAUDE_EFFORT must be one of ...` | Typo in `.env` | Look at the value | Use `low`, `medium`, `high`, `xhigh` or `max` | Any command runs |
| `LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET not set` | LinkedIn app not created or not copied into `.env` | `doctor` | Follow Startup Guide step 4 | `login` opens the browser |
| Browser shows LinkedIn error `redirect_uri does not match` | Redirect URL not registered, or a typo/extra slash | Compare the app's Auth tab with `LINKEDIN_REDIRECT_URI` | Register exactly `http://localhost:8765/callback` | Consent page appears |
| `OSError: [Errno 98] Address already in use` during `login` | Port 8765 busy | `lsof -i :8765` (macOS/Linux) or `netstat -ano | findstr 8765` (Windows) | Close that program, or change the port in both `.env` and the LinkedIn app | `login` completes |
| `No response from LinkedIn within 300 seconds` | Consent not completed, or browser on another machine | Did the browser show "LinkedIn login complete"? | Run `login` again, or `login --manual` on remote machines | Token saved message |
| `Login response did not match this session (state mismatch)` | An old or reused redirect address was pasted | - | Run `login` again and use the newest address only | Token saved message |
| `Token exchange failed (400): ... code expired` | The code is single-use and short-lived | - | Run `login` again and approve promptly | Token saved |
| `Could not read your LinkedIn profile (403)` | "Sign In with LinkedIn using OpenID Connect" product missing | App **Products** tab | Add the product, then `login` again | `doctor` shows your name |
| Warning `LinkedIn did not grant w_member_social` | "Share on LinkedIn" product missing | App **Products** tab | Add the product, then `login` again | No warning at login |
| `Not logged in to LinkedIn` *(observed)* | No token file yet, or a different `DATA_DIR` | `doctor`; check `DATA_DIR` | `linkedin-agent login` | `doctor` shows `[OK] LinkedIn login` |
| `Your LinkedIn login has expired` | Tokens last about 60 days | `doctor` shows days left | `linkedin-agent login` | `doctor` shows about 59 days |
| `LinkedIn API error 401` | Token revoked (password change, app removed) | - | `linkedin-agent login` | `publish` works |
| `LinkedIn API error 403 ... w_member_social` | App lacks posting permission | Products tab | Add "Share on LinkedIn", log in again | `publish` works |
| `LinkedIn API error 426` or a version error | `LINKEDIN_API_VERSION` has been retired | LinkedIn API docs list current versions | Set a recent `YYYYMM`, e.g. the current month minus one | `publish` works |
| `LinkedIn API error 400 ... FIELD_LENGTH_TOO_LONG` | Post text too long | `show <id>` shows the character count | `unapprove`, `edit` or `revise ... --feedback "shorter"`, approve again | `publish` works |
| `LinkedIn API error 429` | LinkedIn daily limit | - | Wait until the next day (UTC). The agent already retried | Later `retry <id>` succeeds |
| `... The post may have been created; check your LinkedIn profile before retrying.` | Server error or timeout while creating the post | Look at your LinkedIn activity | If the post exists, `discard <id>`. If not, `retry <id>` | No duplicate on your profile |
| Draft stuck in `publishing` | The program stopped mid-publish (crash, power loss) | `list -s publishing` | Check LinkedIn. If not posted, `retry <id> --force`. If it was posted, run `retry <id> --force` then immediately `discard <id>` (stop the scheduler first so it is not posted again) | Status changes |
| `Video is still processing on LinkedIn` | Large video still being processed | Wait 10 minutes | `retry <id>` | `published` |
| `LinkedIn could not process the video: ...` | Unsupported codec or a corrupt file | `ffprobe video.mp4` | Re-export as H.264 MP4 | New media draft publishes |
| `... does not look like a real MP4 file` / `does not match its extension` | File renamed rather than converted | `file name.ext` | Export or convert to the real format | `media` succeeds |
| Warning `ffmpeg not installed; Claude cannot see the video` | ffmpeg missing | `ffmpeg -version` | Install ffmpeg (Startup Guide step 2), or rely on detailed notes | Warning gone |
| `Error: Draft N is 'X'; expected one of (...)` | Action not allowed in this status | `show N` | Follow the status table in the User Guide | Action succeeds |
| `database is locked` | Another process holds a long write | Other agent processes running? | Stop duplicates; retry. Locks wait up to 30 s | Command succeeds |
| Post text looks cut off on LinkedIn | Text sent without escaping (not expected with this agent) | `publish` in dry-run shows the escaped text | Report with the dry-run output | - |

## Logs and diagnostics

- The agent logs to the terminal. With the scheduler under cron or Task Scheduler, redirect output to `data/scheduler.log` (see the Startup Guide).
- Use `-v` for debug logs and full tracebacks.
- Logs never contain your access token or client secret. Before sharing diagnostics, still check for personal content in post text.
- Safe diagnostic bundle: the output of `linkedin-agent doctor`, `linkedin-agent list`, and `linkedin-agent -v <failing command>`. Never share `.env` or `data/linkedin_token.json`.

## Recovering data safely

- The queue is `data/agent.db`. Stop the scheduler and copy it before any manual fix.
- Do not delete `agent.db-wal` while the agent runs; it holds recent writes.
- To reset only the LinkedIn login, delete `data/linkedin_token.json` and run `login`. Drafts are unaffected.
