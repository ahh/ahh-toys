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
