"""The private GitHub repo as a one-day mailbox between this Mac and the cloud routine.

- `inbox` branch: force-pushed each run as a single orphan commit holding that day's
  posts, their images, and the routine's rubric + helper script.
- `claude/outbox-<run_id>` branches: pushed by the routine with scores and picks;
  deleted here once archived.

History never accumulates; the long-term archive lives in ~/.local/share/xdigest.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import store

REPO_URL = "https://github.com/ahh/xdigest-data.git"
ROUTINE_FILES = Path(__file__).parent / "routine"


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
        for f in ROUTINE_FILES.iterdir():
            shutil.copy2(f, root / f.name)

        _git("init", "-q", "-b", "inbox", cwd=root)
        _git("add", "-A", cwd=root)
        _git("-c", "user.name=xdigest", "-c", "user.email=xdigest@localhost",
             "commit", "-q", "-m", f"inbox {run_id}", cwd=root)
        _git("push", "-q", "--force", REPO_URL, "inbox:inbox", cwd=root)


def outbox_ready(run_id: str) -> bool:
    return bool(_git("ls-remote", REPO_URL, f"refs/heads/{outbox_branch(run_id)}").strip())


def pending_outboxes() -> list[str]:
    """Run ids of every outbox branch still on the remote."""
    prefix = "refs/heads/" + outbox_branch("")
    refs = [line.split("\t")[1] for line in _git("ls-remote", REPO_URL).splitlines()]
    return sorted(r[len(prefix):] for r in refs if r.startswith(prefix))


def read_outbox(run_id: str) -> dict:
    """Return {"run": ..., "scored": [...], "picks": [...]} from an outbox branch."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _git("init", "-q", cwd=root)
        _git("fetch", "-q", "--depth=1", REPO_URL, outbox_branch(run_id), cwd=root)
        _git("checkout", "-q", "FETCH_HEAD", cwd=root)
        return {
            "run": json.loads((root / "out" / "run.json").read_text()),
            "scored": store.read_jsonl(root / "out" / "scored.jsonl"),
            "picks": store.read_jsonl(root / "out" / "picks.jsonl"),
        }


def delete_outbox(run_id: str) -> None:
    _git("push", "-q", REPO_URL, "--delete", outbox_branch(run_id))
