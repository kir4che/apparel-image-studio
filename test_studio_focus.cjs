const {chromium} = require('/Users/moinmoin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('path');
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1100}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:8765/');
    const first=page.locator('.photo').first();
    await first.focus();await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('photo')),true);
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('photo')),true);
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('photo')),true);
    assert.equal(await page.locator('.photo[aria-pressed=true]').count(),2);
    await page.locator('#clear-selection').click();
    await page.getByRole('button',{name:'編輯第 1 組',exact:true}).focus();await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'編輯第 1 組');
    await page.locator('#crop-details summary').click();
    await page.waitForFunction(()=>{const img=document.querySelector('#preview');return !img.hidden&&img.complete&&img.naturalWidth>0&&!document.querySelector('#preview-stage').hasAttribute('aria-busy');});
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(process.cwd(),'work/browser-check-sqktw2/desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(process.cwd(),'work/browser-check-sqktw2/mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.deepEqual(errors,[]);
    console.log('PASS: selected-photo focus retained; Tab moves to next photo; saved-pair focus retained; desktop/mobile recaptured; no page errors or horizontal overflow.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
