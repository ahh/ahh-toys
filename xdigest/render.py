"""Render a post (with its quoted post) as a PNG card, from saved data only.

Uses a throwaway headless browser with no profile: no X session, no requests to X
beyond fetching the post's public images.
"""

import base64
import html
import re
import subprocess
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

CSS = """
* { box-sizing: border-box; margin: 0; }
body { background: transparent; font: 16px/1.4 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
       color: #0f1419; }
.card { width: 560px; background: #fff; border-radius: 18px; padding: 16px 18px; }
.who { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; flex: none; display: grid; place-items: center;
          color: #fff; font-weight: 700; font-size: 17px; }
.name { font-weight: 700; } .handle { color: #536471; }
.who .lines { display: flex; flex-direction: column; line-height: 1.25; }
.text { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 17px; }
.text a { color: #1d9bf0; text-decoration: none; }
.media { display: grid; gap: 2px; margin-top: 12px; border-radius: 14px; overflow: hidden; }
.media.n1 { grid-template-columns: 1fr; } .media.n2, .media.n3, .media.n4 { grid-template-columns: 1fr 1fr; }
.media img { width: 100%; height: 100%; object-fit: cover; display: block; }
.media.n1 img { max-height: 440px; object-fit: contain; background: #f2f2f2; }
.media.n2 .m, .media.n4 .m { aspect-ratio: 1 / 1; } .media.n3 .m:first-child { grid-row: span 2; }
.m { position: relative; min-height: 120px; }
.play { position: absolute; inset: 0; display: grid; place-items: center; }
.play span { width: 56px; height: 56px; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff;
             display: grid; place-items: center; font-size: 24px; padding-left: 4px; }
.quote { margin-top: 12px; border: 1px solid #cfd9de; border-radius: 14px; padding: 12px 14px; }
.quote .who { margin-bottom: 4px; } .quote .avatar { width: 22px; height: 22px; font-size: 11px; }
.quote .who .lines { flex-direction: row; gap: 6px; }
.quote .text { font-size: 15px; }
.quote .media { margin: 10px -14px -12px; border-radius: 0 0 13px 13px; }
.quote .media.n1 img { max-height: 360px; }
.quote .media .m { min-height: 90px; }
"""

PALETTE = ["#1d9bf0", "#f91880", "#00ba7c", "#7856ff", "#ff7a00", "#e0245e"]


def _data_uri(url: str) -> str:
    big = url.replace("name=small", "name=large")
    try:
        with urllib.request.urlopen(urllib.request.Request(big, headers={"User-Agent": "Mozilla/5.0"}),
                                    timeout=30) as resp:
            data, ctype = resp.read(), resp.headers.get("Content-Type", "image/jpeg")
    except OSError:
        return ""
    return f"data:{ctype};base64,{base64.b64encode(data).decode()}"


def _linkify(text: str) -> str:
    esc = html.escape(text, quote=False)  # element content: quotes need no escaping
    return re.sub(r"(https?://\S+|(?<![\w/&])[@#]\w+)", r"<a>\1</a>", esc)


def _avatar(author: dict) -> str:
    color = PALETTE[sum(map(ord, author["handle"])) % len(PALETTE)]
    initial = html.escape((author["name"] or author["handle"] or "?")[0].upper())
    return f'<div class="avatar" style="background:{color}">{initial}</div>'


def _who(author: dict) -> str:
    return (f'<div class="who">{_avatar(author)}<div class="lines">'
            f'<span class="name">{html.escape(author["name"])}</span>'
            f'<span class="handle">@{html.escape(author["handle"])}</span></div></div>')


def _images(part: dict) -> list[str]:
    """Image URLs, de-duplicated (a video thumbnail can be recorded twice)."""
    seen, out = set(), []
    for url in part.get("images") or []:
        if url.split("?")[0] not in seen:
            seen.add(url.split("?")[0])
            out.append(url)
    return out


