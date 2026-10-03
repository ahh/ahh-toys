"""Hand-label archived posts for training a ranker: four at a time, pick best and worst.

    uv run label.py            # then open http://127.0.0.1:8765

Labels append to ~/.local/share/xdigest/labels.jsonl, one row per screen:
    {"ts", "shown": [4 ids, display order], "best", "worst", "want": [ids], "skip",
     "claude": {id: score}, "sampler"}
Best/worst over 4 gives 5 of the 6 pairwise orderings; "want" marks posts you'd actually
want in the digest, which pins down where the cut goes (rankings alone can't).

Bound to localhost only. Post text is from strangers, so the page renders it as text,
never HTML.
"""

import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import store

LABELS = store.DATA_DIR / "labels.jsonl"
PORT = 8765


def load_posts() -> dict[str, dict]:
    posts = {}
    for row in store.read_jsonl(store.SCORE_LOG):
        if not row.get("is_ad"):
            posts[row["id"]] = row  # later runs win
    return posts


def claude_score(post: dict) -> float:
    s = post.get("scoring") or {}
    return 0.0 if s.get("rejected") else float(s.get("score") or 0)


class Sampler:
    """Picks 4 posts per screen, favouring posts shown least so far. Most screens span
    the range of Claude scores (so they aren't four near-identical posts); some are
    drawn from near the digest bar, where the ordering actually matters; some uniform."""

    def __init__(self, posts: dict[str, dict]):
        self.posts = posts
        self.shown = Counter()
        for row in store.read_jsonl(LABELS) if LABELS.exists() else []:
            self.shown.update(row["shown"])

    def _pool(self, ids: list[str], k: int, taken: set[str]) -> list[str]:
        ids = [i for i in ids if i not in taken]
        random.shuffle(ids)
        ids.sort(key=lambda i: self.shown[i])  # stable: random within equal counts
        return ids[:k]

    def batch(self) -> tuple[list[str], str]:
        ranked = sorted(self.posts, key=lambda i: claude_score(self.posts[i]))
        r = random.random()
        if r < 0.5:
            mode, out = "stratified", []
            q = len(ranked) // 4
            for n in range(4):
                band = ranked[n * q:(n + 1) * q if n < 3 else len(ranked)]
                out += self._pool(band, 1, set(out))
        elif r < 0.8:
            mode = "near-bar"
            out = self._pool([i for i in ranked if 5 <= claude_score(self.posts[i]) <= 7.5],
                             4, set())
        else:
            mode, out = "uniform", self._pool(ranked, 4, set())
        if len(out) < 4:
            out += self._pool(ranked, 4 - len(out), set(out))
        random.shuffle(out)
        self.shown.update(out)
        return out, mode


def public(post: dict) -> dict:
    keys = ("id", "url", "author", "created_at", "text", "images", "videos", "has_video",
            "quote", "card", "social_context", "is_reply", "thread", "community_note",
            "metrics", "source")
    return {k: post.get(k) for k in keys}


def stats() -> dict:
    rows = store.read_jsonl(LABELS) if LABELS.exists() else []
    done = [r for r in rows if not r.get("skip")]
    return {"screens": len(done), "pairs": 5 * len(done), "skipped": len(rows) - len(done)}


class Handler(BaseHTTPRequestHandler):
    posts: dict[str, dict] = {}
    sampler: Sampler

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/next":
            ids, mode = self.sampler.batch()
            self._json({"posts": [public(self.posts[i]) for i in ids], "sampler": mode,
                        "stats": stats()})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/label":
            shown = body.get("shown") or []
            if len(shown) != 4 or any(i not in self.posts for i in shown):
                return self._json({"error": "bad batch"}, 400)
            skip = bool(body.get("skip"))
            best, worst = body.get("best"), body.get("worst")
            if not skip and (best not in shown or worst not in shown or best == worst):
                return self._json({"error": "need distinct best and worst"}, 400)
            row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "shown": shown, "best": None if skip else best,
                   "worst": None if skip else worst,
                   "want": [i for i in body.get("want") or [] if i in shown], "skip": skip,
                   "claude": {i: claude_score(self.posts[i]) for i in shown},
                   "sampler": body.get("sampler")}
            store.append_jsonl(LABELS, [row])
            self._json({"ok": True, "stats": stats(), "claude": row["claude"]})
        elif self.path == "/api/undo":
            rows = store.read_jsonl(LABELS) if LABELS.exists() else []
            if not rows:
                return self._json({"error": "nothing to undo"}, 400)
            store.write_jsonl(LABELS, rows[:-1])
            last = rows[-1]
            self._json({"posts": [public(self.posts[i]) for i in last["shown"]],
                        "label": last, "stats": stats()})
        else:
            self._send(404, b"not found", "text/plain")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>xdigest labeller</title>
