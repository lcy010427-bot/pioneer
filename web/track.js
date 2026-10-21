'use strict';
/* PIONEER 지정 물질 추적 페이지.
   /track                       추적 중인 물질 + 스크리닝 후보 고르기
   /track?candidate=ID&start=&end=   스크리닝 후보를 추적 대상으로 등록
   /track?chemical=ID[&start=&end=&unit=]  추적 결과 (언급량 변화 + 보고 내용) */

const app = document.getElementById('app');
const params = new URLSearchParams(location.search);
let config = { read_only: false, admin_url: null };
let pollTimer = null;

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function svg(tag, attrs, ...children) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  for (const c of children.flat()) if (c) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

async function api(path, body) {
  const options = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {};
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}

function show(...nodes) { app.replaceChildren(...nodes.filter(n => n !== null && n !== undefined && n !== false)); }
function errorBox(err) { return h('p', { class: 'notice error', role: 'alert' }, err.message || String(err)); }
function fmt(n) { return n === null || n === undefined ? '–' : n.toLocaleString('ko-KR'); }
function pct(p) { return p === null || p === undefined ? '증가율 없음' : `${p > 0 ? '+' : ''}${p}%`; }
function signed(n) { return n === null || n === undefined ? '–' : `${n > 0 ? '+' : ''}${n}`; }
function link(params) { return '/track?' + new URLSearchParams(params).toString(); }

function articleUrl(p) {
  if (p.doi) return 'https://doi.org/' + p.doi;
  const [src, id] = (p.record_key || '').split(':');
  return src && id ? `https://europepmc.org/article/${src}/${id}` : null;
}

function operatorNote() {
  if (!config.read_only) return null;
  return h('p', { class: 'notice' }, '조회 전용 화면입니다. 추적을 시작하거나 다시 수집하려면 ',
    config.admin_url ? h('a', { href: config.admin_url }, '운영자로 로그인') : '운영자 사이트를 이용',
    '하세요.');
}

/* ---------- 1. 시작 화면: 추적 목록 + 스크리닝 후보 고르기 ---------- */

async function renderHome() {
  const listBox = h('section', { class: 'block' }, h('h2', {}, '추적 중인 물질'), h('p', { class: 'muted' }, '불러오는 중…'));
  const pickBox = h('section', { class: 'block' }, h('h2', {}, '스크리닝 후보에서 고르기'), h('p', { class: 'muted' }, '불러오는 중…'));
  show(h('h1', { class: 'title' }, '지정 물질 추적'),
    h('p', { class: 'lede' }, '전체 스크리닝에서 눈에 띈 물질을 골라, 그 물질만의 언급량 변화와 문헌이 보고하는 내용을 따로 추적합니다.'),
    operatorNote(), listBox, pickBox);

  try {
    const { chemicals } = await api('/api/track/list');
    const items = chemicals.length ? h('ul', { class: 'plain' }, chemicals.map(c => h('li', {},
      h('a', { href: link({ chemical: c.id }) }, c.name),
      h('span', { class: 'muted' }, ` 문헌 ${fmt(c.papers)}건`,
        c.from_screening_run ? `, 스크리닝 #${c.from_screening_run}에서 선택` : '',
        c.last_status && c.last_status !== 'complete' ? `, 최근 수집 ${statusWord(c.last_status)}` : ''))))
      : h('p', { class: 'muted' }, '아직 추적 중인 물질이 없습니다. 아래 스크리닝 후보에서 골라 시작하세요.');
    listBox.replaceChildren(h('h2', {}, '추적 중인 물질'), items);
  } catch (e) { listBox.replaceChildren(h('h2', {}, '추적 중인 물질'), errorBox(e)); }

  try {
    const meta = await api('/api/screen/meta');
    const runs = meta.runs.filter(r => r.status === 'complete' || r.status === 'partial');
    if (!runs.length) {
      pickBox.replaceChildren(h('h2', {}, '스크리닝 후보에서 고르기'),
        h('p', { class: 'muted' }, '저장된 스크리닝 결과가 없습니다. ', h('a', { href: '/' }, '전체 스크리닝'), '을 먼저 실행하세요.'));
      return;
    }
    const select = h('select', { id: 'run' }, runs.map(r => h('option', { value: r.id },
      `${r.start_date} ~ ${r.end_date}${r.status === 'partial' ? ' (부분 수집)' : ''}`)));
    const search = h('input', { id: 'q', type: 'search', placeholder: '물질명 검색', autocomplete: 'off' });
    const results = h('div', {});
    const load = async () => {
      const run = runs.find(r => String(r.id) === select.value);
      results.replaceChildren(h('p', { class: 'muted' }, '불러오는 중…'));
      try {
        const data = await api('/api/screen?' + new URLSearchParams({ start: run.start_date, end: run.end_date, search: search.value.trim() }));
        results.replaceChildren(candidateTable(data.rows.slice(0, 40), run));
      } catch (e) { results.replaceChildren(errorBox(e)); }
    };
    let t;
    select.addEventListener('change', load);
    search.addEventListener('input', () => { clearTimeout(t); t = setTimeout(load, 300); });
    pickBox.replaceChildren(h('h2', {}, '스크리닝 후보에서 고르기'),
      h('div', { class: 'controls' },
        h('label', { for: 'run' }, '스크리닝 기간', select),
        h('label', { for: 'q' }, '검색', search)),
      results);
    load();
  } catch (e) { pickBox.replaceChildren(h('h2', {}, '스크리닝 후보에서 고르기'), errorBox(e)); }
}

