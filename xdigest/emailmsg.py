"""Email each post to one fixed address via Resend (https://resend.com).

Env: RESEND_API_KEY, EMAIL_TO; optional EMAIL_FROM (default Resend's shared test
sender, which on the free plan can only deliver to your own account's address) and
EMAIL_SPREAD_HOURS (default 12: space the day's posts out over that many hours using
Resend's scheduled sending; 0 sends them all at once).
"""

import base64
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import render

API = "https://api.resend.com/emails"
DEFAULT_FROM = "X Digest <onboarding@resend.dev>"

FONT = "-apple-system,BlinkMacSystemFont,'Helvetica Neue',Helvetica,Arial,sans-serif"


def _post(payload: dict) -> dict:
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), headers={
        "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
        "Content-Type": "application/json",
        "User-Agent": "xdigest",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _email(subject: str, body_html: str, text: str, attachments: list[dict] = (),
           send_at: datetime | None = None) -> None:
    payload = {
        "from": os.environ.get("EMAIL_FROM") or DEFAULT_FROM,
        "to": [os.environ["EMAIL_TO"]],
        "subject": subject,
        "html": body_html,
        "text": text,
    }
    if attachments:
        payload["attachments"] = list(attachments)
    if send_at:
        payload["scheduled_at"] = send_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    _post(payload)


def send(text: str, preview: bool = False) -> None:
    """A status message (logged out, failures, ...) as a plain email."""
    first = text.splitlines()[0]
    _email(first[:120], f'<div style="font:16px/1.4 {FONT}">{html.escape(text)}</div>', text)


def _linkify(text: str) -> str:
    """Escape text and link URLs, @handles and #tags to X."""
    out, pos = [], 0
    for m in re.finditer(r"https?://\S+|(?<![\w/])@\w+|(?<![\w/&])#\w+", text):
        out.append(html.escape(text[pos:m.start()]))
        tok = m.group()
        href = (tok if tok.startswith("http") else
                f"https://x.com/{tok[1:]}" if tok.startswith("@") else
                f"https://x.com/hashtag/{tok[1:]}")
        out.append(f'<a href="{html.escape(href)}" style="color:#1d9bf0;text-decoration:none">{html.escape(tok)}</a>')
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out).replace("\n", "<br>")


def _who(author: dict, small: bool = False) -> str:
    size = 20 if small else 40
    avatar = author.get("avatar")
    img = (f'<img src="{html.escape(avatar.replace("_normal.", "_x96."))}" width="{size}" height="{size}" '
           f'style="border-radius:50%;display:block" alt="">' if avatar else "")
    name = html.escape(author["name"])
    handle = f'<a href="https://x.com/{html.escape(author["handle"])}" style="color:#536471;text-decoration:none">' \
             f'@{html.escape(author["handle"])}</a>'
    names = (f'<b>{name}</b> {handle}' if small else f'<b>{name}</b><br>{handle}')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin-bottom:8px"><tr>'
            f'<td style="padding-right:10px;vertical-align:middle">{img}</td>'
            f'<td style="vertical-align:middle;font:{14 if small else 15}px/1.3 {FONT};color:#0f1419">{names}</td>'
            f'</tr></table>')


