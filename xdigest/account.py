"""Follow / unfollow accounts from the dedicated X profile, so you can curate who you
follow without opening X yourself.

These are the only account-changing actions in xdigest, and they run only when you
invoke them by hand (`xdigest.py follow ...`). The scheduled job, the fetcher and the
scoring routine never call them, so nothing in a post can make you follow anyone.
"""

import re
import time

from playwright.sync_api import Page, sync_playwright

import fetch

HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
RESERVED = {"i", "home", "explore", "search", "settings", "notifications", "messages", "intent"}


def _handle_from(arg: str) -> tuple[str | None, str | None]:
    """(handle, post_id) from '@name', 'name', a profile URL or a post URL."""
    arg = arg.strip()
    m = re.match(r"^(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/([^/?#]+)(?:/status/(\d+))?", arg)
    if m:
        handle, post_id = m.group(1), m.group(2)
        return (None if handle.lower() in RESERVED else handle), post_id
    name = arg.lstrip("@")
    if HANDLE.match(name):
        return name, None
    raise ValueError(f"not a handle or x.com link: {arg!r}")


def _author_of_post(page: Page, post_id: str) -> str:
    page.goto(f"https://x.com/i/status/{post_id}", wait_until="domcontentloaded")
    page.wait_for_selector('article[data-testid="tweet"]', timeout=30_000)
    fetch._check_state(page)
    href = page.locator('article[data-testid="tweet"]').first.locator('[data-testid="User-Name"] a[href^="/"]').first.get_attribute("href")
    return href.strip("/").split("/")[0]


def _profile_button(page: Page, handle: str):
    """The Follow/Following button for this profile only (never 'Who to follow' ones)."""
    buttons = page.locator('[data-testid="primaryColumn"] [data-testid$="-follow"], '
                           '[data-testid="primaryColumn"] [data-testid$="-unfollow"]')
    for i in range(buttons.count()):
        b = buttons.nth(i)
        if f"@{handle.lower()}" in (b.get_attribute("aria-label") or "").lower():
            return b
    return None


def _state(button) -> str:
    return "following" if (button.get_attribute("data-testid") or "").endswith("-unfollow") else "not following"


def set_follow(target: str, follow: bool, headless: bool = True) -> str:
    """Follow (or unfollow) the account named by `target`. Returns a one-line result."""
    handle, post_id = _handle_from(target)
    with sync_playwright() as p:
        ctx = fetch._open(p, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if not handle:
                handle = _author_of_post(page, post_id)
            page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded")
            deadline = time.time() + 30
            button = None
            while time.time() < deadline and button is None:
                fetch._check_state(page)
                button = _profile_button(page, handle)
                if button is None:
                    time.sleep(1)
            if button is None:
                return f"@{handle}: no follow button found (account missing, suspended, or blocked?)"
            before = _state(button)
            if (before == "following") == follow:
                return f"@{handle}: already {before}"
            button.click()
            if not follow:
                confirm = page.locator('[data-testid="confirmationSheetConfirm"]')
                confirm.wait_for(timeout=10_000)
                confirm.click()
            for _ in range(20):
                time.sleep(0.5)
                b = _profile_button(page, handle)
                if b is not None and _state(b) == ("following" if follow else "not following"):
                    return f"@{handle}: {'now following' if follow else 'unfollowed'}"
            return f"@{handle}: clicked, but X didn't confirm the change; check the profile"
        finally:
            ctx.close()
