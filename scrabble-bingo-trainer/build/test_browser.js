// End-to-end browser test: render, drag, hint-places-a-tile, win path,
// board mode + fit, persistence.
const { chromium } = require("playwright");
const BASE = "http://localhost:8137/index.html?test";

(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  page.on("console", m => { if (m.type() === "error") errors.push("console: " + m.text()); });

  await page.goto(BASE);
  await page.waitForFunction(() => document.querySelectorAll("#board .tile").length > 0, { timeout: 8000 });

  let fail = 0;
  const ok = (c, m) => { if (!c) { console.error("FAIL:", m); fail++; } else console.log("ok:", m); };
  const centers = () => page.$$eval("#board .tile", els => els.map(e => {
    const r = e.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2, ch: e.firstChild.textContent,
             locked: e.classList.contains("locked"), hint: e.classList.contains("hint") };
  }));

  // 1. rack mode renders 7 tiles
  ok((await centers()).length === 7, "rack mode shows 7 tiles");

  // 2. real pointer drag reorders tiles (before any hint, so it's a clean rack)
  let before = await centers();
  const first = before[0], last = before[before.length - 1];
  await page.mouse.move(first.x, first.y);
  await page.mouse.down();
  await page.mouse.move(first.x + 8, first.y, { steps: 3 });
  await page.mouse.move(last.x + 40, last.y, { steps: 14 });
  await page.mouse.up();
  await page.waitForTimeout(300);
  ok((await centers()).map(t => t.ch).join("") !== before.map(t => t.ch).join(""), "drag reordered tiles");

  // 3. Hint places one correct tile into a slot and locks it
  await page.click("#btnHint");
  await page.waitForFunction(() => document.querySelectorAll("#board .tile.hint").length === 1, { timeout: 3000 });
  const hintTiles = (await centers()).filter(t => t.hint);
  ok(hintTiles.length === 1, "hint placed exactly one tile");
  ok(hintTiles.every(t => t.hint), "hinted tile is styled as a hint");
  const hintFixed = await page.$$eval("#board .tile.hint", els => els.every(e => e.classList.contains("fixed")));
  ok(hintFixed, "hinted tile is fixed (not draggable)");
  ok(/placed/.test(await page.textContent("#message")), "hint message notes a placed letter");

  // 4. WIN path (with a hint used) via deterministic solve
  const streak0 = +(await page.textContent("#stStreak"));
  const solved0 = +(await page.textContent("#stSolved"));
  const played = await page.evaluate(() => window.__sbt.solveFirst());
  await page.waitForFunction(() => window.__sbt.state().solved === true, { timeout: 3000 });
  const msg = await page.textContent("#message");
  ok(msg.toUpperCase().includes(played.toUpperCase()), "win message shows the word " + played);
  ok(/pts/.test(msg), "win shows score");
  ok(/with hints/i.test(msg), "win notes that hints were used");
  ok(+(await page.textContent("#stStreak")) === streak0 + 1, "streak increments on solve");
  ok(+(await page.textContent("#stSolved")) === solved0 + 1, "solved count increments");
  ok(!(await page.$eval("#btnNext", b => b.hidden)), "Next button appears after win");

  // 5. board mode: 8 tiles, one locked, fits the screen, solvable, clean win
  await page.click("#btnNext");
  await page.evaluate(() => { document.querySelector("details.settings").open = true; });
  await page.click('#modeSeg button[data-mode="board"]');
  await page.waitForFunction(() => document.querySelectorAll("#board .tile").length === 8, { timeout: 3000 });
  await page.evaluate(() => { document.querySelector("details.settings").open = false; });
  let bt = await centers();
  ok(bt.length === 8, "board mode shows 8 tiles");
  ok(bt.filter(t => t.locked).length === 1, "exactly one locked board tile");
  const overflow = await page.evaluate(() => document.body.scrollWidth - window.innerWidth);
  ok(overflow <= 1, "8-tile row does not overflow the viewport (overflow=" + overflow + "px)");
  const rightmost = Math.max(...bt.map(t => t.x));
  ok(rightmost < 390, "rightmost tile is on-screen");
  const bplayed = await page.evaluate(() => window.__sbt.solveFirst());
  await page.waitForFunction(() => window.__sbt.state().solved === true, { timeout: 3000 });
  ok(bplayed.length === 8, "board solution is 8 letters (" + bplayed + ")");
  ok(!/with hints/i.test(await page.textContent("#message")), "clean board win is not marked as hinted");

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