function statusWord(s) {
  return { complete: '완료', partial: '부분 수집', failed: '실패', running: '진행 중', queued: '대기' }[s] || s;
}

function candidateTable(rows, run) {
  if (!rows.length) return h('p', { class: 'muted' }, '조건에 맞는 후보가 없습니다.');
  return h('div', { class: 'scroll' }, h('table', {},
    h('thead', {}, h('tr', {}, h('th', {}, '후보'), h('th', { class: 'num' }, '전반'), h('th', { class: 'num' }, '후반'),
      h('th', { class: 'num' }, '변화'), h('th', {}, ''))),
    h('tbody', {}, rows.map(r => h('tr', {},
      h('td', {}, r.name, r.status === 'growing' ? h('span', { class: 'tag up' }, '증가') : r.status === 'early' ? h('span', { class: 'tag new' }, '초기 신호') : null),
      h('td', { class: 'num' }, fmt(r.baseline)), h('td', { class: 'num' }, fmt(r.recent)),
      h('td', { class: 'num' }, signed(r.delta)),
      h('td', {}, r.tracked_chemical_id
        ? h('a', { href: link({ chemical: r.tracked_chemical_id }) }, '추적 결과 보기')
        : h('a', { href: link({ candidate: r.id, start: run.start_date, end: run.end_date }) }, '추적하기')))))));
}

/* ---------- 2. 후보 → 추적 시작 ---------- */

