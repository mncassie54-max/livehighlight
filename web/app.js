'use strict';

// --------------------------------------------------------------------- 유틸
const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
  const data = await r.json().catch(() => ({ error: 'JSON 파싱 실패' }));
  if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}
const post = (p, body) => api(p, { method: 'POST', body: JSON.stringify(body || {}) });

function fmtTC(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? h + ':' : '') + String(m).padStart(h ? 2 : 1, '0') + ':' + String(s).padStart(2, '0');
}
function fmtSize(n) {
  if (!n) return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(n < 10 && i > 0 ? 1 : 0) + u[i];
}
function toast(msg, bad) { console[bad ? 'error' : 'log'](msg); if (bad) alert(msg); }

const WLABEL = {
  chat_rate: '💬 채팅 폭주', chat_laugh: '😂 채팅 ㅋㅋ', chat_hype: '🔥 채팅 흥분',
  chat_clip: '✂️ 클립 요청', chat_paid: '💸 슈퍼챗', mic_excite: '📢 마이크 음량', mic_laugh: '🤣 웃음 진동',
};
const PLABEL = {
  threshold: ['검출 임계값', 0.5, 5, 0.1], min_gap: ['후보 최소 간격(초)', 10, 300, 5],
  pre_roll: ['앞 여유(초)', 0, 90, 1], post_roll: ['뒤 여유(초)', 0, 90, 1],
  max_len: ['최대 길이(초)', 20, 600, 10], top_n: ['최대 후보 수', 5, 200, 5],
  smooth: ['스무딩(초)', 2, 30, 1],
};
const SERIES_COLOR = {
  score: '#4c8dff', chat_rate: '#7f8ea6', chat_laugh: '#39c07f', chat_hype: '#e6a13c',
  chat_clip: '#ff6bd6', chat_paid: '#ffd24c', mic_excite: '#8f6bff', mic_laugh: '#ef5b5b',
};

// --------------------------------------------------------------------- 상태
const S = { projects: [], proj: null, curves: null, poll: null, fsPath: null };

// --------------------------------------------------------------------- 환경 점검
async function loadDoctor() {
  try {
    const d = await api('/api/doctor');
    const box = $('#doctor'); box.innerHTML = '';
    box.appendChild(el('span', 'badge ' + (d.ffmpeg ? 'ok' : 'bad'), d.ffmpeg ? 'ffmpeg ' + (d.ffmpeg_version || '').replace('ffmpeg version ', '').split(' ')[0] : 'ffmpeg 없음'));
    box.appendChild(el('span', 'badge ' + (d.yt_dlp ? 'ok' : 'bad'), d.yt_dlp ? 'yt-dlp ' + d.yt_dlp : 'yt-dlp 없음'));
    if (!d.ffprobe) box.appendChild(el('span', 'badge', 'ffprobe 대체모드'));
  } catch (e) { /* noop */ }
}

// --------------------------------------------------------------------- 프로젝트 목록
async function loadProjects() {
  const { projects } = await api('/api/projects');
  S.projects = projects;
  const ul = $('#project-list'); ul.innerHTML = '';
  if (!projects.length) ul.appendChild(el('li', 'dim', '아직 없습니다'));
  projects.forEach((p) => {
    const li = el('li', S.proj && S.proj.id === p.id ? 'active' : '');
    li.appendChild(el('span', '', p.name));
    li.appendChild(el('span', 'pm', (p.duration ? fmtTC(p.duration) + ' · ' : '') + (p.n_segments ? '후보 ' + p.n_segments : '미분석')));
    li.onclick = () => openProject(p.id);
    ul.appendChild(li);
  });
}

