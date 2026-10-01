"""Scroll the For You timeline in a dedicated Chrome profile and extract posts.

This is deliberately dumb code: it navigates to x.com/home, scrolls, and reads
the DOM plus the API responses the page itself loads. The only things it ever clicks
are the "For you" tab and X's own "Retry" button when the feed stops loading; the
only other pages it opens are threads' own post pages, to read the rest of a thread.
No model ever sees page content while this browser is open.
"""

import json
import os
import random
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

import store
import xdata

EXTRACT_JS = (Path(__file__).parent / "extract.js").read_text()

LOGGED_IN_SELECTOR = '[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="AppTabBar_Profile_Link"]'
LOGGED_OUT_SELECTOR = '[data-testid="loginButton"], [data-testid="login"], a[href="/login"]'

# When the feed stops loading new posts: after STALL_SCROLLS empty scrolls, pause,
# nudge the page (and press X's Retry if it's showing), up to MAX_RECOVERIES times.
STALL_SCROLLS = 5
MAX_RECOVERIES = 3
STALL_SCREENSHOT = store.DATA_DIR / "last-stall.png"

# Pace like a reader: pause about this long (seconds, randomized) per new post read,
# on top of the pause after each scroll. Spaces out X's feed requests.
READ_PAUSE = (0.6, 1.4)

# Threads: open at most this many per run in a second tab to read the whole thread.
MAX_THREADS = 10
MAX_THREAD_POSTS = 25


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


def _keep_json(response, into: list) -> None:
    try:
        into.append(response.json())
    except Exception:
        pass


def _select_tab(page: Page, name: str) -> None:
    """Select a home timeline tab by its visible label (the Following tab's accessible
    name includes extra text) and make sure it actually became selected."""
    tab = page.get_by_role("tab").filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$"))
    try:
        tab.first.wait_for(timeout=30_000)  # the tabs render a moment after the page loads
    except Exception:
        pass
    if not tab.count():
        raise RuntimeError(f"no {name!r} tab on the home page")
    if tab.first.get_attribute("aria-selected") != "true":
        tab.first.click()
        time.sleep(random.uniform(2, 3))
        if tab.first.get_attribute("aria-selected") != "true":
            raise RuntimeError(f"clicked the {name!r} tab but it didn't become selected")
        page.mouse.wheel(0, -5000)  # a fresh tab starts at the top
        time.sleep(1)
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=30_000)
    except Exception:
        pass


def _scan(page: Page, source: str, posts: dict, counted: set, seen: dict, stats: dict,
          want_new: int, stop_after_seen: int) -> str:
    """Scroll the current tab, adding up to `want_new` unseen posts (tagged with
    `source`). Returns why it stopped: "enough", "caught_up" (a run of posts already
    seen) or "stalled"."""
    added = consecutive_seen = stalled = 0
    while added < want_new:
        if consecutive_seen >= stop_after_seen:
            return "caught_up"
        if stalled >= STALL_SCROLLS:
            if stats["recoveries"] >= MAX_RECOVERIES:
                _record_stall(page, stats)
                return "stalled"
            _recover(page, stats)
            stalled = 0
        found_new = False
        new_this_pass = 0
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
            elif post["id"] in seen or post["id"] in posts:
                stats["already_seen"] += 1
                consecutive_seen += 1
            elif added < want_new:
                consecutive_seen = 0
                posts[post["id"]] = {**post, "source": source}
                added += 1
                new_this_pass += 1
        stalled = 0 if found_new else stalled + 1
        time.sleep(sum(random.uniform(*READ_PAUSE) for _ in range(new_this_pass)))

        page.mouse.wheel(0, random.randint(600, 1000))
        stats["scrolls"] += 1
        time.sleep(random.uniform(1.2, 2.8))
        _check_state(page)
    return "enough"


