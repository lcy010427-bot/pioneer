// Called by deployment-check.py only while its isolated TLS fixture is running.
const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--host-resolver-rules=MAP pioneer.localhost 127.0.0.1, MAP admin.pioneer.localhost 127.0.0.1']});
 const errors=[];const ctx=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000}});const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto('https://pioneer.localhost:19443');await page.waitForSelector('[data-radar-open]');
 assert.ok((await page.locator('#site-access').innerText()).includes('공개 조회'));
 assert.equal(await page.locator('#site-access a').getAttribute('href'),'https://admin.pioneer.localhost:19443');
 for(const id of ['radar-save','radar-refresh','radar-corpus-scan'])assert.equal(await page.locator('#'+id).isDisabled(),true);
 await page.locator('#radar-search').fill('HFPO');await page.waitForFunction(()=>radarData.options.search==='HFPO');assert.equal(await page.locator('.radar-table tbody tr').count(),1);
 await page.locator('[data-radar-period="1y"]').click();await page.waitForFunction(()=>radarData.options.unit==='month');
 await page.locator('[data-main-view="detail"]').click();await page.waitForSelector('.cell-button',{state:'attached'});
 await page.locator('[data-tab="compare"]').click();await page.waitForFunction(()=>document.querySelector('#compare-result')?.textContent.includes('정량 비교를 보류'));
 for(const tab of ['observe','context','questions','compare','candidates','audit']){await page.locator(`[data-tab="${tab}"]`).click();}
 await page.locator('[data-main-view="radar"]').click();await page.waitForSelector('[data-radar-open]');
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 const auth=await browser.newContext({ignoreHTTPSErrors:true,httpCredentials:{username:'admin',password:'deployment-test-fixture-only'}});const admin=await auth.newPage();admin.on('pageerror',e=>errors.push(e.message));
 await admin.goto('https://admin.pioneer.localhost:19443');await admin.waitForSelector('[data-radar-open]');
 assert.equal(await admin.locator('#radar-refresh').isEnabled(),true);assert.equal(await admin.locator('#radar-save').isEnabled(),true);
 assert.deepEqual(errors,[]);await browser.close();console.log(JSON.stringify({passed:['public-mode banner and administrator link','public write controls disabled','public name and date filters','public detail and six tabs','public mobile layout','authenticated administrator controls'],browserErrors:errors}));
})().catch(e=>{console.error(e);process.exit(1)});
