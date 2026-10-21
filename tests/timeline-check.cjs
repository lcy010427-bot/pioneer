const assert=require('node:assert/strict');
const t=require('../web/timeline.js');
const chemical=(papers=[],status='complete',start='2021-01-01',end='2026-10-20')=>({papers,run:{query:`FIRST_PDATE:[${start} TO ${end}]`,status,finished_at:'2026-10-20T12:00:00Z'}});
const paper=(id,date,year=Number(date?.slice(0,4)))=>({record_key:id,date,year});
let checks=0;function test(name,f){f();checks++;console.log('OK',name);}
test('trailing five-year inclusive calendar range',()=>assert.deepEqual(t.preset('5y','2026-10-20'),{start:'2021-10-21',end:'2026-10-20',unit:'quarter'}));
test('year and quarter defaults',()=>{assert.deepEqual(t.preset('1y','2026-10-20'),{start:'2025-10-21',end:'2026-10-20',unit:'month'});assert.deepEqual(t.preset('1q','2026-10-20'),{start:'2026-07-21',end:'2026-10-20',unit:'week'});});
test('leap-day and month-end clamping',()=>{assert.equal(t.shiftMonths('2024-02-29',-12),'2023-02-28');assert.equal(t.shiftMonths('2024-05-31',-3),'2024-02-29');});
test('invalid dates and reversed ranges rejected',()=>{assert.equal(t.parse('2025-02-29'),null);assert.throws(()=>t.validate('2026-10-20','2026-01-01','week'));assert.throws(()=>t.validate('1900-01-01','2026-01-01','month'));});
test('exact boundary inclusion and quarter counts',()=>{const c=chemical([paper('before','2021-10-20'),paper('start','2021-10-21'),paper('end','2026-10-20'),paper('after','2026-10-21')]);const s=t.series(c,t.preset('5y','2026-10-20'));assert.equal(s.count,2);assert.equal(s.bins.reduce((n,b)=>n+(b.count||0),0),2);assert.deepEqual(s.bins.flatMap(b=>b.records),['start','end']);assert.ok(s.fully_covered);});
test('week buckets respect Monday and year boundaries',()=>{const s=t.series(chemical([paper('a','2024-12-30'),paper('b','2025-01-05'),paper('c','2025-01-06')]),{start:'2024-12-30',end:'2025-01-06',unit:'week'});assert.deepEqual(s.bins.map(b=>b.count),[2,1]);assert.ok(s.bins[1].incomplete);});
test('uncollected months are gaps, not zeros',()=>{const s=t.series(chemical([paper('a','2025-12-10')],'complete','2024-01-01','2025-12-31'),t.preset('1y','2026-10-20'));assert.equal(s.bins.find(b=>b.start==='2026-01-01').count,null);assert.equal(s.bins.find(b=>b.start==='2025-12-01').count,1);assert.equal(s.bins.find(b=>b.start==='2025-11-01').count,0);assert.equal(s.fully_covered,false);});
test('year-only dates never assigned invented months',()=>{const s=t.series(chemical([paper('unknown','',2025)]),{start:'2025-01-01',end:'2025-12-31',unit:'month'});assert.equal(s.unknown_dates,1);assert.ok(s.bins.every(b=>b.count===null));});
test('partial collection and partial boundary remain labelled',()=>{const s=t.series(chemical([paper('a','2026-10-20')],'partial'),t.preset('1q','2026-10-20'));assert.equal(s.fully_covered,false);assert.ok(s.bins.every(b=>b.partial));assert.equal(s.count,1);});
test('missing snapshot yields no plotted points',()=>{const s=t.series({run:null,papers:[]},t.preset('1q','2026-10-20'));assert.ok(s.bins.every(b=>b.count===null));});
console.log(`${checks} timeline checks passed`);
