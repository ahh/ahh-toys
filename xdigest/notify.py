"""Route digests and status messages to the chosen transport (DIGEST_TRANSPORT):
telegram (default), signal, or email."""

import os

import emailmsg
import signalmsg
import telegram

TRANSPORTS = {"telegram": telegram, "signal": signalmsg, "email": emailmsg}


def _transport():
    name = os.environ.get("DIGEST_TRANSPORT", "telegram")
    if name not in TRANSPORTS:
        raise SystemExit(f"DIGEST_TRANSPORT must be one of {', '.join(TRANSPORTS)}, not {name!r}")
    return TRANSPORTS[name]


def send(text: str) -> None:
    _transport().send(text)


def send_posts(posts: list[dict]) -> None:
    _transport().send_digest(posts)
