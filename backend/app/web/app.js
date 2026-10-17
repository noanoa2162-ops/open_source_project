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
let view = load('view', 'tiles') === 'table' ? 'table' : 'tiles';   // tiles | table | form (טופס פתוח מתוך האריחים)
let finishing = false;
let filter = 'all', senderFilter = '', query = '';
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
  applyBrand(c.brand);
  const reader = c.model + (c.effort ? ' · חשיבה ' + c.effort : '');
  $('#modelName').textContent = reader;
  $('#limits').textContent = 'PDF, JPG או PNG · עד ' + c.max_files + ' קבצים ו-' + c.max_pages +
    ' טפסים בגרירה אחת · קורא: ' + reader;
  $('#head').innerHTML = '<th scope="col">טופס</th>' + FIELDS.map(function(f){
    return '<th scope="col" title="' + esc(f.hint) + '">' + esc(f.label) + '</th>';
  }).join('') + '<th scope="col">אישור</th>';
  $('#sampleBtn').classList.toggle('hidden', !c.sample);
  if (!c.has_key) notice('err', 'אין מפתח API', 'לא נמצא ANTHROPIC_API_KEY בקובץ .env. הוסיפו והפעילו מחדש את השרת.');
  if (load('toured', '') !== '1') openTour();
});

/* ------------------------------------------------------------ מיתוג */
// moh = עיצוב משרד הבריאות (ברירת המחדל); neutral = הסמל והשם שלנו, בלי שם המשרד ובלי הלוגו.
const NEUTRAL_MARK = 'data:image/svg+xml;utf8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect x="9" y="5" width="22" height="30" rx="3" fill="#fff" stroke="#186cb4" stroke-width="2.5"/>' +
  '<path d="M14 13h12M14 18h9" stroke="#9db4c8" stroke-width="2.5" stroke-linecap="round"/>' +
  '<path d="M14 26l4 4 8-9" fill="none" stroke="#00a8cc" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>');
function applyBrand(brand){
  root.dataset.brand = brand === 'neutral' ? 'neutral' : 'moh';
  if (root.dataset.brand !== 'neutral') return;
  $('#brand .mark img').src = NEUTRAL_MARK;
  $('#brandOrg').textContent = 'טופס למידע';
  $('#brandUnit').textContent = 'קליטת טפסי התחייבות';
  $('#footMark').classList.add('hidden');
  $('#footOrg').textContent = 'טופס למידע';
  $('#footUnit').textContent = 'קליטת טפסי התחייבות';
  const icon = document.querySelector('link[rel="icon"]');
  if (icon) icon.href = NEUTRAL_MARK;
  document.title = 'קליטת טפסי התחייבות — טופס למידע';
}

// שלבי העבודה בראש העמוד: 1 העלאה → 2 בדיקה ואישור → 3 ייצוא
function setStep(n){
  $$('#steps li').forEach(function(li){
    const step = Number(li.dataset.step);
    li.classList.toggle('now', step === n);
    li.classList.toggle('done', step < n);
    if (step === n) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
  });
}

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
function firstValue(r, key){ return (r.fields[key] && r.fields[key].values[0]) || ''; }
// תג מצב בצורת "שירות מקוון" של gov.il. ירוק רק כשאדם אישר.
function statusBadge(r){
  if (r.error) return '<span class="badge fail">✗ הקריאה נכשלה</span>';
  if (isApproved(r)) return '<span class="badge ok">✓ ' + (r.verified ? 'מאומת' : 'מאושר') + '</span>';
  if (r.review_count) return '<span class="badge review">⚠ <b>' + r.review_count + '</b> לבדיקה</span>';
  return '<span class="badge ready">מוכן לאישור</span>';
}

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
  const all = $$('#toasts .toast:not(.out)');
  all.slice(0, Math.max(0, all.length - 3)).forEach(function(old){
    old.classList.add('out');
    setTimeout(function(){ old.remove(); }, 260);
  });
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
  setStep(2);
  drop.classList.add('hidden');
  $('#results').classList.add('hidden');
  $('#progress').classList.remove('hidden');
  $('#pTitle').textContent = title;
  $('#pCount').textContent = '';
  resetRing();
  $('#tiles').innerHTML = '';
  Object.keys(tilesBuilt).forEach(function(k){ delete tilesBuilt[k]; });
  $('#pFiles').innerHTML = (names || []).map(function(n){ return '<li><bdi>' + esc(n) + '</bdi></li>'; }).join('');
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
  finishing = false;
  view = 'tiles';
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
  setStep(1);
  notice('err', 'לא הצלחנו לעבד את הקבצים', msg);
}