// --------------------------------------------------------------------- 파일 브라우저
async function loadFs(path) {
  const d = await api('/api/fs?path=' + encodeURIComponent(path || ''));
  S.fsPath = d.path;
  $('#fs-path').value = d.path;
  const sc = $('#fs-shortcuts'); sc.innerHTML = '';
  d.shortcuts.forEach((s) => { const b = el('button', 'ghost', s.name); b.onclick = () => loadFs(s.path); sc.appendChild(b); });
  const dirs = $('#fs-dirs'); dirs.innerHTML = '';
  d.dirs.forEach((x) => { const li = el('li', '', '📁 ' + x.name); li.onclick = () => loadFs(x.path); dirs.appendChild(li); });
  if (!d.dirs.length) dirs.appendChild(el('li', 'dim', '(하위 폴더 없음)'));
  const files = $('#fs-files'); files.innerHTML = '';
  d.files.forEach((x) => {
    const li = el('li', '', '🎬 ' + x.name);
    li.appendChild(el('span', 'sz', fmtSize(x.size)));
    li.onclick = () => {
      files.querySelectorAll('li').forEach((n) => n.classList.remove('sel'));
      li.classList.add('sel');
      $('#new-video').value = x.path;
      if (!$('#new-name').value) $('#new-name').value = x.name.replace(/\.[^.]+$/, '');
    };
    files.appendChild(li);
  });
  if (!d.files.length) files.appendChild(el('li', 'dim', '(이 폴더에 영상 파일 없음)'));
  $('#fs-up').disabled = !d.parent;
  $('#fs-up').onclick = () => loadFs(d.parent);
}

// --------------------------------------------------------------------- 프로젝트 화면
async function openProject(pid) {
  const { project } = await api('/api/projects/' + encodeURIComponent(pid));
  S.proj = project;
  $('#view-new').classList.add('hidden');
  $('#view-proj').classList.remove('hidden');
  renderProject();
  loadProjects();
  refreshCurves();
  startPolling();
}

function renderProject() {
  const p = S.proj, m = p.media || {};
  $('#p-name').textContent = p.name;
  const bits = [];
  if (m.duration) bits.push('길이 ' + fmtTC(m.duration));
  if (m.width) bits.push(m.width + '×' + m.height);
  if (m.fps) bits.push(m.fps.toFixed(2) + 'fps');
  if ((m.audio_streams || []).length) bits.push('오디오 ' + m.audio_streams.length + '트랙');
  bits.push(p.video_path);
  $('#p-meta').textContent = bits.join(' · ');
  if (p.probe_error) $('#p-meta').textContent += ' ⚠ ' + p.probe_error;

  // 트랙 선택
  const opts = (m.audio_streams || []).map((s, i) => {
    const t = ['트랙 ' + (i + 1)];
    if (s.title) t.push(s.title);
    if (s.channels) t.push(s.channels === 1 ? 'mono' : s.channels === 2 ? 'stereo' : s.channels + 'ch');
    if (s.codec) t.push(s.codec);
    return { v: s.index, t: t.join(' · ') };
  });
  fillSelect($('#sel-mic'), opts, p.mic_stream);
  fillSelect($('#sel-mix'), opts, p.mix_stream);
  $('#p-url').value = p.youtube_url || '';

  // 통계
  const st = $('#stats-box'); st.innerHTML = '';
  const cs = p.chat_stats, as = p.audio_stats;
  if (cs && cs.count) {
    st.appendChild(html('span', '채팅 <b>' + cs.count.toLocaleString() + '</b>개'));
    st.appendChild(html('span', 'ㅋㅋ <b>' + cs.laugh.toLocaleString() + '</b>'));
    st.appendChild(html('span', '클립 요청 <b>' + cs.clip_requests + '</b>'));
    st.appendChild(html('span', '슈퍼챗/멤버십 <b>' + cs.paid + '</b>'));
    if (cs.per_min) st.appendChild(html('span', '분당 <b>' + cs.per_min + '</b>개'));
    if (cs.unique_authors) st.appendChild(html('span', '참여자 <b>' + cs.unique_authors + '</b>명'));
  }
  if (as) {
    st.appendChild(html('span', '발화 비율 <b>' + Math.round(as.voiced_ratio * 100) + '%</b>'));
    st.appendChild(html('span', '음량 중앙 <b>' + as.median_db + 'dB</b>'));
  }

  // 정렬
  const off = p.offset_sec || 0;
  $('#off-num').value = off;
  $('#off-range').value = Math.max(-3600, Math.min(3600, off));
  const ai = [];
  ai.push('현재 오프셋 <b>' + off.toFixed(1) + '초</b>');
  if (p.offset_source) ai.push('근거: ' + p.offset_source);
  if (p.offset_refine && p.offset_refine.note) ai.push(p.offset_refine.note);
  if (!p.stream_start_utc) ai.push('⚠ 유튜브 방송 시작시각을 못 얻었습니다 — 자동 보정 또는 수동 조정 필요');
  $('#align-info').innerHTML = ai.join(' · ');

  renderWeights();
  renderParams();
  renderExportOpts();
  renderSegments();
}

