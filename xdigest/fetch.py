"""Scroll the For You timeline in a dedicated Chrome profile and extract posts.

This is deliberately dumb code: it navigates to x.com/home, scrolls, and reads
the DOM. It never clicks anything except the "For you" tab, and no model ever
sees page content while this browser is open.
"""

import random
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

import store

EXTRACT_JS = (Path(__file__).parent / "extract.js").read_text()

LOGGED_IN_SELECTOR = '[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="AppTabBar_Profile_Link"]'
LOGGED_OUT_SELECTOR = '[data-testid="loginButton"], [data-testid="login"], a[href="/login"]'


class LoggedOut(Exception):
    pass


class Challenged(Exception):
    pass


def _open(p, headless: bool) -> BrowserContext:
    store.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(store.PROFILE_DIR),
        channel="chrome",  # the installed Google Chrome, with its own profile dir
        chromium_sandbox=True,  # Playwright defaults to --no-sandbox; this browser renders strangers' posts
        # Use the real macOS Keychain so cookies saved by `login` (plain Chrome) decrypt here.
        ignore_default_args=["--use-mock-keychain"],
        headless=headless,
        viewport={"width": 1100, "height": 1000},
    )


def _check_state(page: Page) -> None:
    url = page.url
    if "/account/access" in url or page.locator('iframe[src*="arkose"], iframe[src*="captcha"]').count():
        raise Challenged(url)
    if "/login" in url or "/i/flow/" in url or page.locator(LOGGED_OUT_SELECTOR).count():
        raise LoggedOut(url)


def login() -> None:
    """Open plain (non-automated) Chrome on the dedicated profile so a human can sign
    in normally; the daily fetch then reuses the saved session."""
    store.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("Log in to X in the Chrome window that opens (X password, not Google),\n"
          "then quit that Chrome window (Cmd-Q) to continue.")
    subprocess.run(["open", "-n", "-W", "-a", "Google Chrome", "--args",
                    f"--user-data-dir={store.PROFILE_DIR}", "--no-first-run", "https://x.com/login"],
                   check=True)
    print("logged in" if is_logged_in() else "NOT logged in: try again later")


def is_logged_in() -> bool:
    with sync_playwright() as p:
        ctx = _open(p, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            page.wait_for_selector(f'{LOGGED_IN_SELECTOR}, {LOGGED_OUT_SELECTOR}', timeout=30_000)
            return page.locator(LOGGED_IN_SELECTOR).count() > 0
        except Exception:
            return False
        finally:
            ctx.close()


def _select_for_you(page: Page) -> None:
    tab = page.get_by_role("tab", name="For you")
    if tab.count() and tab.first.get_attribute("aria-selected") != "true":
        tab.first.click()
        time.sleep(2)


def fetch(max_posts: int = 300, stop_after_seen: int = 20, headless: bool = False) -> tuple[list[dict], dict]:
    """Return (new posts, stats). Posts already in seen.json are skipped."""
    seen = store.load_seen()
    posts: dict[str, dict] = {}
    stats = {"ads": 0, "already_seen": 0, "no_id": 0, "scrolls": 0}
    consecutive_seen = 0
    stalled = 0

    with sync_playwright() as p:
        ctx = _open(p, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # The timeline's own API responses carry video file URLs, which the DOM
        # (blob: players) does not. Keep them to read after scrolling.
        timeline_responses = []
        page.on("response", lambda r: timeline_responses.append(r)
                if "/graphql/" in r.url and "Timeline" in r.url else None)
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            try:
                page.wait_for_selector(f'{LOGGED_IN_SELECTOR}, {LOGGED_OUT_SELECTOR}', timeout=30_000)
            except Exception:
                pass
            _check_state(page)
            _select_for_you(page)
            page.wait_for_selector('article[data-testid="tweet"]', timeout=30_000)

            counted: set[str] = set()
            while len(posts) < max_posts and consecutive_seen < stop_after_seen and stalled < 8:
                found_new = False
                for post in page.evaluate(EXTRACT_JS):
                    key = post["id"] or (post["author"]["handle"] + post["text"][:40])
                    if key in counted:
                        continue
                    counted.add(key)
                    found_new = True
                    if post["is_ad"]:
                        stats["ads"] += 1
                    elif not post["id"]:
                        stats["no_id"] += 1
                    elif post["id"] in seen:
                        stats["already_seen"] += 1
                        consecutive_seen += 1
                    else:
                        consecutive_seen = 0
                        posts[post["id"]] = post
                stalled = 0 if found_new else stalled + 1

                page.mouse.wheel(0, random.randint(600, 1000))
                stats["scrolls"] += 1
                time.sleep(random.uniform(1.2, 2.8))
                _check_state(page)
            _attach_videos(posts, timeline_responses)
        finally:
            ctx.close()

    stats["stop_reason"] = (
        "max_posts" if len(posts) >= max_posts
        else "seen_streak" if consecutive_seen >= stop_after_seen
        else "stalled"
    )
    return list(posts.values())[:max_posts], stats


def _pick_mp4(video_info: dict | None) -> str | None:
    """Highest-bitrate mp4 under ~1.1 Mbps (Telegram fetches URLs up to 20MB)."""
    mp4s = sorted((v for v in (video_info or {}).get("variants", [])
                   if v.get("content_type") == "video/mp4"), key=lambda v: v.get("bitrate", 0))
    if not mp4s:
        return None
    small = [v for v in mp4s if v.get("bitrate", 0) <= 1_100_000]
    return (small[-1] if small else mp4s[0])["url"]


def _attach_videos(posts: dict[str, dict], responses: list) -> None:
    """Set post["videos"] and post["quote"]["videos"] from captured timeline JSON."""
    videos: dict[str, list[str]] = {}
    quoted: dict[str, str] = {}

    def walk(o):
        if isinstance(o, dict):
            rid, legacy = o.get("rest_id"), o.get("legacy")
            if rid and isinstance(legacy, dict):
                for m in (legacy.get("extended_entities") or {}).get("media", []):
                    url = _pick_mp4(m.get("video_info"))
                    if url and url not in videos.setdefault(rid, []):
                        videos[rid].append(url)
                q = (o.get("quoted_status_result") or {}).get("result") or {}
                q = q.get("tweet", q)  # TweetWithVisibilityResults wrapper
                if q.get("rest_id"):
                    quoted[rid] = q["rest_id"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for r in responses:
        try:
            walk(r.json())
        except Exception:
            continue
    for pid, post in posts.items():
        post["videos"] = videos.get(pid, [])
        if post.get("quote"):
            qid = quoted.get(pid)
            post["quote"]["id"] = qid
            post["quote"]["videos"] = videos.get(qid, []) if qid else []


def download_images(posts: list[dict], max_per_post: int = 4) -> int:
    """Save each post's images (own + quoted) under IMAGES_DIR; record filenames
    on the post as `image_files`. Plain HTTP, no browser session involved."""
    store.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for post in posts:
        urls = post.get("images", []) + ((post.get("quote") or {}).get("images") or [])
        post["image_files"] = []
        for n, url in enumerate(urls[:max_per_post]):
            name = f"{post['id']}-{n}.jpg"
            path = store.IMAGES_DIR / name
            if not path.exists():
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        path.write_bytes(resp.read())
                    saved += 1
                except OSError:
                    continue
                time.sleep(random.uniform(0.1, 0.3))
            post["image_files"].append(name)
    return saved


def mark_seen(posts: list[dict]) -> None:
    seen = store.load_seen()
    ts = store.now().isoformat()
    for post in posts:
        seen.setdefault(post["id"], ts)
    store.save_seen(seen)
