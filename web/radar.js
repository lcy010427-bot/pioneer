'use strict';
let mainView=new URLSearchParams(location.search).get('view')==='detail'?'detail':'radar';
let radarData=null,radarRequest=0,radarFilters={search:'',status:'all',sort:'delta',field:'all',medium:'',endpoint:'',model:'',exclude_reviews:true,min_recent:5,min_baseline:3,min_delta:3,min_percent:50};
let radarCompared=new Set();
let radarHistoryId=null,radarBusy=false,radarTimer=null,radarCurrent=null,jobTimer=null;
const radarLabels={growing:'문헌 관심 증가',early:'기저 0건 초기 신호',low_volume:'저빈도·해석 주의',declining:'문헌 수 감소',not_flagged:'증가 조건 미충족',insufficient:'자료 부족·비교 보류'};
function switchMain(view){mainView=view;$('hot-radar').hidden=view!=='radar';$('detail-view').hidden=view!=='detail';document.querySelectorAll('[data-main-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mainView===view)));if(view==='detail'&&state&&periodRange){window.pioneerDetailLoaded=true;if(!state.chemicals[0]?.evidence)load(true).catch(e=>{$('mode').textContent=e.message;});else{render();requestAnimationFrame(chart);}}else if(view==='radar'&&state)renderHotRadar();}
document.querySelectorAll('[data-main-view]').forEach(b=>b.addEventListener('click',()=>switchMain(b.dataset.mainView)));
switchMain(mainView);
function radarOptions(){return {...radarFilters,start:periodRange.start,end:periodRange.end,unit:periodRange.unit};}
function radarParams(){return new URLSearchParams(radarOptions());}
function miniChart(row){
 const points=row.buckets,max=Math.max(1,...points.map(p=>p.count||0));const n=Math.max(1,points.length-1);let lines='';
 for(let i=1;i<points.length;i++){const a=points[i-1],b=points[i];if(a.count!==null&&b.count!==null)lines+=`<path class="mini-line ${a.partial||b.partial?'partial':''}" d="M${8+(i-1)/n*144},${43-a.count/max*34} L${8+i/n*144},${43-b.count/max*34}"/>`;}
 const dots=points.map((p,i)=>p.count===null?'':`<circle class="mini-dot" cx="${8+i/n*144}" cy="${43-p.count/max*34}" r="2.5"><title>${p.start} ~ ${p.end}: ${p.count}건</title></circle>`).join('');
 return `<svg viewBox="0 0 160 52" role="img" aria-label="${esc(row.name)}의 선택 기간 문헌 추이">${lines}${dots}</svg>`;
}
async function radarPost(path,data){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(result.error||'처리에 실패했습니다.');return result;}
function radarMessage(message,error=false){const target=$('radar-message');if(target){target.textContent=message;target.classList.toggle('error',error);}}
function renderHotRadar(){
 if(!state||!periodRange)return;
 $('hot-radar').innerHTML=`<section class="radar-top panel"><div class="panel-heading"><div><h2>Hot Radar</h2><p class="muted">정해진 기간에 문헌 관심이 늘어난 물질을 찾고, 선정 이유와 논문을 확인하세요.</p></div><div class="radar-actions"><button id="radar-save" class="quiet">분석 저장</button><a id="radar-download" class="button quiet" download="pioneer-hot-radar.csv">CSV ↓</a><button id="radar-refresh">조회 목록 갱신</button></div></div><div class="radar-period-row"><div class="period-presets" role="group" aria-label="Radar 조회 기간">${[['5y','5년간'],['1y','1년간'],['1q','1분기 간']].map(([id,label])=>`<button data-radar-period="${id}" aria-pressed="${periodKind===id}">${label}</button>`).join('')}</div><form id="radar-period-form"><label>시작일<input id="radar-start" type="date" value="${periodRange.start}" required></label><label>기준일·종료일<input id="radar-end" type="date" value="${periodRange.end}" required></label><label>선그래프 단위<select id="radar-unit">${[['quarter','분기별'],['month','월별'],['week','주별']].map(([v,l])=>`<option value="${v}" ${periodRange.unit===v?'selected':''}>${l}</option>`).join('')}</select></label><button type="submit" class="quiet">기간 적용</button></form></div><p class="muted">전반부와 후반부를 동일 일수로 비교합니다. 1분기는 최근 3개월입니다. 문헌 관심 증가는 위해성·연구 우선순위와 다릅니다.</p><div id="radar-summary" class="radar-summary" aria-live="polite"></div><div id="radar-job" role="status"></div><p id="radar-message" role="status"></p></section>
<section class="panel radar-results"><div class="radar-search-row"><label class="radar-name-label">조회 목록에서 찾기<input id="radar-search" type="search" value="${esc(radarFilters.search)}" placeholder="물질명·동의어·PubChem CID"></label><label>표시<select id="radar-status"><option value="all">전체 상태</option>${Object.entries(radarLabels).map(([k,v])=>`<option value="${k}" ${radarFilters.status===k?'selected':''}>${v}</option>`).join('')}</select></label><label>정렬<select id="radar-sort">${[['delta','절대 증가량'],['percent','증가율'],['recent','후반 문헌 수'],['name','이름순']].map(([v,l])=>`<option value="${v}" ${radarFilters.sort===v?'selected':''}>${l}</option>`).join('')}</select></label></div><div class="radar-filters"><label>연구 맥락<select id="radar-field">${[['all','전체 문헌'],['health','독성·노출 키워드'],['occurrence','검출·매질 키워드'],['materials','소재·합성 키워드']].map(([v,l])=>`<option value="${v}" ${radarFilters.field===v?'selected':''}>${l}</option>`).join('')}</select></label>${['medium','endpoint','model'].map(d=>`<label>${{medium:'매질 언급',endpoint:'endpoint 언급',model:'대상·모델 언급'}[d]}<select id="radar-${d}"><option value="">전체</option>${Object.entries(state.labels).filter(([label])=>({medium:['water','wastewater','sediment','soil','air','dust','blood','urine'],endpoint:['mortality','endocrine','neural','reproduction','liver','genotoxicity'],model:['zebrafish','fish','mammal','human','cell','in_silico']})[d].includes(label)).map(([v,l])=>`<option value="${v}" ${radarFilters[d]===v?'selected':''}>${esc(l)}</option>`).join('')}</select></label>`).join('')}<label class="checkbox-label"><input id="radar-doc-filter" type="checkbox" ${radarFilters.exclude_reviews?'checked':''}>리뷰·preprint·정정·철회 표기 제외</label></div><details class="radar-criteria"><summary>잠정 조건 조정·계산 방식</summary><form id="radar-criteria-form">${[['min_recent','후반 최소 건수'],['min_baseline','전반 최소 건수'],['min_delta','최소 증가량'],['min_percent','최소 증가율 %']].map(([k,label])=>`<label>${label}<input name="${k}" type="number" min="0" max="10000" value="${radarFilters[k]}" required></label>`).join('')}<button type="submit" class="quiet">조건 적용</button></form><p class="muted">조건은 미검증 운영값입니다. 같은 일수의 전·후반을 비교하고 홀수 기간의 가운데 하루는 증가 비교에서만 제외합니다. 기저 0건의 증가율은 N/A입니다. 분야 전체 출판량·색인 지연을 보정하지 않았습니다.</p></details><div id="radar-results-body" aria-live="polite">자료를 확인하는 중입니다.</div></section>
<section class="radar-tools"><section class="panel"><h3>목록에 없는 물질 찾기</h3><p class="muted">공식 PubChem 명칭 매핑 후 조회 목록에 추가합니다. 문헌의 직접 대상 여부는 미검토입니다.</p><form id="radar-lookup"><label>물질명<input id="radar-lookup-name" placeholder="예: bisphenol F" minlength="2" maxlength="180" required></label><button type="submit">추가·문헌 수집</button></form><p class="muted">외부 DB 조회와 최대 20페이지·2,000개 원본 레코드의 문헌 수집을 요청합니다. 이름이 모호하면 자동 편입하지 않습니다.</p></section><section class="panel"><h3>환경 문헌에서 새 후보 탐색</h3><p class="muted">지정 물질 목록 밖에서도, 범위를 정한 환경 문헌 검색으로 명칭 후보를 제안합니다.</p><button id="radar-corpus-scan" class="quiet">선택 기간 문헌 탐색 · 최대 200건</button><p class="muted">키워드 검색의 제한된 페이지이며 전체 화학물질 발견·정규식 추출 성능 검증을 뜻하지 않습니다.</p><button id="radar-corpus-show" class="text-button">최근 탐색·후보 확인 →</button></section></section>
<details class="panel" id="radar-history"><summary>저장된 분석·후보 선정 기록</summary><div id="radar-history-body"></div></details>`;
 bindRadar();if(mainView==='radar')fetchRadar();pollJobs();
}
function bindRadar(){
 document.querySelectorAll('[data-radar-period]').forEach(b=>b.addEventListener('click',()=>{radarHistoryId=null;const p=time.preset(b.dataset.radarPeriod,$('radar-end').value);applyPeriod(p.start,p.end,p.unit,b.dataset.radarPeriod);}));
 $('radar-period-form').addEventListener('submit',e=>{e.preventDefault();radarHistoryId=null;applyPeriod($('radar-start').value,$('radar-end').value,$('radar-unit').value,'custom');if($('period-error').textContent)radarMessage($('period-error').textContent,true);});
 $('radar-search').addEventListener('input',e=>{radarFilters.search=e.target.value;radarHistoryId=null;clearTimeout(radarTimer);radarTimer=setTimeout(fetchRadar,220);});
 for(const k of ['status','sort','field','medium','endpoint','model'])$('radar-'+k).addEventListener('change',e=>{radarFilters[k]=e.target.value;radarHistoryId=null;fetchRadar();});
 $('radar-doc-filter').addEventListener('change',e=>{radarFilters.exclude_reviews=e.target.checked;radarHistoryId=null;fetchRadar();});
 $('radar-criteria-form').addEventListener('submit',e=>{e.preventDefault();const form=new FormData(e.target);for(const k of ['min_recent','min_baseline','min_delta','min_percent'])radarFilters[k]=Number(form.get(k));radarHistoryId=null;fetchRadar();});
 $('radar-refresh').addEventListener('click',()=>beginRefresh(state.chemicals.map(c=>c.id)).catch(e=>radarMessage(e.message,true)));
 $('radar-save').addEventListener('click',()=>saveRadar().catch(e=>radarMessage(e.message,true)));
 $('radar-lookup').addEventListener('submit',async e=>{e.preventDefault();const name=$('radar-lookup-name').value;try{radarMessage('PubChem 공식 명칭 매핑을 확인합니다.');const result=await radarPost('/api/radar/lookup',{name});if(!result.chemical_id){radarMessage(`자동 편입 보류: ${result.status}. 명칭·염·이성질체를 확인하세요.`,true);return;}await load();await beginRefresh([result.chemical_id]);}catch(error){radarMessage(error.message,true);}});
 $('radar-corpus-scan').addEventListener('click',async()=>{if(radarBusy)return;radarBusy=true;$('radar-corpus-scan').disabled=true;try{radarMessage('환경 문헌 검색 중입니다. 최대 2페이지를 요청합니다.');const result=await radarPost('/api/radar/corpus',{start:periodRange.start,end:periodRange.end,max_pages:2});radarMessage(`${result.fetched}/${result.source_total}개 검색 레코드 확인 · 명칭 후보 ${result.candidates}개 · ${statusLabel(result.status)}. 표본의 후보이며 증가 분석 전입니다.`);await showCorpus();}catch(e){radarMessage(e.message,true);}finally{radarBusy=false;if($('radar-corpus-scan'))$('radar-corpus-scan').disabled=false;}});
 $('radar-corpus-show').addEventListener('click',()=>showCorpus().catch(e=>radarMessage(e.message,true)));
 $('radar-history').addEventListener('toggle',()=>{if($('radar-history').open)loadRadarHistory();});
}
async function fetchRadar(){
 const request=++radarRequest;
 $('radar-save').disabled=true;
 try{const params=radarHistoryId?new URLSearchParams({report_id:radarHistoryId}):radarParams();const response=await fetch('/api/radar?'+params);const result=await response.json();if(!response.ok)throw Error(result.error);if(request!==radarRequest)return;radarData=result;paintRadar();}
 catch(e){if(request===radarRequest){radarData=null;$('radar-download').removeAttribute('href');$('radar-results-body').textContent=e.message;radarMessage(e.message,true);}}
 finally{if(request===radarRequest&&$('radar-save'))$('radar-save').disabled=!radarData;}
}
function paintRadar(){
 const data=radarData;
 radarCompared=new Set([...radarCompared].filter(id=>data.rows.some(r=>r.chemical_id===id)));
 $('radar-summary').innerHTML=`<span><strong>${data.total_monitored}</strong> 조회 물질</span><span><strong>${data.eligible}</strong> 기간 비교 가능</span><span><strong>${data.growing}</strong> 증가 조건 충족</span><span class="radar-date">${data.options.start} ~ ${data.options.end}</span>`;
 $('radar-download').href='/api/radar.csv?'+(radarHistoryId?new URLSearchParams({report_id:radarHistoryId}):radarParams());
 const w=data.windows;
 $('radar-results-body').innerHTML=`<p class="radar-scope">${esc(data.scope)} ${radarHistoryId?'저장된 분석 스냅샷을 보고 있습니다.':''}</p><p class="muted">전반 ${w.baseline_start} ~ ${w.baseline_end} / 후반 ${w.recent_start} ~ ${w.recent_end} · 각각 ${w.days_per_window}일. 키워드 필터는 실제 측정·효과 판정이 아닙니다.</p><div class="radar-compare-bar"><button id="radar-compare-selected" class="quiet">선택한 2~3개 물질 비교</button><span class="muted">이름 앞에서 선택하세요.</span></div><div class="table-scroll"><table class="radar-table"><thead><tr><th>물질·조회 상태</th><th>선택 기간 추이</th><th>전반 → 후반</th><th>증가량</th><th>증가율</th><th>신호</th><th>근거</th></tr></thead><tbody>${data.rows.map(r=>`<tr><td><input type="checkbox" data-radar-check="${r.chemical_id}" aria-label="${esc(r.name)} 비교 선택" ${radarCompared.has(r.chemical_id)?'checked':''}><button class="radar-name" data-radar-open="${esc(r.chemical_id)}">${esc(r.name)}</button><small>${r.run?`${r.total}건 · 자료 ${esc((r.run.finished_at||'').slice(0,10))}`:'미수집'}${r.unknown_dates?` · 상세 날짜 미상 ${r.unknown_dates}건`:''}</small></td><td><button class="spark-button" data-radar-open="${r.chemical_id}" aria-label="${esc(r.name)} 추이와 근거 확인">${miniChart(r)}<small class="spark-dates"><span>${data.options.start.slice(2)}</span><span>${data.options.end.slice(2)}</span></small></button></td><td>${r.comparable?`${r.baseline} → ${r.recent}`:'비교 보류'}</td><td>${r.delta===null?'—':`${r.delta>0?'+':''}${r.delta}건`}</td><td>${r.percent===null?'N/A':`${r.percent>0?'+':''}${r.percent}%`}</td><td><span class="signal-badge signal-${r.status}">${esc(r.status_label)}</span>${r.unstable?'<small>조건에 따라 상태 변동</small>':''}</td><td><button class="quiet" data-radar-open="${r.chemical_id}">이유·논문</button></td></tr>`).join('')||'<tr><td colspan="7">이 조건의 후보가 없습니다. 조회명·필터를 바꾸거나 물질을 추가하세요.</td></tr>'}</tbody></table></div><p class="muted">신호는 잠정 조건에 따른 기술적 비교이며 ‘검증된 hot’ 또는 위해성 순위가 아닙니다. 미수집 구간은 0건으로 채우지 않습니다.</p>`;
 $('radar-compare-selected').onclick=compareRadar;
 $('radar-results-body').querySelectorAll('[data-radar-check]').forEach(b=>b.onchange=()=>{if(b.checked&&radarCompared.size>=3){b.checked=false;radarMessage('한 번에 최대 3개 물질을 비교합니다.',true);return;}if(b.checked)radarCompared.add(b.dataset.radarCheck);else radarCompared.delete(b.dataset.radarCheck);});
 $('radar-results-body').querySelectorAll('[data-radar-open]').forEach(b=>b.addEventListener('click',()=>openRadar(b.dataset.radarOpen)));
}
function radarDialog(title,html){$('radar-dialog-title').textContent=title;$('radar-dialog-body').innerHTML=html;if(!$('radar-dialog').open)$('radar-dialog').showModal();}
$('radar-dialog-close').addEventListener('click',()=>$('radar-dialog').close());
function radarPaper(p){
 const [source,...ids]=p.source_id.split(':');
 const link=radarData?.dataset_kind==='demo'?'':`<a target="_blank" rel="noopener noreferrer" href="https://europepmc.org/article/${encodeURIComponent(source)}/${encodeURIComponent(ids.join(':'))}">자료원 ↗</a>${p.doi?` · <a target="_blank" rel="noopener noreferrer" href="https://doi.org/${encodeURIComponent(p.doi)}">DOI ↗</a>`:''}`;
 return `<article class="radar-paper"><p class="muted">${esc(p.date||'날짜 미상')} · ${esc(p.source_id)} · 검색 일치·직접 근거 미검토</p><h4>${esc(p.title)}</h4><p>${link}</p>${p.abstract?`<details><summary>초록 확인</summary><p>${esc(p.abstract)}</p></details>`:''}</article>`;
}
function openRadar(cid){
 const r=radarData.rows.find(r=>r.chemical_id===cid);radarCurrent=cid;
 radarDialog(r.name,`<div class="radar-detail-head">${miniChart(r)}<div><span class="signal-badge signal-${r.status}">${esc(r.status_label)}</span><p>전반 ${r.baseline}건 → 후반 ${r.recent}건 · ${r.delta===null?'비교 보류':`${r.delta>0?'+':''}${r.delta}건`} · ${r.percent===null?'증가율 N/A':r.percent+'%'}</p></div></div><h3>이렇게 표시한 이유</h3><ul>${r.reasons.map(x=>`<li>${esc(x.text)}</li>`).join('')}</ul><p class="muted">${esc(radarData.methodology)}</p><details><summary>검색식·자료 범위·기준 민감도</summary><pre>${esc(r.run?.query||'미수집')}</pre><p class="muted">run #${r.run?.id||'—'} · ${esc(r.coverage?`${r.coverage.start} ~ ${r.coverage.end}`:'수집 범위 없음')} · ${esc(r.run?.status||'미수집')} · ${esc(r.rules_version)}<br>스냅샷에서 문서유형·검토 상태로 제외 ${r.excluded_documents}건 · 선택 기간 관련 상세 날짜 미상 ${r.unknown_dates}건</p>${r.last_attempt?.error?`<p class="error">최근 시도 오류: ${esc(r.last_attempt.error)}</p>`:''}<ul>${r.sensitivity.map(p=>`<li>${esc(p.name)}: ${esc(radarLabels[p.status])} · 최소 후반 ${p.settings.min_recent}, 전반 ${p.settings.min_baseline}, 증가 ${p.settings.min_delta}, 증가율 ${p.settings.min_percent}%</li>`).join('')}</ul><p class="muted">민감도는 동일 데이터에 조건을 바꾼 결과이며 검증된 적정값을 뜻하지 않습니다.</p></details><h3>후반부에서 새로 언급된 맥락</h3>${r.new_contexts.length?`<ul>${r.new_contexts.map(x=>`<li>${esc(x.name)}: 전반 ${x.baseline} → 후반 ${x.recent}개 문헌</li>`).join('')}</ul>`:'<p class="muted">현재 키워드 범위에서 새 맥락 미확인입니다.</p>'}<p class="muted">언급의 신규성입니다. 새로 측정·발견됐거나 연구 공백이 입증됐다는 뜻이 아닙니다.</p><div class="radar-actions"><button id="radar-to-detail">물질 분석 열기</button><button id="radar-choose" class="quiet">선정·보류 이유 기록</button></div><div id="radar-choice"></div><h3>집계에 포함된 논문</h3><p class="muted">선택 기간·필터와 일치하는 저장 논문입니다. 포함·제외의 과학적 타당성은 별도 검토가 필요합니다.</p><div id="radar-articles"></div><button id="radar-articles-more" class="quiet" hidden>논문 더 보기</button>`);
 $('radar-to-detail').onclick=()=>{selected=cid;pointFilter=null;limit=15;facetFilters={};$('radar-dialog').close();render();switchMain('detail');};
 $('radar-choose').onclick=()=>showChoice(cid);
 let offset=0;$('radar-articles-more').onclick=()=>{offset+=20;loadRadarArticles(cid,offset).catch(e=>{$('radar-articles').textContent=e.message;});};
 loadRadarArticles(cid,0).catch(e=>{$('radar-articles').textContent=e.message;});
}
async function loadRadarArticles(cid,offset){
 const p=radarHistoryId?new URLSearchParams({report_id:radarHistoryId}):new URLSearchParams({...radarData.options,expected_report_id:radarData.report_id});p.set('chemical_id',cid);p.set('offset',offset);
 const res=await fetch('/api/radar/articles?'+p),data=await res.json();if(!res.ok)throw Error(data.error);if(radarCurrent!==cid||!$('radar-articles'))return;
 if(!offset)$('radar-articles').innerHTML=`<p class="muted">${data.total}건 · 최신 출판일 순</p>`;
 $('radar-articles').insertAdjacentHTML('beforeend',data.articles.map(radarPaper).join(''));$('radar-articles-more').hidden=offset+20>=data.total;
}
async function saveRadar(){
 if(!radarData)throw Error('분석 결과를 먼저 조회하세요.');
 if(!radarHistoryId){const result=await radarPost('/api/radar/report',{...radarData.options,expected_report_id:radarData.report_id});radarHistoryId=result.report_id;}
 radarMessage('현재 조건·건수·검색식·근거를 분석 스냅샷으로 저장했습니다.');await fetchRadar();return radarHistoryId;
}
async function showChoice(cid){
 $('radar-choice').innerHTML=`<form id="radar-choice-form"><label>판단<select name="status"><option value="selected">후속 검토 대상으로 선정</option><option value="deferred">보류</option><option value="rejected">선정에서 제외</option></select></label><label>기록자<input name="actor" minlength="2" maxlength="100" required></label><label>구체적인 이유<textarea name="reason" minlength="2" maxlength="2000" required></textarea></label><button>분석 저장·판단 기록</button><p id="radar-choice-message" role="status" class="muted">입력한 판단은 해당 분석 조건과 연결됩니다. 과학적 검증 완료를 뜻하지 않습니다.</p></form>`;
 $('radar-choice-form').onsubmit=async e=>{e.preventDefault();try{const report_id=await saveRadar();await radarPost('/api/radar/decision',{...Object.fromEntries(new FormData(e.target)),report_id,chemical_id:cid});$('radar-choice-message').textContent='판단과 이유를 기록했습니다. 저장된 분석 CSV에 함께 내보냅니다.';}catch(error){$('radar-choice-message').textContent=error.message;}};
}
async function beginRefresh(ids){
 const start=Number(periodRange.start.slice(0,4)),end=Number(periodRange.end.slice(0,4));
 radarMessage(`선택 연도 ${start} ~ ${end}의 자료를 갱신합니다. 같은 범위의 최신 완전 수집본은 재사용합니다.`);
 await radarPost('/api/radar/refresh',{chemical_ids:ids,start_year:start,end_year:end,max_pages:20});await pollJobs();
}
async function pollJobs(){
 clearTimeout(jobTimer);if(siteConfig.read_only)return;
 try{const response=await fetch('/api/radar/jobs');if(!response.ok)return;const jobs=await response.json(),job=jobs[0];if(!job||!$('radar-job'))return;
 const running=['queued','running'].includes(job.status),names=new Map(state.chemicals.map(c=>[c.id,c.name]));
 $('radar-job').innerHTML=`<details ${running?'open':''}><summary>최근 갱신 · ${esc(({queued:'대기',running:'진행 중',complete:'완료',completed_with_issues:'일부 결과 확인 필요',failed:'실패',interrupted:'중단'})[job.status]||job.status)} · ${job.progress.length}/${job.request.chemical_ids.length}개</summary><ul>${job.progress.map(p=>`<li>${esc(names.get(p.chemical_id)||p.chemical_id)}: ${esc(p.status==='reused'?'기존 수집본 재사용':statusLabel(p.status))}${p.fetched!==undefined?` · ${p.fetched}/${p.source_total}건`:''}${p.error?` · ${esc(p.error)}`:''}</li>`).join('')}</ul>${job.error?`<p class="error">${esc(job.error)}</p>`:''}</details>`;
 if(running){jobTimer=setTimeout(pollJobs,1800);window.radarSeenJob=job.id;}
 else if(window.radarSeenJob===job.id){window.radarSeenJob=null;radarHistoryId=null;await load();radarMessage('갱신이 끝났습니다. 실패·부분 수집 여부와 판정 보류 상태를 확인하세요.');}
 }catch(e){radarMessage('갱신 상태 확인 실패: '+e.message,true);}
}
async function showCorpus(){
 const response=await fetch('/api/radar/corpus'),data=await response.json();if(!response.ok)throw Error(data.error);
 const run=data.run;
 radarDialog('환경 문헌의 명칭 후보',`<p>${esc(data.scope)}</p>${run?`<p>${esc(run.start_date)} ~ ${esc(run.end_date)} · ${esc(statusLabel(run.status))} · 수신 ${run.fetched}/${run.source_total??'미상'}개</p><pre>${esc(run.query)}</pre>${run.error?`<p class="error">${esc(run.error)}</p>`:''}`:'<p>아직 탐색한 기록이 없습니다.</p>'}<p class="muted">명칭 후보는 정규식의 자동 제안입니다. 유전자·분석법·비물질 문자열이 섞일 수 있습니다. 근거를 확인한 뒤 명칭 조회를 요청하세요. 후보가 곧 hot 물질을 뜻하지 않습니다.</p>${data.candidates.map(c=>`<article class="candidate"><h3>${esc(c.name)}</h3><p class="muted">${c.evidence.records.length}개 문헌에서 추출 · 탐색 #${c.corpus_run_id}</p>${c.evidence.examples.map(e=>`<blockquote>${esc(e.snippet)}<br><a href="https://europepmc.org/search?query=${encodeURIComponent(e.record_key)}" target="_blank" rel="noopener noreferrer">${esc(e.record_key)} ↗</a></blockquote>`).join('')}<button class="quiet" data-corpus-name="${esc(c.name)}">이 명칭을 조회란으로 가져오기</button></article>`).join('')}`);
 $('radar-dialog-body').querySelectorAll('[data-corpus-name]').forEach(b=>b.onclick=()=>{$('radar-lookup-name').value=b.dataset.corpusName;$('radar-dialog').close();$('radar-lookup-name').focus();$('radar-lookup').scrollIntoView({block:'center'});});
}
async function loadRadarHistory(){
 try{const response=await fetch('/api/radar/reports'),data=await response.json();if(!response.ok)throw Error(data.error);
 $('radar-history-body').innerHTML=(data.reports.map(r=>`<article class="radar-saved"><button class="text-button" data-report="${r.id}">${esc(r.options.start)} ~ ${esc(r.options.end)} · ${esc(r.created_at)}</button><a href="/api/radar.csv?report_id=${r.id}" download>CSV ↓</a><a href="/api/radar?report_id=${r.id}" download="pioneer-radar-${r.id}.json">JSON ↓</a></article>`).join('')||'<p class="muted">저장된 분석이 없습니다.</p>')+`<h4>최근 선정 판단</h4>${data.decisions.map(d=>`<p>${esc(d.chemical_id)} · ${esc({selected:'선정',deferred:'보류',rejected:'제외'}[d.status])} · ${esc(d.actor)} · ${esc(d.reason)} · ${esc(d.created_at)}</p>`).join('')||'<p class="muted">아직 사람이 기록한 판단이 없습니다.</p>'}`;
 $('radar-history-body').querySelectorAll('[data-report]').forEach(b=>b.onclick=async()=>{radarHistoryId=b.dataset.report;await fetchRadar();if(!radarData)return;const {start,end,unit,...filters}=radarData.options;periodRange={start,end,unit};periodKind='custom';radarFilters={...radarFilters,...filters};renderHotRadar();radarMessage('저장 당시의 조건·자료로 조회합니다. 새 기간·필터 적용 시 현재 자료 조회로 돌아갑니다.');$('radar-results-body').scrollIntoView({block:'start'});});
 }catch(e){$('radar-history-body').textContent=e.message;}
}

function compareRadar(){
 const rows=radarData.rows.filter(r=>radarCompared.has(r.chemical_id));
 if(rows.length<2){radarMessage('현재 결과에서 비교할 물질 2~3개를 선택하세요.',true);return;}
 const cells=f=>rows.map(r=>`<td>${f(r)}</td>`).join('');
 radarDialog('후보 비교 · 같은 기간과 필터',`<p>${esc(radarData.options.start)} ~ ${esc(radarData.options.end)} · 각 ${radarData.windows.days_per_window}일 비교</p><p class="muted">각 선그래프의 세로축은 자체 문헌 수에 맞춘 크기입니다. 문헌 수 비교는 수치로 확인하세요. 물질마다 검색명·동의어 범위가 달라 직접 중요성 순위를 뜻하지 않습니다.</p><div class="table-scroll"><table class="radar-table radar-compare-table"><thead><tr>${rows.map(r=>`<th>${esc(r.name)}</th>`).join('')}</tr></thead><tbody><tr>${cells(r=>miniChart(r))}</tr><tr>${cells(r=>esc(r.status_label))}</tr><tr>${cells(r=>`전반 ${r.baseline} → 후반 ${r.recent}건<br>${r.delta===null?'비교 보류':`${r.delta>0?'+':''}${r.delta}건`} · ${r.percent===null?'N/A':r.percent+'%'}`)}</tr><tr>${cells(r=>`<ul>${r.reasons.map(x=>`<li>${esc(x.text)}</li>`).join('')}</ul>`)}</tr><tr>${cells(r=>r.new_contexts.map(x=>`${esc(x.name)}: ${x.recent}개 문헌`).join('<br>')||'현재 키워드 범위에서 새 맥락 미확인')}</tr><tr>${cells(r=>`<button class="quiet" data-compare-open="${r.chemical_id}">이유·논문 확인</button>`)}</tr></tbody></table></div><p class="muted">동일한 세부 매질·모델·생애단계와 실제 시험 결과의 비교는 물질 분석에서 검토하세요.</p>`);
 $('radar-dialog-body').querySelectorAll('[data-compare-open]').forEach(b=>b.onclick=()=>openRadar(b.dataset.compareOpen));
}
