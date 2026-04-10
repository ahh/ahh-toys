import json
import sys
import time

import anthropic

SYSTEM_PROMPT = """You are a ruthlessly discerning content curator. Evaluate tweets for a thoughtful person's daily digest.

HARD REJECT (rejected=true, all scores=0) anything containing:
- Politics, politicians, elections, policy, ideology, government
- Outrage, manufactured controversy, dunking, hot takes designed to provoke anger
- Advertisements, product promotion, "I built a thing" hype posts
- Personal drama, venting, complaints, interpersonal conflict
- Generic motivational or productivity advice ("hustle", "mindset", "consistency")
- Celebrity gossip, entertainment drama, pop culture takes
- Discourse about social media itself (ratio, viral moments, quote-tweet drama)
- Replies lacking standalone context (text begins with "@")
- Anything that would make a thoughtful reader roll their eyes

For tweets that survive the filter, score each dimension 1-10:

meme (1-10): Is it genuinely clever or funny in a way that rewards attention?
  1=not funny, 4=mildly amusing, 6=legitimately funny you'd show a friend,
  8=genuinely clever wit that takes a second to land, 10=exceptional

insight (1-10): Does it offer a non-obvious, well-articulated perspective on something real?
  1=obvious/banal, 4=somewhat interesting, 6=makes you think briefly,
  8=genuinely reframes how you see something, 10=paradigm-shifting

fact (1-10): Does it convey a specific, true, surprising fact about the world?
  1=not factual or well-known, 4=mildly interesting datum, 6=genuinely surprising,
  8=fact you'd repeat at dinner, 10=jaw-dropping and hard to believe

CALIBRATION: Most tweets score 3-5. Score 7+ means excellent. Score 9+ means one of the
best things you've read this month. Be harsh — if unsure between 6 and 7, score 5.
Only one dimension needs to score 7+ for the tweet to be worth sending.

Respond ONLY with valid JSON, no preamble, no trailing text:
{"rejected": false, "rejection_reason": null, "meme": 3, "insight": 7, "fact": 2,
 "reasoning": "One sentence explaining the highest score and what specifically earns that rating."}"""


def score_tweet(client: anthropic.Anthropic, tweet: dict) -> dict:
    user_msg = f"Author: @{tweet['author_username']} ({tweet['author_name']})\nTweet: {tweet['text']}"
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        result = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        print(f"Score parse error for tweet {tweet.get('tweet_id')}: {e}", file=sys.stderr)
        return {"rejected": True, "rejection_reason": "parse_error",
                "meme": 0, "insight": 0, "fact": 0, "reasoning": ""}

    top_score = max(result.get("meme", 0), result.get("insight", 0), result.get("fact", 0))
    category = max(
        ("insight", result.get("insight", 0)),
        ("fact", result.get("fact", 0)),
        ("meme", result.get("meme", 0)),
        key=lambda x: x[1],
    )[0]
    result["top_score"] = top_score
    result["category"] = category
    return result


def score_batch(tweets: list, api_key: str) -> list:
    """Score all tweets; return all non-rejected ones sorted by top_score descending."""
    if not tweets:
        return []
    client = anthropic.Anthropic(api_key=api_key)
    passing = []
    for tweet in tweets:
        scores = score_tweet(client, tweet)
        if not scores.get("rejected"):
            passing.append((tweet, scores))
        time.sleep(0.5)
    passing.sort(key=lambda x: x[1]["top_score"], reverse=True)
    return passing


