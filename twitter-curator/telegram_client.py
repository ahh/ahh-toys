import asyncio
import sys
from datetime import datetime, timezone

from telegram import Bot
from telegram.error import TelegramError

from state_manager import save_state

CATEGORY_EMOJI = {
    "insight": "💡",
    "fact": "🔬",
    "meme": "😂",
    "visual": "🖼️",
}


def format_tweet_message(tweet: dict, scores: dict) -> str:
    category = scores.get("category", "insight")
    emoji = CATEGORY_EMOJI.get(category, "💡")

    score_parts = []
    for dim in ("insight", "fact", "meme"):
        val = scores.get(dim, 0)
        if val >= 5:
            score_parts.append(f"{dim}: {val}/10")
    score_line = " · ".join(score_parts) if score_parts else f"{category}: {scores.get('top_score', 0)}/10"

    return (
        f"{emoji} {tweet['text']}\n\n"
        f"— @{tweet['author_username']} ({tweet['author_name']})\n\n"
        f"{score_line}\n"
        f"{tweet['url']}"
    )


def send_message(bot_token: str, chat_id: str, text: str) -> bool:
    async def _send():
        bot = Bot(token=bot_token)
        await bot.send_message(chat_id=chat_id, text=text)

    try:
        asyncio.run(_send())
        return True
    except TelegramError as e:
        print(f"Telegram send failed: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Unexpected error sending Telegram message: {e}", file=sys.stderr)
        return False


def format_reddit_message(post: dict, scores: dict) -> str:
    category = scores.get("category", "insight")
    emoji = CATEGORY_EMOJI.get(category, "💡")

    score_parts = []
    for dim in ("insight", "fact", "meme", "visual"):
        val = scores.get(dim, 0)
        if val >= 5:
            score_parts.append(f"{dim}: {val}/10")
    score_line = " · ".join(score_parts) if score_parts else f"{category}: {scores.get('top_score', 0)}/10"

    lines = [f"{emoji} r/{post['subreddit']}", f"{post['title']}"]

    if post.get("text"):
        lines.append(f"\n{post['text'][:300]}")

    if post.get("external_url"):
        lines.append(f"\n{post['external_url']}")

    comments = post.get("comments", [])
    if comments:
        lines.append("")
        for c in comments:
            lines.append(f'💬 u/{c["author"]}: "{c["text"][:250]}"')

    lines.append(f"\n{score_line}")
    lines.append(post["url"])

    return "\n".join(lines)


async def _send_reddit_post_async(bot_token: str, chat_id: str, post: dict, scores: dict) -> bool:
    bot = Bot(token=bot_token)
    image_url = post.get("image_url")
    text = format_reddit_message(post, scores)

    if image_url:
        # Send image with the text as caption (max 1024 chars for captions)
        caption = text[:1024]
        try:
            await bot.send_photo(chat_id=chat_id, photo=image_url, caption=caption)
            return True
        except TelegramError:
            # Fall back to text-only if image fails (e.g. format not supported)
            await bot.send_message(chat_id=chat_id, text=text[:4096])
            return True
    else:
        await bot.send_message(chat_id=chat_id, text=text[:4096])
        return True


def send_reddit_post(bot_token: str, chat_id: str, post: dict, scores: dict) -> bool:
    try:
        asyncio.run(_send_reddit_post_async(bot_token, chat_id, post, scores))
        return True
    except TelegramError as e:
        print(f"Telegram send failed: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Unexpected error sending Reddit post: {e}", file=sys.stderr)
        return False


def send_due_reddit_posts(due: list, bot_token: str, chat_id: str, state: dict) -> dict:
    now = datetime.now(timezone.utc)
    for entry in due:
        success = send_reddit_post(bot_token, chat_id, entry, entry.get("scores", {}))
        if success:
            entry["status"] = "sent"
            entry["sent_at"] = now.isoformat()
            save_state(state)
    return state


def send_due_tweets(due: list, bot_token: str, chat_id: str, state: dict) -> dict:
    now = datetime.now(timezone.utc)
    for entry in due:
        text = format_tweet_message(entry, entry.get("scores", {}))
        success = send_message(bot_token, chat_id, text)
        if success:
            entry["status"] = "sent"
            entry["sent_at"] = now.isoformat()
            save_state(state)
    return state
