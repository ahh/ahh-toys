from datetime import datetime, timezone, timedelta


def compute_schedule(n_tweets: int, fetch_time: datetime) -> list:
    """
    Spread n_tweets send times over a 15-hour window starting 3h after fetch_time.
    All datetimes are UTC-aware.
    """
    start = fetch_time + timedelta(hours=3)
    if n_tweets == 1:
        return [start]
    window = timedelta(hours=15)
    interval = window / (n_tweets - 1)
    return [start + interval * i for i in range(n_tweets)]


def get_due_tweets(queue: list, now: datetime) -> list:
    """Return pending queue entries whose scheduled_at is <= now, oldest first."""
    due = []
    for entry in queue:
        if entry.get("status") != "pending":
            continue
        scheduled_at = datetime.fromisoformat(
            entry["scheduled_at"].replace("Z", "+00:00")
        )
        if scheduled_at <= now:
            due.append(entry)
    due.sort(key=lambda e: e["scheduled_at"])
    return due


def is_fetch_due(state: dict, now: datetime) -> bool:
    """Return True if it's been >= 22 hours since the last fetch."""
    last = datetime.fromisoformat(
        state.get("last_fetch_at", "1970-01-01T00:00:00Z").replace("Z", "+00:00")
    )
    return (now - last) >= timedelta(hours=22)
