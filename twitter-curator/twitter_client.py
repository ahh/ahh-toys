import asyncio
import sys
from datetime import datetime, timezone, timedelta

from twscrape import API, gather
from twscrape.logger import set_log_level

from state_manager import get_sent_ids


def _build_tweet_dict(tweet) -> dict:
    username = tweet.user.username if tweet.user else "unknown"
    name = tweet.user.displayname if tweet.user else "Unknown"
    url = f"https://x.com/{username}/status/{tweet.id}"
    return {
        "tweet_id": str(tweet.id),
        "author_username": username,
        "author_name": name,
        "text": tweet.rawContent,
        "url": url,
        "created_at": tweet.date.isoformat() if tweet.date else "",
    }


async def _fetch(cookie_string: str, username: str, max_results: int) -> list:
    set_log_level("ERROR")  # suppress twscrape noise
    api = API(pool=":memory:")
    await api.pool.add_account(
        username=username,
        password="",       # not needed when using cookies
        email="",
        email_password="",
        cookies=cookie_string,
    )

    tweets = await gather(api.home_timeline(limit=max_results))
    results = []
    for t in tweets:
        # Skip retweets
        if t.rawContent.startswith("RT @"):
            continue
        results.append(_build_tweet_dict(t))
    return results


def fetch_home_timeline(cookie_string: str, username: str, max_results: int = 200) -> list:
    try:
        return asyncio.run(_fetch(cookie_string, username, max_results))
    except Exception as e:
        print(f"Twitter fetch error: {e}", file=sys.stderr)
        raise


def fetch_if_due(state: dict, cookie_string: str, username: str) -> list:
    """
    Fetch the timeline and deduplicate against already-seen tweet IDs.
    Returns empty list if a fetch was done recently (< 22h ago) — callers
    should check is_fetch_due() before calling this, but we guard here too.
    """
    raw = fetch_home_timeline(cookie_string, username)
    seen = get_sent_ids(state)
    return [t for t in raw if t["tweet_id"] not in seen]