function html(tag, inner) { const e = document.createElement(tag); e.innerHTML = inner; return e; }
function fillSelect(sel, opts, val) {
  sel.innerHTML = '';
  opts.forEach((o) => { const e = el('option', '', o.t); e.value = o.v; sel.appendChild(e); });
  if (val != null) sel.value = val;
  if (!opts.length) sel.appendChild(el('option', '', '오디오 트랙 없음'));
}

function renderWeights() {
  const box = $('#weights'); box.innerHTML = '';
  Object.keys(WLABEL).forEach((k) => {
    const v = (S.proj.weights || {})[k] != null ? S.proj.weights[k] : 0;
    box.appendChild(slider(WLABEL[k], v, 0, 3, 0.1, (nv) => { S.proj.weights[k] = nv; }));
  });
}
function renderParams() {
  const box = $('#params'); box.innerHTML = '';
  Object.keys(PLABEL).forEach((k) => {
    const [lab, lo, hi, step] = PLABEL[k];
    const v = (S.proj.detect || {})[k];
    box.appendChild(slider(lab, v, lo, hi, step, (nv) => { S.proj.detect[k] = nv; }));
  });
}
function slider(label, value, lo, hi, step, onchange) {
  const cell = el('div', 'wcell');
  const lab = el('div', 'lab'); lab.appendChild(el('span', '', label));
  const b = el('b', '', String(value)); lab.appendChild(b);
  const inp = document.createElement('input');
  inp.type = 'range'; inp.min = lo; inp.max = hi; inp.step = step; inp.value = value;
  inp.oninput = () => { const nv = parseFloat(inp.value); b.textContent = String(nv); onchange(nv); };
  cell.appendChild(lab); cell.appendChild(inp);
  return cell;
}

function renderExportOpts() {
  const ex = S.proj.export || {};
  $('#ex-layout').value = ex.shorts_layout || 'blur';
  $('#ex-focus').value = ex.shorts_focus_x != null ? ex.shorts_focus_x : 0.5;
  $('#ex-shortslen').value = ex.shorts_max_len || 59;
}

// --------------------------------------------------------------------- 후보 목록
function renderSegments() {
  const segs = S.proj.segments || [];
  $('#seg-count').textContent = segs.length ? '(' + segs.length + '개 · 선택 ' + segs.filter((s) => s.selected).length + '개)' : '(없음)';
  const box = $('#segments'); box.innerHTML = '';
  if (!segs.length) { box.appendChild(el('p', 'dim', '아직 후보가 없습니다. 분석을 실행하거나 임계값을 낮춰 다시 검출하세요.')); return; }
  segs.forEach((s) => box.appendChild(segCard(s)));
}

