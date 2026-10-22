'use strict';
let state, selected, pointFilter=null, limit=15, busy=false;
let siteConfig={read_only:true,admin_url:null},siteConfigPromise;
let periodKind='5y', periodRange=null, activeSeries=null;
const time=PioneerTimeline;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statusLabel = s => ({complete:'완전 수집',partial:'부분 수집',failed:'수집 실패',running:'진행 중'}[s] || '미수집');
const metric = (label,value,note) => `<div class="metric"><div class="label">${esc(label)}</div><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`;
function savedScreenIds(){try{return JSON.parse(localStorage.getItem('pioneer-analysis-shelf-v1')||'[]').map(x=>x.id).filter(Boolean)}catch(_){return []}}
function papers() {
  const c = state.chemicals.find(c => c.id === selected);
  const term = $('search').value.trim().toLowerCase();
  const all = c.papers.filter(p => paperMatchesPeriod(p) && `${p.title} ${p.abstract}`.toLowerCase().includes(term) && (typeof facetMatches!=='function'||facetMatches(c,p)));
  $('paper-count').textContent = `${all.length}건${pointFilter ? ` · ${activeSeries.bins.find(b=>b.id===pointFilter)?.label||''}` : ''}`;
  $('clear-year').hidden = pointFilter === null;
  $('more').hidden = limit >= all.length;
  $('papers').innerHTML = all.length ? all.slice(0,limit).map(p => {
    const links = [];
    if (state.dataset_kind !== 'demo') {
      if (p.doi) links.push(`<a href="https://doi.org/${esc(encodeURI(p.doi))}" target="_blank" rel="noopener noreferrer">DOI ↗</a>`);
      if (p.pmid) links.push(`<a href="https://europepmc.org/article/MED/${esc(p.pmid)}" target="_blank" rel="noopener noreferrer">Europe PMC ↗</a>`);
      else if (p.source_id) { const [src,...ids] = p.source_id.split(':'); links.push(`<a href="https://europepmc.org/article/${encodeURIComponent(src)}/${encodeURIComponent(ids.join(':'))}" target="_blank" rel="noopener noreferrer">자료원 레코드 ↗</a>`); }
    }
    return `<article class="paper"><div><span class="tag">${esc(p.year || '연도 미상')}</span><span class="tag">${state.dataset_kind === 'demo' ? '합성 데모' : p.review?.status ? '검토 기록 있음' : '검색 일치 · 사람 미검토'}</span>${p.pub_types.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div><h4>${esc(p.title)}</h4><div class="meta">${esc([p.authors,p.journal].filter(Boolean).join(' · '))}</div>${p.fulltext_license?`<details><summary>확보한 공개 원문의 이용 조건</summary><p class="muted">${esc(p.fulltext_license)}</p></details>`:''}<div class="meta">${esc(p.source_id)} · 날짜 기준: ${esc(p.date_basis)}</div>${p.abstract ? `<details><summary>${state.dataset_kind === 'demo' ? '데모 설명' : '초록 확인 · 원문 분석 아님'}</summary><p class="abstract">${esc(p.abstract)}</p></details>` : '<p class="muted">초록 미제공 · 메타데이터만 확보</p>'}<div>${links.join('')}</div><div class="paper-actions"><button class="quiet" data-review-kind="paper" data-review-id="${c.run.id}:${esc(p.record_key)}">문헌 검토 기록</button>${state.dataset_kind!=='demo'?`<button class="quiet" data-fulltext="${esc(p.record_key)}">${p.fulltext_available?'공개 원문 확보됨':'공개 원문 가져오기'}</button>`:''}</div></article>`;
  }).join('') : '<div class="empty">이 범위에서 표시할 문헌이 없습니다. 미수집·검색 결과 0건·필터 결과를 구분해서 확인하세요.</div>';
}
function initPeriod(){
 if(periodRange)return;
 const ends=state.chemicals.map(time.coverage).filter(Boolean).map(c=>c.end).sort();
 const end=ends.at(-1)||state.generated_at.slice(0,10);
 periodRange=time.preset(periodKind,end);
}
function paperMatchesPeriod(p){
 if(!periodRange||!time.inRange(p,periodRange))return false;
 if(time.parse(p.date)&&activeSeries&&!activeSeries.records.includes(p.record_key))return false;
 if(pointFilter){const bin=activeSeries?.bins.find(b=>b.id===pointFilter);return !!bin&&bin.records.includes(p.record_key);}
 return true;
}
function periodLabel(){return ({'5y':'최근 5년','1y':'최근 1년','1q':'최근 1분기',custom:'직접 설정'})[periodKind];}
function syncPeriodControls(){
 $('period-start').value=periodRange.start;$('period-end').value=periodRange.end;$('period-unit').value=periodRange.unit;
 document.querySelectorAll('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period===periodKind)));
 $('period-caption').textContent=`${periodRange.start} ~ ${periodRange.end} · ${periodLabel()} · 추이·문헌에 적용. 맥락·공백·물질 비교는 저장 스냅샷 기준입니다.`;
}
function chart(){
 const c=state.chemicals.find(c=>c.id===selected);activeSeries=time.series(c,periodRange);
 const bins=activeSeries.bins, known=bins.filter(b=>b.count!==null), unitLabel=({quarter:'분기',month:'월',week:'주'})[periodRange.unit];
 const width=Math.max(260,$('chart').clientWidth),height=305,left=48,right=20,top=28,bottom=58;
 const plotWidth=width-left-right,plotHeight=height-top-bottom;
 const max=Math.max(1,...known.map(b=>b.count)),step=Math.max(1,Math.ceil(max/4)),yMax=step*4;
 const span=Math.max(86400000,+time.parse(periodRange.end)-time.parse(periodRange.start));
 const x=b=>left+((+time.parse(b.start)+ +time.parse(b.end))/2- +time.parse(periodRange.start))/span*plotWidth;
 const y=count=>top+plotHeight-count/yMax*plotHeight;
 let svg=`<svg class="trend-svg" viewBox="0 0 ${width} ${height}" role="group" aria-label="출판 시점을 X축으로 한 ${unitLabel}별 문헌 수 선그래프"><text class="axis-title" x="${left}" y="14">문헌 수 (건)</text>`;
 for(let i=0;i<=4;i++){const value=step*i;svg+=`<line class="chart-grid" x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}"/><text class="axis-label" x="${left-10}" y="${y(value)+4}" text-anchor="end">${value}</text>`;}
 for(let i=1;i<bins.length;i++){const a=bins[i-1],b=bins[i];if(a.count!==null&&b.count!==null)svg+=`<path class="trend-line${a.partial||b.partial?' partial':''}" d="M ${x(a)} ${y(a.count)} L ${x(b)} ${y(b.count)}"/>`;}
 const stride=Math.max(1,Math.ceil(bins.length/Math.max(2,Math.floor(plotWidth/95))));
 bins.forEach((b,i)=>{
  if(i%stride===0||i===bins.length-1)svg+=`<text class="axis-label" x="${x(b)}" y="${height-bottom+25}" text-anchor="middle">${esc(b.label)}</text>`;
  if(b.count===null)return;
  const label=`${b.start} ~ ${b.end} · ${b.count}건${b.partial?' · 일부 기간 또는 날짜 미확인 포함':''}`;
  svg+=`<g class="trend-point${b.partial?' partial':''}${pointFilter===b.id?' selected':''}" data-bin="${b.id}" role="button" tabindex="0" aria-label="${esc(label)}" aria-pressed="${pointFilter===b.id}"><title>${esc(label)}</title><circle class="point-hit" cx="${x(b)}" cy="${y(b.count)}" r="12"/><circle class="point-dot" cx="${x(b)}" cy="${y(b.count)}" r="4.5"/></g>`;
 });
 svg+=`<text class="axis-title" x="${left+plotWidth/2}" y="${height-6}" text-anchor="middle">출판 시점 · ${unitLabel}별 집계</text></svg>`;
 $('chart').innerHTML=known.length?svg:activeSeries.unknown_dates?'<p class="empty">문헌의 상세 출판일이 없어 이 단위로 집계할 수 없습니다. 연도만 알려진 문헌은 목록에서 확인하세요.</p>':'<p class="empty">이 기간을 집계할 수집 자료가 없습니다. 다른 기간을 고르거나 수동 갱신에서 필요한 연도를 수집하세요.</p>';
 const notes=[`저장된 출판일 기준 ${unitLabel}별 건수입니다. 점을 선택하면 해당 구간의 문헌을 확인할 수 있습니다.`];
 if(!activeSeries.fully_covered)notes.push('선택 기간 중 미수집 구간이 있습니다. 미수집은 0건으로 그리지 않습니다.');
 if(bins.some(b=>b.incomplete))notes.push('점선·빈 원은 시작·종료일에 잘린 구간 등 일부 기간만 집계한 값입니다.');
 if(c.run?.status==='partial')notes.push('부분 수집본이므로 확인한 문헌 수만 표시합니다.');
 if(activeSeries.unknown_dates)notes.push(`상세 출판일이 없는 ${activeSeries.unknown_dates}건은 선그래프에서 제외했습니다.`);
 if(activeSeries.coverage)notes.push(`저장된 수집 범위: ${activeSeries.coverage.start} ~ ${activeSeries.coverage.end}.`);
 $('chart-note').textContent=notes.join(' ');
 const inspect=b=>{$('chart-inspect').textContent=b?`${b.label} · ${b.start} ~ ${b.end} · ${b.count}건${b.partial?' · 일부 기간/자료만 확인':''}`:'점을 가리키거나 선택해 해당 기간의 건수와 논문을 확인하세요.';};
 inspect(bins.find(b=>b.id===pointFilter));
 $('chart').querySelectorAll('[data-bin]').forEach(node=>{
  const bin=bins.find(b=>b.id===node.dataset.bin);
  const select=()=>{pointFilter=pointFilter===bin.id?null:bin.id;limit=15;chart();papers();$('chart').querySelector(`[data-bin="${bin.id}"]`)?.focus({preventScroll:true});};
  node.addEventListener('click',select);node.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select();}});
  node.addEventListener('mouseenter',()=>inspect(bin));node.addEventListener('focus',()=>inspect(bin));
 });
}
function render() {
  document.querySelector('#workspace .detail').hidden=false;
  initPeriod();syncPeriodControls();
  const c = state.chemicals.find(c=>c.id===selected);
  const demo = state.dataset_kind === 'demo';
  $('mode').textContent = demo ? '합성 데모 모드 — 모든 문헌·건수는 기능 검증용 예시입니다. 실제 연구 결과가 아니며 외부 수집은 비활성화되어 있습니다.' : '실자료 모드 — 지정 목록의 제목·초록 검색 결과입니다. 검색 일치가 해당 물질의 직접 근거를 뜻하지 않습니다. 과학적 판정·위해성 순위는 제공하지 않습니다.';
  if(state.validation_copy)$('mode').textContent='검증용 복제 DB — 여기의 자동화 검토 기록은 실제 사람 평가가 아닙니다. '+$('mode').textContent;
  $('chemical-count').textContent = state.chemicals.length;
  $('watchlist').innerHTML = state.chemicals.map(x=>`<button class="watch${x.id===selected?' active':''}" data-chemical="${esc(x.id)}" aria-pressed="${x.id===selected}">${esc(x.name)}${x.derived_from_screening?'<span class="shelf-origin">Hot Radar 저장</span>':''}<small>${periodBadge(x)}</small></button>`).join('');
  $('watchlist').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{selected=b.dataset.chemical;pointFilter=null;limit=15;if(typeof facetFilters!=='undefined')facetFilters={};$('search').value='';render();}));
  $('chemical-name').textContent = c.name;
  $('aliases').textContent = `검색 동의어: ${c.terms.join(' / ')} · 식별자 검증 미실시`;
  const temporal=time.series(c,periodRange);
  $('metrics').innerHTML=metric('선택 기간 문헌 수',temporal.bins.some(b=>b.count!==null)?`${temporal.count}건`:'—',temporal.fully_covered?'출판일 확인 문헌 · 독립 연구 수 아님':'선택 기간 자료 부족 · 저장 범위 확인 필요')+metric('조회 기간',periodLabel(),`${periodRange.start} ~ ${periodRange.end}`)+metric('분석 상태','사람 미검토','판정 임계값·중요성·연구공백 미확정');
  $('snapshot-status').textContent=c.run?statusLabel(c.run.status):'미수집';
  $('collection').hidden = demo||c.derived_from_screening;
  $('collect-button').disabled = busy;
  $('provenance').innerHTML = c.run ? `<p>출처: ${esc(c.run.source)} · 쿼리 버전: ${esc(c.run.query_version)}</p><pre>${esc(c.run.query)}</pre><p>수집 시작: ${esc(c.run.started_at)}<br>완료: ${esc(c.run.finished_at)}<br>자료원 검색 ${c.run.source_total}건 / 수신 ${c.run.fetched}건 / 중복 제거 ${c.run.unique_count}건</p><p>날짜: FIRST_PDATE 검색 / firstPublicationDate 집계, 미제공 시 pubYear 대체. 언어·문서유형 제한 없음. 리뷰·preprint가 포함될 수 있으며 버전·동일 연구 연결은 미실시.</p>${attempt(c)}` : `<p>선택된 스냅샷 없음</p>${attempt(c)}`;
  chart();papers();
  if(typeof renderResearch==='function'&&!$('detail-view').hidden)renderResearch();
  if(typeof renderHotRadar==='function')renderHotRadar();
}
function attempt(c){const a=c.latest_attempt;return a?`<p>최근 시도: ${esc(a.started_at)} · ${esc(statusLabel(a.status))} · run #${a.id}</p>${a.error?`<pre class="error">${esc(a.error)}</pre>`:''}${c.run&&a.id!==c.run.id?'<p class="error">최근 시도와 표시 중인 스냅샷이 다릅니다. 마지막 완전 수집본을 표시합니다.</p>':''}`:'<p>수집 이력 없음</p>';}
async function load(forceResearch=false){await loadSiteConfig();const detailed=forceResearch||window.pioneerDetailLoaded||new URLSearchParams(location.search).get('view')==='detail';const res=await fetch(detailed?'/api/research':'/api/state');if(!res.ok)throw Error('상태 조회 실패');state=await res.json();state.chemicals=[];const ids=savedScreenIds();if(ids.length){const pr=await fetch('/api/screen/profiles?'+new URLSearchParams({ids:ids.join(',')}));if(pr.ok){const profiles=await pr.json();state.chemicals.push(...profiles.filter(p=>!state.chemicals.some(c=>c.id===p.id)));}}if(!state.chemicals.some(c=>c.id===selected))selected=state.chemicals[0]?.id;if(!selected){$('mode').textContent='물질 분석 대상이 없습니다. Hot Radar에서 후보를 보관함에 추가하세요.';document.querySelector('#workspace .detail').hidden=true;$('chemical-count').textContent='0';$('watchlist').innerHTML='<p class="empty">Hot Radar에서 저장한 물질이 여기에 표시됩니다.</p>';if(typeof renderHotRadar==='function'&&mainView==='radar')renderHotRadar();return;}render();}
function periodBadge(c){const s=time.series(c,periodRange);return s.bins.some(b=>b.count!==null)?`${s.count}건 · ${s.fully_covered?'기간 수집됨':'일부 기간 미수집'}`:'선택 기간 미수집';}
function applyPeriod(start,end,unit,kind){
 try{time.validate(start,end,unit);periodRange={start,end,unit};periodKind=kind;pointFilter=null;limit=15;$('period-error').textContent='';render();}
 catch(error){$('period-error').textContent=error.message;}
}
document.querySelectorAll('[data-period]').forEach(b=>b.addEventListener('click',()=>{const p=time.preset(b.dataset.period,$('period-end').value||periodRange.end);applyPeriod(p.start,p.end,p.unit,b.dataset.period);}));
$('period-form').addEventListener('submit',e=>{e.preventDefault();applyPeriod($('period-start').value,$('period-end').value,$('period-unit').value,'custom');});
window.addEventListener('resize',()=>{if(state&&periodRange&&selected)chart();});
$('search').addEventListener('input',()=>{limit=15;papers();});
$('more').addEventListener('click',()=>{limit+=15;papers();});
$('clear-year').addEventListener('click',()=>{pointFilter=null;limit=15;chart();papers();});
$('methods-button').addEventListener('click',()=>{const open=$('methods').hidden;$('methods').hidden=!open;$('methods-button').setAttribute('aria-expanded',String(open));if(open)$('methods').scrollIntoView({behavior:'auto'});});
$('collect-form').addEventListener('submit',async e=>{e.preventDefault();if(busy)return;busy=true;$('collect-button').disabled=true;$('collect-message').textContent='Europe PMC 조회 중입니다. 결과를 저장할 때까지 기다려 주세요.';try{const res=await fetch('/api/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chemical_id:selected,start_year:Number($('start-year').value),end_year:Number($('end-year').value)})});const result=await res.json();if(!res.ok)throw Error(result.error);$('collect-message').textContent=`수집 완료 · run #${result.run_id}. 완전/부분 수집 상태를 확인하세요.`;}catch(err){$('collect-message').textContent=err.message;}finally{busy=false;try{await load();}catch(err){$('collect-message').textContent+=`\n${err.message}`;}$('collect-button').disabled=false;}});
const thisYear=new Date().getFullYear();$('start-year').value=thisYear-5;$('end-year').value=thisYear;$('end-year').max=thisYear;$('start-year').max=thisYear;
load().catch(err=>{$('mode').textContent=`화면을 불러오지 못했습니다: ${err.message}`;});

async function loadSiteConfig(){
 if(!siteConfigPromise)siteConfigPromise=(async()=>{const r=await fetch('/api/config');if(!r.ok)throw Error('접근 설정 조회 실패');siteConfig=await r.json();const banner=$('site-access');if(siteConfig.read_only){banner.hidden=false;banner.innerHTML=`<span>공개 조회 · 갱신과 기록은 운영자가 관리합니다.</span>${siteConfig.admin_url?`<a href="${esc(siteConfig.admin_url)}" rel="noopener noreferrer">운영자 로그인 ↗</a>`:''}`;}return siteConfig;})();
 return siteConfigPromise;
}
function enforceSiteAccess(){
 if(!siteConfig.read_only)return;
 document.querySelectorAll('#collect-form input,#collect-form button,#radar-save,#radar-refresh,#radar-corpus-scan,#radar-lookup input,#radar-lookup button,#radar-choose,#discover-button,[data-review-kind],[data-fulltext],[data-identify],[data-promote],[data-followup]').forEach(b=>{b.disabled=true;b.title='운영자 사이트에서 이용할 수 있습니다.';});
 document.querySelectorAll('#collection,#radar-history,#radar-job,#evaluation-panel,.manual-relation,.operator-only,a[href^="/api/export"]').forEach(e=>e.hidden=true);
}
new MutationObserver(enforceSiteAccess).observe(document.body,{childList:true,subtree:true});