/* ------------------------------------------------------------ התקדמות חיה */
// גלגל שמתמלא לפי הטפסים המוכנים, ומספר שסופר אליהם בהדרגה.
const RING_C = 2 * Math.PI * 52;
const calm = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
let ringShown = 0, ringTarget = 0, ringTimer = null;

function resetRing(){
  clearTimeout(ringTimer);
  ringTimer = null;
  ringShown = ringTarget = 0;
  $('#ring').className = 'ring indeterminate';
  $('#ringArc').style.strokeDashoffset = '';
  $('#ringNum').textContent = '0';
  $('#ringOf').textContent = 'מכין…';
}

function setRing(done, total){
  if (!total) return;
  $('#ring').classList.remove('indeterminate');
  $('#ringArc').style.strokeDashoffset = String(RING_C * (1 - Math.min(1, done / total)));
  $('#ringOf').textContent = 'מתוך ' + total + (total === 1 ? ' טופס' : ' טפסים');
  countTo(done);
}

function countTo(n){
  ringTarget = n;
  if (calm) { ringShown = n; $('#ringNum').textContent = n; return; }
  if (ringTimer) return;
  (function step(){
    if (ringShown === ringTarget) { ringTimer = null; return; }
    ringShown += ringShown < ringTarget ? 1 : -1;
    $('#ringNum').textContent = ringShown;
    ringTimer = setTimeout(step, Math.max(45, 450 / Math.max(1, Math.abs(ringTarget - ringShown))));
  })();
}

async function tick(){
  const me = batch;
  const res = await fetch('/api/batches/' + me);
  if (me !== batch || finishing) return;
  if (!res.ok) { clearInterval(timer); return fail('האצווה לא נמצאה'); }
  const d = await res.json();
  if (me !== batch || finishing) return;
  const total = d.total_pages || 0, done = d.processed_pages || 0;
  const finished = ['completed', 'partial', 'failed'].indexOf(d.status) !== -1;
  const reading = (d.pages || []).filter(function(p){ return p.status === 'reading'; }).length;

  setRing(done, total);
  if (total) {
    $('#pTitle').textContent = 'קורא את הטפסים';
    $('#pCount').textContent = 'נקראים עכשיו: ' + reading + ' · בתור: ' + Math.max(0, total - done - reading);
  }
  $('#pFiles').innerHTML = (d.sources || []).map(function(s){
    const count = s.page_count === 1 ? 'טופס אחד' : (s.page_count || '…') + ' טפסים';
    const detail = s.error ? '<span class="bad">' + esc(s.error) + '</span>' : '<span class="good">' + count + '</span>';
    return '<li><bdi>' + esc(s.filename) + '</bdi> — ' + detail + '</li>';
  }).join('');
  updateTiles(d);
  if (!finished) return;

  // הכול נקרא: הגלגל מתמלא, ✓, ואז עוברים לאריחים. עד עכשיו אין מה ללחוץ.
  clearInterval(timer);
  finishing = true;
  if (d.status === 'failed') { finishing = false; return fail(d.error || 'העיבוד נכשל'); }
  const n = d.records.length;
  setRing(total || n, total || n);
  $('#ring').classList.add('complete');
  $('#pTitle').textContent = n === 1 ? 'הטופס נקלט' : 'כל ' + n + ' הטפסים נקלטו';
  $('#pCount').textContent = 'עוברים לבדיקה ולאישור';
  setTimeout(function(){
    if (me !== batch) return;
    finishing = false;
    processing = false;
    $('#progress').classList.add('hidden');
    if (d.status === 'partial') notice('warn', 'חלק מהקבצים לא עובדו במלואם', d.error);
    if (d.skipped_pages)
      notice('warn', 'נחצתה תקרת הטפסים', d.skipped_pages + ' טפסים לא עובדו. עובדו ' + total +
        ' הראשונים. ניתן לשנות את MAX_PAGES_PER_BATCH בקובץ .env.');
    (d.warnings || []).forEach(function(w){ notice('warn', 'לתשומת לב', w); });
    lastSignature = '';
    render(d, true);
    $('#results').scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
  }, calm ? 250 : 1300);
}

