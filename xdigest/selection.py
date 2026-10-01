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

import store

WINDOW_DAYS = 3
MIN_HISTORY_DAYS = 0.5  # below this, fall back to pick.py's per-batch rule
FLOOR = 5               # never pick below this, however quiet the window


def picks_per_day() -> float:
    return float(os.environ.get("XDIGEST_PICKS_PER_DAY", "25"))


def _run_time(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def threshold(now: datetime | None = None) -> dict | None:
    """{"score", "fraction", "window_days", "window_posts", "expected_per_day"}, or None
    when there isn't enough history yet."""
    if not store.SCORE_LOG.exists():
        return None
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=WINDOW_DAYS)
    scores, oldest = [], None
    for r in store.read_jsonl(store.SCORE_LOG):
        t = _run_time(r.get("run"))
        if not t or t < start:
            continue
        oldest = min(oldest or t, t)
        s = r.get("scoring") or {}
        if not s.get("rejected") and not s.get("error"):
            scores.append(float(s.get("score", 0)))
    if oldest is None:
        return None
    days = (now - oldest).total_seconds() / 86400
    if days < MIN_HISTORY_DAYS:
        return None
    want = picks_per_day() * days  # picks the window should have produced

    # Walk down from the top: the bar is the score where the cumulative count crosses `want`.
    distinct = sorted(set(scores), reverse=True)
    above = 0
    for s in distinct:
        at = sum(1 for x in scores if x == s)
        if above + at >= want:
            frac = (want - above) / at
            bar = {"score": s, "fraction": round(frac, 3)}
            break
        above += at
    else:
        bar = {"score": distinct[-1] if distinct else FLOOR, "fraction": 1.0}
    if bar["score"] < FLOOR:
        bar = {"score": FLOOR, "fraction": 1.0}
    expected = (sum(1 for x in scores if x > bar["score"])
                + bar["fraction"] * sum(1 for x in scores if x == bar["score"])) / days
    return {**bar, "window_days": round(days, 2), "window_posts": len(scores),
            "expected_per_day": round(expected, 1)}
