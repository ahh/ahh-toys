/* Scrabble Bingo Trainer -- vanilla, offline, no deps.
 *
 * Pipeline:
 *   words7.js / words8.js ship raw newline word lists.
 *   At load we build, per length: a Set (validation), an alphagram->words map
 *   (answers), and a list of alphagrams ranked by draw-probability from the real
 *   Scrabble bag (so "common" difficulty surfaces the bingos you actually draw).
 *   Racks are generated *backwards* from a chosen word, so a bingo always exists.
 *   Missed racks re-enter a Leitner spaced-repetition queue kept in localStorage.
 */
(function () {
  "use strict";

  // ---- Scrabble constants -------------------------------------------------
  var BAG = { A:9,B:2,C:2,D:4,E:12,F:2,G:3,H:2,I:9,J:1,K:1,L:4,M:2,N:6,
              O:8,P:2,Q:1,R:6,S:4,T:6,U:4,V:2,W:2,X:1,Y:2,Z:1 };
  var PTS = { A:1,B:3,C:3,D:2,E:1,F:4,G:2,H:4,I:1,J:8,K:5,L:1,M:3,N:1,
              O:1,P:3,Q:10,R:1,S:1,T:1,U:1,V:4,W:4,X:8,Y:4,Z:10 };

  function choose(n, k) {           // n-choose-k, small numbers
    if (k < 0 || k > n) return 0;
    var r = 1;
    for (var i = 0; i < k; i++) r = r * (n - i) / (i + 1);
    return r;
  }
  function alphagram(w) { return w.split("").sort().join(""); }

  // Probability weight of drawing this exact multiset of tiles from a fresh bag.
  // Proportional to prod C(bagcount(letter), needed). Words needing more of a
  // letter than the bag holds (only drawable via blanks) get weight 0 -> ranked
  // last, so they only appear at high difficulty.
  function drawWeight(alpha) {
    var counts = {}, ch, i;
    for (i = 0; i < alpha.length; i++) { ch = alpha[i]; counts[ch] = (counts[ch]||0)+1; }
    var w = 1;
    for (ch in counts) {
      w *= choose(BAG[ch.toUpperCase()] || 0, counts[ch]);
      if (w === 0) return 0;
    }
    return w;
  }

  // ---- Word index ---------------------------------------------------------
  var INDEX = {};   // len -> { set, map(alpha->[words]), ranked([alpha,...]) }
  function buildIndex(len, raw) {
    var words = raw.split("\n");
    var set = new Set(words);
    var map = Object.create(null);
    for (var i = 0; i < words.length; i++) {
      var a = alphagram(words[i]);
      (map[a] || (map[a] = [])).push(words[i]);
    }
    var alphas = Object.keys(map);
    var weight = Object.create(null);
    for (i = 0; i < alphas.length; i++) weight[alphas[i]] = drawWeight(alphas[i]);
    alphas.sort(function (x, y) { return weight[y] - weight[x]; });
    INDEX[len] = { set: set, map: map, ranked: alphas };
  }

  // ---- Spaced repetition (Leitner) ---------------------------------------
  // Each item keyed by alphagram. box 0..5 (5 = mastered). Due measured in a
  // persistent global "turn" counter so review works within and across sessions.
  var LS = "sbt.v1";
  var INTERVAL = [2, 6, 15, 40, 100, 100000];  // turns until due per box
  var store = load();
  function load() {
    try {
      var s = JSON.parse(localStorage.getItem(LS));
      if (s && s.srs) return s;
    } catch (e) {}
    return { turn: 0, srs: {}, stats: { solved:0, revealed:0, streak:0, best:0 },
             settings: { mode:"rack", reach:12 } };
  }
  var saveTimer = null;
  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      try { localStorage.setItem(LS, JSON.stringify(store)); } catch (e) {}
    }, 120);
  }

  function dueItems(len) {
    var out = [];
    for (var a in store.srs) {
      var it = store.srs[a];
      if (a.length === len && it.box < 5 && it.due <= store.turn) out.push(a);
    }
    out.sort(function (x, y) { return store.srs[x].due - store.srs[y].due; });
    return out;
  }
  function recordResult(alpha, ok) {
    var it = store.srs[alpha] || (store.srs[alpha] = { box:0, due:0, seen:0, miss:0 });
    it.seen++;
    if (ok) { it.box = Math.min(5, it.box + 1); }
    else { it.box = 0; it.miss++; }
    it.due = store.turn + INTERVAL[it.box];
    save();
  }

  // ---- Round state --------------------------------------------------------
  var mode = store.settings.mode;   // 'rack' | 'board'
  var len = mode === "board" ? 8 : 7;
  var cur = null;                   // { alpha, solutions:Set, reachable:[], tiles, order, lockPos, lockTile }
  var solved = false, revealed = false, hintLevel = 0;
  var tileSeq = 0;

  function randInt(n) { return Math.floor(Math.random() * n); }
  function pick(arr) { return arr[randInt(arr.length)]; }
  function shuffled(arr) {
    arr = arr.slice();
    for (var i = arr.length - 1; i > 0; i--) {
      var j = randInt(i + 1), t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }

  function pickFreshAlpha() {
    var ranked = INDEX[len].ranked;
    var reach = store.settings.reach / 100;           // fraction of pool in play
    var top = Math.max(1, Math.floor(ranked.length * reach));
    var r = Math.random();
    var idx = Math.floor(top * r * r);                // bias toward the common end
    return ranked[Math.min(idx, ranked.length - 1)];
  }

  function chooseAlpha() {
    var due = dueItems(len);
    // Review probability scales with how much is due, capped so new words keep coming.
    var pReview = Math.min(0.6, due.length / (due.length + 4));
    if (due.length && Math.random() < pReview) return due[0];
    // avoid immediately re-serving something already mastered/queued far out
    for (var tries = 0; tries < 6; tries++) {
      var a = pickFreshAlpha();
      var it = store.srs[a];
      if (!it || it.due <= store.turn + INTERVAL[1]) return a;
    }
    return pickFreshAlpha();
  }

  function newRound() {
    store.turn++;
    solved = false; revealed = false; hintLevel = 0;
    len = mode === "board" ? 8 : 7;
    var alpha = chooseAlpha();
    var solutions = INDEX[len].map[alpha];
    cur = { alpha: alpha, solutions: new Set(solutions), lockPos: -1, lockTile: null };

    var letters;
    if (mode === "board") {
      var w = pick(solutions);
      var i = 1 + randInt(len - 2);   // interior: you play *through* a tile
      cur.lockPos = i;
      cur.lockTile = mkTile(w[i], true);
      cur.reachable = solutions.filter(function (s) { return s[i] === w[i]; });
      letters = (w.slice(0, i) + w.slice(i + 1)).split("");
    } else {
      cur.reachable = solutions;
      letters = alpha.split("");
    }
    cur.order = shuffled(letters).map(function (ch) { return mkTile(ch, false); });
    save();
    render();
    updateStats();
    setMessage("");
    prompt.innerHTML = mode === "board"
      ? 'Play all 7 rack tiles through the <b>board</b> tile to make an 8-letter word.'
      : 'Rearrange all 7 tiles into a <b>bingo</b>.';
    btnNext.hidden = true;
    btnReveal.disabled = false; btnHint.disabled = false;
  }
  function mkTile(ch, locked) { return { id: ++tileSeq, ch: ch, locked: !!locked }; }

  // current arrangement, left to right
  function spelled() {
    var out = "", oi = 0;
    for (var p = 0; p < len; p++) {
      if (p === cur.lockPos) out += cur.lockTile.ch;
      else out += cur.order[oi++].ch;
    }
    return out;
  }
  function wordScore(w) {
    var s = 0;
    for (var i = 0; i < w.length; i++) s += PTS[w[i].toUpperCase()] || 0;
    return s + 50; // bingo bonus
  }

  // ---- Rendering ----------------------------------------------------------
  var board = document.getElementById("board");
  var prompt = document.getElementById("prompt");
  var message = document.getElementById("message");
  var btnShuffle = document.getElementById("btnShuffle");
  var btnHint = document.getElementById("btnHint");
  var btnReveal = document.getElementById("btnReveal");
  var btnNext = document.getElementById("btnNext");

  function render() {
    board.innerHTML = "";
    var oi = 0;
    for (var p = 0; p < len; p++) {
      var tile = (p === cur.lockPos) ? cur.lockTile : cur.order[oi++];
      board.appendChild(tileEl(tile, p === cur.lockPos ? -1 : oi - 1));
    }
  }
  function tileEl(tile, orderIndex) {
    var el = document.createElement("div");
    el.className = "tile" + (tile.locked ? " locked" : "");
    el.dataset.oi = orderIndex;
    el.dataset.id = tile.id;
    el.textContent = tile.ch;
    var pts = document.createElement("span");
    pts.className = "pts";
    pts.textContent = PTS[tile.ch.toUpperCase()];
    el.appendChild(pts);
    if (!tile.locked) attachDrag(el, tile);
    return el;
  }

  function setMessage(html) { message.innerHTML = html; }

  function onArranged() {
    if (solved || revealed) return;
    var w = spelled();
    if (cur.solutions.has(w)) win(w);
  }

  function win(w) {
    solved = true;
    recordResult(cur.alpha, true);
    store.stats.solved++;
    store.stats.streak++;
    store.stats.best = Math.max(store.stats.best, store.stats.streak);
    save();
    [].forEach.call(board.children, function (el, i) {
      setTimeout(function () { el.classList.add("win"); }, i * 45);
    });
    var also = cur.reachable.filter(function (s) { return s !== w; });
    var alsoHtml = also.length
      ? '<div class="also">also valid: <b>' + also.slice(0, 12).join("</b> &middot; <b>") + "</b>"
        + (also.length > 12 ? " &hellip;" : "") + "</div>"
      : '<div class="also">the only bingo here</div>';
    setMessage('<div class="word good">' + w.toUpperCase() + "</div>"
      + '<div class="score">' + wordScore(w) + " pts with the bingo bonus</div>" + alsoHtml);
    finishRound();
  }

  function reveal() {
    if (solved || revealed) return;
    revealed = true;
    recordResult(cur.alpha, false);
    store.stats.streak = 0;
    save();
    var list = cur.reachable.slice().sort();
    setMessage('<div class="word">' + list.slice(0, 1).join("").toUpperCase() + "</div>"
      + '<div class="also">' + list.length + " playable here: <b>"
      + list.slice(0, 16).join("</b> &middot; <b>") + "</b>"
      + (list.length > 16 ? " &hellip;" : "") + "</div>");
    finishRound();
  }

  function finishRound() {
    btnNext.hidden = false;
    btnReveal.disabled = true; btnHint.disabled = true;
    updateStats();
    renderBoxes();
  }

  function hint() {
    if (solved || revealed) return;
    hintLevel++;
    var sol = cur.reachable.slice().sort();
    var n = sol.length;
    if (hintLevel === 1) {
      setMessage('<div class="hint">' + n + (n === 1 ? " bingo" : " bingos") + " hiding in these tiles.</div>");
    } else {
      var nShown = Math.min(hintLevel - 1, len - 1);
      var w = sol[0];
      var shown = w.slice(0, nShown).toUpperCase() + "·".repeat(len - nShown);
      setMessage('<div class="hint">starts with <span class="word">' + shown + "</span></div>");
    }
  }

  // ---- Stats / boxes ------------------------------------------------------
  function updateStats() {
    document.getElementById("stStreak").textContent = store.stats.streak;
    document.getElementById("stSolved").textContent = store.stats.solved;
    document.getElementById("stDue").textContent = dueItems(len).length;
  }
  function renderBoxes() {
    var counts = [0,0,0,0,0,0];
    for (var a in store.srs) counts[store.srs[a].box]++;
    var max = Math.max(1, Math.max.apply(null, counts));
    var host = document.getElementById("boxes");
    host.innerHTML = "";
    var labels = ["new","","","","","known"];
    for (var b = 0; b < 6; b++) {
      var d = document.createElement("div");
      d.className = "b";
      d.style.height = (6 + 28 * counts[b] / max) + "px";
      d.title = counts[b] + " words in box " + b;
      var s = document.createElement("span"); s.textContent = labels[b] || counts[b];
      if (counts[b] && !labels[b]) s.textContent = counts[b];
      d.appendChild(s);
      host.appendChild(d);
    }
  }

  // ---- Drag engine (pointer events, insert-reorder with gap preview) ------
  var drag = null;
  function attachDrag(el, tile) {
    el.addEventListener("pointerdown", function (e) {
      if (solved || revealed || drag) return;
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      var opens = [].filter.call(board.children, function (c) { return !c.classList.contains("locked"); });
      var base = opens.map(function (c) { var r = c.getBoundingClientRect(); return { left: r.left, w: r.width, mid: r.left + r.width / 2 }; });
      var oi = opens.indexOf(el);
      drag = { el: el, pid: e.pointerId, opens: opens, base: base, oi: oi,
               startX: e.clientX, target: oi, moved: false };
      el.classList.add("dragging");
    });
    el.addEventListener("pointermove", function (e) {
      if (!drag || drag.pid !== e.pointerId) return;
      var dx = e.clientX - drag.startX;
      if (Math.abs(dx) > 3) drag.moved = true;
      drag.el.style.transform = "translateX(" + dx + "px)";
      // insertion target = # of non-dragged open tiles whose midpoint is left of pointer
      var t = 0;
      for (var i = 0; i < drag.base.length; i++) {
        if (i === drag.oi) continue;
        if (drag.base[i].mid < e.clientX) t++;
      }
      if (t !== drag.target) { drag.target = t; applyGap(); }
    });
    function end(e) {
      if (!drag || drag.pid !== e.pointerId) return;
      var d = drag; drag = null;
      d.el.classList.remove("dragging");
      // commit new order
      var moving = cur.order[d.oi];
      cur.order.splice(d.oi, 1);
      cur.order.splice(d.target, 0, moving);
      render();
      onArranged();
    }
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }
  function applyGap() {
    var d = drag;
    for (var k = 0; k < d.opens.length; k++) {
      if (k === d.oi) continue;
      var p = k < d.oi ? k : k - 1;         // index after removing the dragged tile
      var mNew = p >= d.target ? p + 1 : p;  // index after reinserting at target
      var shift = d.base[mNew].left - d.base[k].left;
      d.opens[k].style.transform = "translateX(" + shift + "px)";
    }
  }

  // ---- Controls -----------------------------------------------------------
  btnShuffle.addEventListener("click", function () {
    if (solved || revealed) return;
    cur.order = shuffled(cur.order);
    render();
  });
  btnHint.addEventListener("click", hint);
  btnReveal.addEventListener("click", reveal);
  btnNext.addEventListener("click", newRound);

  // Settings
  var modeSeg = document.getElementById("modeSeg");
  [].forEach.call(modeSeg.children, function (b) {
    if (b.dataset.mode === mode) setSeg(b); else b.classList.remove("on");
    b.addEventListener("click", function () {
      mode = b.dataset.mode;
      store.settings.mode = mode; save();
      setSeg(b);
      newRound();
    });
  });
  function setSeg(on) { [].forEach.call(modeSeg.children, function (b) { b.classList.toggle("on", b === on); }); }

  var reach = document.getElementById("reach");
  var reachVal = document.getElementById("reachVal");
  reach.value = store.settings.reach;
  function reachLabel(v) {
    if (v <= 8) return "very common";
    if (v <= 20) return "common";
    if (v <= 45) return "mixed";
    if (v <= 75) return "tricky";
    return "obscure";
  }
  function syncReach() { reachVal.textContent = reachLabel(+reach.value); }
  reach.addEventListener("input", function () { store.settings.reach = +reach.value; syncReach(); save(); });
  syncReach();

  document.getElementById("btnReset").addEventListener("click", function () {
    if (!confirm("Erase all learning progress and stats?")) return;
    store = { turn: 0, srs: {}, stats: { solved:0, revealed:0, streak:0, best:0 },
              settings: store.settings };
    save(); updateStats(); renderBoxes(); newRound();
  });

  // ---- Boot ---------------------------------------------------------------
  buildIndex(7, window.WORDS7);
  buildIndex(8, window.WORDS8);
  window.WORDS7 = window.WORDS8 = null; // free the raw strings
  renderBoxes();
  newRound();

  // Test-only introspection, enabled with ?test in the URL. No prod surface.
  if (location.search.indexOf("test") >= 0) {
    window.__sbt = {
      state: function () {
        return { len: len, mode: mode, spelled: spelled(), solved: solved,
                 reachable: cur.reachable.slice(), lockPos: cur.lockPos };
      },
      // Arrange the open tiles into the first reachable solution and fire the
      // real onArranged() win path.
      solveFirst: function () {
        var w = cur.reachable[0], want = [], p;
        for (p = 0; p < len; p++) if (p !== cur.lockPos) want.push(w[p]);
        var pool = cur.order.slice();
        cur.order = want.map(function (ch) {
          for (var i = 0; i < pool.length; i++) if (pool[i].ch === ch) return pool.splice(i, 1)[0];
        });
        render();
        onArranged();
        return spelled();
      }
    };
  }
})();
