"""Deterministic helpers for the scoring routine. Runs in the cloud sandbox
(stdlib only), inside a checkout of the day's inbox commit.

    python3 pick.py prepare     # posts.jsonl -> batches/batch-NN.md
    python3 pick.py finish      # batches/*.scores.jsonl -> out/{scored,picks}.jsonl
"""

import json
import math
import sys
from pathlib import Path

BATCH_SIZE = 25
FRACTION = 0.10
MIN_SCORE = 6

REJECT_REASONS = {"none", "politics", "ragebait", "ad", "other"}
CATEGORIES = {"joke", "meme", "tech", "learning", "delight", "other"}

ROOT = Path(__file__).parent
BATCHES = ROOT / "batches"
OUT = ROOT / "out"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def describe(post: dict) -> str:
    a = post["author"]
    lines = [f"### post {post['id']}", f"Author: {a['name']} (@{a['handle']})"]
    if post.get("social_context"):
        lines.append(f"Shown because: {post['social_context']}")
    if post.get("is_reply"):
        lines.append("This post is a reply.")
    lines.append("<post_text>\n" + (post["text"] or "(no text)") +
                 ("\n[text truncated]" if post.get("truncated") else "") + "\n</post_text>")
    if post.get("has_video"):
        lines.append("Has a video (only its thumbnail, if any, is attached).")
    q = post.get("quote")
    if q:
        lines.append(f"Quotes a post by {q['author']['name']} (@{q['author']['handle']}):\n"
                     f"<quoted_text>\n{q['text'] or '(no text)'}\n</quoted_text>")
    if post.get("card"):
        lines.append(f"Link card:\n<card>\n{post['card']['text']}\n</card>")
    m = post.get("metrics") or {}
    if m:
        lines.append("Engagement: " + ", ".join(f"{v} {k}" for k, v in m.items()))
    files = post.get("image_files") or []
    lines.append("Images: " + (", ".join(f"images/{f}" for f in files) if files else "none"))
    return "\n".join(lines)


def prepare() -> None:
    posts = read_jsonl(ROOT / "posts.jsonl")
    BATCHES.mkdir(exist_ok=True)
    n = math.ceil(len(posts) / BATCH_SIZE)
    for i in range(n):
        chunk = posts[i * BATCH_SIZE:(i + 1) * BATCH_SIZE]
        (BATCHES / f"batch-{i:02d}.md").write_text("\n\n".join(describe(p) for p in chunk) + "\n")
    print(f"{len(posts)} posts -> {n} batches in {BATCHES}/ "
          f"(write scores to batches/batch-NN.scores.jsonl)")


def _valid(s: dict) -> bool:
    return (isinstance(s.get("rejected"), bool)
            and s.get("reject_reason") in REJECT_REASONS
            and s.get("category") in CATEGORIES
            and isinstance(s.get("score"), int)
            and isinstance(s.get("why"), str))


def finish() -> None:
    posts = read_jsonl(ROOT / "posts.jsonl")
    ids = {p["id"] for p in posts}
    scores: dict[str, dict] = {}
    bad = 0
    for path in sorted(BATCHES.glob("batch-*.scores.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if _valid(s) and str(s.get("id")) in ids:
                s["score"] = max(0, min(10, s["score"]))
                scores[str(s["id"])] = {k: s[k] for k in ("rejected", "reject_reason", "category", "score", "why")}
            else:
                bad += 1

    missing = [p["id"] for p in posts if p["id"] not in scores]
    scored = [{"id": p["id"], "url": p["url"], "author": p["author"]["handle"],
               "scoring": scores.get(p["id"], {"rejected": True, "reject_reason": "other", "category": "other",
                                               "score": 0, "why": "not scored", "error": True})}
              for p in posts]

    keep = [r for r in scored if not r["scoring"]["rejected"] and r["scoring"]["score"] >= MIN_SCORE]
    likes = {p["id"]: (p.get("metrics") or {}).get("likes", 0) for p in posts}
    keep.sort(key=lambda r: (r["scoring"]["score"], likes[r["id"]]), reverse=True)
    picks = keep[:math.ceil(len(scored) * FRACTION)]

    write_jsonl(OUT / "scored.jsonl", scored)
    write_jsonl(OUT / "picks.jsonl", picks)
    run = json.loads((ROOT / "run.json").read_text())
    (OUT / "run.json").write_text(json.dumps({
        **run, "n_posts": len(posts), "n_scored": len(scores), "n_missing": len(missing),
        "n_invalid_lines": bad, "n_picks": len(picks),
    }, indent=2))
    print(f"scored {len(scores)}/{len(posts)} | invalid lines {bad} | missing {len(missing)} | picks {len(picks)}")
    if missing:
        print("missing ids:", " ".join(missing))


if __name__ == "__main__":
    {"prepare": prepare, "finish": finish}[sys.argv[1]]()
