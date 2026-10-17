/* טופס למידע — המסך. JavaScript פשוט, בלי ספריות ובלי שלב בנייה.
 *
 * מבנה: הגדרות → כלים קטנים → העלאה והתקדמות → טבלה ותקריב צף → מצב טופס →
 * פעולות על שדות (אישור, תיקון, בטל) → סיור, הודעות ורגע הצלחה.
 * שום פעולה כאן לא קוראת למודל: הכול מול השרת המקומי. */
'use strict';

const $ = s => document.querySelector(s);
const $$ = s => Array.prototype.slice.call(document.querySelectorAll(s));

let CONFIG = null, FIELDS = [];
let batch = null, timer = null, last = null, processing = false;
let selected = null;            // { rec, key } בחלונית המקור (מצב טבלה)
let view = load('view', 'table');
let filter = 'all', senderFilter = '';
let formIndex = 0, cardIndex = 0;
let lastSignature = '';
let celebrated = false;
const viewTurn = {};            // סיבוב תצוגה לכל טופס — תצוגה בלבד
let inkView = load('inkView', '0') === '1';
const tilesBuilt = {};

/* ------------------------------------------------------------ זיכרון קטן בדפדפן */
function load(key, fallback){ try { const v = localStorage.getItem(key); return v == null ? fallback : v; } catch (e) { return fallback; } }
function save(key, value){ try { localStorage.setItem(key, value); } catch (e) {} }

/* ------------------------------------------------------------ מראה: נושא וגודל טקסט */
const root = document.documentElement;
root.dataset.theme = load('theme', window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
root.dataset.size = load('size', '1');
$('#themeBtn').onclick = function(){
  root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
  save('theme', root.dataset.theme);
};
function setSize(delta){
  const next = Math.max(0, Math.min(3, Number(root.dataset.size || 1) + delta));
  root.dataset.size = String(next);
  save('size', root.dataset.size);
  closePop();
}
$('#sizeUp').onclick = function(){ setSize(1); };
$('#sizeDown').onclick = function(){ setSize(-1); };

/* ------------------------------------------------------------ הגדרות מהשרת */
fetch('/api/config').then(function(r){ return r.json(); }).then(function(c){
  CONFIG = c;
  FIELDS = c.fields;
  const reader = c.model + (c.effort ? ' · חשיבה ' + c.effort : '');
  $('#modelName').textContent = reader;
  $('#limits').textContent = 'PDF, JPG או PNG · עד ' + c.max_files + ' קבצים ו-' + c.max_pages +
    ' טפסים בגרירה אחת · קורא: ' + reader;
  $('#head').innerHTML = FIELDS.map(function(f){
    return '<th scope="col" title="' + esc(f.hint) + '">' + esc(f.label) + '</th>';
  }).join('') + '<th scope="col">קובץ מקור</th><th scope="col">עמוד</th><th scope="col">אישור</th>';
  $('#sampleBtn').classList.toggle('hidden', !c.sample);
  if (!c.has_key) notice('err', 'אין מפתח API', 'לא נמצא ANTHROPIC_API_KEY בקובץ .env. הוסיפו והפעילו מחדש את השרת.');
  if (load('toured', '') !== '1') openTour();
});

/* ------------------------------------------------------------ כלים קטנים */
function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
    return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c];
  });
}
function money(usd){
  if (usd == null) return '—';
  return usd < 0.01 ? (usd * 100).toFixed(2) + '¢' : '$' + usd.toFixed(3);
}
function markUnreadable(text){
  return esc(text).replace(/\?/g, '<mark class="q" title="תו שלא נקרא">?</mark>');
}
function fieldLabel(key){ const f = FIELDS.find(function(x){ return x.key === key; }); return f ? f.label : key; }
function recordById(id){ return last ? last.records.find(function(r){ return r.record_id === id; }) : null; }
function pageUrl(r){ return '/api/batches/' + batch + '/sources/' + r.source_index + '/pages/' + r.page_number; }
function cropUrl(r, key){ return pageUrl(r) + '/crops/' + key + (inkView ? '?ink=1' : ''); }
function isApproved(r){ return r.verified || FIELDS.every(function(f){ return r.fields[f.key].state === 'approved'; }); }
function senderOf(r){ return (r.fields.sending_entity.values[0] || '').trim(); }
function valueHtml(f){
  if (f.values.length) return markUnreadable(f.values.join(' | '));
  const notOnForm = (f.reason_codes || []).indexOf('not_on_form') !== -1;
  return '<span class="empty">' + (notOnForm ? 'לא קיים בטופס' : 'לא נמצא') + '</span>';
}
function editing(){ return !!document.querySelector('td input, .cardEdit input'); }

/* ------------------------------------------------------------ הודעות */
function notice(kind, title, text){
  $('#notices').insertAdjacentHTML('beforeend',
    '<div class="notice ' + kind + '" role="' + (kind === 'err' ? 'alert' : 'status') + '"><b>' +
    esc(title) + '</b>' + esc(text) + '</div>');
}
function clearNotices(){ $('#notices').innerHTML = ''; }

function toast(text, opts){
  opts = opts || {};
  const el = document.createElement('div');
  el.className = 'toast' + (opts.big ? ' big' : '');
  el.innerHTML = '<span>' + esc(text) + '</span>';
  if (opts.undo) {
    const b = document.createElement('button');
    b.textContent = 'בטל';
    b.onclick = function(){ dismiss(); opts.undo(); };
    el.appendChild(b);
  }
  $('#toasts').appendChild(el);
  const t = setTimeout(dismiss, opts.ms || (opts.undo ? 6500 : 3500));
  function dismiss(){
    clearTimeout(t);
    el.classList.add('out');
    setTimeout(function(){ el.remove(); }, 260);
  }
}

