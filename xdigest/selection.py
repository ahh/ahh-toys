"""Work out the score bar for picks from recent history.

Instead of a fixed number per batch, every post at or above a bar is sent, and the bar
is set so that, over the last few days of scores, it would have produced about
PICKS_PER_DAY picks a day. Good batches then send more and quiet ones fewer.

Scores are mostly whole numbers with many ties, so the bar is a score plus a fraction:
everything above `score` is picked, and posts exactly at `score` are picked with
probability `fraction` (decided by a hash of the post id, so it's stable).
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import store

WINDOW_DAYS = 3
MIN_HISTORY_DAYS = 0.5  # below this, fall back to pick.py's per-batch rule
RECENT_RUNS = 3         # volume is estimated from this many latest runs
FLOOR = 5               # never pick below this, however quiet the window


def picks_per_day() -> float:
    return float(os.environ.get("XDIGEST_PICKS_PER_DAY", "25"))


def _run_time(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _runs_per_day() -> int:
    """How many times a day the schedule runs (from the installed launchd job), else 5."""
    try:
        import plistlib
        plist = plistlib.loads((Path.home() / "Library/LaunchAgents/com.xdigest.daily.plist").read_bytes())
        times = plist.get("StartCalendarInterval")
        return len(times) if isinstance(times, list) else 1
    except Exception:
        return 5


def threshold(now: datetime | None = None) -> dict | None:
    """{"score", "fraction", ...}, or None when there isn't enough history yet.

    The score distribution comes from the last WINDOW_DAYS of scored posts; the volume
    (eligible posts per day) from the most recent runs, so a change in batch size (e.g.
    the Following tab getting busier) moves the bar right away instead of over days."""
    if not store.SCORE_LOG.exists():
        return None
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=WINDOW_DAYS)
    scores, oldest = [], None
    per_run: dict[str, int] = {}
    for r in store.read_jsonl(store.SCORE_LOG):
        t = _run_time(r.get("run"))
        if not t or t < start:
            continue
        oldest = min(oldest or t, t)
        s = r.get("scoring") or {}
        if not s.get("rejected") and not s.get("error"):
            scores.append(float(s.get("score", 0)))
            per_run[r["run"]] = per_run.get(r["run"], 0) + 1
    if oldest is None or (now - oldest).total_seconds() / 86400 < MIN_HISTORY_DAYS or not scores:
        return None

    recent = [per_run[k] for k in sorted(per_run)[-RECENT_RUNS:]]
    eligible_per_day = sum(recent) / len(recent) * _runs_per_day()
    share = min(1.0, picks_per_day() / eligible_per_day)  # fraction of eligible posts to pick
    want = share * len(scores)

    # Walk down from the top: the bar is the score where the cumulative count crosses `want`.
    above = 0
    for s in sorted(set(scores), reverse=True):
        at = sum(1 for x in scores if x == s)
        if above + at >= want:
            bar = {"score": s, "fraction": round((want - above) / at, 3)}
            break
        above += at
    else:
        bar = {"score": FLOOR, "fraction": 1.0}
    if bar["score"] < FLOOR:
        bar = {"score": FLOOR, "fraction": 1.0}
    share_at_bar = (sum(1 for x in scores if x > bar["score"])
                    + bar["fraction"] * sum(1 for x in scores if x == bar["score"])) / len(scores)
    return {**bar, "window_posts": len(scores), "eligible_per_day": round(eligible_per_day),
            "expected_per_day": round(share_at_bar * eligible_per_day, 1)}