function segCard(s) {
  const c = el('div', 'seg' + (s.selected ? '' : ' off'));
  c.id = 'seg-' + s.id;
  const hd = el('div', 'hd');
  const chk = document.createElement('input');
  chk.type = 'checkbox'; chk.checked = !!s.selected;
  chk.onchange = () => { s.selected = chk.checked; c.classList.toggle('off', !chk.checked); saveSegments(); };
  hd.appendChild(chk);
  hd.appendChild(el('span', 'rank', '#' + s.rank));
  hd.appendChild(el('span', '', s.label || ''));
  hd.appendChild(el('span', 'tc', fmtTC(s.start) + ' ~ ' + fmtTC(s.end) + '  (' + Math.round(s.dur) + '초)'));
  hd.appendChild(el('span', 'sc', '점수 ' + s.score));
  c.appendChild(hd);
  if (s.reason) c.appendChild(el('div', 'why', s.reason));

  if ((s.chat || []).length) {
    const ch = el('div', 'chat');
    s.chat.forEach((m) => {
      const d = el('div', m.kind !== 'text' ? 'paid' : '');
      d.appendChild(el('span', 'k', fmtTC(m.t)));
      d.appendChild(document.createTextNode((m.author ? m.author + ': ' : '') + m.text));
      ch.appendChild(d);
    });
    c.appendChild(ch);
  }

  const acts = el('div', 'acts');
  const title = document.createElement('input');
  title.type = 'text'; title.placeholder = '제목/메모 (XML 마커 이름)'; title.value = s.title || '';
  title.onchange = () => { s.title = title.value; saveSegments(); };
  acts.appendChild(title);
  acts.appendChild(nudge('◀ 시작-5', () => { s.start = Math.max(0, s.start - 5); saveSegments(true); }));
  acts.appendChild(nudge('시작+5 ▶', () => { s.start = Math.min(s.end - 2, s.start + 5); saveSegments(true); }));
  acts.appendChild(nudge('◀ 끝-5', () => { s.end = Math.max(s.start + 2, s.end - 5); saveSegments(true); }));
  acts.appendChild(nudge('끝+5 ▶', () => { s.end = s.end + 5; saveSegments(true); }));

  const bPrev = el('button', 'small primary', s.preview ? '미리보기 재생' : '미리보기 만들기');
  bPrev.onclick = async () => {
    bPrev.disabled = true; bPrev.textContent = '만드는 중…';
    try {
      const { job } = await post('/api/projects/' + S.proj.id + '/export', { kind: 'previews', seg_ids: [s.id] });
      const done = await waitJob(job.id, (j) => { bPrev.textContent = j.message || '진행중…'; });
      const name = (done.result && done.result.clips && done.result.clips[0]) || s.preview;
      s.preview = name;
      let v = c.querySelector('video');
      if (!v) { v = document.createElement('video'); v.controls = true; c.appendChild(v); }
      v.src = '/media/' + S.proj.id + '/clips/' + encodeURIComponent(name) + '?t=' + Date.now();
      v.play().catch(() => {});
      bPrev.textContent = '미리보기 재생';
    } catch (e) { toast('미리보기 실패: ' + e.message, true); bPrev.textContent = '다시 시도'; }
    bPrev.disabled = false;
  };
  acts.appendChild(bPrev);

  const bShort = el('button', 'small', '숏츠로 뽑기');
  bShort.onclick = async () => {
    bShort.disabled = true;
    try {
      await saveExportOpts();
      const { job } = await post('/api/projects/' + S.proj.id + '/export', { kind: 'shorts', seg_ids: [s.id] });
      const done = await waitJob(job.id, (j) => { bShort.textContent = j.message || '진행중…'; });
      bShort.textContent = '숏츠 완료';
      showExportResult('숏츠', done.result);
    } catch (e) { toast('숏츠 실패: ' + e.message, true); bShort.textContent = '다시 시도'; }
    bShort.disabled = false;
  };
  acts.appendChild(bShort);
  c.appendChild(acts);
  return c;
}
function nudge(txt, fn) { const b = el('button', 'small ghost', txt); b.onclick = fn; return b; }

let saveTimer = null;
function saveSegments(rerender) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      await post('/api/projects/' + S.proj.id + '/segments', {
        segments: (S.proj.segments || []).map((s) => ({ id: s.id, selected: s.selected, title: s.title, start: s.start, end: s.end })),
      });
      if (rerender) { renderSegments(); drawTimeline(); }
      else $('#seg-count').textContent = '(' + S.proj.segments.length + '개 · 선택 ' + S.proj.segments.filter((s) => s.selected).length + '개)';
    } catch (e) { toast('저장 실패: ' + e.message, true); }
  }, 250);
}

// --------------------------------------------------------------------- 타임라인
async function refreshCurves() {
  try {
    const w = Math.max(400, Math.floor($('#timeline').clientWidth || 900));
    S.curves = await api('/api/projects/' + S.proj.id + '/curves?width=' + w * 2);
    renderLegend();
    drawTimeline();
  } catch (e) { S.curves = null; }
}

