"""X For You digest: fetch on this Mac -> score in a Claude routine -> email, Telegram, or Signal.

    uv run xdigest.py login          # one-time: sign in to X in a dedicated Chrome profile
    uv run xdigest.py chat-id        # find your Telegram chat id (message the bot first)
    uv run xdigest.py fetch          # scroll For You, save posts + images locally (no push)
    uv run xdigest.py push           # push the latest local run to the inbox branch
    uv run xdigest.py deliver        # send + archive any scored runs waiting in the repo
    uv run xdigest.py run            # daily job: deliver leftovers, fetch, push, wait, deliver
    uv run xdigest.py install-schedule [--at 07:30]   # run `run --headless` daily via launchd
    uv run xdigest.py uninstall-schedule
"""

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

import fetch
import mailbox
import notify
import store
import telegram


def cmd_login(args) -> None:
    fetch.login()


def cmd_chat_id(args) -> None:
    for chat in telegram.recent_chat_ids() or [{"chat_id": "none yet: send your bot a message first"}]:
        print(chat)


def cmd_fetch(args) -> tuple[str, list[dict]]:
    posts, stats = fetch.fetch(max_posts=args.max_posts, headless=args.headless)
    stats["images_saved"] = fetch.download_images(posts)
    run_dir = store.new_run_dir()
    store.write_jsonl(run_dir / "posts.jsonl", posts)
    print(f"{len(posts)} new posts -> {run_dir}")
    print(f"stats: {stats}")
    # Shape check without printing any post content.
    print("with text:", sum(bool(p["text"]) for p in posts),
          "| with images:", sum(bool(p["image_files"]) for p in posts),
          "| video:", sum(p["has_video"] for p in posts),
          "| quotes:", sum(bool(p["quote"]) for p in posts),
          "| cards:", sum(bool(p["card"]) for p in posts),
          "| replies:", sum(p["is_reply"] for p in posts),
          "| with metrics:", sum(bool(p["metrics"]) for p in posts))
    return run_dir.name, posts


def cmd_push(args, run_id: str | None = None, posts: list[dict] | None = None) -> None:
    if run_id is None:
        run_dir = store.latest_run_dir()
        run_id, posts = run_dir.name, store.read_jsonl(run_dir / "posts.jsonl")
    mailbox.push_inbox(run_id, posts)
    fetch.mark_seen(posts)
    print(f"pushed inbox {run_id} ({len(posts)} posts)")


def deliver(run_id: str) -> None:
    """Send one scored run, archive its scores locally, delete the branch."""
    run_dir = store.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out = mailbox.read_outbox(run_id)
    posts_file = run_dir / "posts.jsonl"
    local = {p["id"]: p for p in store.read_jsonl(posts_file)} if posts_file.exists() else {}
    if not (run_dir / "sent").exists():
        if out["picks"]:
            # Full post from the local fetch; bare link if this Mac doesn't have it.
            notify.send_posts([local.get(pick["id"]) or {"author": {"name": "", "handle": pick["author"]},
                                                        "url": pick["url"]} for pick in out["picks"]])
        else:
            notify.send(f"📭 X digest: nothing cleared the bar out of {out['run']['n_posts']} posts.")
        (run_dir / "sent").touch()

    store.write_jsonl(run_dir / "scored.jsonl", out["scored"])
    store.write_jsonl(run_dir / "picks.jsonl", out["picks"])
    store.append_jsonl(store.SCORE_LOG, [
        {"run": run_id, **local.get(row["id"], {}), "scoring": row["scoring"]} for row in out["scored"]
    ])
    (run_dir / "outbox.json").write_text(json.dumps(out["run"], indent=2))
    mailbox.delete_outbox(run_id)
    print(f"delivered {run_id}: {len(out['picks'])} picks from {out['run']['n_posts']} posts "
          f"({out['run'].get('n_missing', 0)} unscored)")


def cmd_deliver(args) -> None:
    pending = mailbox.pending_outboxes()
    for run_id in pending:
        deliver(run_id)
    if not pending:
        print("no scored runs waiting")


def cmd_run(args) -> None:
    try:
        cmd_deliver(args)  # anything the routine finished after a previous run gave up waiting
        run_id, posts = cmd_fetch(args)
        if not posts:
            notify.send("📭 X digest: no new posts in For You today.")
            return
        cmd_push(args, run_id, posts)

        deadline = time.time() + args.wait_hours * 3600
        while not mailbox.outbox_ready(run_id):
            if time.time() > deadline:
                notify.send(f"⏳ X digest: routine hasn't scored run {run_id} after {args.wait_hours}h. "
                            "It'll be delivered on the next run if it shows up.")
                sys.exit(4)
            time.sleep(120)
        deliver(run_id)
    except fetch.LoggedOut:
        notify.send("🔑 X digest: logged out of X. Run `uv run xdigest.py login` in the xdigest folder.")
        sys.exit(2)
    except fetch.Challenged:
        notify.send("🤖 X digest: X wants a human check. Run `uv run xdigest.py login` and clear it.")
        sys.exit(3)
    except Exception as e:
        notify.send(f"💥 X digest failed: {type(e).__name__}: {str(e)[:200]}")
        raise


SCHEDULE_LABEL = "com.xdigest.daily"
SCHEDULE_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{SCHEDULE_LABEL}.plist"


def cmd_install_schedule(args) -> None:
    hour, minute = (int(x) for x in args.at.split(":"))
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv not found on PATH")
    # launchd's default PATH lacks Homebrew; include wherever our tools live.
    tool_dirs = [str(Path(t).parent) for t in map(shutil.which, ["uv", "gh", "git", "signal-cli", "ffmpeg"]) if t]
    path = ":".join(dict.fromkeys(tool_dirs + ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = str(store.DATA_DIR / "launchd.log")
    SCHEDULE_PLIST.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_PLIST.write_bytes(plistlib.dumps({
        "Label": SCHEDULE_LABEL,
        "ProgramArguments": [uv, "run", "--directory", str(Path(__file__).resolve().parent),
                             "xdigest.py", "run", "--headless"],
        "EnvironmentVariables": {"PATH": path, "PYTHONUNBUFFERED": "1"},
        # If the Mac is asleep at this time, launchd runs the job when it wakes.
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(SCHEDULE_PLIST)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(SCHEDULE_PLIST)], check=True)
    print(f"installed: daily at {hour:02d}:{minute:02d} -> {SCHEDULE_PLIST}\nlog: {log}")


def cmd_uninstall_schedule(args) -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(SCHEDULE_PLIST)], capture_output=True)
    SCHEDULE_PLIST.unlink(missing_ok=True)
    print("schedule removed")


def main() -> None:
    store.load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in [("login", cmd_login), ("chat-id", cmd_chat_id), ("push", cmd_push), ("deliver", cmd_deliver),
                     ("uninstall-schedule", cmd_uninstall_schedule)]:
        sub.add_parser(name).set_defaults(fn=fn)
    p = sub.add_parser("install-schedule")
    p.add_argument("--at", default="07:30", help="local time, HH:MM (default 07:30)")
    p.set_defaults(fn=cmd_install_schedule)
    for name, fn in [("fetch", cmd_fetch), ("run", cmd_run)]:
        p = sub.add_parser(name)
        p.add_argument("--max-posts", type=int, default=300)
        p.add_argument("--headless", action="store_true")
        p.add_argument("--wait-hours", type=float, default=4)
        p.set_defaults(fn=fn)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
