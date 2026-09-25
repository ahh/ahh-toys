"""The private GitHub repo as a one-day mailbox between this Mac and the cloud routine.

- `inbox` branch: force-pushed each run as a single orphan commit holding that day's
  posts, their images, and the routine's rubric + helper script.
- `claude/outbox-<run_id>` branches: pushed by the routine with scores and picks;
  deleted here once archived.

History never accumulates; the long-term archive lives in ~/.local/share/xdigest.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import store

# What the routine gets alongside the posts. Listed explicitly so stray files in
# routine/ (e.g. __pycache__) are never shipped.
ROUTINE_DIR = Path(__file__).parent / "routine"
ROUTINE_FILES = ["SCORING.md", "pick.py"]


def _repo_url() -> str:
    """The private data repo, from XDIGEST_DATA_REPO ("owner/name") in the env file."""
    repo = os.environ.get("XDIGEST_DATA_REPO")
    if not repo:
        raise SystemExit("Set XDIGEST_DATA_REPO=owner/name (your private data repo) in ~/.config/xdigest/env")
    return f"https://github.com/{repo}.git"


def _git(*args: str, cwd: Path | None = None) -> str:
    # Authenticate with the gh CLI's token without touching global git config.
    cmd = ["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def outbox_branch(run_id: str) -> str:
    return f"claude/outbox-{run_id}"


def push_inbox(run_id: str, posts: list[dict]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store.write_jsonl(root / "posts.jsonl", posts)
        (root / "run.json").write_text(json.dumps({"run_id": run_id, "n_posts": len(posts)}, indent=2))
        (root / "images").mkdir()
        for post in posts:
            for name in post.get("image_files", []):
                shutil.copy2(store.IMAGES_DIR / name, root / "images" / name)
        for name in ROUTINE_FILES:
            shutil.copy2(ROUTINE_DIR / name, root / name)

        _git("init", "-q", "-b", "inbox", cwd=root)
        _git("add", "-A", cwd=root)
        _git("-c", "user.name=xdigest", "-c", "user.email=xdigest@localhost",
             "commit", "-q", "-m", f"inbox {run_id}", cwd=root)
        _git("push", "-q", "--force", _repo_url(), "inbox:inbox", cwd=root)
        sha = _git("rev-parse", "HEAD", cwd=root).strip()
    (store.RUNS_DIR / run_id).mkdir(parents=True, exist_ok=True)
    (store.RUNS_DIR / run_id / "inbox_sha").write_text(sha)


def clear_inbox(run_id: str) -> None:
    """After delivering run_id, delete the inbox branch so later routine runs (the
    backup schedule, a stray fire) find nothing to do, but only if the inbox still
    holds this run: the lease makes git refuse if a newer batch has replaced it."""
    sha_file = store.RUNS_DIR / run_id / "inbox_sha"
    if not sha_file.exists():
        return
    try:
        _git("push", "-q", f"--force-with-lease=refs/heads/inbox:{sha_file.read_text().strip()}",
             _repo_url(), ":refs/heads/inbox")
    except subprocess.CalledProcessError:
        pass  # already gone, or a newer batch is waiting: leave it


def fire_routine(run_id: str) -> None:
    """Start the scoring routine now via its API trigger, if configured
    (ROUTINE_FIRE_URL + ROUTINE_FIRE_TOKEN). The token can only start that one routine.
    Best effort: if this fails, the routine's own schedule picks the batch up."""
    url, token = os.environ.get("ROUTINE_FIRE_URL"), os.environ.get("ROUTINE_FIRE_TOKEN")
    if not (url and token):
        return
    req = urllib.request.Request(url, data=json.dumps({"text": f"inbox {run_id} is ready"}).encode(), headers={
        "Authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "User-Agent": "xdigest",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print("fired routine:", json.load(resp).get("claude_code_session_url"))
    except OSError as e:
        print(f"routine fire failed ({e}); relying on the routine's schedule", file=sys.stderr)


def outbox_ready(run_id: str) -> bool:
    return bool(_git("ls-remote", _repo_url(), f"refs/heads/{outbox_branch(run_id)}").strip())


def pending_outboxes() -> list[str]:
    """Run ids of every outbox branch still on the remote."""
    prefix = "refs/heads/" + outbox_branch("")
    refs = [line.split("\t")[1] for line in _git("ls-remote", _repo_url()).splitlines()]
    return sorted(r[len(prefix):] for r in refs if r.startswith(prefix))


def read_outbox(run_id: str) -> dict:
    """Return {"run": ..., "scored": [...], "picks": [...]} from an outbox branch."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _git("init", "-q", cwd=root)
        _git("fetch", "-q", "--depth=1", _repo_url(), outbox_branch(run_id), cwd=root)
        _git("checkout", "-q", "FETCH_HEAD", cwd=root)
        return {
            "run": json.loads((root / "out" / "run.json").read_text()),
            "scored": store.read_jsonl(root / "out" / "scored.jsonl"),
            "picks": store.read_jsonl(root / "out" / "picks.jsonl"),
            "samples": (store.read_jsonl(root / "out" / "samples.jsonl")
                        if (root / "out" / "samples.jsonl").exists() else []),
        }


def delete_outbox(run_id: str) -> None:
    _git("push", "-q", _repo_url(), "--delete", outbox_branch(run_id))
