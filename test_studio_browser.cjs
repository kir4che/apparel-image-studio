const {chromium} = require('/Users/moinmoin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const crypto = require('crypto');

(async () => {
  const root = process.cwd(), out = fs.mkdtempSync(path.join(root, 'work/browser-check-'));
  const source = '/Users/moinmoin/Downloads/原圖';
  const hashes = () => Object.fromEntries(fs.readdirSync(source).filter(n => n.endsWith('.jpeg')).map(n => [n, crypto.createHash('sha256').update(fs.readFileSync(path.join(source, n))).digest('hex')]));
  const before = hashes();
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  const page = await browser.newPage({viewport:{width:1440,height:1100},acceptDownloads:true});
  const errors=[], external=[], checks=[];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('request', r=>{if (!r.url().startsWith('http://127.0.0.1:8765/') && !r.url().startsWith('data:')) external.push(r.url());});
  page.on('dialog', d=>d.accept());
  const state = async()=> (await page.request.get('http://127.0.0.1:8765/api/state')).json();
  const saved = async()=>page.waitForFunction(()=>document.querySelector('#save-status').textContent==='已在本機保存');
  const preview = async()=>{await page.waitForFunction(()=>{const el=document.querySelector('#preview');return !el.hidden&&el.complete&&el.naturalWidth>0&&!document.querySelector('#preview-stage').hasAttribute('aria-busy');});};
  const pick = n=>page.getByRole('button',{name:`選擇 下載 (${n}).jpeg`,exact:true}).click();
  try {
    await page.goto('http://127.0.0.1:8765/');
    await page.getByText('匯入照片後，勾選兩張開始配對',{exact:true}).waitFor();
    assert.equal((await state()).photos.length,0,'Do not overwrite an existing session during testing');
    assert.equal(await page.locator('#export').isDisabled(),true);
    checks.push('空白狀態禁止匯出');
    await page.locator('#sample').click();
    await page.getByText('已匯入 22 張，略過 1 張完全重複照片',{exact:true}).waitFor({timeout:90000});
    assert.equal(await page.locator('.photo').count(),22);
    assert.equal((await state()).photos.find(p=>p.name==='下載 (1).jpeg').suggested_top,101);
    checks.push('23 張原圖匯入為 22 張，原圖 1 與 23 完全重複，裁線快取讀取正確');
    await pick(1); await pick(22); await preview();
    await page.locator('#swap').click();
    await page.locator('#add-pair').click(); await saved();
    let data=await state(); assert.equal(data.groups.length,1);
    assert.equal(data.groups[0].left.photo,data.photos.find(p=>p.name==='下載 (22).jpeg').id);
    checks.push('勾選兩張、自動預覽、交換左右、加入一組');
    await pick(2); await pick(3); await preview();
    await page.locator('#add-pair').click(); await saved();
    assert.equal((await state()).groups.length,2);
    await page.locator('#crop-details summary').click();
    const line=page.getByRole('slider',{name:'左圖照片上的裁線',exact:true});
    await line.focus(); await line.press('Shift+ArrowDown'); await saved();
    assert.equal((await state()).groups[1].left.top,10);
    await page.getByRole('checkbox',{name:'左圖裁上緣',exact:true}).uncheck(); await saved();
    assert.equal((await state()).groups[1].left.cut,false);
    await page.locator('#remove-pair').click(); await saved();
    assert.equal((await state()).groups.length,1);
    await pick(2); await pick(3); await page.locator('#add-pair').click(); await saved();
    checks.push('調整裁線、停用裁切、解除配對、重新加入');
    await page.locator('#files-input').setInputFiles([
      {name:'下載 (1).jpeg',mimeType:'image/jpeg',buffer:fs.readFileSync(path.join(source,'下載 (1).jpeg'))},
      {name:'損壞圖片.png',mimeType:'image/png',buffer:Buffer.from('broken image fixture')}
    ]);
    await page.getByText('已匯入 0 張，略過 1 張完全重複照片，另有 1 個檔案未匯入',{exact:true}).waitFor();
    assert.equal((await state()).photos.length,22);
    checks.push('中文檔名、檔案選擇、損壞圖片提示、重複匯入');
    const exportResult = async format=>{
      await page.locator('#format').selectOption(format); await saved();
      const response=page.waitForResponse(r=>r.url().endsWith('/api/export')&&r.request().method()==='POST');
      await page.locator('#export').click(); const result=await (await response).json();
      await page.locator('#export-result').waitFor({state:'visible'});
      assert.equal(result.count,2);
      for(const r of result.results) {assert(r.bytes>0);assert.equal(r.upscaled,false); if(format==='square')assert.deepEqual(r.size,[1024,1024]);else assert.equal(r.padding,false);}
      return result;
    };
    const square=await exportResult('square'), natural=await exportResult('natural');
    assert.notEqual(square.folder,natural.folder);
    checks.push('正方形與自然比例各匯出 2 張、保留原尺寸、全新批次、ZIP 下載');
    await page.reload(); await page.getByText('已還原上次工作，勾選照片或點下方配對繼續',{exact:true}).waitFor();
    assert.equal(await page.locator('.pair-card').count(),2); assert.equal(await page.locator('#format').inputValue(),'natural');
    await page.getByRole('button',{name:'編輯第 1 組',exact:true}).click(); await preview();
    await page.locator('#crop-details summary').click();
    const rect=await page.getByRole('slider',{name:'左圖照片上的裁線',exact:true}).boundingBox();
    await page.mouse.move(rect.x+rect.width/2,rect.y+rect.height/2);await page.mouse.down();await page.mouse.move(rect.x+rect.width/2,rect.y+rect.height/2+4);await page.mouse.up();await saved();
    assert((await state()).groups[0].left.top>198);
    await page.getByRole('button',{name:'左圖還原裁線建議',exact:true}).click(); await saved();
    checks.push('重新整理還原 22 張照片與兩組配對；拖動照片裁線與還原建議');
    await preview(); await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile horizontal overflow');
    checks.push('390 px 手機寬度無水平溢出');
    assert.deepEqual(hashes(),before); assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    checks.push('原圖 SHA-256 完全不變、瀏覽器無程式錯誤、所有頁面請求僅到 127.0.0.1');
    const report={checks,consoleErrors:errors,externalRequests:external,sourceHashes:before,square,natural,screenshots:out};
    fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