/* ------------------------------------------------------------ העלאה */
const drop = $('#drop');
$('#browse').onclick = function(e){ e.stopPropagation(); $('#file').click(); };
$('#sampleBtn').onclick = function(e){ e.stopPropagation(); startSample(); };
drop.onclick = function(){ $('#file').click(); };
$('#file').onchange = function(e){ upload(e.target.files); };
['dragenter', 'dragover'].forEach(function(ev){
  drop.addEventListener(ev, function(e){ e.preventDefault(); drop.classList.add('over'); });
});
['dragleave', 'drop'].forEach(function(ev){
  drop.addEventListener(ev, function(e){ e.preventDefault(); drop.classList.remove('over'); });
});
drop.addEventListener('drop', function(e){ upload(e.dataTransfer.files); });

function showProgress(title, names){
  clearNotices();
  drop.classList.add('hidden');
  $('#results').classList.add('hidden');
  $('#progress').classList.remove('hidden');
  $('#pTitle').textContent = title;
  $('#pCount').textContent = '';
  $('#pFill').style.width = '4%';
  $('#tiles').innerHTML = '';
  Object.keys(tilesBuilt).forEach(function(k){ delete tilesBuilt[k]; });
  $('#pFiles').innerHTML = (names || []).map(function(n){ return '<li>' + esc(n) + '</li>'; }).join('');
}

async function upload(fileList){
  const files = Array.prototype.slice.call(fileList || []);
  if (!files.length) return;
  showProgress(files.length === 1 ? 'מעלה את הקובץ…' : 'מעלה ' + files.length + ' קבצים…',
               files.map(function(f){ return f.name; }));
  const body = new FormData();
  files.forEach(function(f){ body.append('files', f); });
  try {
    const res = await fetch('/api/batches', { method: 'POST', body: body });
    if (!res.ok) {
      const d = await res.json().catch(function(){ return {}; });
      throw new Error(d.detail || 'ההעלאה נכשלה');
    }
    begin(await res.json());
  } catch (err) {
    fail(err.message);
  }
}

async function startSample(){
  showProgress('פותח טופס דוגמה…', ['טופס דוגמה.pdf']);
  try {
    const res = await fetch('/api/batches/sample', { method: 'POST' });
    if (!res.ok) throw new Error('טופס הדוגמה לא זמין');
    begin(await res.json());
  } catch (err) {
    fail(err.message);
  }
}

function begin(info){
  batch = info.batch_id;
  last = null;
  selected = null;
  lastSignature = '';
  celebrated = false;
  formIndex = 0;
  cardIndex = 0;
  processing = true;
  if (info.rejected)
    notice('warn', info.rejected + ' קבצים לא התקבלו', 'הם מופיעים ברשימה עם הסיבה. שאר הקבצים מעובדים כרגיל.');
  $('#pTitle').textContent = 'מזהה טפסים…';
  clearInterval(timer);
  timer = setInterval(tick, 900);
  tick();
}

function fail(msg){
  clearInterval(timer);
  processing = false;
  $('#progress').classList.add('hidden');
  $('#pFiles').innerHTML = '';
  drop.classList.remove('hidden');
  notice('err', 'לא הצלחנו לעבד את הקבצים', msg);
}

/* ------------------------------------------------------------ התקדמות חיה */
async function tick(){
  const res = await fetch('/api/batches/' + batch);
  if (!res.ok) { clearInterval(timer); return fail('האצווה לא נמצאה'); }
  const d = await res.json();
  const total = d.total_pages || 0, done = d.processed_pages;
  const finished = ['completed', 'partial', 'failed'].indexOf(d.status) !== -1;

  $('#pFill').style.width = Math.max(6, total ? (done / total) * 100 : 6) + '%';
  if (total) {
    const reading = (d.pages || []).filter(function(p){ return p.status === 'reading'; }).length;
    $('#pTitle').textContent = 'קורא ' + total + ' טפסים';
    $('#pCount').textContent = done + ' מוכנים · ' + reading + ' נקראים עכשיו · ' +
      Math.max(0, total - done - reading) + ' בתור';
  }
  $('#pFiles').innerHTML = (d.sources || []).map(function(s){
    const detail = s.error ? '<span class="bad">' + esc(s.error) + '</span>'
                           : '<span class="good">' + (s.page_count || '…') + ' טפסים</span>';
    return '<li>' + esc(s.filename) + ' — ' + detail + '</li>';
  }).join('');
  updateTiles(d);

  if (d.records.length) render(d);

  if (finished) {
    clearInterval(timer);
    processing = false;
    $('#progress').classList.add('hidden');
    if (d.status === 'failed') return fail(d.error || 'העיבוד נכשל');
    if (d.status === 'partial') notice('warn', 'חלק מהקבצים לא עובדו במלואם', d.error);
    if (d.skipped_pages)
      notice('warn', 'נחצתה תקרת הטפסים', d.skipped_pages + ' טפסים לא עובדו. עובדו ' + total +
        ' הראשונים. ניתן לשנות את MAX_PAGES_PER_BATCH בקובץ .env.');
    (d.warnings || []).forEach(function(w){ notice('warn', 'לתשומת לב', w); });
    render(d, !editing());
  }
}

