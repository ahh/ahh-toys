import sys

import praw

from state_manager import get_sent_ids

POSTS_PER_SUB = 25
TOP_COMMENTS = 2
MIN_COMMENT_SCORE = 5
MIN_COMMENT_LEN = 40


def build_reddit_client(client_id: str, client_secret: str, username: str, password: str) -> praw.Reddit:
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        username=username,
        password=password,
        user_agent=f"python:ahh-toys-curator:v1.0 (by u/{username})",
    )


def _detect_media(post) -> tuple[str | None, bool]:
    """Returns (image_url, is_video)."""
    url = post.url or ""
    if url.startswith("https://v.redd.it") or getattr(post, "is_video", False):
        return None, True
    if url.startswith("https://i.redd.it") or any(
        url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")
    ):
        return url, False
    # Gallery posts
    if hasattr(post, "is_gallery") and post.is_gallery:
        try:
            first_id = next(iter(post.gallery_data["items"]))["media_id"]
            return f"https://i.redd.it/{first_id}.jpg", False
        except Exception:
            pass
    return None, False


def _post_to_dict(post) -> dict:
    image_url, is_video = _detect_media(post)

    if post.is_self:
        body = post.selftext[:600].strip() if post.selftext else ""
        external_url = None
    else:
        body = ""
        external_url = post.url if not post.url.startswith("https://www.reddit.com") and not image_url else None

    return {
        "post_id": post.id,
        "subreddit": post.subreddit.display_name,
        "title": post.title,
        "text": body,
        "image_url": image_url,
        "is_video": is_video,
        "external_url": external_url,
        "url": f"https://reddit.com{post.permalink}",
        "author": post.author.name if post.author else "[deleted]",
        "reddit_score": post.score,
        "is_nsfw": post.over_18,
        "comments": [],  # filled in later for selected posts
    }


def fetch_top_comments(reddit: praw.Reddit, post_id: str) -> list[dict]:
    """Fetch top comments for a post. Called only for posts that made the cut."""
    try:
        submission = reddit.submission(id=post_id)
        submission.comments.replace_more(limit=0)
        top = sorted(
            (c for c in submission.comments if
             not c.stickied
             and c.score >= MIN_COMMENT_SCORE
             and len(c.body) >= MIN_COMMENT_LEN
             and c.body not in ("[deleted]", "[removed]")),
            key=lambda c: c.score,
            reverse=True,
        )[:TOP_COMMENTS]
        return [
            {
                "author": c.author.name if c.author else "[deleted]",
                "text": c.body[:350].strip(),
                "score": c.score,
            }
            for c in top
        ]
    except Exception as e:
        print(f"Error fetching comments for {post_id}: {e}", file=sys.stderr)
        return []


def fetch_top_posts(reddit: praw.Reddit, subreddits: list[str]) -> list[dict]:
    posts = []
    for sub_name in subreddits:
        try:
            sub = reddit.subreddit(sub_name)
            for post in sub.top(time_filter="day", limit=POSTS_PER_SUB):
                if post.stickied or not post.author:
                    continue
                posts.append(_post_to_dict(post))
        except Exception as e:
            print(f"Error fetching r/{sub_name}: {e}", file=sys.stderr)
    return posts


def enrich_with_comments(reddit: praw.Reddit, posts_with_scores: list[tuple]) -> list[tuple]:
    """After scoring/selection, fetch comments for the winning posts."""
    enriched = []
    for post, scores in posts_with_scores:
        post["comments"] = fetch_top_comments(reddit, post["post_id"])
        enriched.append((post, scores))
    return enriched


def fetch_if_due(state: dict, reddit: praw.Reddit, subreddits: list[str]) -> list[dict]:
    raw = fetch_top_posts(reddit, subreddits)
    seen = get_sent_ids(state)
    return [p for p in raw if p["post_id"] not in seen]
