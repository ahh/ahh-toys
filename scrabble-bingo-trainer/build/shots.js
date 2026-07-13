const { chromium } = require("playwright");
const dir = "/tmp/claude-0/-home-user-ahh-toys/d8e032ec-4bcc-543f-9907-c6f8000c5cca/scratchpad/";
(async () => {
  const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: scheme });
    const page = await ctx.newPage();
    // rack mode, mid-solve + a revealed win
    await page.goto("http://localhost:8137/index.html?test");
    await page.waitForFunction(() => document.querySelectorAll("#board .tile").length > 0);
    await page.evaluate(() => window.__sbt.solveFirst());
    await page.waitForTimeout(600);
    await page.screenshot({ path: dir + `shot-rack-${scheme}.png` });
    // board mode fresh puzzle
    await page.click("#btnNext");
    await page.evaluate(() => { document.querySelector("details.settings").open = true; });
    await page.click('#modeSeg button[data-mode="board"]');
    await page.waitForFunction(() => document.querySelectorAll("#board .tile").length === 8);
    await page.evaluate(() => { document.querySelector("details.settings").open = false; });
    await page.waitForTimeout(200);
    await page.screenshot({ path: dir + `shot-board-${scheme}.png` });
    await ctx.close();
  }
  await browser.close();
  console.log("shots done");
})();