function updateTiles(d){
  const box = $('#tiles');
  (d.pages || []).forEach(function(p, i){
    const id = p.source_index + '-' + p.page_number;
    let tile = tilesBuilt[id];
    if (!tile) {
      tile = document.createElement('button');
      tile.className = 'tile queued';
      tile.type = 'button';
      tile.innerHTML = '<img alt="" loading="lazy" src="/api/batches/' + batch + '/sources/' + p.source_index +
        '/pages/' + p.page_number + '/thumb"><span class="num">' + (i + 1) + '</span><span class="tag">בתור</span>';
      tile.querySelector('img').onerror = function(){ this.style.visibility = 'hidden'; };
      tile.onclick = function(){ if (tile.dataset.rec) openRecord(tile.dataset.rec); };
      box.appendChild(tile);
      tilesBuilt[id] = tile;
    }
    const record = p.record_id ? d.records.find(function(r){ return r.record_id === p.record_id; }) : null;
    let cls = p.status, tag = 'בתור';
    if (p.status === 'reading') tag = 'נקרא…';
    if (p.status === 'skipped') tag = 'לא עובד';
    if (p.status === 'failed') tag = '✗ נכשל';
    if (p.status === 'done' && record) {
      cls = record.review_count ? 'review' : 'done';
      tag = record.review_count ? '⚠ ' + record.review_count + ' לבדיקה' : '✓ מוכן';
    }
    if (!tile.classList.contains(cls)) tile.className = 'tile ' + cls;
    tile.querySelector('.tag').textContent = tag;
    tile.dataset.rec = record ? record.record_id : '';
    tile.disabled = !record;
    tile.setAttribute('aria-label', 'טופס ' + (i + 1) + ': ' + tag + (record ? ' — לחיצה פותחת אותו' : ''));
  });
}

/* ------------------------------------------------------------ תצוגה כללית */
function visibleRecords(){
  if (!last) return [];
  return last.records.filter(function(r){
    if (filter === 'review' && !(r.review_count > 0)) return false;
    if (filter === 'approved' && !isApproved(r)) return false;
    if (senderFilter && senderOf(r) !== senderFilter) return false;
    return true;
  });
}

function signature(d){
  return JSON.stringify(d.records.map(function(r){
    return [r.record_id, r.verified, FIELDS.map(function(f){
      const x = r.fields[f.key]; return [x.state, x.values, x.reasons];
    })];
  }));
}

function render(d, force){
  last = d;
  $('#results').classList.remove('hidden');
  updateChips(d);
  const sig = signature(d) + '|' + filter + '|' + senderFilter + '|' + view;
  // רענון אוטומטי (בזמן הקריאה) לא מוחק את מה שהמזכירה מקלידה.
  // אחרי פעולה שלה (force) — תמיד מציירים מחדש.
  if (!force && (sig === lastSignature || editing())) return;
  lastSignature = sig;
  showView();
  maybeCelebrate(d);
}

function updateChips(d){
  $('#cRecords').textContent = d.records.length;
  $('#cReview').textContent = d.review_total;
  $('#cFile').textContent = d.display_name;
  $('#cCost').textContent = money(d.cost_usd);
  $('#cTime').textContent = d.seconds ? d.seconds + ' שנ׳' : (processing ? '…' : '—');
  $('#cSources').textContent = (d.sources || []).filter(function(s){ return !s.error; }).length;
  $('#cReviewChip').classList.toggle('hidden', d.review_total === 0);
  $('#nAll').textContent = d.records.length;
  $('#nReview').textContent = d.records.filter(function(r){ return r.review_count > 0; }).length;
  $('#nApproved').textContent = d.records.filter(isApproved).length;
  const senders = Array.from(new Set(d.records.map(senderOf).filter(Boolean))).sort();
  const sel = $('#senderFilter');
  const options = '<option value="">כל הגורמים</option>' + senders.map(function(s){
    return '<option' + (s === senderFilter ? ' selected' : '') + '>' + esc(s) + '</option>';
  }).join('');
  if (sel.dataset.options !== options) { sel.innerHTML = options; sel.dataset.options = options; }
}

function showView(){
  $('#viewTable').setAttribute('aria-selected', String(view === 'table'));
  $('#viewForm').setAttribute('aria-selected', String(view === 'form'));
  $('#tableView').classList.toggle('hidden', view !== 'table');
  $('#formView').classList.toggle('hidden', view !== 'form');
  closePop();
  if (view === 'table') renderTable(); else renderForm();
}

$('#viewTable').onclick = function(){ view = 'table'; save('view', view); lastSignature = ''; if (last) render(last, true); };
$('#viewForm').onclick = function(){ view = 'form'; save('view', view); lastSignature = ''; if (last) render(last, true); };
$$('.filters button').forEach(function(b){
  b.onclick = function(){
    filter = b.dataset.filter;
    $$('.filters button').forEach(function(x){ x.classList.toggle('on', x === b); });
    formIndex = 0;
    if (last) render(last, true);
  };
});
$('#senderFilter').onchange = function(e){ senderFilter = e.target.value; formIndex = 0; if (last) render(last, true); };

/* ------------------------------------------------------------ מצב טבלה */
let focusedCell = null;

function renderTable(){
  const rows = visibleRecords();
  const span = FIELDS.length + 3;
  $('#emptyFilter').classList.toggle('hidden', rows.length > 0);
  $('#body').innerHTML = rows.map(function(r){
    const notes = [];
    if (r.low_resolution) notes.push('רזולוציה נמוכה — כתב יד עלול לא להיקרא. מומלץ להעלות את הסריקה המקורית.');
    if (r.straightened) notes.push(r.straightened);
    if (r.error) notes.push('הקריאה נכשלה: ' + r.error);
    else if (r.note) notes.push('הערה: ' + r.note);
    const noteRow = notes.length ? '<tr class="noterow"><td colspan="' + span + '">' + esc(notes.join(' · ')) + '</td></tr>' : '';
    const open = r.review_count;
    const acts = r.verified
      ? '<span class="verified">✓ מאומת</span>'
      : '<button class="approveRow" data-rec="' + r.record_id + '"' + (open ? ' disabled' : '') +
        ' title="' + (open ? 'קודם צריך לבדוק ' + open + ' שדות כתומים' : 'אישור כל הטופס ושמירה כמאומת') + '">✓ אשר טופס</button>';
    return '<tr data-rec="' + r.record_id + '">' + FIELDS.map(function(f){ return cell(r, f); }).join('') +
      '<td class="page" title="' + esc(r.source_name) + '">' + esc(r.source_name) + '</td>' +
      '<td class="page">' + r.page_number + '</td><td class="acts">' + acts + '</td></tr>' + noteRow;
  }).join('');
  if (selected) {
    if (selected.key) showField(selected.rec, selected.key); else showPage(selected.rec);
  }
  if (focusedCell) {
    const td = document.querySelector('td.cell[data-rec="' + focusedCell.rec + '"][data-key="' + focusedCell.key + '"]');
    if (td) td.focus({ preventScroll: true });
  }
}