<style>
:root { --bg:#f6f6f4; --card:#fff; --ink:#16181c; --dim:#6b7076; --line:#e3e3df;
  --best:#1a7f37; --worst:#c2410c; --want:#2563eb; --quote:#f3f3f0; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1012; --card:#18191c; --ink:#e7e9ea;
  --dim:#8b9096; --line:#2a2c30; --best:#3fb950; --worst:#f0883e; --want:#58a6ff; --quote:#202226; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
header { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line);
  padding:10px 16px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
header b { font-size:15px; }
#prompt { font-weight:600; }
#prompt.best { color:var(--best); } #prompt.worst { color:var(--worst); }
.stat, .keys { color:var(--dim); font-size:13px; }
.keys kbd { border:1px solid var(--line); border-radius:4px; padding:0 4px; font:12px ui-monospace, monospace; }
header .sp { flex:1; }
button { font:inherit; border:1px solid var(--line); background:var(--card); color:var(--ink);
  border-radius:8px; padding:5px 10px; cursor:pointer; }
button:hover { border-color:var(--dim); }
main { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px; padding:12px 16px 40px; }
@media (max-width:1300px) { main { grid-template-columns:repeat(2, minmax(0,1fr)); } }
@media (max-width:700px) { main { grid-template-columns:1fr; } }
.post { background:var(--card); border:2px solid var(--line); border-radius:14px; padding:12px;
  display:flex; flex-direction:column; gap:8px; min-width:0; cursor:pointer; position:relative;
  max-height:calc(100vh - 90px); overflow-y:auto; }
@media (max-width:700px) { .post { max-height:none; } }
.post.best { border-color:var(--best); box-shadow:0 0 0 3px color-mix(in srgb, var(--best) 25%, transparent); }
.post.worst { border-color:var(--worst); opacity:.7; }
.badge { position:absolute; top:7px; right:30px; font-size:12px; font-weight:700; padding:1px 8px;
  border-radius:10px; color:#fff; display:none; }
.post.best .badge.b { display:block; background:var(--best); }
.post.worst .badge.w { display:block; background:var(--worst); }
.num { position:absolute; top:8px; right:10px; color:var(--dim); font:600 13px ui-monospace, monospace; }
.ctx { color:var(--dim); font-size:12px; }
.who .name { font-weight:700; } .who .handle { color:var(--dim); margin-left:6px; }
.text { white-space:pre-wrap; overflow-wrap:anywhere; }
.media { display:grid; gap:4px; grid-template-columns:repeat(2, 1fr); border-radius:10px; overflow:hidden; }
.media.n1 { grid-template-columns:1fr; }
.media img, .media video { width:100%; max-height:420px; object-fit:cover; display:block; background:#0002; }
.media.n1 img, .media.n1 video { object-fit:contain; }
.quote { border:1px solid var(--line); background:var(--quote); border-radius:10px; padding:8px;
  display:flex; flex-direction:column; gap:6px; font-size:14px; }
.lcard { border:1px solid var(--line); border-radius:10px; padding:8px; font-size:13px; color:var(--dim); }
.note { border-left:3px solid var(--want); padding-left:8px; font-size:13px; color:var(--dim); }
.thread { border-left:2px solid var(--line); padding-left:10px; display:flex; flex-direction:column; gap:8px; font-size:14px; }
.meta { color:var(--dim); font-size:12px; display:flex; gap:10px; flex-wrap:wrap; margin-top:auto; }
.meta a { color:var(--dim); }
.claude { font-weight:600; }
.want { display:flex; align-items:center; gap:6px; font-size:13px; color:var(--dim); cursor:pointer; user-select:none; }
.post.wanted .want { color:var(--want); font-weight:600; }
.post.wanted { outline:2px dashed var(--want); outline-offset:3px; }
#toast { position:fixed; bottom:16px; left:50%; transform:translateX(-50%); background:var(--ink); color:var(--bg);
  padding:8px 14px; border-radius:10px; font-size:13px; opacity:0; transition:opacity .2s; pointer-events:none; }
#toast.on { opacity:.92; }
</style></head><body>
<header>
  <b>xdigest labeller</b>
  <span id="prompt"></span>
  <span class="sp"></span>
  <span class="stat" id="stat"></span>
  <span class="keys"><kbd>1</kbd>–<kbd>4</kbd> best, then worst · <kbd>⇧1</kbd>–<kbd>⇧4</kbd> want ·
    <kbd>␣</kbd> can't tell · <kbd>u</kbd> undo · <kbd>c</kbd> Claude scores</span>
  <button id="skip">Can't tell</button><button id="undo">Undo</button>
</header>
<main id="grid"></main>
<div id="toast"></div>
<script>
const $ = s => document.querySelector(s);
let batch = null, best = null, worst = null, want = new Set(), showClaude = false, busy = false;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;   // never innerHTML: post text is untrusted
  return e;
}
function safeHref(u) { try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : null; } catch { return null; } }
function link(href, text) { const a = el("a", null, text); const h = safeHref(href); if (h) { a.href = h; a.target = "_blank"; a.rel = "noopener noreferrer"; } a.onclick = e => e.stopPropagation(); return a; }
function fmt(n) { return n == null ? "–" : n >= 1e6 ? (n/1e6).toFixed(1)+"M" : n >= 1e3 ? (n/1e3).toFixed(1)+"k" : String(n); }