async function renderCandidate(id, start, end) {
  let row = null;
  if (start && end) {
    try {
      const data = await api('/api/screen?' + new URLSearchParams({ start, end, ids: id }));
      row = data.rows.find(r => r.id === id) || null;
    } catch (e) { /* 근거 미리보기만 생략 */ }
  }
  if (row && row.tracked_chemical_id) { location.replace(link({ chemical: row.tracked_chemical_id })); return; }

  const thisYear = new Date().getFullYear();
  const endYear = Math.min(thisYear, Number((end || String(thisYear)).slice(0, 4)));
  const fromY = h('input', { id: 'from', type: 'number', min: 1900, max: thisYear, value: endYear - 4, required: true });
  const toY = h('input', { id: 'to', type: 'number', min: 1900, max: thisYear, value: endYear, required: true });
  const pages = h('select', { id: 'pages' }, [5, 10, 20].map(n => h('option', { value: n, selected: n === 10 }, `${n * 100}건까지`)));
  const nameInput = h('input', { id: 'name', type: 'text', maxlength: 180, placeholder: '비워두면 스크리닝 후보명을 사용' });
  const status = h('div', { 'aria-live': 'polite' });
  const button = h('button', { type: 'submit', class: 'primary', disabled: config.read_only }, '이 물질 추적 시작');

  const form = h('form', { class: 'block', onsubmit: async ev => {
    ev.preventDefault();
    button.disabled = true;
    status.replaceChildren(h('p', { class: 'muted' }, 'PubChem에서 물질을 확인하고 수집을 시작하는 중…'));
    const body = { candidate_id: id, start_year: Number(fromY.value), end_year: Number(toY.value), max_pages: Number(pages.value) };
    if (nameInput.value.trim()) body.name = nameInput.value.trim();
    if (start && end) { body.screening_start = start; body.screening_end = end; }
    try {
      const r = await api('/api/screen/track', body);
      if (r.status === 'unresolved' || r.status === 'lookup_failed') {
        status.replaceChildren(h('div', { class: 'notice error', role: 'alert' }, h('p', {}, r.message),
          h('ul', {}, r.tried.map(t => h('li', {}, `${t.name}: ${triedWord(t)}`)))));
        nameInput.focus();
        button.disabled = false;
        return;
      }
      if (r.job_error) sessionStorage.setItem('pioneer-track-note', r.job_error);
      location.assign(link({ chemical: r.chemical_id, ...(start && end ? { start, end } : {}) }));
    } catch (e) { status.replaceChildren(errorBox(e)); button.disabled = false; }
  } },
    h('div', { class: 'controls' },
      h('label', { for: 'from' }, '수집 시작 연도', fromY),
      h('label', { for: 'to' }, '수집 끝 연도', toY),
      h('label', { for: 'pages' }, '최대 문헌 수', pages)),
    h('label', { for: 'name', class: 'wide' }, '검색에 쓸 물질명 (선택)', nameInput),
    h('p', { class: 'muted small' }, '물질명은 PubChem에서 하나의 화합물로 확인될 때만 추적 목록에 들어갑니다. 약어(예: BPS)는 자동으로 쓰지 않습니다.'),
    button, status);

  show(h('p', {}, h('a', { href: '/track' }, '추적 목록으로')),
    h('h1', { class: 'title' }, row ? row.name : '스크리닝 후보'),
    row ? h('p', { class: 'lede' }, `스크리닝 ${start} ~ ${end}에서 전반 ${fmt(row.baseline)}건 → 후반 ${fmt(row.recent)}건 (${signed(row.delta)}).`) : null,
    operatorNote(), form,
    row && row.evidence.length ? h('section', { class: 'block' }, h('h2', {}, '스크리닝에서 찾은 근거'),
      h('ul', { class: 'papers' }, row.evidence.map(e => paperItem({ ...e, title: e.title }, e.snippet)))) : null);
}

function triedWord(t) {
  return { ambiguous: 'PubChem에서 여러 화합물과 일치' + (t.cids ? ` (CID ${t.cids.join(', ')})` : ''),
    not_found: 'PubChem에서 찾지 못함', failed: 'PubChem 조회 실패', invalid_name: '물질명 형식 오류' }[t.status] || t.status;
}

/* ---------- 3. 추적 결과 ---------- */

async function renderChemical(id) {
  clearTimeout(pollTimer);
  const query = { chemical: id };
  for (const k of ['start', 'end', 'unit']) if (params.get(k)) query[k] = params.get(k);
  let d;
  try { d = await api('/api/track?' + new URLSearchParams(query)); }
  catch (e) { show(h('p', {}, h('a', { href: '/track' }, '추적 목록으로')), errorBox(e)); return; }

  const running = d.job && (d.job.status === 'queued' || d.job.status === 'running');
  const note = sessionStorage.getItem('pioneer-track-note');
  sessionStorage.removeItem('pioneer-track-note');

  show(
    h('p', {}, h('a', { href: '/track' }, '추적 목록으로')),
    header(d),
    note ? h('p', { class: 'notice' }, `수집이 시작되지 않았습니다: ${note}`) : null,
    running ? h('p', { class: 'notice' }, `문헌을 수집하고 있습니다 (${d.job.years.join('–')}년). 끝나면 이 화면이 자동으로 갱신됩니다.`) : null,
    jobProblem(d.job),
    d.snapshot ? trendSection(d) : h('section', { class: 'block' }, h('p', { class: 'muted' }, running ? '첫 수집이 끝나면 결과가 나타납니다.' : '수집된 문헌이 없습니다. 아래에서 수집을 시작하세요.')),
    controls(d),
    d.snapshot ? contentSection(d) : null,
    d.snapshot ? papersSection(d) : null,
    h('section', { class: 'block' }, h('h2', {}, '해석할 때 주의할 점'), h('ul', { class: 'muted' }, d.limitations.map(x => h('li', {}, x)))));

  if (running) pollTimer = setTimeout(() => renderChemical(id), 4000);
}

