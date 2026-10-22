'use strict';
let mainView=new URLSearchParams(location.search).get('view')==='detail'?'detail':'radar';
let radarData=null,radarRequest=0,radarFilter='all',radarSearch='',radarTimer=null,radarReady=false;
let radarRange=null,radarKind='1y',radarBoot=null;
const SHELF='pioneer-analysis-shelf-v1';
const signalNames={new_mention:'새롭게 언급',publication_growth:'문헌 증가',environmental_detection:'환경 검출',hazard_signal:'독성 신호',regulatory_attention:'규제 관심'};

function shelf(){try{const x=JSON.parse(localStorage.getItem(SHELF)||'[]');return Array.isArray(x)?x:[]}catch(_){return []}}
function setShelf(x){localStorage.setItem(SHELF,JSON.stringify(x))}
function onShelf(id){return shelf().some(x=>x.id===id)}
function sourceLink(m){const [src,...id]=m.source_id.split(':');return `https://europepmc.org/article/${encodeURIComponent(src)}/${encodeURIComponent(id.join(':'))}`}
function spark(row){const p=row.buckets,max=Math.max(1,...p.map(x=>x.count)),n=Math.max(1,p.length-1);return `<svg viewBox="0 0 160 52" role="img" aria-label="${esc(row.name)} 시간 추이">${p.slice(1).map((x,i)=>`<path class="mini-line" d="M${8+i/n*144},${43-p[i].count/max*34} L${8+(i+1)/n*144},${43-x.count/max*34}"/>`).join('')}${p.map((x,i)=>`<circle class="mini-dot" cx="${8+i/n*144}" cy="${43-x.count/max*34}" r="2.5"><title>${x.start}~${x.end}: ${x.count}건</title></circle>`).join('')}</svg>`}