function renderLegend() {
  const box = $('#legend'); box.innerHTML = '';
  if (!S.curves) return;
  const names = ['score'].concat(Object.keys(WLABEL).filter((k) => S.curves.series[k]));
  names.forEach((k) => {
    const sp = el('span');
    const i = el('i'); i.style.background = SERIES_COLOR[k] || '#888';
    sp.appendChild(i);
    sp.appendChild(document.createTextNode(k === 'score' ? '융합 점수' : WLABEL[k]));
    if (k !== 'score') sp.style.opacity = 0.75;
    box.appendChild(sp);
  });
}

function drawTimeline() {
  const cv = $('#timeline');
  if (!S.curves || !S.curves.duration) { const g = cv.getContext('2d'); g.clearRect(0, 0, cv.width, cv.height); return; }
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = 200;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);

  const dur = S.curves.duration;
  const padB = 26, padT = 8;
  const plotH = H - padB - padT;
  const yOf = (v) => padT + plotH - (Math.max(-1, Math.min(8, v)) + 1) / 9 * plotH;

  // 격자 + 시간 라벨 (30분 단위)
  g.strokeStyle = '#232a34'; g.fillStyle = '#94a0b2'; g.font = '10px sans-serif'; g.lineWidth = 1;
  for (let t = 0; t <= dur; t += 1800) {
    const x = t / dur * W;
    g.beginPath(); g.moveTo(x, padT); g.lineTo(x, H - padB); g.stroke();
    g.fillText(fmtTC(t), Math.min(x + 3, W - 34), H - padB + 12);
  }

  // 후보 구간
  (S.proj.segments || []).forEach((s) => {
    const x0 = s.start / dur * W, x1 = s.end / dur * W;
    g.fillStyle = s.selected ? 'rgba(255,182,76,0.20)' : 'rgba(148,160,178,0.10)';
    g.fillRect(x0, padT, Math.max(1.5, x1 - x0), plotH);
    g.fillStyle = s.selected ? '#ffb64c' : '#5b6675';
    g.fillRect(x0, H - padB - 3, Math.max(1.5, x1 - x0), 3);
  });

  // 임계값
  if (S.curves.threshold != null) {
    g.strokeStyle = '#ef5b5b'; g.setLineDash([4, 4]); g.beginPath();
    g.moveTo(0, yOf(S.curves.threshold)); g.lineTo(W, yOf(S.curves.threshold)); g.stroke();
    g.setLineDash([]);
  }

  // 보조 신호
  Object.keys(WLABEL).forEach((k) => {
    const arr = S.curves.series[k]; if (!arr) return;
    g.strokeStyle = SERIES_COLOR[k] || '#666'; g.globalAlpha = 0.35; g.lineWidth = 1;
    line(g, arr, W, yOf);
  });
  g.globalAlpha = 1;
  // 융합 점수
  const sc = S.curves.series.score;
  if (sc) { g.strokeStyle = SERIES_COLOR.score; g.lineWidth = 1.6; line(g, sc, W, yOf); }

  cv.onmousemove = (ev) => {
    const rect = cv.getBoundingClientRect();
    const t = (ev.clientX - rect.left) / rect.width * dur;
    const tip = $('#tl-tip');
    const seg = (S.proj.segments || []).find((s) => t >= s.start && t <= s.end);
    tip.textContent = fmtTC(t) + (seg ? '  →  #' + seg.rank + ' ' + seg.label + ' (점수 ' + seg.score + ')' : '');
    tip.style.left = (ev.clientX + 12) + 'px';
    tip.style.top = (ev.clientY - 28) + 'px';
    tip.classList.remove('hidden');
  };
  cv.onmouseleave = () => $('#tl-tip').classList.add('hidden');
  cv.onclick = (ev) => {
    const rect = cv.getBoundingClientRect();
    const t = (ev.clientX - rect.left) / rect.width * dur;
    let best = null, bd = 1e9;
    (S.proj.segments || []).forEach((s) => { const d = Math.abs((s.start + s.end) / 2 - t); if (d < bd) { bd = d; best = s; } });
    if (best) {
      const node = $('#seg-' + best.id);
      if (node) {
        node.scrollIntoView({ behavior: 'smooth', block: 'center' });
        node.classList.add('flash'); setTimeout(() => node.classList.remove('flash'), 1600);
      }
    }
  };
}
function line(g, arr, W, yOf) {
  g.beginPath();
  for (let i = 0; i < arr.length; i++) {
    const x = i / (arr.length - 1 || 1) * W, y = yOf(arr[i]);
    i ? g.lineTo(x, y) : g.moveTo(x, y);
  }
  g.stroke();
}