function header(d) {
  const c = d.chemical;
  const facts = [c.cid ? h('a', { href: `https://pubchem.ncbi.nlm.nih.gov/compound/${c.cid}`, rel: 'noopener', target: '_blank' }, `PubChem CID ${c.cid}`) : null,
    c.formula ? h('span', {}, c.formula) : null,
    h('span', {}, '검색어 ', c.terms.map(t => `“${t}”`).join(', '))].filter(Boolean);
  return h('div', { class: 'head' },
    h('h1', { class: 'title' }, c.name),
    h('p', { class: 'facts' }, facts.flatMap((f, i) => i ? [h('span', { class: 'sep', 'aria-hidden': 'true' }, '/'), f] : [f])),
    d.origin.kind === 'screening'
      ? h('p', { class: 'muted' }, `스크리닝 #${d.origin.run_id} (${d.origin.start} ~ ${d.origin.end})에서 골라 추적을 시작했습니다.`)
      : null);
}

function jobProblem(job) {
  if (!job || !job.progress) return null;
  const s = job.progress.status;
  if (s === 'failed') return h('p', { class: 'notice error', role: 'alert' }, '최근 수집이 실패했습니다. 기존 결과가 있으면 그대로 보여줍니다. 잠시 뒤 다시 수집하세요.');
  if (s === 'partial') return h('p', { class: 'notice' }, `최근 수집이 일부만 끝났습니다 (총 ${fmt(job.progress.source_total)}건 중 ${fmt(job.progress.fetched)}건). 최대 문헌 수를 늘려 다시 수집하면 증가 판정이 가능해집니다.`);
  return null;
}

function trendSection(d) {
  const t = d.trend, w = d.windows;
  const verdict = t.comparable
    ? `${t.status_label}: ${fmt(t.baseline)}건에서 ${fmt(t.recent)}건으로 ${signed(t.delta)}건 (${pct(t.percent)})`
    : `${t.status_label}`;
  return h('section', { class: 'block trend' },
    h('h2', {}, '언급량 변화'),
    h('p', { class: `verdict s-${t.status}` }, verdict),
    h('div', { class: 'halves' },
      h('div', { class: 'half base' }, h('span', { class: 'n' }, fmt(t.baseline)), h('span', {}, `전반 ${w.baseline_start} ~ ${w.baseline_end}`)),
      h('div', { class: 'half recent' }, h('span', { class: 'n' }, fmt(t.recent)), h('span', {}, `후반 ${w.recent_start} ~ ${w.recent_end}`))),
    chart(t.buckets, w),
    h('ul', { class: 'reasons muted' }, t.reasons.map(r => h('li', {}, r.text)),
      t.unknown_dates ? h('li', {}, `날짜를 알 수 없는 문헌 ${t.unknown_dates}건 때문에 비교를 보류합니다.`) : null,
      t.excluded ? h('li', {}, `리뷰·프리프린트·정정 등 ${t.excluded}건은 집계에서 뺐습니다.`) : null),
    h('p', { class: 'muted small' }, '기준을 바꿔 본 판정: ',
      t.sensitivity.map((s, i) => `${i ? ', ' : ''}${s.name} 기준 ${sensitivityWord(s.status)}`).join('')));
}

