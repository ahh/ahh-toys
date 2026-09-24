"""Route digests and status messages to Telegram or Signal (DIGEST_TRANSPORT)."""

import os

import signalmsg
import telegram


def _transport():
    return signalmsg if os.environ.get("DIGEST_TRANSPORT") == "signal" else telegram


def send(text: str) -> None:
    _transport().send(text)


def send_posts(posts: list[dict]) -> None:
    _transport().send_digest(posts)