REDDIT_SYSTEM_PROMPT = """You are a ruthlessly discerning content curator. Evaluate Reddit posts for a thoughtful person's daily digest.

HARD REJECT (rejected=true, all scores=0) anything that is:
- Political, partisan, about elections/politicians/policy/ideology
- Outrage bait, manufactured controversy, or designed to provoke anger
- Advertisements, self-promotion, or "I made a thing" hype
- Generic advice, life tips, motivational content
- Discourse about Reddit/social media itself
- Low-effort memes with no actual wit (just a reaction image or basic format)
- Anything that would make a thoughtful reader roll their eyes

Note: NSFW/adult content is fine and should NOT be rejected on that basis alone.
Judge on quality, not topic.

For posts that survive the filter, score each dimension 1-10:

meme (1-10): Is it genuinely funny or clever? Does the humor reward attention?
  1=not funny, 4=mildly amusing, 6=actually funny, 8=clever and memorable, 10=exceptional

insight (1-10): Is there a genuinely interesting perspective, discussion, or analysis?
  1=obvious, 4=somewhat interesting, 6=makes you think, 8=reframes something real, 10=paradigm-shifting

fact (1-10): Does it convey something surprising, true, and specific about the world?
  1=not factual/known, 4=mildly interesting, 6=genuinely surprising, 8=dinner-party worthy, 10=jaw-dropping

visual (1-10): If it's an image/video, is the visual content itself striking, beautiful, or remarkable?
  1=unremarkable, 4=decent, 6=genuinely interesting, 8=stunning or fascinating, 10=exceptional
  (Score 0 if there is no visual content)

CALIBRATION: Most posts score 3-5. Score 7+ means excellent. Be harsh.
Only one dimension needs to score 7+ for the post to be worth sending.

Respond ONLY with valid JSON, no preamble:
{"rejected": false, "rejection_reason": null, "meme": 0, "insight": 7, "fact": 3, "visual": 0,
 "reasoning": "One sentence on the highest score and what specifically earns it."}"""


def _compute_top(result: dict, dims: list[str]) -> tuple[int, str]:
    top_score = max(result.get(d, 0) for d in dims)
    category = max(dims, key=lambda d: result.get(d, 0))
    return top_score, category


def score_reddit_post(client: anthropic.Anthropic, post: dict) -> dict:
    parts = [f"Subreddit: r/{post['subreddit']}", f"Title: {post['title']}"]
    if post.get("text"):
        parts.append(f"Body: {post['text'][:400]}")
    if post.get("image_url"):
        parts.append("[Has image/visual content]")
    if post.get("external_url"):
        parts.append(f"Links to: {post['external_url']}")

    user_msg = "\n".join(parts)
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            system=REDDIT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        result = json.loads(raw)
    except Exception as e:
        print(f"Score parse error for post {post.get('post_id')}: {e}", file=sys.stderr)
        return {"rejected": True, "rejection_reason": "parse_error",
                "meme": 0, "insight": 0, "fact": 0, "visual": 0, "reasoning": ""}

    dims = ["meme", "insight", "fact", "visual"]
    result["top_score"], result["category"] = _compute_top(result, dims)
    return result


def score_reddit_batch(posts: list, api_key: str) -> list:
    """Score Reddit posts; return all non-rejected ones sorted by top_score descending."""
    if not posts:
        return []
    client = anthropic.Anthropic(api_key=api_key)
    passing = []
    for post in posts:
        scores = score_reddit_post(client, post)
        if not scores.get("rejected"):
            passing.append((post, scores))
        time.sleep(0.5)
    passing.sort(key=lambda x: x[1]["top_score"], reverse=True)
    return passing


def select_top_tweets(scored: list, n_min: int = 5, n_max: int = 7) -> list:
    """
    Pick the best tweets from the scored list.
    Prefer top_score >= 7. If fewer than n_min qualify, lower bar to >= 5.
    Returns at most n_max tweets.
    """
    excellent = [(t, s) for t, s in scored if s.get("top_score", 0) >= 7]
    if len(excellent) >= n_min:
        return excellent[:n_max]

    # Not enough excellent ones — include >= 5 to fill up to n_min
    good = [(t, s) for t, s in scored if 5 <= s.get("top_score", 0) < 7]
    combined = excellent + good
    return combined[:max(n_min, len(combined))][:n_max]
