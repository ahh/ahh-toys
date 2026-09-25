"""Send to one hard-coded chat via the Telegram Bot API."""

import html
import json
import os
import time
import urllib.request

import xdata


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


def fallback_link(url: str) -> str:
    # fxtwitter (fixupx.com, open source) previews include quotes and video, but come
    # with its own summary/footer clutter; used only if a rich message fails.
    return url.replace("https://x.com/", "https://fixupx.com/", 1)


def _media(post: dict) -> list[dict]:
    """Photos and videos for a post and its quote. Video posters are skipped when the
    video itself is available. Photo URLs are bumped to X's large size."""
    items = []
    for part in (post, post.get("quote") or {}):
        videos = part.get("videos") or []
        for url in part.get("images") or []:
            if videos and "video_thumb" in url:
                continue
            items.append({"type": "photo", "media": url.replace("name=small", "name=large")})
        items += [{"type": "video", "media": url} for url in videos]
    return items[:10]


def _caption(post: dict) -> str:
    esc = html.escape
    a = post["author"]
    parts = [f"<i>🔍 {esc(post['note'])}</i>"] if post.get("note") else []
    parts.append(f"<b>{esc(a['name'])}</b> @{esc(a['handle'])}")
    if post.get("text"):
        parts.append(esc(post["text"]))
    q = post.get("quote")
    if q:
        qa = q["author"]
        parts.append(f"<blockquote><b>{esc(qa['name'])}</b> @{esc(qa['handle'])}"
                     + (f"\n{esc(q['text'])}" if q.get("text") else "") + "</blockquote>")
    for n, part in enumerate(post.get("thread") or [], 2):
        parts.append(esc(xdata.numbered(n, part.get("text"))))
    parts.append(f'<a href="{esc(post["url"])}">original →</a>')
    return "\n\n".join(parts)


def send_post(post: dict) -> None:
    """One post as a single Telegram message: text, quote, and media inline."""
    chat = os.environ["TELEGRAM_CHAT_ID"]
    caption, media = _caption(post), _media(post)
    try:
        if len(caption) > 4000:  # message limit is 4096
            caption = caption[:3990].rsplit("\n", 1)[0]
        if media and len(caption) > 1024:  # caption limit; send text separately
            _api("sendMessage", {"chat_id": chat, "text": caption, "parse_mode": "HTML",
                                 "link_preview_options": {"is_disabled": True}})
            caption = None
        if not media:
            _api("sendMessage", {"chat_id": chat, "text": caption, "parse_mode": "HTML",
                                 "link_preview_options": {"is_disabled": True}})
        elif len(media) == 1:
            kind = media[0]["type"]
            _api("sendPhoto" if kind == "photo" else "sendVideo",
                 {"chat_id": chat, kind: media[0]["media"],
                  **({"caption": caption, "parse_mode": "HTML"} if caption else {})})
        else:
            if caption:
                media[0] = {**media[0], "caption": caption, "parse_mode": "HTML"}
            _api("sendMediaGroup", {"chat_id": chat, "media": media})
    except Exception:
        # e.g. Telegram couldn't fetch a media URL: fall back to a link preview.
        send(fallback_link(post["url"]), preview=True)


def send_digest(posts: list[dict]) -> None:
    for post in posts:
        send_post(post)
        time.sleep(3)  # a media group counts as several messages against rate limits


def recent_chat_ids() -> list[dict]:
    """Chats that have messaged the bot recently, to find TELEGRAM_CHAT_ID."""
    chats = {}
    for update in _api("getUpdates"):
        chat = (update.get("message") or {}).get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    return [{"chat_id": cid, "name": name} for cid, name in chats.items()]