function switchMain(view){
 mainView=view;$('hot-radar').hidden=view!=='radar';$('detail-view').hidden=view!=='detail';
 document.querySelectorAll('[data-main-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mainView===view)));
 if(view==='detail'&&state){window.pioneerDetailLoaded=true;load(true).catch(e=>{$('mode').textContent=e.message})}
 else if(view==='radar')renderHotRadar();
}
document.querySelectorAll('[data-main-view]').forEach(b=>b.addEventListener('click',()=>switchMain(b.dataset.mainView)));

function renderHotRadar(){
 $('mode').textContent='전체 문헌에서 새롭게 포착된 화학물질 후보와 그 근거 문헌을 표시합니다.';
 if(!radarReady){
  radarReady=true;
  $('hot-radar').innerHTML=`
  <section class="panel radar-top"><div class="panel-heading"><div><p class="eyebrow">OPEN DISCOVERY</p><h2>Hot Radar</h2><p class="muted">미리 정한 물질 목록 없이 문헌을 수집해 후보를 찾습니다. 후보를 누르면 실제 근거 문장과 원문 출처를 확인할 수 있습니다.</p></div><div class="radar-actions"><button id="radar-refresh" hidden>이 기간 새로 수집</button></div></div>
  <div class="radar-period-row"><div class="period-presets" role="group" aria-label="조회 기간">${[['5y','5년간'],['1y','1년간'],['1q','1분기 간']].map(([id,label])=>`<button data-radar-period="${id}">${label}</button>`).join('')}</div><form id="radar-period-form"><label>시작일<input id="radar-start" type="date" required></label><label>종료일<input id="radar-end" type="date" required></label><label>선그래프 단위<select id="radar-unit"><option value="quarter">분기별</option><option value="month">월별</option><option value="week">주별</option></select></label><button class="quiet">기간 적용</button></form></div><p id="radar-message" class="muted" role="status"></p><div id="radar-summary" class="radar-summary"></div></section>
  <section class="panel shelf-panel"><div class="panel-heading"><div><h3>내 보관함</h3><p class="muted">Hot Radar에서 고른 물질만 ‘물질 분석’ 탭의 분석 대상이 됩니다.</p></div><button id="shelf-open-analysis" class="quiet">물질 분석 열기</button></div><div id="shelf-list"></div></section>
  <section class="panel radar-results"><div class="radar-search-row"><label class="radar-name-label">물질 검색<input id="radar-search" type="search" placeholder="물질명 또는 ChEBI ID"></label><label>신호 유형<select id="radar-signal-filter"><option value="all">전체 신호</option>${Object.entries(signalNames).map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label></div><div id="radar-results-body">불러오는 중입니다.</div></section>`;
  bindRadar();
 }
 paintShelf();bootRadar();
}

async function bootRadar(){
 if(!radarBoot)radarBoot=(async()=>{const [meta]=await Promise.all([fetch('/api/screen/meta').then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.error||'수집 범위를 불러오지 못했습니다.');return d}),loadSiteConfig()]);radarRange=time.preset(radarKind,meta.latest_end);syncRadarPeriod();await fetchRadar()})().catch(e=>{$('radar-results-body').innerHTML=`<p class="error">${esc(e.message)}</p>`});
 return radarBoot;
}
function setRadarPeriod(start,end,unit,kind){try{time.validate(start,end,unit);radarRange={start,end,unit};radarKind=kind;syncRadarPeriod();fetchRadar()}catch(e){$('radar-message').textContent=e.message}}
function syncRadarPeriod(){if(!radarRange)return;$('radar-start').value=radarRange.start;$('radar-end').value=radarRange.end;$('radar-unit').value=radarRange.unit;document.querySelectorAll('[data-radar-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.radarPeriod===radarKind)))}
function bindRadar(){
 document.querySelectorAll('[data-radar-period]').forEach(b=>b.onclick=()=>{const p=time.preset(b.dataset.radarPeriod,$('radar-end').value||radarRange.end);setRadarPeriod(p.start,p.end,p.unit,b.dataset.radarPeriod)});
 $('radar-period-form').onsubmit=e=>{e.preventDefault();setRadarPeriod($('radar-start').value,$('radar-end').value,$('radar-unit').value,'custom')};
 $('radar-search').oninput=e=>{radarSearch=e.target.value;clearTimeout(radarTimer);radarTimer=setTimeout(fetchRadar,250)};
 $('radar-signal-filter').onchange=e=>{radarFilter=e.target.value;paintRadar()};
 $('shelf-open-analysis').onclick=()=>switchMain('detail');
 $('radar-refresh').onclick=refreshScreen;
 $('radar-dialog-close').onclick=()=>$('radar-dialog').close();
}

async function fetchRadar(){
 if(!radarRange)return;const req=++radarRequest;$('radar-results-body').textContent='후보물질과 근거 문헌을 불러오는 중입니다.';
 try{const p=new URLSearchParams({start:radarRange.start,end:radarRange.end,unit:radarRange.unit,search:radarSearch,ids:shelf().map(x=>x.id).join(',')});const r=await fetch('/api/screen?'+p),data=await r.json();if(!r.ok)throw Error(data.error);if(req!==radarRequest)return;radarData=data;paintRadar()}
 catch(e){if(req===radarRequest)$('radar-results-body').innerHTML=`<p class="error">${esc(e.message)}</p>`}
}
function paintShelf(){
 if(!$('shelf-list'))return;const list=shelf();$('shelf-open-analysis').disabled=!list.length;
 $('shelf-list').innerHTML=list.length?list.map(x=>`<span class="shelf-chip">${esc(x.name)}<button data-shelf-remove="${esc(x.id)}" aria-label="${esc(x.name)} 보관함에서 제거">×</button></span>`).join(''):'<p class="empty">아직 저장한 물질이 없습니다. 아래 후보에서 ‘추가’를 누르세요.</p>';
 $('shelf-list').querySelectorAll('[data-shelf-remove]').forEach(b=>b.onclick=()=>{setShelf(shelf().filter(x=>x.id!==b.dataset.shelfRemove));paintShelf();paintRadar()});
}
function storeRow(row){const list=shelf(),i=list.findIndex(x=>x.id===row.id);if(i>=0)list.splice(i,1);else list.push({id:row.id,name:row.name,identifier:row.identifier||'',saved_at:new Date().toISOString()});setShelf(list);paintRadar()}
async function analyzeRow(row){if(!onShelf(row.id))storeRow(row);if($('radar-dialog').open)$('radar-dialog').close();await load(true);selected='screen:'+row.id;pointFilter=null;limit=15;render();switchMain('detail')}

function paintRadar(){
 if(!radarData)return;paintShelf();const d=radarData,run=d.run;
 const rows=d.rows.filter(r=>r.signal_types.length&&(radarFilter==='all'||r.signal_types.includes(radarFilter)));
 $('radar-summary').innerHTML=`<span><strong>${run?run.baseline_fetched+run.recent_fetched:0}</strong> 확인 문헌</span><span><strong>${d.total_candidates}</strong> 추출 후보</span><span><strong>${d.growing}</strong> 근거 신호 후보</span><span class="radar-date">${d.start} ~ ${d.end}</span>`;
 $('radar-message').textContent=run?`Europe PMC · ${run.status==='complete'?'전체 수집':'부분 표본'} · 전반 ${run.baseline_fetched}건 / 후반 ${run.recent_fetched}건`:'이 기간의 수집 결과가 없습니다.';
 $('radar-refresh').hidden=siteConfig.read_only;
 $('radar-results-body').innerHTML=run?`<div class="table-scroll"><table class="radar-table discovery-table"><thead><tr><th>후보물질</th><th>포착된 신호</th><th>근거 문헌</th><th>추이</th><th>보관함</th></tr></thead><tbody>${rows.map(r=>{const e=r.evidence[0];return `<tr><td><button class="radar-name" data-radar-open="${r.id}">${esc(r.name)}</button><small>${r.identifier?esc(r.identifier.split('/').pop()):'식별자 미확인'} · ${r.total}개 문헌 · 점수 ${r.priority_score}</small></td><td><div class="signal-stack">${r.signal_types.map(s=>`<span class="signal-badge signal-${s}">${esc(signalNames[s])}</span>`).join('')}</div></td><td>${e?`<button class="evidence-preview" data-radar-open="${r.id}"><b>${esc(e.title)}</b><small>${esc(e.date||'날짜 미상')} · ${esc(e.exact||r.name)}</small></button>`:'<span class="muted">근거 문헌 확인 필요</span>'}</td><td><button class="spark-button" data-radar-open="${r.id}">${spark(r)}</button></td><td><button class="shelf-button ${onShelf(r.id)?'saved':''}" data-shelf="${r.id}">${onShelf(r.id)?'✓ 보관 중':'＋ 추가'}</button></td></tr>`}).join('')||'<tr><td colspan="5">이 기간에 근거 신호가 있는 후보가 없습니다.</td></tr>'}</tbody></table></div>`:'<p class="empty">이 기간의 저장 결과가 없습니다. 운영자 화면에서 원하는 기간을 지정해 수집하세요.</p>';
 $('radar-results-body').querySelectorAll('[data-shelf]').forEach(b=>b.onclick=()=>storeRow(d.rows.find(r=>r.id===b.dataset.shelf)));
 $('radar-results-body').querySelectorAll('[data-radar-open]').forEach(b=>b.onclick=()=>openRadar(d.rows.find(r=>r.id===b.dataset.radarOpen)));
}

function openRadar(r){
 $('radar-dialog-title').textContent=r.name;
 $('radar-dialog-body').innerHTML=`<div class="radar-detail-head">${spark(r)}<div><p class="eyebrow">SCREENING CANDIDATE</p><strong class="detail-score">${r.priority_score}</strong><p>${r.identifier?esc(r.identifier):'식별자 미확인'} · 전반 ${r.baseline} → 후반 ${r.recent}개 문헌</p></div></div><h3>포착된 신호</h3><div class="signal-stack">${r.signal_types.map(s=>`<span class="signal-badge signal-${s}">${esc(signalNames[s])}</span>`).join('')}</div><div class="radar-actions"><button id="radar-shelf-detail">${onShelf(r.id)?'보관함에서 빼기':'내 보관함에 추가'}</button><button id="radar-analyze-detail" class="quiet">저장하고 물질 분석</button></div><h3>근거와 출처</h3>${r.evidence.map(m=>`<article class="radar-paper"><p class="muted">${esc(m.date||'날짜 미상')} · ${esc(m.section)}에서 “${esc(m.exact)}”</p><h4>${esc(m.title)}</h4><blockquote>${esc(m.snippet)}</blockquote><a href="${sourceLink(m)}" target="_blank" rel="noopener noreferrer">Europe PMC 원문 레코드 ↗</a>${m.doi?` · <a href="https://doi.org/${encodeURIComponent(m.doi)}" target="_blank" rel="noopener noreferrer">DOI ↗</a>`:''}</article>`).join('')||'<p class="empty">표시할 근거 문헌이 없습니다.</p>'}`;
 $('radar-shelf-detail').onclick=()=>{storeRow(r);$('radar-shelf-detail').textContent=onShelf(r.id)?'보관함에서 빼기':'내 보관함에 추가'};
 $('radar-analyze-detail').onclick=()=>analyzeRow(r);$('radar-dialog').showModal();
}
async function refreshScreen(){
 if(siteConfig.read_only)return;$('radar-refresh').disabled=true;$('radar-message').textContent='선택 기간의 문헌에서 후보와 근거를 수집하고 있습니다.';
 try{const r=await fetch('/api/screen',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start:radarRange.start,end:radarRange.end,max_pages:10})}),d=await r.json();if(!r.ok)throw Error(d.error);$('radar-message').textContent='수집을 시작했습니다. 완료되면 후보 목록을 갱신합니다.';setTimeout(fetchRadar,3000)}
 catch(e){$('radar-message').textContent=e.message}finally{$('radar-refresh').disabled=false}
}
switchMain(mainView);