// --------------------------------------------------------------------- 작업 진행률
function startPolling() {
  if (S.poll) clearInterval(S.poll);
  S.poll = setInterval(pollJobs, 1200);
  pollJobs();
}
async function pollJobs() {
  if (!S.proj) return;
  try {
    const { jobs } = await api('/api/jobs?project=' + encodeURIComponent(S.proj.id));
    const box = $('#job-box'); box.innerHTML = '';
    jobs.slice(0, 4).forEach((j) => {
      const d = el('div', 'job ' + j.status);
      const top = el('div', 'top');
      top.appendChild(el('span', '', j.name));
      top.appendChild(el('span', '', j.status === 'done' ? '완료' : j.status === 'error' ? '오류' : Math.round(j.progress * 100) + '%'));
      d.appendChild(top);
      const bar = el('div', 'bar'); const fill = el('div'); fill.style.width = (j.progress * 100) + '%'; bar.appendChild(fill);
      d.appendChild(bar);
      d.appendChild(el('div', 'msg', j.error || j.message || ''));
      box.appendChild(d);
    });
    const running = jobs.some((j) => j.status === 'running');
    if (!running && S.lastRunning) { await openProject(S.proj.id); }
    S.lastRunning = running;
  } catch (e) { /* noop */ }
}
async function waitJob(jid, onTick) {
  for (;;) {
    const { job } = await api('/api/jobs/' + jid);
    if (onTick) onTick(job);
    if (job.status === 'done') return job;
    if (job.status === 'error') throw new Error(job.error || '작업 실패');
    await new Promise((r) => setTimeout(r, 800));
  }
}

// --------------------------------------------------------------------- 익스포트 결과
function showExportResult(kind, result) {
  const box = $('#export-out');
  const wrap = el('div');
  wrap.appendChild(el('h3', '', kind + ' 결과'));
  const files = [];
  if (!result) return;
  ['xml', 'csv', 'txt', 'source'].forEach((k) => { if (result[k]) files.push([k.toUpperCase(), result[k]]); });
  if (result.dir && result.clips) files.push(['폴더 (' + result.clips.length + '개)', result.dir]);
  files.forEach(([lab, path]) => {
    const f = el('div', 'f');
    const left = el('div'); left.appendChild(el('div', '', lab)); left.appendChild(html('code', path));
    f.appendChild(left);
    const b = el('button', 'small ghost', 'Finder에서 보기');
    b.onclick = () => post('/api/reveal', { path }).catch((e) => toast(e.message, true));
    f.appendChild(b);
    wrap.appendChild(f);
  });
  box.prepend(wrap);
}

async function saveExportOpts() {
  const ex = {
    shorts_layout: $('#ex-layout').value,
    shorts_focus_x: parseFloat($('#ex-focus').value),
    shorts_max_len: parseFloat($('#ex-shortslen').value),
  };
  const { project } = await post('/api/projects/' + S.proj.id + '/settings', { export: ex });
  S.proj = project;
}

async function runExport(kind, mode, label) {
  try {
    await saveExportOpts();
    const { job } = await post('/api/projects/' + S.proj.id + '/export', { kind, mode });
    const done = await waitJob(job.id);
    showExportResult(label, done.result);
  } catch (e) { toast(label + ' 실패: ' + e.message, true); }
}