function cell(r, spec){
  const f = r.fields[spec.key];
  const cls = f.state === 'needs_review' ? 'review' : f.state === 'approved' ? 'approved' : '';
  const why = (f.state === 'needs_review' && f.reasons.length) ? '<span class="why">' + esc(f.reasons[0]) + '</span>' : '';
  const okEmpty = (f.state === 'needs_review' && !f.values.length)
    ? '<button class="okempty" data-act="approve" title="אישור שהשדה באמת ריק בטופס">אשר כריק</button>' : '';
  const mini = '<span class="mini">' +
    (f.state !== 'approved' && f.values.length ? '<button class="ok" data-act="approve" tabindex="-1" title="אשר">✓</button>' : '') +
    '<button data-act="edit" tabindex="-1" title="תקן">✎</button></span>';
  return '<td class="cell ' + cls + '" tabindex="0" data-key="' + spec.key + '" data-rec="' + r.record_id +
    '" data-raw="' + esc(f.values.join(' | ')) + '" aria-label="' + esc(spec.label) + ': ' +
    esc(f.values.join(' | ') || 'ריק') + (cls === 'review' ? ', לבדיקה' : cls === 'approved' ? ', אושר' : '') + '">' +
    mini + '<span class="val">' + valueHtml(f) + '</span>' + why + okEmpty + '</td>';
}

const tbody = $('#body');
tbody.addEventListener('click', function(e){
  const btn = e.target.closest('button');
  const td = e.target.closest('td');
  if (!td) return;
  const rec = td.closest('tr').dataset.rec;
  if (btn && btn.classList.contains('approveRow')) { e.stopPropagation(); return verify(btn.dataset.rec); }
  if (btn && btn.dataset.act === 'approve') { e.stopPropagation(); return approveField(rec, td.dataset.key); }
  if (btn && btn.dataset.act === 'edit') { e.stopPropagation(); return edit(td); }
  if (td.classList.contains('cell')) showField(rec, td.dataset.key);
  else if (td.classList.contains('page')) showPage(rec);
});
tbody.addEventListener('dblclick', function(e){
  const td = e.target.closest('td.cell');
  if (td && !e.target.closest('button')) edit(td);
});
tbody.addEventListener('keydown', function(e){
  const td = e.target.closest && e.target.closest('td.cell');
  if (!td || e.target.tagName === 'INPUT') return;
  if (e.key === 'Enter') { e.preventDefault(); showField(td.dataset.rec, td.dataset.key); }
  else if (e.key === 'F2' || e.code === 'KeyE') { e.preventDefault(); edit(td); }
  else if (e.code === 'KeyA') { e.preventDefault(); approveField(td.dataset.rec, td.dataset.key); }
});
tbody.addEventListener('focusin', function(e){
  const td = e.target.closest('td.cell');
  if (!td) return;
  focusedCell = { rec: td.dataset.rec, key: td.dataset.key };
  // תקריב מיידי רק במעבר מקלדת (Tab); בעכבר הוא נפתח במעבר מעל התא
  let keyboard = false;
  try { keyboard = td.matches(':focus-visible'); } catch (err) {}
  if (e.target === td && keyboard) schedulePop(td, 0);
});
tbody.addEventListener('focusout', function(e){
  if (!e.relatedTarget || !$('#pop').contains(e.relatedTarget)) scheduleClose();
});
tbody.addEventListener('mouseover', function(e){
  const td = e.target.closest('td.cell');
  if (td) schedulePop(td, 280);
});
tbody.addEventListener('mouseout', function(e){
  const td = e.target.closest('td.cell');
  if (td && !td.contains(e.relatedTarget)) scheduleClose();
});

/* ------------------------------------------------------------ תקריב צף */
let popTimer = null, popFor = null;
const pop = $('#pop');
pop.addEventListener('mouseenter', function(){ clearTimeout(popTimer); });
pop.addEventListener('mouseleave', scheduleClose);
pop.addEventListener('click', function(e){
  const b = e.target.closest('button');
  if (!b || !popFor) return;
  const td = popFor;
  if (b.dataset.act === 'approve') approveField(td.dataset.rec, td.dataset.key);
  if (b.dataset.act === 'edit') { closePop(); edit(td); }
});

function schedulePop(td, delay){
  clearTimeout(popTimer);
  if (popFor === td && !pop.classList.contains('hidden')) return;
  popTimer = setTimeout(function(){ openPop(td); }, delay);
}
function scheduleClose(){
  clearTimeout(popTimer);
  popTimer = setTimeout(closePop, 220);
}
function closePop(){
  clearTimeout(popTimer);
  pop.classList.add('hidden');
  popFor = null;
}