function media(part) {
  const seen = new Set(), imgs = [];
  for (const u of part.images || []) { const k = u.split("?")[0]; if (!seen.has(k)) { seen.add(k); imgs.push(u); } }
  const vids = [...(part.videos || [])];
  if (!imgs.length && !vids.length) return null;
  const box = el("div", "media");
  for (const u of imgs.slice(0, 4)) {
    if (u.includes("video_thumb") && vids.length) {
      const v = el("video"); v.src = vids.shift(); v.poster = u; v.muted = true; v.loop = true;
      v.controls = true; v.playsInline = true; v.preload = "none";
      v.onclick = e => e.stopPropagation(); box.append(v);
    } else {
      const i = el("img"); i.src = u; i.loading = "lazy"; i.referrerPolicy = "no-referrer"; box.append(i);
    }
  }
  for (const u of vids) { if (box.children.length >= 4) break;
    const v = el("video"); v.src = u; v.muted = true; v.loop = true; v.controls = true; v.preload = "metadata";
    v.onclick = e => e.stopPropagation(); box.append(v); }
  box.classList.add("n" + Math.min(box.children.length, 4));
  return box;
}

function who(a) { const w = el("div", "who"); w.append(el("span", "name", a?.name || "?"), el("span", "handle", "@" + (a?.handle || "?"))); return w; }

function render(p, n) {
  const c = el("div", "post"); c.dataset.id = p.id;
  c.append(el("span", "badge b", "BEST"), el("span", "badge w", "WORST"), el("span", "num", n));
  const ctx = [p.social_context, p.is_reply ? "reply" : null, p.source].filter(Boolean).join(" · ");
  if (ctx) c.append(el("div", "ctx", ctx));
  c.append(who(p.author));
  const thread = p.thread && p.thread.length ? p.thread : null;
  if (p.text) c.append(el("div", "text", p.text));
  const m = media(p); if (m) c.append(m);
  if (thread) {
    const t = el("div", "thread");
    for (const part of thread.slice(0, 6)) { if (part.text) t.append(el("div", "text", part.text)); const mm = media(part); if (mm) t.append(mm); }
    if (thread.length > 6) t.append(el("div", "ctx", `…${thread.length - 6} more in thread`));
    c.append(t);
  }
  if (p.quote) {
    const q = el("div", "quote"); q.append(who(p.quote.author));
    if (p.quote.text) q.append(el("div", "text", p.quote.text));
    const qm = media(p.quote); if (qm) q.append(qm);
    c.append(q);
  }
  if (p.card) { const lc = el("div", "lcard"); lc.append(link(p.card.href, "🔗 " + (p.card.text || p.card.href))); c.append(lc); }
  if (p.community_note) c.append(el("div", "note", "Community note: " + p.community_note));
  const mt = p.metrics || {}, meta = el("div", "meta");
  meta.append(el("span", null, `♥ ${fmt(mt.likes)}`), el("span", null, `⟲ ${fmt(mt.reposts)}`),
              el("span", null, `👁 ${fmt(mt.views)}`), link(p.url, "open on X"));
  const cl = el("span", "claude"); cl.hidden = true; meta.append(cl);
  c.append(meta);
  const w = el("label", "want"); w.append(el("span", null, "☐"), el("span", null, "want in digest"));
  w.onclick = e => { e.stopPropagation(); e.preventDefault(); toggleWant(p.id); };
  c.append(w);
  c.onclick = () => pick(p.id);
  return c;
}