// --------------------------------------------------------------------- 이벤트 연결
function bind() {
  $('#btn-new').onclick = () => {
    S.proj = null;
    $('#view-proj').classList.add('hidden');
    $('#view-new').classList.remove('hidden');
    loadFs(S.fsPath || null);
    loadProjects();
  };
  $('#fs-go').onclick = () => loadFs($('#fs-path').value);
  $('#fs-path').onkeydown = (e) => { if (e.key === 'Enter') loadFs($('#fs-path').value); };

  $('#btn-create').onclick = async () => {
    const video_path = $('#new-video').value.trim();
    if (!video_path) return toast('영상 파일을 선택하세요', true);
    try {
      const { project } = await post('/api/projects', {
        video_path, youtube_url: $('#new-url').value.trim(), name: $('#new-name').value.trim(),
      });
      await openProject(project.id);
    } catch (e) { toast('생성 실패: ' + e.message, true); }
  };

  $('#btn-delete').onclick = async () => {
    if (!confirm('프로젝트와 생성된 클립을 모두 삭제합니다. 원본 영상은 지우지 않습니다.')) return;
    await post('/api/projects/' + S.proj.id + '/delete');
    S.proj = null;
    $('#view-proj').classList.add('hidden');
    $('#btn-new').click();
  };

  $('#btn-analyze').onclick = async () => {
    try {
      await post('/api/projects/' + S.proj.id + '/analyze', {
        mic_stream: parseInt($('#sel-mic').value, 10),
        mix_stream: parseInt($('#sel-mix').value, 10),
        youtube_url: $('#p-url').value.trim(),
        do_chat: $('#chk-chat').checked,
        do_audio: $('#chk-audio').checked,
        auto_align: $('#chk-align').checked,
      });
      S.lastRunning = true;
      pollJobs();
    } catch (e) { toast('분석 시작 실패: ' + e.message, true); }
  };

  const applyOffset = async (v) => {
    const { project } = await post('/api/projects/' + S.proj.id + '/settings', { offset_sec: v });
    S.proj = project;
    await post('/api/projects/' + S.proj.id + '/detect', {});
    await openProject(S.proj.id);
  };
  $('#off-num').onchange = () => applyOffset(parseFloat($('#off-num').value));
  $('#off-range').onchange = () => applyOffset(parseFloat($('#off-range').value));
  $('#btn-refine').onclick = async () => {
    try {
      const { refine } = await post('/api/projects/' + S.proj.id + '/refine');
      await post('/api/projects/' + S.proj.id + '/detect', {});
      await openProject(S.proj.id);
      toast('자동 보정: ' + refine.note);
      alert('자동 보정 결과\n오프셋 ' + refine.offset_sec + '초 (변화 ' + refine.delta + '초)\n' + refine.note);
    } catch (e) { toast('자동 보정 실패: ' + e.message, true); }
  };

  $('#btn-detect').onclick = async () => {
    try {
      await post('/api/projects/' + S.proj.id + '/settings', { weights: S.proj.weights, detect: S.proj.detect });
      await post('/api/projects/' + S.proj.id + '/detect', {});
      await openProject(S.proj.id);
    } catch (e) { toast('검출 실패: ' + e.message, true); }
  };

  $('#btn-all').onclick = () => { (S.proj.segments || []).forEach((s) => (s.selected = true)); renderSegments(); drawTimeline(); saveSegments(); };
  $('#btn-none').onclick = () => { (S.proj.segments || []).forEach((s) => (s.selected = false)); renderSegments(); drawTimeline(); saveSegments(); };
  $('#btn-top10').onclick = () => { (S.proj.segments || []).forEach((s) => (s.selected = s.rank <= 10)); renderSegments(); drawTimeline(); saveSegments(); };

  $('#btn-xml-markers').onclick = () => runExport('xml', 'markers', '프리미어 XML(마커)');
  $('#btn-xml-rough').onclick = () => runExport('xml', 'roughcut', '프리미어 XML(러프컷)');
  $('#btn-previews').onclick = () => runExport('previews', null, '미리보기 클립');
  $('#btn-shorts').onclick = () => runExport('shorts', null, '숏츠');

  window.addEventListener('resize', () => { if (S.curves) drawTimeline(); });
}

// --------------------------------------------------------------------- 시작
(async function init() {
  bind();
  await loadDoctor();
  await loadProjects();
  if (S.projects.length) openProject(S.projects[0].id);
  else $('#btn-new').click();
})();