function openPop(td){
  if (!document.body.contains(td) || td.querySelector('input')) return;
  const r = recordById(td.dataset.rec);
  if (!r) return;
  const key = td.dataset.key, f = r.fields[key];
  const state = f.state === 'needs_review' ? '⚠ לבדיקה' : f.state === 'approved' ? '✓ אושר' : '';
  pop.innerHTML =
    '<div class="lbl"><span>' + esc(fieldLabel(key)) + '</span><span>' + state + '</span></div>' +
    (f.crop_box ? '<div class="crop"><img alt="תקריב של ' + esc(fieldLabel(key)) + '" src="' + cropUrl(r, key) + '"></div>'
                : '<div class="crop"><div class="nocrop">אין מיקום לשדה הזה — לחיצה על התא מציגה את הטופס המלא</div></div>') +
    '<div class="val">' + valueHtml(f) + '</div>' +
    (f.state === 'needs_review' && f.reasons.length ? '<div class="why">' + esc(f.reasons.join(' · ')) + '</div>' : '') +
    '<div class="btns">' +
    (f.state !== 'approved' ? '<button class="primary" data-act="approve">✓ ' + (f.values.length ? 'אשר' : 'אשר כריק') + '</button>' : '') +
    '<button data-act="edit">✎ תקן</button></div>';
  pop.classList.remove('hidden');
  popFor = td;
  placePop(td);
  const img = pop.querySelector('img');
  if (img) img.addEventListener('load', function(){ if (popFor === td) placePop(td); }, { once: true });
}

function placePop(td){
  const cellBox = td.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  const below = cellBox.top < h + 90;
  let left = cellBox.left + cellBox.width / 2 - w / 2;
  left = Math.max(10, Math.min(left, window.innerWidth - w - 10));
  const top = below ? cellBox.bottom + 10 : cellBox.top - h - 10;
  pop.classList.toggle('below', below);
  pop.style.left = (left + window.scrollX) + 'px';
  pop.style.top = (top + window.scrollY) + 'px';
  pop.style.setProperty('--arrow', Math.max(14, Math.min(w - 14, cellBox.left + cellBox.width / 2 - left)) + 'px');
}
window.addEventListener('scroll', closePop, { passive: true });
window.addEventListener('resize', closePop);

/* ------------------------------------------------------------ חלונית המקור (טבלה) וצופה (טופס) */
function turnButtons(withInk){
  return ' <button class="ink-toggle" data-turn="1" title="לסובב את התצוגה ב-90° — לא משנה את הקובץ">↻ סובב</button>' +
    (withInk ? ' <button class="ink-toggle' + (inkView ? ' on' : '') + '" data-ink="1" title="להציג את ההגדלה בלי מרקרים, עם כתב היד בשחור">דיו מודגש</button>' : '');
}
function boxHtml(f){
  const b = f && f.crop_box;
  return b ? '<div class="pv-box" style="left:' + b[0] * 100 + '%;top:' + b[1] * 100 + '%;width:' +
    (b[2] - b[0]) * 100 + '%;height:' + (b[3] - b[1]) * 100 + '%"></div>' : '';
}
function pageHtml(r, f, withInk){
  return '<div class="pv-sec"><div class="pv-lbl">הטופס המלא' + turnButtons(withInk) + '</div>' +
    '<div class="pv-pagewrap"><div class="pv-page"><img src="' + pageUrl(r) + '" alt="עמוד ' + r.page_number +
    ' של ' + esc(r.source_name) + '">' + boxHtml(f) + '</div></div></div>';
}

function showPage(recordId){
  const r = recordById(recordId);
  if (!r) return;
  selected = { rec: recordId, key: null };
  mark(recordId, null);
  $('#pvHint').textContent = r.source_name + ' · עמוד ' + r.page_number;
  $('#pvFrame').innerHTML = pageHtml(r, null, false);
  applyTurn($('#pvFrame'), recordId);
}

function showField(recordId, key){
  const r = recordById(recordId);
  if (!r) return;
  selected = { rec: recordId, key: key };
  mark(recordId, key);
  const f = r.fields[key];
  $('#pvHint').textContent = fieldLabel(key) + ' · ' + r.source_name + ' · עמוד ' + r.page_number;
  const zoomed = f.crop_box
    ? '<div class="pv-sec pv-crop"><div class="pv-lbl">' + esc(fieldLabel(key)) + ' — מוגדל</div>' +
      '<div class="pv-cropwrap"><img src="' + cropUrl(r, key) + '" alt="' + esc(fieldLabel(key)) + ' מוגדל"></div></div>'
    : '<div class="pv-sec pv-crop"><div class="pv-lbl">' + esc(fieldLabel(key)) + ' — מוגדל</div>' +
      '<div class="pv-missing">אין מיקום לשדה הזה בעמוד — הוצג הטופס המלא בלבד, בלי תקריב מומצא.</div></div>';
  $('#pvFrame').innerHTML = pageHtml(r, f, !!f.crop_box) + zoomed;
  applyTurn($('#pvFrame'), recordId);
}

function mark(recordId, key){
  $$('#body tr[data-rec]').forEach(function(row){ row.classList.toggle('sel', row.dataset.rec === recordId); });
  $$('#body td.cell').forEach(function(td){
    td.classList.toggle('focus', !!key && td.dataset.rec === recordId && td.dataset.key === key);
  });
}

document.addEventListener('click', function(e){
  const b = e.target.closest && e.target.closest('[data-turn],[data-ink]');
  if (!b) return;
  e.stopPropagation();
  const rec = view === 'form' ? currentRecord() && currentRecord().record_id : selected && selected.rec;
  if (!rec) return;
  if (b.dataset.turn) {
    viewTurn[rec] = ((viewTurn[rec] || 0) + 90) % 360;
    applyTurn(b.closest('.frame'), rec);
  } else {
    inkView = !inkView;
    save('inkView', inkView ? '1' : '0');
    if (view === 'form') renderForm();
    else if (selected && selected.key) showField(selected.rec, selected.key);
  }
});

function applyTurn(frame, rec){
  if (!frame) return;
  const deg = viewTurn[rec] || 0;
  const page = frame.querySelector('.pv-page');
  if (page) fitTurned(page, page.parentNode, page.querySelector('img'), deg, view === 'form' ? 0.66 : 0.44);
  frame.querySelectorAll('.pv-cropwrap').forEach(function(wrap){
    const img = wrap.querySelector('img');
    if (img) fitTurned(img, wrap, img, deg, 0.30);
  });
}

