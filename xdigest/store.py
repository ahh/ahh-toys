"""Paths, secrets, and on-disk state. Nothing here lives in the repo."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "xdigest"
DATA_DIR = Path.home() / ".local" / "share" / "xdigest"
ENV_FILE = CONFIG_DIR / "env"
PROFILE_DIR = DATA_DIR / "browser-profile"
SEEN_FILE = DATA_DIR / "seen.json"
RUNS_DIR = DATA_DIR / "runs"
IMAGES_DIR = DATA_DIR / "images"
# Every scored post from every run (merged in from the routine's outbox), for later
# hand-labelling / classifier work.
SCORE_LOG = DATA_DIR / "scored.jsonl"

SEEN_TTL = timedelta(days=30)


def load_env() -> None:
    """Load KEY=VALUE lines from ~/.config/xdigest/env into os.environ."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_dir() -> Path:
    path = RUNS_DIR / now().strftime("%Y-%m-%dT%H%M%SZ")
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_run_dir() -> Path:
    runs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir()) if RUNS_DIR.exists() else []
    if not runs:
        raise SystemExit("No runs yet; run `fetch` first.")
    return runs[-1]


def load_seen() -> dict[str, str]:
    if not SEEN_FILE.exists():
        return {}
    seen = json.loads(SEEN_FILE.read_text())
    cutoff = (now() - SEEN_TTL).isoformat()
    return {pid: ts for pid, ts in seen.items() if ts >= cutoff}


def save_seen(seen: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen))
    os.replace(tmp, SEEN_FILE)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]
