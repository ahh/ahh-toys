"""X For You digest: fetch on this Mac -> score in a Claude routine -> email, Telegram, or Signal.

    uv run xdigest.py check          # which setup steps are done (no secrets printed)
    uv run xdigest.py login          # one-time: sign in to X in a dedicated Chrome profile
    uv run xdigest.py chat-id        # find your Telegram chat id (message the bot first)
    uv run xdigest.py fetch          # scroll For You, save posts + images locally (no push)
    uv run xdigest.py push           # push the latest local run to the inbox branch
    uv run xdigest.py deliver        # send + archive any scored runs waiting in the repo
    uv run xdigest.py run            # daily job: deliver leftovers, fetch, push, wait, deliver
    uv run xdigest.py install-schedule [--at 07:30,10:30,...]  # run `run --headless` via launchd
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


TRANSPORT_VARS = {
    "email": ["RESEND_API_KEY", "EMAIL_TO"],
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "signal": ["SIGNAL_ACCOUNT"],
}


def cmd_check(args) -> None:
    """Report which setup steps are done. Never prints secret values."""
    ok_all = True

    def report(ok: bool, what: str, fix: str = "") -> None:
        nonlocal ok_all
        ok_all &= ok
        print(("  ✓ " if ok else "  ✗ ") + what + ("" if ok or not fix else f"  → {fix}"))

    transport = os.environ.get("DIGEST_TRANSPORT", "telegram")
    print("tools")
    for tool in ["uv", "gh", "git"] + (["ffmpeg"] if transport in ("email", "signal") else []) \
            + (["signal-cli"] if transport == "signal" else []):
        report(bool(shutil.which(tool)), tool, f"brew install {tool}")
    report(Path("/Applications/Google Chrome.app").exists(), "Google Chrome", "install Google Chrome")
    gh_ok = subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0 if shutil.which("gh") else False
    report(gh_ok, "gh logged in", "gh auth login")

    print(f"config ({store.ENV_FILE})")
    exists = store.ENV_FILE.exists()
    report(exists, "env file exists", "create it (see README step 2)")
    if exists:
        report(oct(store.ENV_FILE.stat().st_mode & 0o777) == "0o600", "env file is private", f"chmod 600 {store.ENV_FILE}")
    report(transport in TRANSPORT_VARS, f"DIGEST_TRANSPORT={transport}", "email, telegram, or signal")
    for var in ["XDIGEST_DATA_REPO"] + TRANSPORT_VARS.get(transport, []):
        report(bool(os.environ.get(var)), f"{var} set", "add it to the env file")
    if os.environ.get("XDIGEST_DATA_REPO") and gh_ok:
        try:
            mailbox._git("ls-remote", mailbox._repo_url())
            report(True, f"data repo {os.environ['XDIGEST_DATA_REPO']} reachable")
        except subprocess.CalledProcessError:
            report(False, "data repo reachable", "gh repo create <name> --private --add-readme")

    if transport == "email":
        sender = os.environ.get("EMAIL_FROM") or "onboarding@resend.dev"
        domain = sender.rsplit("@", 1)[-1].strip(" >\"'")
        if domain == "resend.dev":
            print("  · shared sender: each day's picks arrive at once (own domain needed to spread them)")
        else:
            def txt(name: str) -> str:
                return subprocess.run(["dig", "+short", "TXT", name], capture_output=True, text=True).stdout
            report("p=" in txt(f"resend._domainkey.{domain}"), f"DKIM record for {domain}",
                   "add the DNS records Resend shows for this domain")
            org = ".".join(domain.split(".")[-2:])
            report("v=DMARC1" in txt(f"_dmarc.{domain}") + txt(f"_dmarc.{org}"), f"DMARC record for {org}",
                   f"TXT _dmarc.{org} = v=DMARC1; p=none;")
            print("  · Resend must also show the domain as Verified (can lag DNS by up to an hour)")

    fire = bool(os.environ.get("ROUTINE_FIRE_URL") and os.environ.get("ROUTINE_FIRE_TOKEN"))
    print(("  ✓ " if fire else "  · ") + "routine API trigger " +
          ("set (scoring starts right after each push)" if fire else
           "not set (optional: ROUTINE_FIRE_URL + ROUTINE_FIRE_TOKEN; otherwise scoring waits for the routine's schedule)"))

    print("X login")
    cookies = store.PROFILE_DIR / "Default" / "Cookies"
    logged_in = False
    if cookies.exists():
        q = subprocess.run(["sqlite3", "-readonly", f"file:{cookies}?immutable=1",
                            "select count(*) from cookies where host_key like '%x.com' and name='auth_token'"],
                           capture_output=True, text=True)
        logged_in = q.stdout.strip() not in ("", "0")
    report(logged_in, "dedicated Chrome profile has an X session", "uv run xdigest.py login")

    print("schedule")
    report(SCHEDULE_PLIST.exists(), f"daily job installed ({SCHEDULE_PLIST.name})",
           "uv run xdigest.py install-schedule")
    print("  ? scoring routine: can't be checked from here; see ROUTINE_PROMPT.md")
    print("all set" if ok_all else "some steps remain")


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
        def full(row: dict) -> dict:
            # Full post from the local fetch; bare link if this Mac doesn't have it.
            post = local.get(row["id"]) or {"author": {"name": "", "handle": row["author"]}, "url": row["url"]}
            return {**post, "note": row["note"]} if row.get("note") else post

        picks, samples = [full(r) for r in out["picks"]], [full(r) for r in out["samples"]]
        if not picks:
            notify.send(f"📭 X digest: nothing cleared the bar out of {out['run']['n_posts']} posts.")
        # Spread the calibration samples evenly among the picks rather than at the end.
        order = sorted([((i + 0.5) / max(len(picks), 1), 0, p) for i, p in enumerate(picks)] +
                       [((j + 1) / (len(samples) + 1), 1, p) for j, p in enumerate(samples)],
                       key=lambda t: t[:2])
        if order:
            notify.send_posts([p for _, _, p in order])
        (run_dir / "sent").touch()

    if not (run_dir / "outbox.json").exists():  # archive each run once, even if re-scored
        store.write_jsonl(run_dir / "scored.jsonl", out["scored"])
        store.write_jsonl(run_dir / "picks.jsonl", out["picks"])
        store.write_jsonl(run_dir / "samples.jsonl", out["samples"])
        store.append_jsonl(store.SCORE_LOG, [
            {"run": run_id, **local.get(row["id"], {}), "scoring": row["scoring"]} for row in out["scored"]
        ])
        (run_dir / "outbox.json").write_text(json.dumps(out["run"], indent=2))
    mailbox.delete_outbox(run_id)
    mailbox.clear_inbox(run_id)
    print(f"delivered {run_id}: {len(out['picks'])} picks + {len(out['samples'])} samples "
          f"from {out['run']['n_posts']} posts "
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
        mailbox.fire_routine(run_id)

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
    times = [tuple(int(x) for x in t.strip().split(":")) for t in args.at.split(",")]
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
                             "xdigest.py", "run", "--headless", "--max-posts", str(args.max_posts),
                             "--wait-hours", str(args.wait_hours)],
        "EnvironmentVariables": {"PATH": path, "PYTHONUNBUFFERED": "1"},
        # If the Mac is asleep at one of these times, launchd runs the job when it wakes
        # (and never starts a second copy while one is still running).
        "StartCalendarInterval": [{"Hour": h, "Minute": m} for h, m in times],
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(SCHEDULE_PLIST)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(SCHEDULE_PLIST)], check=True)
    when = ", ".join(f"{h:02d}:{m:02d}" for h, m in times)
    print(f"installed: daily at {when}, {args.max_posts} posts each -> {SCHEDULE_PLIST}\nlog: {log}")


def cmd_uninstall_schedule(args) -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(SCHEDULE_PLIST)], capture_output=True)
    SCHEDULE_PLIST.unlink(missing_ok=True)
    print("schedule removed")


def main() -> None:
    store.load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in [("check", cmd_check), ("login", cmd_login), ("chat-id", cmd_chat_id), ("push", cmd_push), ("deliver", cmd_deliver),
                     ("uninstall-schedule", cmd_uninstall_schedule)]:
        sub.add_parser(name).set_defaults(fn=fn)
    p = sub.add_parser("install-schedule")
    p.add_argument("--at", default="07:30,10:30,13:30,16:30,19:30",
                   help="comma-separated local times, HH:MM (default 5 runs, every 3h from 07:30)")
    p.add_argument("--max-posts", type=int, default=60, help="posts to read per run (default 60)")
    p.add_argument("--wait-hours", type=float, default=2,
                   help="how long each run waits for scores; keep it under the gap between runs")
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