def fetch(max_posts: int = 60, max_following: int = 100, min_for_you: int = 15,
          stop_after_seen: int = 10, headless: bool = False) -> tuple[list[dict], dict]:
    """Return (new posts, stats). Reads the Following tab first, until it reaches posts
    already seen (it's chronological, so that means caught up) or `max_following`; then
    For You until the batch has `max_posts` posts and at least `min_for_you` from For
    You. Posts already in seen.json are skipped."""
    seen = store.load_seen()
    posts: dict[str, dict] = {}
    stats = {"ads": 0, "already_seen": 0, "no_id": 0, "scrolls": 0, "recoveries": 0, "retry_clicks": 0}

    with sync_playwright() as p:
        ctx = _open(p, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # The timelines' own API responses carry video file URLs and other data the DOM
        # lacks. Read each body as it arrives: Chrome drops older bodies from memory, so
        # reading them after scrolling loses most of a long session.
        timeline_data = []
        stats["api_ops"] = {}
        def on_response(r):
            if "/graphql/" in r.url and "Timeline" in r.url:
                op = r.url.split("?")[0].rsplit("/", 1)[-1]
                stats["api_ops"][op] = stats["api_ops"].get(op, 0) + 1
                _keep_json(r, timeline_data)
        page.on("response", on_response)
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            try:
                page.wait_for_selector(f'{LOGGED_IN_SELECTOR}, {LOGGED_OUT_SELECTOR}', timeout=30_000)
            except Exception:
                pass
            _check_state(page)

            counted: set[str] = set()
            if max_following > 0:
                _select_tab(page, "Following")
                stats["following_stop"] = _scan(page, "following", posts, counted, seen, stats,
                                                max_following, stop_after_seen)
            stats["following"] = len(posts)
            _select_tab(page, "For you")
            want = max(min_for_you, max_posts - len(posts))
            stats["for_you_stop"] = _scan(page, "for_you", posts, counted, seen, stats, want, 20)
            stats["for_you"] = len(posts) - stats["following"]

            records: dict[str, dict] = {}
            stats["api_responses"] = len(timeline_data)
            for data in timeline_data:
                if os.environ.get("XDIGEST_DEBUG_DUMP"):  # raw samples for checking X's format
                    dump = store.DATA_DIR / "debug-timeline.json"
                    raw = json.dumps(data)
                    if not dump.exists() or len(raw) > dump.stat().st_size:
                        dump.write_text(raw)
                xdata.collect(data, records)
            # Extras: if X's data format shifts, keep the run and skip them.
            for step in (lambda: _enrich(posts, records), lambda: _expand_threads(ctx, posts, records, stats)):
                try:
                    step()
                except (LoggedOut, Challenged):
                    raise
                except Exception as e:
                    stats.setdefault("extras_errors", []).append(f"{type(e).__name__}: {e}"[:200])
        finally:
            ctx.close()

    stats.setdefault("stall", None)
    return list(posts.values()), stats


def _retry_button(page: Page):
    return page.get_by_role("button", name=re.compile(r"^\s*retry\s*$", re.I))


def _recover(page: Page, stats: dict) -> None:
    """The feed stopped loading (usually X throttling): wait, nudge, press Retry."""
    stats["recoveries"] += 1
    time.sleep(random.uniform(30, 60))
    page.mouse.wheel(0, -random.randint(1200, 2000))
    time.sleep(random.uniform(2, 4))
    retry = _retry_button(page)
    if retry.count() and retry.first.is_visible():
        retry.first.click()
        stats["retry_clicks"] += 1
        time.sleep(random.uniform(4, 8))
    page.mouse.wheel(0, random.randint(1500, 2500))
    time.sleep(random.uniform(2, 4))
    _check_state(page)


def _record_stall(page: Page, stats: dict) -> None:
    """Note what the page showed when we gave up, for diagnosing next time."""
    text = page.locator("body").inner_text()
    stats["stall"] = {
        "retry_visible": bool(_retry_button(page).count()),
        "something_went_wrong": "Something went wrong" in text,
        "rate_limit_text": bool(re.search(r"rate limit|too many requests", text, re.I)),
        "screenshot": str(STALL_SCREENSHOT),
    }
    try:
        store.DATA_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(STALL_SCREENSHOT))
    except Exception:
        stats["stall"]["screenshot"] = None