function sensitivityWord(s) {
  return { growing: '증가', early: '초기 신호', low_volume: '저빈도', declining: '감소', not_flagged: '해당 없음', insufficient: '보류' }[s] || s;
}

function chart(buckets, w) {
  const W = 900, H = 200, pad = { l: 34, r: 8, t: 10, b: 26 };
  const max = Math.max(1, ...buckets.map(b => b.count || 0));
  const bw = (W - pad.l - pad.r) / buckets.length;
  const y = v => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const g = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img',
    'aria-label': `기간별 문헌 수 막대그래프, 최대 ${max}건` });
  for (const v of [0, Math.round(max / 2), max]) {
    g.append(svg('line', { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: 'grid' }),
      svg('text', { x: pad.l - 6, y: y(v) + 4, 'text-anchor': 'end', class: 'tick' }, String(v)));
  }
  buckets.forEach((b, i) => {
    const x = pad.l + i * bw + bw * 0.12, width = Math.max(1, bw * 0.76);
    const side = b.end <= w.baseline_end ? 'base' : b.start >= w.recent_start ? 'recent' : 'mid';
    const title = svg('title', {}, `${b.start} ~ ${b.end}: ${b.count === null ? '수집 범위 밖' : b.count + '건'}${b.partial ? ' (일부 기간)' : ''}`);
    if (b.count === null) g.append(svg('rect', { x, y: y(max) , width, height: H - pad.b - y(max), class: 'bar none' }, title));
    else g.append(svg('rect', { x, y: y(b.count), width, height: Math.max(0, H - pad.b - y(b.count)), class: `bar ${side}${b.partial ? ' partial' : ''}` }, title));
  });
  const split = buckets.findIndex(b => b.start >= w.recent_start);
  if (split > 0) {
    const x = pad.l + split * bw;
    g.append(svg('line', { x1: x, x2: x, y1: pad.t - 6, y2: H - pad.b + 4, class: 'divider' }));
  }
  const labelAt = [0, Math.floor(buckets.length / 2), buckets.length - 1].filter((v, i, a) => a.indexOf(v) === i);
  for (const i of labelAt) {
    g.append(svg('text', { x: pad.l + i * bw + bw / 2, y: H - 8, 'text-anchor': i === 0 ? 'start' : i === buckets.length - 1 ? 'end' : 'middle', class: 'tick' }, buckets[i].start.slice(0, 7)));
  }
  return h('div', { class: 'chartbox' }, g);
}

function controls(d) {
  const o = d.options;
  const start = h('input', { id: 's', type: 'date', value: o.start, required: true });
  const end = h('input', { id: 'e', type: 'date', value: o.end, required: true });
  const unit = h('select', { id: 'u' }, [['month', '월'], ['quarter', '분기'], ['week', '주']].map(([v, l]) => h('option', { value: v, selected: v === o.unit }, l)));
  const view = h('form', { class: 'controls', onsubmit: ev => {
    ev.preventDefault();
    location.assign(link({ chemical: d.chemical.id, start: start.value, end: end.value, unit: unit.value }));
  } },
    h('label', { for: 's' }, '비교 시작일', start), h('label', { for: 'e' }, '비교 종료일', end),
    h('label', { for: 'u' }, '막대 단위', unit), h('button', { type: 'submit' }, '이 기간으로 보기'));

  const out = h('div', { 'aria-live': 'polite' });
  const refresh = config.read_only ? null : h('button', { type: 'button', onclick: async ev => {
    ev.target.disabled = true;
    try {
      await api('/api/radar/refresh', { chemical_ids: [d.chemical.id], start_year: Number(o.start.slice(0, 4)),
        end_year: Math.min(new Date().getFullYear(), Number(o.end.slice(0, 4))), max_pages: 20, force: true });
      renderChemical(d.chemical.id);
    } catch (e) { out.replaceChildren(errorBox(e)); ev.target.disabled = false; }
  } }, d.snapshot ? '이 기간 다시 수집' : '이 기간 수집 시작');
  const snap = d.snapshot;
  return h('section', { class: 'block' }, h('h2', {}, '비교 기간'),
    h('p', { class: 'muted small' }, '선택한 기간을 같은 일수로 반씩 나눠 전반과 후반의 문헌 수를 비교합니다.'),
    view,
    snap ? h('p', { class: 'muted small' }, `사용 중인 수집본 #${snap.run_id}: ${statusWord(snap.status)}, 검색 결과 ${fmt(snap.source_total)}건 중 ${fmt(snap.unique_count)}건 저장, 범위 ${snap.coverage.start} ~ ${snap.coverage.end}`) : null,
    refresh, out);
}