function paint() {
  for (const c of document.querySelectorAll(".post")) {
    const id = c.dataset.id;
    c.classList.toggle("best", id === best); c.classList.toggle("worst", id === worst);
    c.classList.toggle("wanted", want.has(id));
    c.querySelector(".want span").textContent = want.has(id) ? "☑" : "☐";
  }
  const pr = $("#prompt");
  pr.className = best ? "worst" : "best";
  pr.textContent = !best ? "Pick the BEST post" : !worst ? "Now the WORST" : "";
}
function showScores(scores) {
  for (const c of document.querySelectorAll(".post")) {
    const s = c.querySelector(".claude"); const v = scores ? scores[c.dataset.id] : null;
    s.hidden = !(showClaude && v != null); s.textContent = v != null ? `Claude ${v}` : "";
  }
}

function load(data, label) {
  batch = data; best = label?.best || null; worst = label?.worst || null; want = new Set(label?.want || []);
  const g = $("#grid"); g.replaceChildren(...data.posts.map((p, i) => render(p, i + 1)));
  paint(); setStats(data.stats); window.scrollTo(0, 0);
  showScores(label?.claude);
}
function setStats(s) { if (s) $("#stat").textContent = `${s.screens} screens · ~${s.pairs} pairs · ${s.skipped} skipped`; }
function toast(t) { const e = $("#toast"); e.textContent = t; e.classList.add("on"); clearTimeout(toast.h); toast.h = setTimeout(() => e.classList.remove("on"), 1600); }

async function next() { const r = await fetch("/api/next"); load(await r.json()); }

function pick(id) {
  if (busy) return;
  if (id === best) best = worst = null;
  else if (!best) best = id;
  else worst = id;
  paint();
  if (best && worst) submit(false);
}
function toggleWant(id) { want.has(id) ? want.delete(id) : want.add(id); paint(); }

async function submit(skip) {
  busy = true;
  const body = { shown: batch.posts.map(p => p.id), best, worst, want: [...want], skip, sampler: batch.sampler };
  const r = await fetch("/api/label", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) { toast(j.error || "error"); busy = false; return; }
  if (showClaude) { showScores(j.claude); await new Promise(res => setTimeout(res, 900)); }
  setTimeout(async () => { await next(); busy = false; }, showClaude ? 0 : 250);
}
async function undo() {
  if (busy) return;
  const r = await fetch("/api/undo", { method: "POST" }); const j = await r.json();
  if (!r.ok) return toast(j.error);
  load({ posts: j.posts, sampler: j.label.sampler, stats: j.stats }, { want: j.label.want });
  toast("Undone: pick again");
}

document.addEventListener("keydown", e => {
  if (e.metaKey || e.ctrlKey || e.altKey || !batch) return;
  const n = "1234".indexOf(e.key), sn = "!@#$".indexOf(e.key);
  if (sn >= 0 || (e.shiftKey && e.code.startsWith("Digit"))) {
    const i = sn >= 0 ? sn : Number(e.code.slice(5)) - 1;
    if (batch.posts[i]) toggleWant(batch.posts[i].id); e.preventDefault(); return;
  }
  if (n >= 0 && batch.posts[n]) { pick(batch.posts[n].id); e.preventDefault(); }
  else if (e.key === " ") { e.preventDefault(); if (!busy) submit(true); }
  else if (e.key === "u") undo();
  else if (e.key === "c") { showClaude = !showClaude; toast(showClaude ? "Claude scores shown after each pick" : "Claude scores hidden"); }
});
$("#skip").onclick = () => !busy && submit(true);
$("#undo").onclick = undo;
next();
</script></body></html>
"""


def main() -> None:
    posts = load_posts()
    if len(posts) < 4:
        sys.exit(f"need at least 4 scored posts in {store.SCORE_LOG}")
    Handler.posts, Handler.sampler = posts, Sampler(posts)
    print(f"{len(posts)} posts; labels -> {LABELS}\nhttp://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