// בסיבוב של 90° הרוחב והגובה מתחלפים: מקטינים כך שייכנס, ומגדילים את המסגרת לגובה החדש.
function fitTurned(el, wrap, img, deg, heightShare){
  const odd = deg % 180 !== 0;
  const tallest = window.innerHeight * heightShare;
  el.style.transform = deg ? 'rotate(' + deg + 'deg)' : '';
  img.style.maxHeight = odd ? Math.min(wrap.clientWidth || 380, tallest) + 'px' : '';
  img.style.maxWidth = odd ? tallest + 'px' : '';
  const size = function(){ wrap.style.height = odd ? el.offsetWidth + 'px' : ''; };
  if (img.complete) size(); else img.addEventListener('load', size, { once: true });
}

/* ------------------------------------------------------------ מצב טופס אחד בכל פעם */
function currentRecord(){
  const list = visibleRecords();
  if (!list.length) return null;
  formIndex = Math.max(0, Math.min(formIndex, list.length - 1));
  return list[formIndex];
}

function firstOpenCard(r){
  const i = FIELDS.findIndex(function(f){ return r.fields[f.key].state === 'needs_review'; });
  return i < 0 ? 0 : i;
}

function renderForm(){
  const list = visibleRecords();
  const r = currentRecord();
  if (!r) {
    $('#formWhere').textContent = 'אין טפסים שמתאימים לסינון';
    $('#formSource').textContent = '';
    $('#formDots').innerHTML = '';
    $('#fvFrame').innerHTML = '<div class="ph">—</div>';
    $('#fvCards').innerHTML = '';
    $('#fvApprove').disabled = true;
    $('#fvApproveHint').textContent = '';
    return;
  }
  cardIndex = Math.max(0, Math.min(cardIndex, FIELDS.length - 1));
  $('#formWhere').textContent = 'טופס ' + (formIndex + 1) + ' מתוך ' + list.length;
  $('#formSource').textContent = r.source_name + ' · עמוד ' + r.page_number +
    (r.straightened ? ' · ' + r.straightened : '') + (r.error ? ' · הקריאה נכשלה: ' + r.error : '');
  $('#prevForm').disabled = formIndex === 0;
  $('#nextForm').disabled = formIndex >= list.length - 1;
  $('#formDots').innerHTML = list.length <= 60 ? list.map(function(x, i){
    const cls = isApproved(x) ? 'done' : x.review_count ? 'review' : '';
    return '<i class="' + cls + (i === formIndex ? ' now' : '') + '"></i>';
  }).join('') : '';

  const key = FIELDS[cardIndex].key;
  $('#fvFrame').innerHTML = pageHtml(r, r.fields[key], true);
  applyTurn($('#fvFrame'), r.record_id);

  $('#fvCards').innerHTML = FIELDS.map(function(spec, i){
    const f = r.fields[spec.key];
    const cls = f.state === 'needs_review' ? 'review' : f.state === 'approved' ? 'approved' : '';
    const approveLabel = f.values.length ? '✓ אשר' : '✓ אשר כריק';
    return '<div class="fcard ' + cls + (i === cardIndex ? ' now' : '') + '" role="listitem" tabindex="0" data-i="' + i +
      '" data-key="' + spec.key + '" aria-label="' + esc(spec.label) + ': ' + esc(f.values.join(' | ') || 'ריק') +
      (cls === 'review' ? ', לבדיקה: ' + esc(f.reasons.join(', ')) : cls === 'approved' ? ', אושר' : '') + '">' +
      '<span class="lbl">' + esc(spec.label) + '</span>' +
      '<div class="btns">' + (f.state !== 'approved' ? '<button class="ok" data-act="approve">' + approveLabel + '</button>' : '') +
      '<button data-act="edit">✎ תקן</button></div>' +
      '<span class="val">' + valueHtml(f) + '</span>' +
      (f.state === 'needs_review' && f.reasons.length ? '<span class="why">⚠ ' + esc(f.reasons.join(' · ')) + '</span>' : '') +
      (f.crop_box ? '<div class="thumb"><img loading="lazy" alt="" src="' + cropUrl(r, spec.key) + '"></div>' : '') +
      '</div>';
  }).join('');

  const open = r.review_count;
  $('#fvApprove').disabled = !!open || r.verified;
  $('#fvApproveHint').textContent = r.verified ? '✓ הטופס אושר ונשמר' :
    open ? 'נשארו ' + open + ' שדות לבדיקה' : 'כל השדות נבדקו — אפשר לאשר את הטופס';
}

