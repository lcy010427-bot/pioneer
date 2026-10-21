// Writes only saved reports/selection reasons in a marked validation copy. No external requests.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.env.PIONEER_TEST_URL||'http://127.0.0.1:8767';
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[],external=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(new URL(r.url()).origin!==base)external.push(r.url());});
 const check=await(await page.request.get(base+'/api/radar')).json();assert.equal(check.validation_copy,true,'Refusing report writes outside validation copy');
 await page.goto(base);await page.waitForSelector('[data-radar-open]',{timeout:60000});
 assert.equal(await page.locator('#hot-radar').isVisible(),true);assert.equal(await page.locator('#detail-view').isVisible(),false);
 assert.equal(await page.locator('.radar-table tbody tr').count(),check.total_monitored);
 assert.ok((await page.locator('#radar-results-body').innerText()).includes('전체 화학물질 순위가 아닙니다'));
 await page.locator('[data-radar-period="1y"]').click();await page.waitForFunction(()=>radarData.options.unit==='month');
 assert.equal(await page.locator('#radar-unit').inputValue(),'month');
 await page.locator('[data-radar-period="1q"]').click();await page.waitForFunction(()=>radarData.options.unit==='week');
 await page.locator('#radar-start').fill('2024-01-01');await page.locator('#radar-end').fill('2025-12-31');
 await page.locator('#radar-period-form button').click();await page.waitForFunction(()=>radarData.options.start==='2024-01-01');
 const expected=await(await page.request.get(base+'/api/radar?start=2024-01-01&end=2025-12-31&unit=week')).json();
 const first=expected.rows[0];assert.ok((await page.locator('.radar-table tbody tr').first().innerText()).includes(`${first.baseline} → ${first.recent}`));
 await page.locator('[data-radar-check]').nth(0).check();await page.locator('[data-radar-check]').nth(1).check();await page.locator('[data-radar-check]').nth(2).check();
 await page.locator('#radar-compare-selected').click();assert.equal(await page.locator('.radar-compare-table th').count(),3);await page.locator('#radar-dialog-close').click();
 await page.locator('[data-radar-open]').first().click();await page.waitForSelector('.radar-paper');
 const total=first.total;assert.equal(await page.locator('.radar-paper').count(),Math.min(total,20));
 if(total>20){await page.locator('#radar-articles-more').click();await page.waitForFunction(n=>document.querySelectorAll('.radar-paper').length===Math.min(n,40),total);}
 await page.locator('#radar-dialog-body>details>summary').click();
 assert.ok((await page.locator('#radar-dialog-body').innerText()).includes('검증된 적정값을 뜻하지 않습니다'));
 await page.locator('#radar-choose').click();await page.locator('#radar-choice-form [name=actor]').fill('UI 자동검사 (사람 아님)');
 await page.locator('#radar-choice-form [name=reason]').fill('복제 DB의 저장 검증입니다. 실제 후보 선정·과학적 평가가 아닙니다.');
 await page.locator('#radar-choice-form button').click();await page.waitForFunction(()=>document.querySelector('#radar-choice-message').textContent.includes('판단과 이유를 기록'));
 await page.locator('#radar-dialog-close').click();await page.locator('#radar-history summary').click();await page.waitForSelector('[data-report]');
 const id=await page.locator('[data-report]').first().getAttribute('data-report');const report=await(await page.request.get(base+'/api/radar?report_id='+id)).json();
 assert.ok(report.decisions.some(d=>d.actor==='UI 자동검사 (사람 아님)'));
 const csv=await(await page.request.get(base+'/api/radar.csv?report_id='+id)).text();assert.ok(csv.includes('실제 후보 선정·과학적 평가가 아닙니다'));assert.ok(csv.includes('criteria'));
 await page.locator('#radar-search').fill('HFPO');await page.waitForFunction(()=>radarData.options.search==='HFPO');assert.equal(await page.locator('.radar-table tbody tr').count(),1);
 await page.locator('#radar-search').fill('');await page.waitForFunction(()=>radarData.options.search==='');
 await page.locator('#radar-corpus-show').click();await page.waitForSelector('[data-corpus-name]');assert.ok((await page.locator('#radar-dialog-body').innerText()).includes('정규식의 자동 제안'));
 assert.ok(await page.locator('[data-corpus-name]').count()>0);await page.locator('[data-corpus-name]').first().click();assert.ok((await page.locator('#radar-lookup-name').inputValue()).length>0);
 for(const width of [390,768,1024,1440]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'overflow at '+width);}
 await page.screenshot({path:'.data/hot-radar-checked-desktop.png',fullPage:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:'.data/hot-radar-checked-mobile.png',fullPage:true});
 await page.locator('[data-main-view="detail"]').click();await page.waitForSelector('.cell-button',{state:'attached'});assert.equal(await page.locator('#detail-view').isVisible(),true);assert.equal(await page.locator('[data-tab]').count(),6);
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 console.log(JSON.stringify({passed:['validation-copy guard','default radar dashboard','shared scope and counts','three period presets','custom date comparison','three-candidate comparison','source pagination','sensitivity disclosure','immutable report saving','selection rationale and export','name search','corpus provenance and lookup handoff','four viewport widths','existing detail navigation','no external browser calls'],browserErrors:errors}));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