def _enrich(posts: dict[str, dict], records: dict[str, dict]) -> None:
    """Fill in what the DOM lacks from X's API data: video files, and the full text of
    long posts (the DOM shows them cut off at "Show more")."""
    about = ("you_follow", "followers", "verified", "bio")
    for pid, post in posts.items():
        rec = records.get(pid) or {}
        post["videos"] = rec.get("videos", [])
        post["author"].update({k: rec["author"][k] for k in about if rec.get("author")})
        post["community_note"] = rec.get("community_note")
        if rec.get("is_long") and rec.get("text"):
            post["text"], post["truncated"] = rec["text"], False
        q = post.get("quote")
        if q:
            qrec = records.get(rec.get("quoted_id") or "") or {}
            q["id"] = qrec.get("id")
            q["videos"] = qrec.get("videos", [])
            q["author"].update({k: qrec["author"][k] for k in about if qrec.get("author")})
            if qrec.get("is_long") and qrec.get("text"):
                q["text"] = qrec["text"]


def _thread_records(ctx: BrowserContext, root_id: str) -> dict[str, dict]:
    """Open the thread's post page in a second tab and parse what it loads."""
    responses = []
    page = ctx.new_page()
    page.on("response", lambda r: _keep_json(r, responses) if "/graphql/" in r.url and "TweetDetail" in r.url else None)
    records: dict[str, dict] = {}
    try:
        page.goto(f"https://x.com/i/status/{root_id}", wait_until="domcontentloaded")
        deadline = time.time() + 20
        while not responses and time.time() < deadline:
            time.sleep(0.5)
        time.sleep(random.uniform(2, 4))
        _check_state(page)
        for data in responses:
            xdata.collect(data, records)
    finally:
        page.close()
    return records


def _expand_threads(ctx: BrowserContext, posts: dict[str, dict], records: dict[str, dict], stats: dict) -> None:
    """Turn each self-thread in the feed into one post carrying the whole thread.
    Feed posts from the same thread are merged; a mid-thread post is replaced by the
    thread from its start."""
    groups: dict[str, list[str]] = {}
    for pid, post in posts.items():
        root = xdata.thread_root(records.get(pid), post.get("text", ""))
        if root:
            groups.setdefault(root, []).append(pid)
    stats["threads"] = 0
    seen = store.load_seen()
    for root, members in list(groups.items())[:MAX_THREADS]:
        time.sleep(random.uniform(3, 6))
        detail = _thread_records(ctx, root)
        chain = xdata.thread_chain(detail, root, MAX_THREAD_POSTS)
        if len(chain) < 2:
            continue
        head = chain[0]
        base = posts.get(root)
        if base is None:
            if root in seen:  # already delivered this thread from its start
                continue
            base = posts[members[0]]
            base.update(id=head["id"], url=head["url"], text=head["text"], truncated=False,
                        images=head["images"], videos=head["videos"], has_video=bool(head["videos"]),
                        quote=None, is_reply=False)
            if head["author"]["handle"]:
                base["author"] = {**base["author"], **{k: v for k, v in head["author"].items() if v}}
        base["thread"] = [{"id": r["id"], "text": r["text"], "images": r["images"], "videos": r["videos"]}
                          for r in chain[1:]]
        base["thread_ids"] = [r["id"] for r in chain]
        # Replace the thread's first feed post in place; drop its other feed posts.
        slot = next(m for m in posts if m in members)
        rebuilt = {}
        for pid, p in posts.items():
            if pid == slot:
                rebuilt[head["id"]] = base
            elif pid not in members:
                rebuilt[pid] = p
        posts.clear()
        posts.update(rebuilt)
        stats["threads"] += 1


def download_images(posts: list[dict], max_per_post: int = 12) -> int:
    """Save each post's images (own + quoted) under IMAGES_DIR; record filenames
    on the post as `image_files`. Plain HTTP, no browser session involved."""
    store.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for post in posts:
        urls = post.get("images", []) + ((post.get("quote") or {}).get("images") or []) + \
            [u for part in post.get("thread") or [] for u in part.get("images") or []]
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
        for pid in post.get("thread_ids") or [post["id"]]:
            seen.setdefault(pid, ts)
    store.save_seen(seen)