function focusCard(i){
  cardIndex = i;
  $$('#fvCards .fcard').forEach(function(c){ c.classList.toggle('now', Number(c.dataset.i) === i); });
  const r = currentRecord();
  if (!r) return;
  const box = $('#fvFrame .pv-page');
  if (box) {
    const old = box.querySelector('.pv-box');
    if (old) old.remove();
    box.insertAdjacentHTML('beforeend', boxHtml(r.fields[FIELDS[i].key]));
  }
  const card = document.querySelector('#fvCards .fcard[data-i="' + i + '"]');
  if (card) { card.focus({ preventScroll: true }); card.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
}

function goForm(delta){
  const list = visibleRecords();
  const next = formIndex + delta;
  if (next < 0 || next >= list.length) return;
  formIndex = next;
  cardIndex = firstOpenCard(list[formIndex]);
  renderForm();
  focusCard(cardIndex);
}
$('#prevForm').onclick = function(){ goForm(-1); };
$('#nextForm').onclick = function(){ goForm(1); };
$('#formDots').addEventListener('click', function(e){
  const dots = $$('#formDots i');
  const i = dots.indexOf(e.target);
  if (i >= 0) { formIndex = i; cardIndex = firstOpenCard(visibleRecords()[i]); renderForm(); }
});
$('#fvApprove').onclick = function(){ const r = currentRecord(); if (r) verify(r.record_id); };

$('#fvCards').addEventListener('click', function(e){
  const card = e.target.closest('.fcard');
  if (!card || e.target.closest('input')) return;
  const r = currentRecord();
  const b = e.target.closest('button');
  if (b && b.dataset.act === 'approve') return approveField(r.record_id, card.dataset.key, true);
  if (b && b.dataset.act === 'edit') return editCard(card);
  focusCard(Number(card.dataset.i));
});

function openRecord(recordId){
  filter = 'all';
  senderFilter = '';
  $$('.filters button').forEach(function(x){ x.classList.toggle('on', x.dataset.filter === 'all'); });
  $('#senderFilter').value = '';
  view = 'form';
  save('view', view);
  const list = visibleRecords();
  formIndex = Math.max(0, list.findIndex(function(r){ return r.record_id === recordId; }));
  cardIndex = list[formIndex] ? firstOpenCard(list[formIndex]) : 0;
  lastSignature = '';
  render(last, true);
  $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ------------------------------------------------------------ עריכה */
// עורך אחד פתוח בכל רגע. פתיחת עורך חדש סוגרת קודם את הקודם (בלי לשמור).
let activeEditor = null;
function cancelEditor(){
  if (!activeEditor) return false;
  const old = activeEditor;
  activeEditor = null;
  old.cancel();
  return true;
}

function edit(td){
  if (td.querySelector('input')) return;
  closePop();
  const key = td.dataset.key, rec = td.dataset.rec;
  if (cancelEditor()) {
    lastSignature = '';
    render(last, true);
    td = document.querySelector('td.cell[data-rec="' + rec + '"][data-key="' + key + '"]');
    if (!td) return;
  }
  const raw = td.dataset.raw || '';
  td.innerHTML = '';
  const input = document.createElement('input');
  input.value = td.dataset.raw || '';
  input.setAttribute('aria-label', 'תיקון ' + fieldLabel(key) + ' — Enter לשמירה, Esc לביטול. כמה ערכים: להפריד ב-|');
  td.appendChild(input);
  input.focus();
  input.select();
  let closed = false;
  // סוגרים את תיבת העריכה לפני שמירה וציור — אחרת המסך "חושב" שעדיין מקלידים
  const finish = function(saveIt){
    if (closed) return;
    closed = true;
    if (activeEditor && activeEditor.input === input) activeEditor = null;
    const text = input.value;
    input.remove();
    if (saveIt) saveValues(rec, key, splitValues(text), raw);
    else { lastSignature = ''; render(last, true); }
  };
  activeEditor = { input: input, cancel: function(){ closed = true; input.remove(); } };
  input.onblur = function(){ finish(true); };
  input.onkeydown = function(e){
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  };
}

function editCard(card){
  if (card.querySelector('input')) return;
  const r = currentRecord();
  const key = card.dataset.key;
  if (cancelEditor()) {
    lastSignature = '';
    render(last, true);
    card = document.querySelector('#fvCards .fcard[data-key="' + key + '"]');
    if (!card) return;
  }
  const raw = r.fields[key].values.join(' | ');
  const box = document.createElement('div');
  box.className = 'cardEdit';
  const input = document.createElement('input');
  input.value = raw;
  input.setAttribute('aria-label', 'תיקון ' + fieldLabel(key) + ' — Enter לשמירה, Esc לביטול');
  box.appendChild(input);
  card.appendChild(box);
  input.focus();
  input.select();
  let closed = false;
  // סוגרים את תיבת העריכה לפני שמירה וציור — אחרת המסך "חושב" שעדיין מקלידים
  const finish = function(saveIt){
    if (closed) return;
    closed = true;
    if (activeEditor && activeEditor.input === input) activeEditor = null;
    const text = input.value;
    box.remove();
    if (saveIt) saveValues(r.record_id, key, splitValues(text), raw, true);
    else { lastSignature = ''; render(last, true); focusCard(cardIndex); }
  };
  activeEditor = { input: input, cancel: function(){ closed = true; box.remove(); } };
  input.onkeydown = function(e){
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  };
  input.onblur = function(){ finish(true); };
}

function splitValues(text){
  return text.split('|').map(function(s){ return s.trim(); }).filter(Boolean);
}

/* ------------------------------------------------------------ פעולות מול השרת */
async function patch(recordId, body){
  const res = await fetch('/api/records/' + recordId, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
  if (!res.ok) { toast('הפעולה לא נשמרה — נסו שוב'); return null; }
  return res.json();
}

async function approveField(recordId, key, fromCard){
  const d = await patch(recordId, { field: key, approve: true });
  if (!d) return;
  lastSignature = '';
  render(d, true);
  closePop();
  toast('✓ ' + fieldLabel(key) + ' אושר', { undo: function(){ undo(recordId, key); } });
  if (fromCard || view === 'form') advanceCard();
}

async function saveValues(recordId, key, values, before, fromCard){
  if (values.join(' | ') === before) { lastSignature = ''; render(last, true); return; }
  const d = await patch(recordId, { field: key, values: values });
  if (!d) return;
  lastSignature = '';
  render(d, true);
  toast('✎ ' + fieldLabel(key) + ' עודכן', { undo: function(){ undo(recordId, key); } });
  if (fromCard || view === 'form') advanceCard();
}

// אחרי פעולה במצב טופס — קופצים לשדה הכתום הבא באותו טופס
function advanceCard(){
  if (view !== 'form') return;
  const r = currentRecord();
  if (!r) return;
  const order = FIELDS.map(function(_, i){ return (cardIndex + 1 + i) % FIELDS.length; });
  const next = order.find(function(i){ return r.fields[FIELDS[i].key].state === 'needs_review'; });
  focusCard(next == null ? cardIndex : next);
}

async function undo(recordId, key){
  const res = await fetch('/api/records/' + recordId + '/undo', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ field: key })
  });
  if (!res.ok) return toast('אין מה לבטל');
  lastSignature = '';
  render(await res.json(), true);
  toast('↶ ' + fieldLabel(key) + ' — בוטל');
}

