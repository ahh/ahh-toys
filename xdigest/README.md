# xdigest

A daily digest of the best ~10% of your X (Twitter) "For You" feed, delivered by
email, Telegram, or Signal. Your Mac reads the feed, a Claude routine scores every post
against a rubric you write (no politics, no ragebait, more jokes/tech/delight, or
whatever you like), and each pick arrives as its own message:

- **Email:** one email per post, spread through the day: text, quoted post, photos,
  and videos as muted GIFs linking to X. Read state syncs across your devices.
- **Telegram:** full posts with images, quoted posts, and playable video.
- **Signal:** rendered post cards (animated GIFs when there's video).

**Setting up with Claude Code:** clone the repo, run `cd ahh-toys/xdigest && claude`,
and say "set this up". Claude follows [CLAUDE.md](CLAUDE.md), does the setup work, and
asks you for the few things only you can do (GitHub login, an API key or bot token,
signing in to X). `uv run xdigest.py check` shows what's done at any point.

**Heads up:** X's terms of service forbid automated scraping. This reads your own feed
at human speed once a day, but X could still challenge or lock your account. Use it at
your own risk.

## How it works

```
your Mac (daily, launchd)             github.com/<you>/xdigest-data (private)     Claude routine (cloud, hourly)
fetch: scroll For You in a     ──▶   inbox  (one commit: today's posts,    ──▶   score every post with
  dedicated Chrome profile             images, rubric, helper script)             subagents against SCORING.md;
                                                                                  pick the top 10%
deliver: email/Telegram/Signal ◀──   claude/outbox-<run>  (scores, picks)  ◀──   push outbox
  + archive scores locally,
  delete the branch
```

**Security design.** The feed is written by strangers, so nothing that reads it can do
anything else:

- The fetcher is plain Playwright code driving Chrome with a dedicated profile that is
  logged in to X and nothing else. No model sees the page; it never likes, posts, or
  follows. Logging in happens in plain Chrome, by you.
- The routine has no X login, no messaging credentials, no claude.ai connectors (you
  remove them, see below), and no internet beyond git to the data repo. The worst a
  malicious post can do is lie about its own score.
- Messages are sent from your Mac, to one fixed recipient.

**Why a GitHub repo in the middle:** routines run in Anthropic's cloud and can't reach
your Mac, X, or messaging services, but they can use git. The data repo is a mailbox,
not an archive: the inbox is replaced daily and outbox branches are deleted after
delivery, so history never grows. The long-term archive (every post, image, and score,
useful for later training your own ranker) stays in `~/.local/share/xdigest/`.

## Requirements

- A Mac that's usually on in the morning (if it's asleep, the job runs when it wakes)
- Google Chrome, [Homebrew](https://brew.sh), and `brew install uv gh ffmpeg`
  (`ffmpeg` is for video GIFs in email and Signal)
- `gh auth login` done (git access to your data repo goes through it)
- A Claude plan with Claude Code routines (scoring runs on your plan's usage)
- An X account with a password (if you sign in with Google/Apple, set one via
  "Forgot password" on the X login page)

## Setup

Run `uv run xdigest.py check` at any point to see which of these steps are done.

### 1. Code and data repo

```
git clone https://github.com/ahh/ahh-toys && cd ahh-toys/xdigest && uv sync
gh repo create xdigest-data --private --add-readme
```

### 2. Config

Create `~/.config/xdigest/env` and `chmod 600` it:

```
XDIGEST_DATA_REPO=<your-github-user>/xdigest-data
DIGEST_TRANSPORT=email          # or telegram, or signal
```

Then set up the one you chose:

### 3a. Email (via Resend)

[Resend](https://resend.com) sends the emails; the free plan (100/day, 3,000/month) is
plenty.

1. Sign up at resend.com **with the address you want the digest at**. Without your own
   domain, Resend's shared sender can only deliver to your account's own address, which
   is exactly what we want: the API key can't email anyone but you.
2. Create an API key (API Keys → Create, permission "Sending access").
3. Add to the env file:

   ```
   RESEND_API_KEY=re_...
   EMAIL_TO=you@example.com         # the address you signed up with
   EMAIL_SPREAD_HOURS=12            # optional: spread the day's picks over N hours (0 = all at once)
   ```

The first few may land in spam or Promotions: mark them "not spam" and add a filter
for `from:onboarding@resend.dev` (skip inbox and label them, if you like). Picks are
handed to Resend with scheduled send times, so your Mac can sleep afterwards.

### 3b. Telegram

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. It gives
   you a token.
2. Add `TELEGRAM_BOT_TOKEN=<token>` to the env file.
3. Send your new bot any message, then run `uv run xdigest.py chat-id` and add
   `TELEGRAM_CHAT_ID=<the number>` to the env file.

Each pick arrives as one message: author, text, the quoted post as a block quote, and
images/video inline, with a link to the original.

### 3c. Signal

Signal has no bot API; this uses [signal-cli](https://github.com/AsamK/signal-cli)
(`brew install signal-cli`). Each pick arrives as a rendered image of the post (a GIF
when it has video, muted) plus a short link.

**Quick start (your own account, Note to Self).** Link signal-cli as a device on your
account:

```
signal-cli link -n "xdigest" | head -1 | qrencode -t ansiutf8   # brew install qrencode
```

Scan it in Signal → Settings → Linked devices → Link new device, then add
`SIGNAL_ACCOUNT=<your number, e.g. +15551234567>` to the env file. Digests land in Note
to Self, but note: (1) messages from your own linked device usually don't notify, and
(2) that linked device can read and send as you. Unlink it in Signal when you're done
with it.

**Better: a separate number for the bot**, so digests arrive as normal notifying
messages and nothing on your Mac can act as you. With a spare number (prepaid SIM):

```
# solve the captcha at https://signalcaptchas.org/registration/generate.html,
# copy the "signalcaptcha://..." link it gives you
signal-cli -a +BOTNUMBER register --captcha 'signalcaptcha://...'
signal-cli -a +BOTNUMBER verify <code from SMS>
signal-cli -a +BOTNUMBER setPin <pin>          # stops takeover if the number lapses
signal-cli -a +BOTNUMBER updateProfile --given-name "X Digest"
```

Then `SIGNAL_ACCOUNT=+BOTNUMBER` and `SIGNAL_TO=+YOURNUMBER` in the env file, and accept
the bot's message request on your phone.

### 4. Log in to X

```
uv run xdigest.py login
```

Plain Chrome opens on a profile dedicated to this. Sign in with your X password, then
quit that window (Cmd-Q). It should print `logged in`. macOS may ask whether Chrome can
use "Chrome Safe Storage" in the Keychain the first time the fetcher runs: choose
**Always Allow**.

### 5. Create the scoring routine

Follow [ROUTINE_PROMPT.md](ROUTINE_PROMPT.md). Don't skip removing the connectors.

### 6. Try it, then schedule it

```
uv run xdigest.py fetch --max-posts 40 --headless   # read 40 posts
uv run xdigest.py push                              # hand them to the routine
# run the routine now from claude.ai/code/routines, wait for it to finish, then:
uv run xdigest.py deliver                           # picks arrive by email/Telegram/Signal
uv run xdigest.py install-schedule --at 07:30       # daily from now on
```

The daily job (`run --headless`) does all of that and waits up to 4 hours for the
routine. Logs: `~/.local/share/xdigest/launchd.log`. If X logs you out or asks for a
human check, you'll get a message saying so; run `login` again.

## Tuning

- **Taste:** edit `routine/SCORING.md`. It's plain English; the routine reads it fresh
  every day.
- **How many:** `FRACTION` (default top 10%) and `MIN_SCORE` (default 6/10) in
  `routine/pick.py`; `--max-posts` (default 300) for how much of the feed to read.
- **Look:** `emailmsg.py` (email), `telegram.py`, `render.py` (Signal cards).

## Files

In this folder: `fetch.py` + `extract.js` (reading the feed), `mailbox.py` (the data
repo), `routine/` (what the routine runs), `render.py` (cards), `telegram.py`,
`signalmsg.py`, `emailmsg.py`, `notify.py` (delivery), `xdigest.py` (commands).

Outside it:

- `~/.config/xdigest/env`: settings and secrets
- `~/.local/share/xdigest/browser-profile/`: the X login
- `~/.local/share/xdigest/runs/<run>/`: each day's posts, scores, and picks
- `~/.local/share/xdigest/images/`: every downloaded image
- `~/.local/share/xdigest/scored.jsonl`: every scored post, ever