// אריחי הרקע של מסך הטעינה: שלד מהבהב בתור, והטופס עולה כשהוא נקרא. לא לחיצים.
function updateTiles(d){
  const box = $('#tiles');
  (d.pages || []).forEach(function(p){
    const id = p.source_index + '-' + p.page_number;
    let tile = tilesBuilt[id];
    if (!tile) {
      tile = document.createElement('div');
      tile.className = 'tile queued';
      tile.innerHTML = '<img alt="" data-src="/api/batches/' + batch + '/sources/' + p.source_index +
        '/pages/' + p.page_number + '/thumb"><i class="dot"></i>';
      tile.querySelector('img').onerror = function(){ this.style.visibility = 'hidden'; };
      box.appendChild(tile);
      tilesBuilt[id] = tile;
    }
    const record = p.record_id ? d.records.find(function(r){ return r.record_id === p.record_id; }) : null;
    let cls = p.status === 'reading' ? 'reading' : 'queued';
    if (p.status === 'done') cls = 'shown ' + (record && record.review_count ? 'review' : 'done');
    if (p.status === 'failed') cls = 'shown failed';
    if (p.status === 'skipped') cls = 'shown';
    if (tile.dataset.cls === cls) return;
    tile.dataset.cls = cls;
    tile.className = 'tile ' + cls;
    const img = tile.querySelector('img');
    if (cls.indexOf('shown') === 0 && !img.getAttribute('src')) img.src = img.dataset.src;
  });
}