def _media(part: dict, slots: list[dict]) -> str:
    """Media grid. Video thumbnails get a data-video slot index (their video URL is
    appended to `slots` with how to fit it) so the animator can overlay the real video there."""
    videos = list(part.get("videos") or [])
    items = []
    for url in _images(part)[:4]:
        src = _data_uri(url)
        if not src:
            continue
        attr, play = "", ""
        if "video_thumb" in url:
            if videos:
                attr = f' data-video="{len(slots)}"'
                slots.append({"url": videos.pop(0), "fit": "cover"})
            else:
                play = '<div class="play"><span>▶</span></div>'
        items.append(f'<div class="m"{attr}><img src="{src}">{play}</div>')
    if not items:
        return ""
    if len(items) == 1 and attr:
        slots[-1]["fit"] = "contain"  # a lone video is shown whole (e.g. vertical phone video)
    return f'<div class="media n{len(items)}">{"".join(items)}</div>'


def card_html(post: dict) -> tuple[str, list[dict]]:
    """Card HTML plus {url, fit} for each data-video slot, in slot order."""
    slots: list[dict] = []
    body = [_who(post["author"])]
    if post.get("text"):
        body.append(f'<div class="text">{_linkify(post["text"])}</div>')
    body.append(_media(post, slots))
    q = post.get("quote")
    if q:
        qparts = [_who(q["author"])]
        if q.get("text"):
            qparts.append(f'<div class="text">{_linkify(q["text"])}</div>')
        qparts.append(_media(q, slots))
        body.append(f'<div class="quote">{"".join(qparts)}</div>')
    page = f'<html><head><style>{CSS}</style></head><body><div class="card">{"".join(body)}</div></body></html>'
    return page, slots


SCALE = 2           # render resolution multiplier
GIF_WIDTH = 600     # output width of animated cards
GIF_FPS = 10
GIF_SECONDS = 10


def render_card(post: dict, out_dir: Path) -> Path:
    """Write the card for `post` into out_dir: card.png, or card.gif (with the post's
    videos playing muted in place) when it has video. Returns the path."""
    page_html, slots = card_html(post)
    png = out_dir / "card.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", chromium_sandbox=True, headless=True)
        try:
            page = browser.new_page(device_scale_factor=SCALE, viewport={"width": 600, "height": 800})
            page.set_content(page_html, wait_until="load")
            card = page.locator(".card")
            card.screenshot(path=str(png), omit_background=True)
            origin = card.bounding_box()
            boxes = []
            for i in range(len(slots)):
                b = page.locator(f'[data-video="{i}"]').bounding_box()
                boxes.append({k: int(round(v * SCALE)) // 2 * 2 for k, v in {
                    "x": b["x"] - origin["x"], "y": b["y"] - origin["y"],
                    "w": b["width"], "h": b["height"]}.items()})
        finally:
            browser.close()
    if not slots:
        return png

    inputs = ["-loop", "1", "-i", str(png)]
    for n, slot in enumerate(slots):
        path = out_dir / f"v{n}.mp4"
        with urllib.request.urlopen(urllib.request.Request(slot["url"], headers={"User-Agent": "Mozilla/5.0"}),
                                    timeout=120) as resp:
            path.write_bytes(resp.read())
        inputs += ["-i", str(path)]

    # Each video: fit into its box (whole with black bars, or cropped to fill in a
    # grid, as X does), then overlay; the card image loops underneath.
    chain, last = [], "[0:v]"
    for n, (b, slot) in enumerate(zip(boxes, slots)):
        w, h = b["w"], b["h"]
        fit = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
               if slot["fit"] == "contain" else
               f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}")
        chain.append(f"[{n + 1}:v]fps={GIF_FPS},{fit},setsar=1[v{n}]")
        chain.append(f"{last}[v{n}]overlay={b['x']}:{b['y']}:shortest={1 if n == 0 else 0}[o{n}]")
        last = f"[o{n}]"
    chain.append(f"{last}fps={GIF_FPS},scale={GIF_WIDTH}:-2:flags=lanczos,split[a][b]")
    chain.append("[a]palettegen=stats_mode=diff[pal]")
    chain.append("[b][pal]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
    gif = out_dir / "card.gif"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chain),
                    "-t", str(GIF_SECONDS), "-loop", "0", str(gif)], check=True)
    return gif
