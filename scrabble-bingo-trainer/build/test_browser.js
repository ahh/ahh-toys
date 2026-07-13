// End-to-end browser test: render, drag, win path, board mode, persistence.
const { chromium } = require("playwright");
const BASE = "http://localhost:8137/index.html?test";

(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  page.on("console", m => { if (m.type() === "error") errors.push("console: " + m.text()); });
  // fresh context starts with empty localStorage -- no clearing needed

  await page.goto(BASE);
  await page.waitForFunction(() => document.querySelectorAll("#board .tile").length > 0, { timeout: 8000 });

  let fail = 0;
  const ok = (c, m) => { if (!c) { console.error("FAIL:", m); fail++; } else console.log("ok:", m); };

  const centers = () => page.$$eval("#board .tile", els => els.map(e => {
    const r = e.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2, ch: e.firstChild.textContent, locked: e.classList.contains("locked") };
  }));

  // 1. rack mode renders 7 tiles
  ok((await centers()).length === 7, "rack mode shows 7 tiles");

  // 2. hint works
  await page.click("#btnHint");
  await page.waitForFunction(() => /bingo/.test(document.getElementById("message").textContent), { timeout: 3000 });
  ok(true, "hint shows bingo count");

  // 3. real pointer drag reorders tiles
  let before = await centers();
  const first = before[0], last = before[before.length - 1];
  await page.mouse.move(first.x, first.y);
  await page.mouse.down();
  await page.mouse.move(first.x + 8, first.y, { steps: 3 });
  await page.mouse.move(last.x + 40, last.y, { steps: 14 });
  await page.mouse.up();
  await page.waitForTimeout(300);
  let after = await centers();
  ok(after.map(t => t.ch).join("") !== before.map(t => t.ch).join(""), "drag reordered tiles");
  ok(after.length === 7 && new Set(after.map(t=>t.ch)).size <= 7, "still 7 tiles after drag");

  // 4. WIN path via deterministic solve
  const streak0 = +(await page.textContent("#stStreak"));
  const solved0 = +(await page.textContent("#stSolved"));
  const played = await page.evaluate(() => window.__sbt.solveFirst());
  await page.waitForFunction(() => window.__sbt.state().solved === true, { timeout: 3000 });
  const msg = await page.textContent("#message");
  ok(msg.toUpperCase().includes(played.toUpperCase()), "win message shows the word " + played);
  ok(/pts/.test(msg), "win shows score");
  ok(+(await page.textContent("#stStreak")) === streak0 + 1, "streak increments on solve");
  ok(+(await page.textContent("#stSolved")) === solved0 + 1, "solved count increments");
  ok(!(await page.$eval("#btnNext", b => b.hidden)), "Next button appears after win");

  // 5. board mode: 8 tiles, exactly one locked, and it's solvable
  await page.evaluate(() => { document.querySelector("details.settings").open = true; });
  await page.click('#modeSeg button[data-mode="board"]');
  await page.waitForFunction(() => document.querySelectorAll("#board .tile").length === 8, { timeout: 3000 });
  let bt = await centers();
  ok(bt.length === 8, "board mode shows 8 tiles");
  ok(bt.filter(t => t.locked).length === 1, "exactly one locked board tile");
  const bstate = await page.evaluate(() => window.__sbt.state());
  ok(bstate.reachable.length >= 1, "board rack has a reachable solution");
  const bplayed = await page.evaluate(() => window.__sbt.solveFirst());
  await page.waitForFunction(() => window.__sbt.state().solved === true, { timeout: 3000 });
  ok(bplayed.length === 8, "board solution is 8 letters (" + bplayed + ")");

  // 6. give up resets streak
  await page.click("#btnNext");
  await page.waitForTimeout(150);
  await page.click("#btnReveal");
  await page.waitForFunction(() => !document.getElementById("btnNext").hidden, { timeout: 3000 });
  ok(+(await page.textContent("#stStreak")) === 0, "streak resets on give up");

  // 7. persistence across reload
  const solvedBefore = await page.textContent("#stSolved");
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll("#board .tile").length > 0, { timeout: 8000 });
  ok((await page.textContent("#stSolved")) === solvedBefore, "stats persist across reload (" + solvedBefore + ")");

  ok(errors.length === 0, "no page errors" + (errors.length ? " -> " + errors.join(" | ") : ""));

  await browser.close();
  console.log(fail ? `\n${fail} BROWSER CHECK(S) FAILED` : "\nALL BROWSER CHECKS PASSED");
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
