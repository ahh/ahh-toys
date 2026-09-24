# xdigest

Once a day: scroll my X "For You" feed, have a Claude routine score every post, and
send the top ~10% to Telegram. Replaces `../twitter-curator`, whose twscrape fetch
from GitHub Actions stopped working.

## Design

```
Mac (launchd, 07:30)                 github.com/ahh/xdigest-data (private)      Claude routine (cloud, hourly 8-12 ET)
fetch.py: scroll For You  ──push──▶  inbox  (one orphan commit: today's   ──▶  score batches with subagents
  + download images                          posts, images, SCORING.md,         pick.py: merge, top 10%
                                             pick.py)
deliver: Telegram + archive  ◀─pull──  claude/outbox-<run>  (scores, picks) ◀──  push outbox
         then delete branch
```

Nothing that reads posts can do anything else:

- **fetch** is plain Playwright code driving the installed Chrome with a dedicated
  profile logged in to X and nothing else. No model sees the page. It never likes,
  posts, or follows.
- **The routine** has no X login, no Telegram token, no claude.ai connectors (cleared,
  since routines inherit them by default), and no internet beyond git to the data
  repo. The worst a hostile post can do is inflate its own score.
- **Telegram** sends happen on the Mac, to the one chat ID in the env file.

The repo is a mailbox, not an archive: `inbox` is force-pushed as a single orphan
commit and outbox branches are deleted after delivery, so history never grows. The
long-term archive (every post, image, and score) lives in `~/.local/share/xdigest/`.

Tune taste by editing `routine/SCORING.md` (rubric) or `routine/pick.py` (batch
size, top fraction, minimum score). Both ship with each day's inbox, so the routine
itself never needs updating.

## Setup

```
cd xdigest && uv sync
uv run xdigest.py login     # sign in with the X password (not Google) in the window that opens
```

`~/.config/xdigest/env` (chmod 600):

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...        # `uv run xdigest.py chat-id` after messaging the bot
```

Git access to the data repo uses the `gh` CLI's login.

Step by step: `fetch --max-posts 40` (local only), `push`, wait for the routine (or
run it now from claude.ai/code/routines), then `deliver`. `run` does all of it and
waits up to `--wait-hours` (default 4) for the routine.

Schedule: see `com.ahh.xdigest.plist`. The routine: "xdigest scorer" at
https://claude.ai/code/routines (cron `0 12-16 * * *` UTC = 8am-noon EDT; it exits
immediately when the inbox is already scored).

## Files (outside the repo)

- `~/.local/share/xdigest/browser-profile/`: the X login
- `~/.local/share/xdigest/seen.json`: post ids already processed (30 days)
- `~/.local/share/xdigest/images/`: every downloaded image
- `~/.local/share/xdigest/runs/<run>/`: posts, scored, and picks for each run
- `~/.local/share/xdigest/scored.jsonl`: every scored post, ever