async function verify(recordId){
  const res = await fetch('/api/records/' + recordId + '/verify', { method: 'POST' });
  const d = await res.json().catch(function(){ return {}; });
  if (!res.ok) return toast(d.detail || 'עוד לא אפשר לאשר את הטופס');
  lastSignature = '';
  render(d, true);
  toast('✓ הטופס אושר ונשמר כמאומת');
  if (view === 'form') goForm(1);
}

/* ------------------------------------------------------------ מקלדת במצב טופס */
document.addEventListener('keydown', function(e){
  if (e.key === 'Escape') { closePop(); if (!$('#tour').classList.contains('hidden')) closeTour(); }
  if (view !== 'form' || $('#results').classList.contains('hidden') || !$('#tour').classList.contains('hidden')) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
  if (e.target.closest && e.target.closest('header')) return;
  const r = currentRecord();
  if (!r) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); goForm(1); }             // מימין לשמאל: שמאלה = הבא
  else if (e.key === 'ArrowRight') { e.preventDefault(); goForm(-1); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); focusCard(Math.min(FIELDS.length - 1, cardIndex + 1)); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); focusCard(Math.max(0, cardIndex - 1)); }
  else if (e.key === 'Enter' && !(e.target.closest && e.target.closest('button'))) {
    e.preventDefault();
    if (r.fields[FIELDS[cardIndex].key].state !== 'approved') approveField(r.record_id, FIELDS[cardIndex].key, true);
  } else if (e.code === 'KeyE') {
    e.preventDefault();
    const card = document.querySelector('#fvCards .fcard[data-i="' + cardIndex + '"]');
    if (card) editCard(card);
  }
});

/* ------------------------------------------------------------ ייצוא וקובץ חדש */
$('#download').onclick = async function(){
  const res = await fetch('/api/batches/' + batch + '/export', { method: 'POST' });
  if (!res.ok) return notice('err', 'הייצוא נכשל', 'נסו שוב.');
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'commitments-' + batch + '.xlsx';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  $('#download').classList.remove('ready');
  toast('⬇ קובץ ה-Excel ירד');
};

$('#again').onclick = function(){
  clearInterval(timer);
  batch = null;
  last = null;
  selected = null;
  processing = false;
  closePop();
  clearNotices();
  $('#results').classList.add('hidden');
  $('#progress').classList.add('hidden');
  $('#download').classList.remove('ready');
  $('#file').value = '';
  drop.classList.remove('hidden');
};

/* ------------------------------------------------------------ רגע הצלחה */
function maybeCelebrate(d){
  if (processing || celebrated || !d.records.length || d.review_total !== 0) return;
  if (!d.records.some(function(r){ return !r.error; })) return;
  celebrated = true;
  $('#download').classList.add('ready');
  toast('🎉 כל ' + d.records.length + ' הטפסים מוכנים לייצוא', { big: true, ms: 5000 });
  if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const party = $('#party');
  const colors = ['#1a8f8f', '#133654', '#ffbf73', '#7fd6a3', '#3cc0c0'];
  party.innerHTML = Array.from({ length: 46 }, function(_, i){
    return '<i style="left:' + (Math.random() * 100) + '%;background:' + colors[i % colors.length] +
      ';animation-delay:' + (Math.random() * 0.6) + 's"></i>';
  }).join('');
  party.classList.remove('hidden');
  setTimeout(function(){ party.classList.add('hidden'); party.innerHTML = ''; }, 3400);
}

/* ------------------------------------------------------------ סיור קצר */
const TOUR = [
  { art: '📄 ➜ 📊', title: 'גוררים טפסים',
    text: 'גוררים קבצי PDF או תמונות — כמה שרוצים. כל טופס נקרא לבד ומופיע ברגע שהוא מוכן. אפשר גם ללחוץ "נסו עם טופס דוגמה".' },
  { art: '<span class="demo-cell n">939376</span><span class="demo-cell r">00112?344</span><span class="demo-cell g">✓ מאוחדת</span>',
    title: 'כתום = לבדוק', text: 'כתום — המנוע לא בטוח או שבדיקה אוטומטית נכשלה, והסיבה כתובה. ירוק — אדם בדק. תא רגיל — לא נמצא ספק.' },
  { art: '🔍', title: 'מעבר על תא = תקריב',
    text: 'עכבר או Tab על תא מציגים תקריב של השדה מהטופס. ✓ מאשר, ✎ מתקן, ולכל פעולה יש "בטל". במצב "טופס" עוברים טופס אחרי טופס עם החיצים.' }
];
let tourStep = 0;
function openTour(){ tourStep = 0; drawTour(); $('#tour').classList.remove('hidden'); $('#tourNext').focus(); }
function closeTour(){ $('#tour').classList.add('hidden'); save('toured', '1'); }
function drawTour(){
  const s = TOUR[tourStep];
  $('#tourArt').innerHTML = s.art;
  $('#tourTitle').textContent = s.title;
  $('#tourText').textContent = s.text;
  $('#tourDots').innerHTML = TOUR.map(function(_, i){ return '<i class="' + (i === tourStep ? 'on' : '') + '"></i>'; }).join('');
  $('#tourNext').textContent = tourStep === TOUR.length - 1 ? 'מתחילים' : 'הבא';
}
$('#tourNext').onclick = function(){ if (tourStep < TOUR.length - 1) { tourStep += 1; drawTour(); } else closeTour(); };
$('#tourSkip').onclick = closeTour;
$('#helpBtn').onclick = openTour;
$('#tour').addEventListener('click', function(e){ if (e.target.id === 'tour') closeTour(); });
