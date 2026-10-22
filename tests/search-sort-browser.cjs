const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.PIONEER_SCREEN_URL||'http://127.0.0.1:8786');
 await page.waitForSelector('[data-radar-open]',{timeout:30000});
 for(const sort of ['delta','percent','recent','name','score']){
  const response=page.waitForResponse(r=>r.url().includes('/api/screen?')&&r.url().includes('sort='+sort));
  await page.selectOption('#radar-sort',sort);await response;
  await page.waitForFunction(()=>document.querySelector('.discovery-table tbody tr'));
 }
 await page.locator('[data-main-view="detail"]').click();
 await page.locator('#manual-name').fill('caffeine');
 await page.locator('#manual-start').fill('2025-10-23');await page.locator('#manual-end').fill('2026-10-22');
 await page.locator('#manual-submit').click();await page.waitForSelector('#manual-add',{timeout:60000});
 assert.ok((await page.locator('#manual-message').innerText()).includes('검색'));
 await page.locator('#manual-add').click();
 await page.waitForFunction(()=>document.querySelector('#watchlist')?.textContent.includes('직접 검색'));
 assert.equal(await page.locator('#chemical-name').innerText(),'caffeine');
 assert.ok(await page.locator('#papers .paper').count()>0);
 await page.reload();await page.locator('[data-main-view="detail"]').click();
 await page.waitForFunction(()=>document.querySelector('#watchlist')?.textContent.includes('caffeine'));
 assert.equal(await page.locator('#watchlist .watch').count(),1);
 await page.screenshot({path:'.data/direct-chemical-search.png'});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.locator('[data-manual-remove]').click();
 await page.waitForFunction(()=>document.querySelectorAll('#watchlist .watch').length===0);
 assert.deepEqual(errors,[]);await browser.close();console.log('sorting and real direct search browser check passed');
})().catch(e=>{console.error(e);process.exit(1)});