def _media(part: dict, post_url: str, tmp: Path, attachments: list[dict]) -> str:
    """Photos hotlinked from X; each video as an inline muted GIF linking to the post."""
    videos = list(part.get("videos") or [])
    blocks = []
    for url in render._images(part)[:4]:
        is_video = "video_thumb" in url
        width = None
        if is_video and videos:
            cid = f"vid{len(attachments)}"
            try:
                gif = render.video_gif(videos.pop(0), tmp / f"{cid}.gif")
                attachments.append({"filename": f"{cid}.gif", "content_id": cid,
                                    "content": base64.b64encode(gif.read_bytes()).decode()})
                src = f"cid:{cid}"
                width = _gif_width(gif)
            except Exception as e:
                print(f"gif failed for {post_url}: {e}", file=sys.stderr)
                src = url.replace("name=small", "name=large")
        else:
            src = url.replace("name=small", "name=large")
        # GIFs show at their own (<=480px) size so vertical videos aren't enormous.
        size = (f'width="{width}" style="display:block;width:{width}px;max-width:100%;border-radius:12px"'
                if width else 'width="100%" style="display:block;width:100%;max-width:560px;border-radius:12px"')
        img = f'<img src="{html.escape(src)}" alt="" {size}>'
        if is_video:
            img = (f'<a href="{html.escape(post_url)}">{img}</a>'
                   f'<div style="font:13px {FONT};margin-top:4px"><a href="{html.escape(post_url)}" '
                   f'style="color:#536471;text-decoration:none">▶ watch with sound on X</a></div>')
        blocks.append(img)
    if len(blocks) <= 1:
        return "".join(f'<div style="margin-top:10px">{b}</div>' for b in blocks)
    # Several images: a two-column grid, so tall screenshots don't make a mile-long email.
    rows = [blocks[i:i + 2] for i in range(0, len(blocks), 2)]
    cells = "".join(
        "<tr>" + "".join(f'<td width="50%" style="padding:2px;vertical-align:top">{b}</td>' for b in row)
        + ("<td></td>" if len(row) == 1 else "") + "</tr>" for row in rows)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin-top:8px;table-layout:fixed">{cells}</table>')


def _gif_width(path: Path) -> int | None:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None


def _body(post: dict, tmp: Path, attachments: list[dict]) -> str:
    url = post["url"]
    parts = [_who(post["author"])]
    if post.get("text"):
        parts.append(f'<div style="font:17px/1.45 {FONT};color:#0f1419">{_linkify(post["text"])}</div>')
    parts.append(_media(post, url, tmp, attachments))
    q = post.get("quote")
    if q:
        qparts = [_who(q["author"], small=True)]
        if q.get("text"):
            qparts.append(f'<div style="font:15px/1.4 {FONT};color:#0f1419">{_linkify(q["text"])}</div>')
        qparts.append(_media(q, url, tmp, attachments))
        parts.append(f'<div style="margin-top:12px;border:1px solid #cfd9de;border-radius:14px;'
                     f'padding:12px 14px">{"".join(qparts)}</div>')
    parts.append(f'<div style="margin-top:14px;font:14px {FONT}"><a href="{html.escape(url)}" '
                 f'style="color:#1d9bf0;text-decoration:none">original →</a></div>')
    return (f'<div style="max-width:560px;margin:0 auto;padding:4px 0">{"".join(parts)}</div>')


def _subject(post: dict) -> str:
    text = " ".join((post.get("text") or (post.get("quote") or {}).get("text") or "").split())
    snippet = text[:80] + ("…" if len(text) > 80 else "")
    return f'{post["author"]["name"]}: {snippet}' if snippet else post["author"]["name"]


def _plain(post: dict) -> str:
    lines = [f'{post["author"]["name"]} @{post["author"]["handle"]}', "", post.get("text") or ""]
    q = post.get("quote")
    if q:
        lines += ["", f'> {q["author"]["name"]} @{q["author"]["handle"]}', "> " + (q.get("text") or "")]
    return "\n".join(lines + ["", post["url"]])


def send_post(post: dict, send_at: datetime | None = None) -> None:
    if not post.get("author", {}).get("name"):
        _email("X digest pick", f'<a href="{html.escape(post["url"])}">{html.escape(post["url"])}</a>',
               post["url"], send_at=send_at)
        return
    with tempfile.TemporaryDirectory() as tmp:
        attachments: list[dict] = []
        body = _body(post, Path(tmp), attachments)
        _email(_subject(post), body, _plain(post), attachments, send_at)


def send_digest(posts: list[dict]) -> None:
    spread = float(os.environ.get("EMAIL_SPREAD_HOURS", "12"))
    start = datetime.now(timezone.utc)
    step = timedelta(hours=spread) / max(len(posts), 1)
    for i, post in enumerate(posts):
        # The first goes out now; the rest are scheduled by Resend, so the Mac can sleep.
        send_at = start + step * i + timedelta(minutes=1) if spread and i else None
        send_post(post, send_at)
        time.sleep(0.2)  # Resend allows 10 requests/second
