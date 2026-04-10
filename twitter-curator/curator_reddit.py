#!/usr/bin/env python3
"""
Reddit Curator Bot — main entry point.
Called by GitHub Actions every 3 hours.

Phase 1: Send any Reddit posts that are due from the existing queue.
Phase 2: If it's been >= 22h since last fetch, fetch + score + queue new posts.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# State file is separate from the Twitter curator's state
STATE_PATH_OVERRIDE = Path(__file__).parent.parent / "state_reddit.json"

# Patch state_manager to use the reddit state file before importing anything else
import state_manager
state_manager.STATE_PATH = STATE_PATH_OVERRIDE

from reddit_client import build_reddit_client, enrich_with_comments, fetch_if_due
from scheduler import compute_schedule, get_due_tweets, is_fetch_due
from scorer import score_reddit_batch, select_top_tweets
from state_manager import get_sent_ids, load_state, prune_old_queue_entries, save_state
from telegram_client import send_due_reddit_posts, send_message

REQUIRED_ENV = [
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME",
    "REDDIT_PASSWORD",
    "REDDIT_SUBREDDITS",
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
    subreddits = [s.strip() for s in env["REDDIT_SUBREDDITS"].split(",") if s.strip()]

    state = load_state()
    state = prune_old_queue_entries(state)
    now = datetime.now(timezone.utc)

    # --- Phase 1: Send due posts ---
    due = get_due_tweets(state["queue"], now)  # same logic works for posts
    if due:
        print(f"Sending {len(due)} due Reddit post(s)...")
        state = send_due_reddit_posts(
            due,
            env["TELEGRAM_BOT_TOKEN"],
            env["TELEGRAM_CHAT_ID"],
            state,
        )
        save_state(state)
    else:
        print("No Reddit posts due to send right now.")

    # --- Phase 2: Fetch + score if it's time ---
    if not is_fetch_due(state, now):
        print("Reddit fetch not due yet. Done.")
        return

    reddit = build_reddit_client(
        env["REDDIT_CLIENT_ID"],
        env["REDDIT_CLIENT_SECRET"],
        env["REDDIT_USERNAME"],
        env["REDDIT_PASSWORD"],
    )

    print(f"Fetching top posts from: {', '.join(f'r/{s}' for s in subreddits)}...")
    try:
        unseen = fetch_if_due(state, reddit, subreddits)
    except Exception as e:
        print(f"Reddit fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Fetched {len(unseen)} unseen posts. Scoring...")
    scored = score_reddit_batch(unseen, env["ANTHROPIC_API_KEY"])
    top = select_top_tweets(scored)  # same selection logic

    if not top:
        print("No Reddit posts passed scoring today.")
        send_message(
            env["TELEGRAM_BOT_TOKEN"],
            env["TELEGRAM_CHAT_ID"],
            f"📭 Nothing great on Reddit today. Checked {len(unseen)} posts across {len(subreddits)} subs.",
        )
    else:
        print(f"Selected {len(top)} posts. Fetching their top comments...")
        top = enrich_with_comments(reddit, top)

        print("Scheduling...")
        schedule = compute_schedule(len(top), now)
        for i, (post, scores) in enumerate(top):
            state["queue"].append({
                **post,
                "scores": scores,
                "scheduled_at": schedule[i].isoformat(),
                "sent_at": None,
                "status": "pending",
            })
        print(f"Queued {len(top)} posts. First delivery at {schedule[0].isoformat()}.")

    state["last_fetch_at"] = now.isoformat()
    save_state(state)
    print("Done.")


if __name__ == "__main__":
    main()
