import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

STATE_PATH = Path(__file__).parent.parent / "state.json"

DEFAULT_STATE = {
    "schema_version": 1,
    "last_fetch_at": "1970-01-01T00:00:00Z",
    "queue": [],
    "sent_history": [],
}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return dict(DEFAULT_STATE)
    with open(STATE_PATH) as f:
        state = json.load(f)
    if state.get("schema_version") != 1:
        raise RuntimeError(
            f"state.json has schema_version={state.get('schema_version')}, expected 1. "
            "Manual migration required."
        )
    return state


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def get_sent_ids(state: dict) -> set:
    ids = {entry["tweet_id"] for entry in state.get("sent_history", [])}
    ids |= {
        entry["tweet_id"]
        for entry in state.get("queue", [])
        if entry.get("status") == "sent"
    }
    return ids


def prune_old_queue_entries(state: dict) -> dict:
    now = datetime.now(timezone.utc)
    cutoff_queue = now - timedelta(hours=48)
    cutoff_history = now - timedelta(days=30)

    new_queue = []
    for entry in state.get("queue", []):
        if entry.get("status") == "sent" and entry.get("sent_at"):
            sent_at = datetime.fromisoformat(entry["sent_at"].replace("Z", "+00:00"))
            if sent_at < cutoff_queue:
                state.setdefault("sent_history", []).append(
                    {"tweet_id": entry["tweet_id"], "sent_at": entry["sent_at"]}
                )
                continue
        new_queue.append(entry)
    state["queue"] = new_queue

    state["sent_history"] = [
        h for h in state.get("sent_history", [])
        if datetime.fromisoformat(
            h["sent_at"].replace("Z", "+00:00")
        ) > cutoff_history
    ]

    return state