function contentSection(d) {
  const groups = d.content.filter(g => g.items.length);
  return h('section', { class: 'block' },
    h('h2', {}, '문헌이 보고하는 내용'),
    h('p', { class: 'muted small' }, '제목·초록에 나온 키워드 기준입니다. 항목을 열면 실제 문장을 볼 수 있습니다. 전반 → 후반 문헌 수.'),
    groups.length ? groups.map(g => h('div', { class: 'dim' }, h('h3', {}, g.name),
      g.items.slice(0, 8).map(item => {
        const max = Math.max(1, ...g.items.map(x => Math.max(x.baseline, x.recent)));
        return h('details', {},
          h('summary', {},
            h('span', { class: 'label' }, item.name, item.new ? h('span', { class: 'tag new' }, '후반에 새로 등장') : null),
            h('span', { class: 'pair', 'aria-label': `전반 ${item.baseline}건, 후반 ${item.recent}건` },
              h('span', { class: 'mini base', 'data-w': Math.round(item.baseline / max * 100) }),
              h('span', { class: 'mini recent', 'data-w': Math.round(item.recent / max * 100) })),
            h('span', { class: 'num' }, `${item.baseline} → ${item.recent}`)),
          item.examples.length ? h('ul', { class: 'quotes' }, item.examples.map(x => h('li', {},
            h('blockquote', {}, highlight(x.snippet, x.matched)),
            h('cite', {}, articleUrl(x) ? h('a', { href: articleUrl(x), rel: 'noopener', target: '_blank' }, x.title) : x.title, ` (${x.date})`))))
            : h('p', { class: 'muted small' }, '부정·가설 표현이 없는 예문이 없습니다.'));
      })))
      : h('p', { class: 'muted' }, '이 기간 문헌에서 맥락 키워드가 나오지 않았습니다.'));
}

function highlight(text, term) {
  const i = term ? text.toLowerCase().indexOf(term.toLowerCase()) : -1;
  if (i < 0) return text;
  return [text.slice(0, i), h('mark', {}, text.slice(i, i + term.length)), text.slice(i + term.length)];
}

function paperItem(p, snippet) {
  const url = articleUrl(p);
  return h('li', {},
    url ? h('a', { href: url, rel: 'noopener', target: '_blank' }, p.title) : p.title,
    h('span', { class: 'muted small' }, ' ', [p.date, p.journal].filter(Boolean).join(', ')),
    snippet ? h('p', { class: 'muted small' }, snippet) : null);
}

function papersSection(d) {
  return h('section', { class: 'block' },
    h('h2', {}, `이 기간 문헌 ${fmt(d.trend.total)}건 중 최근 ${d.papers.length}건`),
    d.papers.length ? h('ul', { class: 'papers' }, d.papers.map(p => paperItem(p))) : h('p', { class: 'muted' }, '이 기간 문헌이 없습니다.'));
}

/* 미니 막대 폭은 CSS 변수 대신 CSSOM으로 설정 (CSP: style 속성 금지) */
new MutationObserver(() => {
  for (const el of app.querySelectorAll('.mini[data-w]')) { el.style.width = el.dataset.w + '%'; el.removeAttribute('data-w'); }
}).observe(app, { childList: true, subtree: true });

(async function start() {
  try { config = await api('/api/config'); } catch (e) { /* 기본값 사용 */ }
  if (params.get('chemical')) renderChemical(params.get('chemical'));
  else if (params.get('candidate')) renderCandidate(params.get('candidate'), params.get('start'), params.get('end'));
  else renderHome();
})();
