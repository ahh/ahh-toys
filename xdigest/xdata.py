"""Parse X's own API responses (the GraphQL JSON the web app loads while you browse)
into plain per-post records. The fetcher captures these responses; nothing here
makes requests.

Field names follow X's web API as observed; everything is read defensively because
X changes it without notice.
"""

import html
import re


def _pick_mp4(video_info: dict | None) -> str | None:
    """Highest-bitrate mp4 under ~1.1 Mbps (small enough for Telegram/GIF making)."""
    mp4s = sorted((v for v in (video_info or {}).get("variants", [])
                   if v.get("content_type") == "video/mp4"), key=lambda v: v.get("bitrate", 0))
    if not mp4s:
        return None
    small = [v for v in mp4s if v.get("bitrate", 0) <= 1_100_000]
    return (small[-1] if small else mp4s[0])["url"]


def _small(url: str) -> str:
    """pbs.twimg.com/media/ABC.jpg -> pbs.twimg.com/media/ABC?format=jpg&name=small"""
    stem, _, ext = url.rpartition(".")
    return f"{stem}?format={ext}&name=small" if stem and "?" not in url else url


def _text(result: dict, legacy: dict) -> str:
    """Full visible text: the long-post body if there is one, else legacy.full_text
    trimmed to its display range, with t.co links expanded and media links dropped."""
    note = (((result.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}).get("text")
    if note:
        return html.unescape(note)
    text = legacy.get("full_text") or ""
    rng = legacy.get("display_text_range")
    if isinstance(rng, list) and len(rng) == 2:
        text = text[rng[0]:rng[1]]
    ents = legacy.get("entities") or {}
    for u in ents.get("urls") or []:
        if u.get("url"):
            text = text.replace(u["url"], u.get("expanded_url") or u["url"])
    for m in (legacy.get("extended_entities") or ents).get("media") or []:
        if m.get("url"):
            text = text.replace(m["url"], "")
    return html.unescape(text).strip()


def _user(result: dict) -> dict:
    u = ((result.get("core") or {}).get("user_results") or {}).get("result") or {}
    core, leg = u.get("core") or {}, u.get("legacy") or {}
    return {
        "name": core.get("name") or leg.get("name") or "",
        "handle": core.get("screen_name") or leg.get("screen_name") or "",
        "avatar": ((u.get("avatar") or {}).get("image_url") or leg.get("profile_image_url_https")),
    }


def _record(result: dict) -> dict | None:
    result = result.get("tweet", result)  # TweetWithVisibilityResults wrapper
    rid, legacy = result.get("rest_id"), result.get("legacy")
    if not rid or not isinstance(legacy, dict):
        return None
    images, videos = [], []
    for m in (legacy.get("extended_entities") or {}).get("media") or []:
        if m.get("media_url_https"):
            images.append(_small(m["media_url_https"]))  # for videos, this is the poster
        url = _pick_mp4(m.get("video_info"))
        if url:
            videos.append(url)
    quoted = ((result.get("quoted_status_result") or {}).get("result") or {})
    quoted = quoted.get("tweet", quoted)
    user = _user(result)
    return {
        "id": rid,
        "user_id": legacy.get("user_id_str"),
        "author": user,
        "url": f"https://x.com/{user['handle'] or 'i'}/status/{rid}",
        "text": _text(result, legacy),
        "is_long": bool((result.get("note_tweet") or {}).get("note_tweet_results")),
        "images": images,
        "videos": videos,
        "reply_to_id": legacy.get("in_reply_to_status_id_str"),
        "reply_to_user_id": legacy.get("in_reply_to_user_id_str"),
        "conversation_id": legacy.get("conversation_id_str"),
        "self_thread_id": (legacy.get("self_thread") or {}).get("id_str"),
        "quoted_id": quoted.get("rest_id"),
    }


def collect(obj, into: dict[str, dict]) -> None:
    """Walk one API response and add a record for every post in it (keyed by id)."""
    if isinstance(obj, dict):
        if "rest_id" in obj and "legacy" in obj:
            rec = _record(obj)
            if rec and rec["id"] not in into:
                into[rec["id"]] = rec
        for v in obj.values():
            collect(v, into)
    elif isinstance(obj, list):
        for v in obj:
            collect(v, into)


# Fallback thread markers: starts with "1/", ends with a bare "1/" or "(1/5)", or 🧵.
# (Not a bare "1/10" at the end: that's usually a rating.)
THREAD_MARKER = re.compile(r"^\s*1/|(^|\s)1/\s*$|\(1/\d+\)\s*$|🧵")


def thread_root(rec: dict | None, dom_text: str = "") -> str | None:
    """The id of the self-thread this post belongs to, or None. Uses X's own
    self-thread / self-reply fields, falling back to a "1/" or 🧵 marker."""
    if not rec:
        return None
    if rec.get("reply_to_user_id") and rec["reply_to_user_id"] == rec.get("user_id"):
        return rec.get("conversation_id") or rec.get("reply_to_id")
    if rec.get("self_thread_id"):
        return rec["self_thread_id"]
    if not rec.get("reply_to_id") and THREAD_MARKER.search(dom_text or rec.get("text") or ""):
        return rec["id"]
    return None


def numbered(n: int, text: str) -> str:
    """"n/ text", unless the author already numbered it."""
    return text if re.match(r"\s*\(?\d+\s*/", text or "") else f"{n}/ {text or ''}"


def thread_chain(records: dict[str, dict], root_id: str, limit: int = 25) -> list[dict]:
    """The author's own chain of replies starting at root_id, in order."""
    root = records.get(root_id)
    if not root:
        return []
    chain, cur = [root], root
    while len(chain) < limit:
        nxt = sorted((r for r in records.values()
                      if r.get("reply_to_id") == cur["id"] and r.get("user_id") == root.get("user_id")),
                     key=lambda r: int(r["id"]))
        if not nxt:
            break
        cur = nxt[0]
        chain.append(cur)
    return chain
