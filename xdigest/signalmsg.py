"""Send via signal-cli to one fixed recipient: Note to Self, or SIGNAL_TO if set.

(Named signalmsg, not signal, to avoid shadowing the stdlib module.)
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import render
import xdata


def _utf16_len(s: str) -> int:
    # Signal text-style offsets are in UTF-16 code units.
    return len(s.encode("utf-16-le")) // 2


def _send(text: str, attachments: list[str] = (), styles: list[str] = ()) -> None:
    to = os.environ.get("SIGNAL_TO")
    cmd = ["signal-cli", "-a", os.environ["SIGNAL_ACCOUNT"], "send",
           *([to] if to else ["--note-to-self"]), "-m", text]
    if attachments:
        cmd += ["-a", *attachments]
    if styles:
        cmd += ["--text-style", *styles]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)


def send(text: str, preview: bool = False) -> None:
    _send(text)


class _Text:
    """Accumulates message text plus Signal text-style ranges."""

    def __init__(self):
        self.text, self.styles = "", []

    def add(self, s: str, style: str | None = None) -> None:
        if style and s:
            self.styles.append(f"{_utf16_len(self.text)}:{_utf16_len(s)}:{style}")
        self.text += s


def _body(post: dict) -> _Text:
    t = _Text()
    if post.get("note"):
        t.add(f"🔍 {post['note']}", "ITALIC")
        t.add("\n\n")
    a = post["author"]
    t.add(a["name"], "BOLD")
    t.add(f" @{a['handle']}")
    if post.get("text"):
        t.add("\n\n" + post["text"])
    q = post.get("quote")
    if q:
        t.add("\n\n")
        t.add(f"↳ {q['author']['name']}", "BOLD")
        t.add(f" @{q['author']['handle']}")
        if q.get("text"):
            t.add("\n")
            t.add(q["text"], "ITALIC")
    for n, part in enumerate(post.get("thread") or [], 2):
        t.add("\n\n" + xdata.numbered(n, part.get("text")))
    t.add(f"\n\noriginal → {post['url']}")
    return t


def _media_urls(post: dict) -> list[str]:
    """The post's own media first, then the quoted post's. Video posters are skipped
    when the video itself is available; photos use X's large size."""
    urls = []
    for part in (post, post.get("quote") or {}):
        videos = part.get("videos") or []
        urls += [u.replace("name=small", "name=large") for u in part.get("images") or []
                 if not (videos and "video_thumb" in u)]
        urls += videos
    return urls


def _download(urls: list[str], into: Path) -> list[str]:
    paths = []
    for n, url in enumerate(urls):
        ext = ".mp4" if ".mp4" in url else ".png" if "format=png" in url else ".jpg"
        path = into / f"{n}{ext}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                path.write_bytes(resp.read())
            paths.append(str(path))
        except OSError:
            continue
    return paths


def _short_link(post: dict) -> str:
    return "x.com/i/status/" + post["url"].rstrip("/").rsplit("/", 1)[-1]


def _send_as_text(post: dict) -> None:
    """Fallback: styled text with the media attached."""
    body = _body(post)
    with tempfile.TemporaryDirectory() as tmp:
        files = _download(_media_urls(post), Path(tmp))
        try:
            _send(body.text, files, body.styles)
        except subprocess.CalledProcessError:
            _send(body.text)  # e.g. an attachment Signal rejected: text + link only


def send_post(post: dict) -> None:
    """One post as one Signal message: a rendered card (GIF if it has video) and a
    short link to the original."""
    if not post.get("text") and not post.get("images") and not post.get("quote"):
        _send(post["url"])  # no local copy of the post, just its link
        return
    with tempfile.TemporaryDirectory() as tmp:
        try:
            card = render.render_card(post, Path(tmp))
            text = (f"🔍 {post['note']}\n" if post.get("note") else "") + _short_link(post)
            _send(text, [str(card)])
            return
        except Exception as e:
            print(f"card failed for {post['url']}: {type(e).__name__}: {e}; sending as text", file=sys.stderr)
    _send_as_text(post)


def _receive() -> None:
    """signal-cli must receive regularly or Signal eventually drops the device.
    Incoming messages are discarded, not read or stored."""
    subprocess.run(["signal-cli", "-a", os.environ["SIGNAL_ACCOUNT"], "receive", "-t", "5",
                    "--ignore-attachments"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)


def send_digest(posts: list[dict]) -> None:
    _receive()
    for post in posts:
        send_post(post)
        time.sleep(1)
