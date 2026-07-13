// Verify the trainer works when served from a /bingo/ subpath, and offline there.
const { chromium } = require("playwright");
const ROOT = "http://localhost:8138";
(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  page.on("console", m => { if (m.type() === "error") errors.push("console: " + m.text()); });
  let fail = 0;
  const ok = (c, m) => { if (!c) { console.error("FAIL:", m); fail++; } else console.log("ok:", m); };

  // landing page links to ./bingo/
  await page.goto(ROOT + "/");
  const href = await page.getAttribute("a.toy", "href");
  ok(href === "./bingo/", "landing links to ./bingo/ (got " + href + ")");
  const iconOk = await page.evaluate(() => {
    const img = document.querySelector("a.toy img");
    return img && img.complete && img.naturalWidth > 0;
  });
  ok(iconOk, "landing shows the trainer icon");

  // trainer loads at the subpath
  await page.goto(ROOT + "/bingo/");
  await page.waitForFunction(() => document.querySelectorAll("#board .tile").length === 7, { timeout: 8000 });
  ok(true, "trainer renders a 7-tile rack at /bingo/");

  // manifest + icons resolve under the subpath (no 404s)
  const assets = await page.evaluate(async () => {
    const urls = ["manifest.webmanifest", "icon.svg", "apple-touch-icon.png", "data/words8.js", "sw.js"];
    const res = {};
    for (const u of urls) { try { res[u] = (await fetch(u)).status; } catch (e) { res[u] = "ERR"; } }
    return res;
  });
  for (const u in assets) ok(assets[u] === 200, `asset ${u} -> ${assets[u]}`);

  // service worker registers at the subpath scope, then works offline
  await page.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.controller, { timeout: 8000 }).catch(()=>{});
  await page.waitForTimeout(1500);
  const scope = await page.evaluate(() => navigator.serviceWorker.controller && navigator.serviceWorker.controller.scriptURL);
  ok(scope && scope.endsWith("/bingo/sw.js"), "SW registered at /bingo/ scope (" + scope + ")");
  await ctx.setOffline(true);
  await page.reload();
  const off = await page.waitForFunction(() => document.querySelectorAll("#board .tile").length === 7, { timeout: 8000 })
    .then(()=>true).catch(()=>false);
  ok(off, "trainer works OFFLINE at /bingo/");

  ok(errors.length === 0, "no page errors" + (errors.length ? " -> " + errors.join(" | ") : ""));
  await browser.close();
  console.log(fail ? `\n${fail} SUBPATH CHECK(S) FAILED` : "\nALL SUBPATH CHECKS PASSED");
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
