"""Scroll the For You timeline in a dedicated Chrome profile and extract posts.

This is deliberately dumb code: it navigates to x.com/home, scrolls, and reads
the DOM. It never clicks anything except the "For you" tab, and no model ever
sees page content while this browser is open.
"""

import random
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
        headless=headless,
        viewport={"width": 1100, "height": 1000},
    )


def _check_state(page: Page) -> None:
    url = page.url
    if "/account/access" in url or page.locator('iframe[src*="arkose"], iframe[src*="captcha"]').count():
        raise Challenged(url)
    if "/login" in url or "/i/flow/" in url or page.locator(LOGGED_OUT_SELECTOR).count():
        raise LoggedOut(url)


def login(timeout_s: int = 600) -> None:
    """Open a visible window at the login page and wait for the human to sign in."""
    with sync_playwright() as p:
        ctx = _open(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://x.com/login")
        print("Log in to X in the Chrome window that just opened (use your X password, not Google).")
        page.wait_for_selector(LOGGED_IN_SELECTOR, timeout=timeout_s * 1000)
        print("Logged in. The session is saved in", store.PROFILE_DIR)
        time.sleep(2)
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
        finally:
            ctx.close()

    stats["stop_reason"] = (
        "max_posts" if len(posts) >= max_posts
        else "seen_streak" if consecutive_seen >= stop_after_seen
        else "stalled"
    )
    return list(posts.values())[:max_posts], stats


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