/* ------------------------------------------------------------ תצוגה כללית */
function visibleRecords(){
  if (!last) return [];
  return last.records.filter(function(r){
    if (filter === 'review' && !(r.review_count > 0)) return false;
    if (filter === 'approved' && !isApproved(r)) return false;
    if (senderFilter && senderOf(r) !== senderFilter) return false;
    if (query) {
      const text = FIELDS.map(function(f){ return r.fields[f.key].values.join(' '); }).join(' ') + ' ' + r.source_name;
      if (text.toLowerCase().indexOf(query) === -1) return false;
    }
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
  setStep(!processing && d.records.length && d.review_total === 0 ? 3 : 2);
  const sig = signature(d) + '|' + filter + '|' + senderFilter + '|' + query + '|' + view;
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
  const reviewForms = d.records.filter(function(r){ return r.review_count > 0; }).length;
  $('#navReviewN').textContent = reviewForms || '';
  $('#navReview').disabled = !d.records.length;
  // ייצוא רק אחרי שכל שדה כתום תוקן או אושר (השרת אוכף את אותו כלל)
  const ready = !processing && d.records.length > 0 && d.review_total === 0;
  const why = !d.records.length ? 'אין טפסים לייצוא' : processing ? 'הייצוא ייפתח בסוף הקריאה'
    : d.review_total ? 'נשארו ' + d.review_total + ' שדות לבדיקה לפני ייצוא' : '';
  $('#download').disabled = !ready;
  $('#navExport').disabled = !ready;
  $('#exportHint').textContent = why;
  $('#download').title = why || 'הורדת כל הטפסים לקובץ Excel';
  $('#navExport').title = why || 'הורדת כל הטפסים לקובץ Excel';
  if (!ready) $('#download').classList.remove('ready');
  const senders = Array.from(new Set(d.records.map(senderOf).filter(Boolean))).sort();
  const sel = $('#senderFilter');
  const options = '<option value="">כל הגורמים</option>' + senders.map(function(s){
    return '<option' + (s === senderFilter ? ' selected' : '') + '>' + esc(s) + '</option>';
  }).join('');
  if (sel.dataset.options !== options) { sel.innerHTML = options; sel.dataset.options = options; }
}

function showView(){
  if (['table', 'tiles', 'form'].indexOf(view) === -1) view = 'tiles';
  $('#viewTiles').setAttribute('aria-selected', String(view !== 'table'));
  $('#viewTable').setAttribute('aria-selected', String(view === 'table'));
  ['table', 'tiles', 'form'].forEach(function(v){
    $('#' + v + 'View').classList.toggle('hidden', view !== v);
  });
  closePop();
  if (view === 'table') renderTable(); else if (view === 'tiles') renderTiles(); else renderForm();
}

function setView(v){ view = v; save('view', view); lastSignature = ''; if (last) render(last, true); }
$('#viewTable').onclick = function(){ setView('table'); };
$('#viewTiles').onclick = function(){ setView('tiles'); };

// חיפוש: התיבה בפס העליון והתיבה בפאנל הסינון מסונכרנות
let queryTimer = null;
['#q', '#q2'].forEach(function(sel, i, all){
  $(sel).addEventListener('input', function(e){
    const other = $(all[1 - i]);
    if (other.value !== e.target.value) other.value = e.target.value;
    clearTimeout(queryTimer);
    queryTimer = setTimeout(function(){
      query = e.target.value.trim().toLowerCase();
      formIndex = 0;
      if (last) render(last, true);
    }, 180);
  });
});
$('#q').addEventListener('keydown', function(e){
  if (e.key === 'Enter' && last) $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

// תפריט הגלולות בכותרת המשנה
$('#navUpload').onclick = function(){ window.scrollTo({ top: 0, behavior: 'smooth' }); };
$('#navReview').onclick = function(){
  const b = document.querySelector('.filters button[data-filter="review"]');
  if (b) b.click();
  $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' });
};
$('#navExport').onclick = function(){ $('#download').click(); };

// כפתור עגול "חזרה לראש הדף", כמו באתר
// מוסתר כשפס הקודם/הבא על המסך, כדי לא לכסות את "הטופס הבא"
let navInView = false;
function updateTopButton(){ $('#backToTop').classList.toggle('show', window.scrollY > 500 && !navInView); }
window.addEventListener('scroll', updateTopButton, { passive: true });
if ('IntersectionObserver' in window) {
  new IntersectionObserver(function(entries){
    navInView = entries.some(function(e){ return e.isIntersecting; });
    updateTopButton();
  }).observe($('#formView .formnav'));
}
$('#backToTop').onclick = function(){ window.scrollTo({ top: 0, behavior: 'smooth' }); };
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
  const span = FIELDS.length + 2;
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
        ' title="' + (open ? 'קודם צריך לבדוק ' + open + ' שדות כתומים' : 'אישור כל הטופס ושמירה כמאומת') + '">✓ אשר</button>';
    const num = last.records.indexOf(r) + 1;
    return '<tr data-rec="' + r.record_id + '"><td class="formcol" title="פתיחת הטופס · ' + esc(r.source_name) +
      ' · עמוד ' + r.page_number + '"><div class="fc"><img loading="lazy" alt="" src="' + pageUrl(r) + '/thumb">' +
      '<span class="t"><b>טופס ' + num + '</b>' + statusBadge(r) + '</span></div></td>' +
      FIELDS.map(function(f){ return cell(r, f); }).join('') +
      '<td class="acts">' + acts + '</td></tr>' + noteRow;
  }).join('');
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
  if (td.classList.contains('cell')) { clearTimeout(popTimer); openPop(td); }
  else if (td.classList.contains('formcol') && rec) openRecord(rec);
});
tbody.addEventListener('dblclick', function(e){
  const td = e.target.closest('td.cell');
  if (td && !e.target.closest('button')) edit(td);
});
tbody.addEventListener('keydown', function(e){
  const td = e.target.closest && e.target.closest('td.cell');
  if (!td || e.target.tagName === 'INPUT') return;
  if (e.key === 'Enter') { e.preventDefault(); openPop(td); }
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

/* ------------------------------------------------------------ מצב אריחים */
// אריח לכל טופס, כמו אריחי השירותים באתר: תמונה, תג מצב, גורם שולח, שם ומספרים.
function renderTiles(){
  const list = visibleRecords();
  $('#tilesEmpty').classList.toggle('hidden', list.length > 0);
  $('#tileGrid').innerHTML = list.map(function(r){
    const name = [firstValue(r, 'first_name'), firstValue(r, 'last_name')].filter(Boolean).join(' ');
    const commit = r.fields.commitment_numbers ? r.fields.commitment_numbers.values.join(', ') : '';
    const sender = senderOf(r);
    return '<li class="ftile" tabindex="0" role="button" data-rec="' + r.record_id + '" aria-label="' +
      esc((sender || 'גורם לא זוהה') + ', ' + (name || 'שם לא זוהה') + '. פתיחת הטופס') + '">' +
      '<div class="thumb"><img loading="lazy" alt="" src="' + pageUrl(r) + '/thumb">' + statusBadge(r) + '</div>' +
      '<div class="body"><h4>' + esc(sender || 'גורם שולח לא זוהה') + '</h4>' +
      '<span class="who">' + esc(name || '—') + '</span>' +
      '<span class="nums">מספר התחייבות: <b>' + esc(commit || '—') + '</b></span>' +
      '<span class="metaline"><span>עמוד ' + r.page_number + '</span><span class="file" title="' + esc(r.source_name) + '">' +
      esc(r.source_name) + '</span></span></div></li>';
  }).join('');
  $$('#tileGrid img').forEach(function(img){ img.onerror = function(){ this.style.visibility = 'hidden'; }; });
}
$('#tileGrid').addEventListener('click', function(e){
  const t = e.target.closest('.ftile');
  if (t) openRecord(t.dataset.rec);
});
$('#tileGrid').addEventListener('keydown', function(e){
  const t = e.target.closest('.ftile');
  if (t && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openRecord(t.dataset.rec); }
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
  if (b.dataset.act === 'open') { closePop(); openRecord(td.dataset.rec); }
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
                : '<div class="crop"><div class="nocrop">אין מיקום לשדה הזה — אפשר לפתוח את הטופס המלא</div></div>') +
    '<div class="val">' + valueHtml(f) + '</div>' +
    (f.state === 'needs_review' && f.reasons.length ? '<div class="why">' + esc(f.reasons.join(' · ')) + '</div>' : '') +
    '<div class="btns"><button class="secondary" data-act="open">פתיחת הטופס</button>' +
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

document.addEventListener('click', function(e){
  const b = e.target.closest && e.target.closest('[data-turn],[data-ink]');
  if (!b) return;
  e.stopPropagation();
  const current = view === 'form' ? currentRecord() : null;
  if (!current) return;
  const rec = current.record_id;
  if (b.dataset.turn) {
    viewTurn[rec] = ((viewTurn[rec] || 0) + 90) % 360;
    applyTurn(b.closest('.frame'), rec);
  } else {
    inkView = !inkView;
    save('inkView', inkView ? '1' : '0');
    renderForm();
    focusCard(cardIndex, true);
  }
});

function applyTurn(frame, rec){
  if (!frame) return;
  const deg = viewTurn[rec] || 0;
  const page = frame.querySelector('.pv-page');
  if (page) fitTurned(page, page.parentNode, page.querySelector('img'), deg, 0.62);
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
    $('#formBadge').innerHTML = '';
    $('#fieldsHint').textContent = '';
    return;
  }
  cardIndex = Math.max(0, Math.min(cardIndex, FIELDS.length - 1));
  $('#formWhere').textContent = 'טופס ' + (formIndex + 1) + ' מתוך ' + list.length;
  $('#formBadge').innerHTML = statusBadge(r);
  $('#fieldsHint').textContent = r.review_count
    ? 'נשארו ' + r.review_count + ' שדות לבדיקה · לחיצה על ריבוע מסמנת אותו בטופס'
    : 'כל השדות נבדקו';
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

function focusCard(i, noScroll){
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
  if (card) {
    card.focus({ preventScroll: true });
    if (!noScroll) card.scrollIntoView({ block: 'nearest', behavior: calm ? 'auto' : 'smooth' });
  }
}

function goForm(delta){
  const list = visibleRecords();
  const next = formIndex + delta;
  if (!delta || next < 0 || next >= list.length) return;
  formIndex = next;
  cardIndex = firstOpenCard(list[formIndex]);
  renderForm();
  focusCard(cardIndex, true);
  $('#formView').scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
}
$('#prevForm').onclick = function(){ goForm(-1); };
$('#nextForm').onclick = function(){ goForm(1); };
$('#formDots').addEventListener('click', function(e){
  const dots = $$('#formDots i');
  const i = dots.indexOf(e.target);
  if (i >= 0) goForm(i - formIndex);
});
$('#fvApprove').onclick = function(){ const r = currentRecord(); if (r) verify(r.record_id); };
$('#backToTiles').onclick = function(){
  const r = currentRecord();
  setView('tiles');
  const t = r && document.querySelector('.ftile[data-rec="' + r.record_id + '"]');
  if (t) { t.scrollIntoView({ block: 'center' }); t.focus({ preventScroll: true }); }
};

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
  query = '';
  $('#q').value = '';
  $('#q2').value = '';
  $$('.filters button').forEach(function(x){ x.classList.toggle('on', x.dataset.filter === 'all'); });
  $('#senderFilter').value = '';
  view = 'form';
  const list = visibleRecords();
  formIndex = Math.max(0, list.findIndex(function(r){ return r.record_id === recordId; }));
  cardIndex = list[formIndex] ? firstOpenCard(list[formIndex]) : 0;
  lastSignature = '';
  render(last, true);
  focusCard(cardIndex, true);
  $('#formView').scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
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
  if ($('#download').disabled) return;
  const res = await fetch('/api/batches/' + batch + '/export', { method: 'POST' });
  if (!res.ok) {
    const e = await res.json().catch(function(){ return {}; });
    return toast(e.detail || 'הייצוא נכשל — נסו שוב');
  }
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
  processing = false;
  finishing = false;
  closePop();
  clearNotices();
  $('#results').classList.add('hidden');
  $('#progress').classList.add('hidden');
  $('#download').classList.remove('ready');
  $('#file').value = '';
  drop.classList.remove('hidden');
  setStep(1);
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
  const colors = ['#0068f5', '#0c306a', '#00a8cc', '#c2d96b', '#48c0d8'];
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
    text: 'בטבלה: עכבר או Tab על תא מציגים תקריב של השדה. באריחים: לחיצה פותחת את הטופס — התמונה למעלה, השדות מתחת, וחצים לטופס הבא. ✓ מאשר, ✎ מתקן, ולכל פעולה יש "בטל". הייצוא לאקסל נפתח כשאין יותר שדות לבדיקה.' }
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
