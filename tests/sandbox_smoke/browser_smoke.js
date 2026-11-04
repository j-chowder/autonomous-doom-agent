// Controller-owned. Runs inside the toolchain container (no network). Exit 1 = environment invalid.
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const bytes = Array.from(fs.readFileSync('/out/hello.wasm'));
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.setContent('<canvas id="c" width="4" height="4"></canvas>');
    const r = await page.evaluate((b) => ({
      product: 6 * 7,
      valid: WebAssembly.validate(new Uint8Array(b)),
    }), bytes);
    if (r.product !== 42 || r.valid !== true) {
      console.error('browser smoke failed', JSON.stringify(r));
      process.exit(1);
    }
    console.log('ok: chromium runs; WebAssembly.validate(hello.wasm) =', r.valid);
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
