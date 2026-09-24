"""Send to one hard-coded chat via the Telegram Bot API."""

import json
import os
import time
import urllib.request


def _api(method: str, payload: dict | None = None) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
    return body["result"]


def send(text: str, preview: bool = False) -> None:
    _api("sendMessage", {
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": text,
        "link_preview_options": {"is_disabled": not preview},
    })


def format_pick(pick: dict) -> str:
    # Just the link; Telegram's preview shows the post itself.
    return pick["url"]


def send_digest(picks: list[dict]) -> None:
    for post in picks:
        send(format_pick(post), preview=True)
        time.sleep(1)  # Telegram allows ~1 msg/sec per chat


def recent_chat_ids() -> list[dict]:
    """Chats that have messaged the bot recently, to find TELEGRAM_CHAT_ID."""
    chats = {}
    for update in _api("getUpdates"):
        chat = (update.get("message") or {}).get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    return [{"chat_id": cid, "name": name} for cid, name in chats.items()]
