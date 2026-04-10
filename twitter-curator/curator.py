#!/usr/bin/env python3
"""
Twitter Curator Bot — main entry point.
Called by GitHub Actions every 3 hours.

Phase 1: Send any tweets that are due from the existing queue.
Phase 2: If it's been >= 22h since last fetch, fetch + score + queue new tweets.
"""

import os
import sys
from datetime import datetime, timezone

from scheduler import compute_schedule, get_due_tweets, is_fetch_due
from scorer import score_batch, select_top_tweets
from state_manager import get_sent_ids, load_state, prune_old_queue_entries, save_state
from telegram_client import send_due_tweets, send_message
from twitter_client import fetch_if_due


REQUIRED_ENV = [
    "TWITTER_COOKIES",
    "TWITTER_USERNAME",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def check_env() -> dict:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return {k: os.environ[k] for k in REQUIRED_ENV}


def main():
    env = check_env()
    state = load_state()
    state = prune_old_queue_entries(state)
    now = datetime.now(timezone.utc)

    # --- Phase 1: Send due tweets ---
    due = get_due_tweets(state["queue"], now)
    if due:
        print(f"Sending {len(due)} due tweet(s)...")
        state = send_due_tweets(
            due,
            env["TELEGRAM_BOT_TOKEN"],
            env["TELEGRAM_CHAT_ID"],
            state,
        )
        save_state(state)
    else:
        print("No tweets due to send right now.")

    # --- Phase 2: Fetch + score if it's time ---
    if not is_fetch_due(state, now):
        print("Fetch not due yet. Done.")
        return

    print("Fetching home timeline...")
    try:
        unseen = fetch_if_due(state, env["TWITTER_COOKIES"], env["TWITTER_USERNAME"])
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        # Don't update last_fetch_at so we retry next tick
        sys.exit(1)

    print(f"Fetched {len(unseen)} unseen tweets. Scoring...")
    scored = score_batch(unseen, env["ANTHROPIC_API_KEY"])
    top = select_top_tweets(scored)

    if not top:
        print("No tweets passed scoring today.")
        send_message(
            env["TELEGRAM_BOT_TOKEN"],
            env["TELEGRAM_CHAT_ID"],
            f"📭 No tweets worth your attention today. Checked {len(unseen)} tweets.",
        )
    else:
        print(f"Selected {len(top)} tweets. Scheduling...")
        schedule = compute_schedule(len(top), now)
        for i, (tweet, scores) in enumerate(top):
            state["queue"].append({
                **tweet,
                "scores": scores,
                "scheduled_at": schedule[i].isoformat(),
                "sent_at": None,
                "status": "pending",
            })
        print(f"Queued {len(top)} tweets. First delivery at {schedule[0].isoformat()}.")

    state["last_fetch_at"] = now.isoformat()
    save_state(state)
    print("Done.")


if __name__ == "__main__":
    main()
