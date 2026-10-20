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
// הטופס הפתוח במסך הטופס נשאר פתוח עד שעוברים ממנו — גם אם יצא מהסינון (למשל "לבדיקה"
// אחרי שאושר השדה הכתום האחרון), כדי שאפשר יהיה ללחוץ "אישור הטופס"
let pinnedId = null;
let zoomOpen = false;           // תקריב השדה ליד הטופס — נפתח רק בלחיצה על ריבוע שדה
let lastSignature = '';
let celebrated = false;
// המזכירה עצמה לחצה "אישור הטופס" בהעלאה הזו. החגיגה וחלונית האקסל רק אחרי לחיצה כזו — לא כשטפסים
// מגיעים מהמערכת כבר מאושרים (תוקן 20/10/2026: הם הקפיצו קונפטי ואקסל מיד בהעלאה)
let approvedHere = false;
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
  $('#limits').textContent = 'PDF, JPG או PNG · עד ' + c.max_pages + ' טפסים בכל פעם';
  $('#head').innerHTML = '<th scope="col">טופס</th>' + FIELDS.map(function(f){
    return '<th scope="col" title="' + esc(f.hint) + '">' + esc(f.label) + '</th>';
  }).join('') + '<th scope="col"><span class="sr-only">אישור</span></th>';
  $('#fvCards').style.setProperty('--n', FIELDS.length);
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
function cropUrl(r, key, i){
  const q = [];
  if (inkView) q.push('ink=1');
  if (i != null) q.push('i=' + i);
  return pageUrl(r) + '/crops/' + key + (q.length ? '?' + q.join('&') : '');
}
// כשיש כמה ערכים עם מיקום לכל אחד (למשל שני מספרים מרכזיים) — תקריב לכל ערך
function perValue(f){ return f && f.crop_boxes && f.crop_boxes.length > 1 ? f.crop_boxes : null; }
function isApproved(r){ return r.verified || FIELDS.every(function(f){ return r.fields[f.key].state === 'approved'; }); }
function senderOf(r){ return (r.fields.sending_entity.values[0] || '').trim(); }
// ערך בלי אותיות עבריות (מספר, תאריך, "X132767") — תמיד משמאל לימין. בלי זה הדפדפן מסדר מחדש
// את החלקים שמסביב ל-"?" או ל-"/" בתוך הדף העברי, ו-"1?007" נראה "007?1".
function isLtr(text){ return !/[֐-׿]/.test(String(text || '')); }
const LTR_KEYS = ['national_id', 'central_numbers', 'sample_received_date', 'commitment_numbers'];
// ערך עם "?" (תו שלא נקרא) — אי אפשר לאשר אותו, רק לתקן (גם השרת מסרב)
function hasUnknown(f){ return !!f && f.values.some(function(v){ return v.indexOf('?') !== -1; }); }
function bdiText(v){ return '<bdi dir="' + (v && isLtr(v) ? 'ltr' : 'auto') + '">' + esc(v || '—') + '</bdi>'; }
function valueHtml(f){
  if (f.values.length) {
    const text = f.values.join(' | ');
    return '<bdi dir="' + (isLtr(text) ? 'ltr' : 'rtl') + '">' + markUnreadable(text) + '</bdi>';
  }
  const notOnForm = (f.reason_codes || []).indexOf('not_on_form') !== -1;
  return '<span class="empty">' + (notOnForm ? 'לא בטופס' : 'חסר') + '</span>';
}
function editing(){ return !!document.querySelector('td input, .cardEdit input'); }
function firstValue(r, key){ return (r.fields[key] && r.fields[key].values[0]) || ''; }
// תג מצב בצורת "שירות מקוון" של gov.il. ירוק רק כשאדם אישר.
function statusBadge(r){
  if (r.error) return '<span class="badge fail">✗ הקריאה נכשלה</span>';
  if (isApproved(r)) return '<span class="badge ok">✓ ' + (r.verified ? 'מאומת' : 'מאושר') + '</span>';
  if (r.review_count) return '<span class="badge review"><b>' + r.review_count + '</b> לבדיקה</span>';
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
  showProgress('קולט את הטפסים…',
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

function begin(info){
  batch = info.batch_id;
  last = null;
  finishing = false;
  view = 'tiles';
  lastSignature = '';
  celebrated = false;
  approvedHere = false;
  formIndex = 0;
  pinnedId = null;
  cardIndex = 0;
  processing = true;
  if (info.rejected)
    notice('warn', info.rejected + ' קבצים לא התקבלו', 'הם מופיעים ברשימה עם הסיבה. שאר הקבצים מעובדים כרגיל.');
  $('#pTitle').textContent = 'קולט את הטפסים…';
  startClock();
  clearInterval(timer);
  timer = setInterval(tick, 900);
  tick();
}

function fail(msg){
  clearInterval(timer);
  stopClock();
  processing = false;
  $('#progress').classList.add('hidden');
  $('#pFiles').innerHTML = '';
  drop.classList.remove('hidden');
  setStep(1);
  notice('err', 'לא הצלחנו לעבד את הקבצים', msg);
}

/* ------------------------------------------------------------ התקדמות חיה */
// גלגל אחד באחוזים, מההעלאה ועד הסוף — בלי שלבים ובלי "מתוך N". המשתמשת לא צריכה לדעת
// שקודם מכינים את העמודים ואחר כך קוראים אותם: היא רואה רק כמה מהעבודה כבר נעשתה.
// הכנת העמודים מהירה ולכן שווה 20%; הקריאה איטית ושווה 80%. טופס שבאמצע קריאה נספר כחצי,
// כדי שהאחוז לא יעמוד בזמן ההמתנה. האחוז אף פעם לא יורד, ו-100% רק כשהכול גמור.
const RING_C = 2 * Math.PI * 52;
const PCT_STEP = 40;        // המספר עולה אחוז-אחוז, לא קופץ
const TILE_STEP = 500;      // אריח עולה מאחור כל חצי שנייה, כשטופס מוכן
const PREPARE_SHARE = 20;   // אחוזים להכנת העמודים; השאר לקריאה
const calm = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
let ringShown = 0, ringTarget = 0, ringTimer = null;
let revealQueue = [], revealTimer = null;
let startedAt = 0, clockTimer = null;

function resetRing(){
  clearTimeout(ringTimer);
  ringTimer = null;
  clearInterval(revealTimer);
  revealTimer = null;
  ringShown = ringTarget = 0;
  revealQueue = [];
  stopClock();
  $('#pTime').textContent = '';
  $('#ring').className = 'ring pct';
  $('#ringArc').style.strokeDashoffset = '';
  $('#ringArc').style.opacity = '0';
  $('#ringNum').textContent = '0';
  $('#ringOf').textContent = 'הושלמו';
}

// כמה מכל העבודה כבר נעשתה, באחוזים (0–99 בזמן העבודה)
function progressOf(d){
  const total = d.total_pages || 0;
  const expected = total || d.expected_pages || d.found_pages || 0;
  if (!expected) return 0;
  const prepared = total ? 1 : Math.min(1, (d.found_pages || 0) / expected);
  const reading = (d.pages || []).filter(function(p){ return p.status === 'reading'; }).length;
  const read = total ? Math.min(1, ((d.processed_pages || 0) + reading / 2) / total) : 0;
  return Math.min(99, Math.floor(PREPARE_SHARE * prepared + (100 - PREPARE_SHARE) * read));
}

function drawRing(){
  $('#ringArc').style.strokeDashoffset = String(RING_C * (1 - Math.min(1, ringShown / 100)));
  $('#ringArc').style.opacity = ringShown ? '' : '0';   // בלי נקודה תקועה למעלה כשעוד אין התקדמות
  $('#ringNum').textContent = ringShown;
}

function countTo(pct){
  ringTarget = Math.max(ringTarget, pct);  // האחוז אף פעם לא יורד
  if (calm) { ringShown = ringTarget; drawRing(); return; }
  if (ringTimer) return;
  (function step(){
    if (ringShown >= ringTarget) { drawRing(); ringTimer = null; return; }
    ringShown += 1;
    drawRing();
    ringTimer = setTimeout(step, PCT_STEP);
  })();
}

// האריחים מאחור עולים בקצב שלהם — אחד כל חצי שנייה — ולא קשורים למספר
function startReveal(){
  if (revealTimer) return;
  revealTimer = setInterval(function(){
    if (revealQueue.length) revealTile(revealQueue.shift());
  }, calm ? 60 : TILE_STEP);
}

// בסוף: האחוז מגיע ל-100 וכל האריחים עלו — ורק אז ✓
function whenCounted(me, cb){
  (function wait(){
    if (me !== batch) return;
    if (ringTimer || ringShown < 100 || revealQueue.length) return setTimeout(wait, 120);
    clearInterval(revealTimer);
    revealTimer = null;
    cb();
  })();
}

// בלי שעון מתחת לגלגל (החלטת נועה, 20/10/2026) — רק האחוזים. נשאר רק מועד ההתחלה, למקרה שיידרש.
function startClock(){
  stopClock();
  startedAt = Date.now();
  $('#pTime').textContent = '';
}
function stopClock(){ clearInterval(clockTimer); clockTimer = null; }

async function tick(){
  const me = batch;
  const res = await fetch('/api/batches/' + me);
  if (me !== batch || finishing) return;
  if (!res.ok) { clearInterval(timer); return fail('האצווה לא נמצאה'); }
  const d = await res.json();
  if (me !== batch || finishing) return;
  const total = d.total_pages || 0;
  const finished = ['completed', 'partial', 'failed'].indexOf(d.status) !== -1;

  updateTiles(d);
  startReveal();
  $('#pTitle').textContent = 'קולט את הטפסים…';
  $('#pCount').textContent = '';
  countTo(progressOf(d));
  $('#pFiles').innerHTML = (d.sources || []).map(function(s){
    const detail = s.error ? ' — <span class="bad">' + esc(s.error) + '</span>' : '';
    return '<li><bdi>' + esc(s.filename) + '</bdi>' + detail + '</li>';
  }).join('');
  if (!finished) return;

  // הכול נקרא: האחוז מגיע ל-100, ✓, ואז עוברים לאריחים. עד עכשיו אין מה ללחוץ.
  clearInterval(timer);
  finishing = true;
  if (d.status === 'failed') { finishing = false; return fail(d.error || 'העיבוד נכשל'); }
  const n = d.records.length;
  countTo(100);
  whenCounted(me, function(){ finish(me, d, n, total); });
}

function finish(me, d, n, total){
  stopClock();
  $('#pTime').textContent = '';
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
    remember();
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
    const img = tile.querySelector('img');
    if (cls.indexOf('shown') !== 0 || tile.dataset.revealed) { tile.className = 'tile ' + cls; return; }
    // טופס שהסתיים לא קופץ מיד: נכנס לתור, והספירה מעלה אותו בתורו (בינתיים התמונה כבר נטענת)
    if (!img.getAttribute('src')) img.src = img.dataset.src;
    if (revealQueue.indexOf(tile) === -1) revealQueue.push(tile);
  });
}

function revealTile(tile){
  tile.dataset.revealed = '1';
  tile.className = 'tile ' + tile.dataset.cls;
}

/* ------------------------------------------------------------ תצוגה כללית */
function visibleRecords(){
  if (!last) return [];
  return last.records.filter(function(r){
    if (view === 'form' && r.record_id === pinnedId) return true;
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
  // תשובה מאוחרת של העלאה שכבר יצאנו ממנה (למשל שמירת שדה שהגיעה אחרי "קליטה חדשה") — לא מציירים אותה מחדש
  if (d && d.batch_id && d.batch_id !== batch) return;
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
  $('#cReviewChip').classList.toggle('hidden', d.review_total === 0);
  $('#nAll').textContent = d.records.length;
  $('#nReview').textContent = d.records.filter(function(r){ return r.review_count > 0; }).length;
  $('#nApproved').textContent = d.records.filter(isApproved).length;
  const reviewForms = d.records.filter(function(r){ return r.review_count > 0; }).length;
  $('#navReviewN').textContent = reviewForms || '';
  $('#navReview').disabled = !d.records.length;
  // ייצוא רק אחרי שכל שדה כתום טופל **וכל טופס אושר** ב"אישור הטופס" (השרת אוכף את אותו כלל)
  const open = unverifiedCount(d);
  const ready = !processing && d.records.length > 0 && d.review_total === 0 && open === 0;
  const why = !d.records.length ? 'אין טפסים לייצוא' : processing ? 'הייצוא ייפתח בסוף הקריאה'
    : d.review_total ? 'נשארו ' + d.review_total + ' שדות לבדיקה לפני ייצוא'
    : open ? (open === 1 ? 'נשאר טופס אחד לאישור לפני ייצוא' : 'נשארו ' + open + ' טפסים לאישור לפני ייצוא') : '';
  ['#download', '#navExport', '#fvExport'].forEach(function(sel){
    $(sel).disabled = !ready;
    $(sel).title = why || 'הורדת כל הטפסים לקובץ Excel';
  });
  $('#exportHint').textContent = why;
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
  // בטופס פתוח מסתירים את הסינון ואת ראש התוצאות — רק התמונה והשדות
  $('#results').classList.toggle('formmode', view === 'form');
  closePop();
  if (view === 'table') renderTable(); else if (view === 'tiles') renderTiles(); else renderForm();
  renderCrumbs();
}

function setView(v){ view = v; save('view', view); lastSignature = ''; if (last) render(last, true); remember(); }
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
      pinnedId = null;
      if (last) render(last, true);
    }, 180);
  });
});
$('#q').addEventListener('keydown', function(e){
  if (e.key === 'Enter' && last) $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

// תפריט הגלולות בכותרת המשנה
$('#navUpload').onclick = function(){
  if (last && last.records && last.records.length) return startNewIntake();
  window.scrollTo({ top: 0, behavior: 'smooth' });
};
$('#navReview').onclick = function(){
  const b = document.querySelector('.filters button[data-filter="review"]');
  if (b) b.click();
  $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' });
};
$('#navExport').onclick = function(){ openExportPreview($('#navExport')); };

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
    pinnedId = null;
    if (last) render(last, true);
  };
});
$('#senderFilter').onchange = function(e){ senderFilter = e.target.value; formIndex = 0; pinnedId = null; if (last) render(last, true); };

/* ------------------------------------------------------------ מצב טבלה */
let focusedCell = null;

function renderTable(){
  const rows = visibleRecords();
  const span = FIELDS.length + 2;
  $('#emptyFilter').classList.toggle('hidden', rows.length > 0);
  $('#body').innerHTML = rows.map(function(r){
    const notes = [];
    if (r.low_resolution) notes.push('רזולוציה נמוכה — כתב יד עלול לא להיקרא. מומלץ להעלות את הסריקה המקורית.');
    if (r.from_store) notes.push(storedText(r));
    if (r.straightened) notes.push(r.straightened);
    if (r.error) notes.push('הקריאה נכשלה: ' + r.error);
    else if (r.note) notes.push('הערה: ' + r.note);
    const noteRow = notes.length ? '<tr class="noterow"><td colspan="' + span + '">' + esc(notes.join(' · ')) + '</td></tr>' : '';
    const open = r.review_count;
    const acts = r.verified
      ? '<span class="verified" title="הטופס אושר">✓</span>'
      : '<button class="approveRow" data-rec="' + r.record_id + '"' + (open ? ' disabled' : '') +
        ' aria-label="אישור הטופס" title="' + (open ? 'נשארו ' + open + ' שדות לבדיקה' : 'אישור כל הטופס') + '">✓</button>';
    const num = last.records.indexOf(r) + 1;
    return '<tr data-rec="' + r.record_id + '"><td class="formcol" tabindex="0" data-rec="' + r.record_id +
      '" aria-label="טופס ' + num + ' — מעבר מציג את הטופס, Enter פותח אותו"><div class="fc"><img loading="lazy" alt="" src="' + pageUrl(r) + '/thumb">' +
      '<span class="t"><b>טופס ' + num + '</b>' + statusBadge(r) +
      (r.template ? '<small class="kind" title="' + esc(r.template) + '">' + esc(r.template) + '</small>' : '') + '</span></div></td>' +
      FIELDS.map(function(f){ return cell(r, f); }).join('') +
      '<td class="acts">' + acts + '</td></tr>' + noteRow;
  }).join('');
  if (focusedCell) {
    const td = document.querySelector('td.cell[data-rec="' + focusedCell.rec + '"][data-key="' + focusedCell.key + '"]');
    if (td) td.focus({ preventScroll: true });
  }
}

// תא בטבלה: ריבוע מעוגל אחד עם הערך. הסיבה בתקריב הצף (ובכותרת), לא כטקסט בתוך הטבלה.
function cell(r, spec){
  const f = r.fields[spec.key];
  const cls = f.state === 'needs_review' ? 'review' : f.state === 'approved' ? 'approved' : '';
  const why = f.state === 'needs_review' && f.reasons.length ? f.reasons.join(' · ') : '';
  const mini = '<span class="mini">' +
    (f.state !== 'approved' && !hasUnknown(f) ? '<button class="ok" data-act="approve" tabindex="-1" title="' +
      (f.values.length ? 'אשר' : 'אשר כריק') + '" aria-label="אשר">✓</button>' : '') +
    '<button data-act="edit" tabindex="-1" title="תקן" aria-label="תקן">✎</button></span>';
  return '<td class="cell ' + cls + '" tabindex="0" data-key="' + spec.key + '" data-rec="' + r.record_id +
    '" data-raw="' + esc(f.values.join(' | ')) + '"' + (why ? ' title="' + esc(why) + '"' : '') +
    ' aria-label="' + esc(spec.label) + ': ' + esc(f.values.join(' | ') || 'ריק') +
    (cls === 'review' ? ', לבדיקה' + (why ? ': ' + esc(why) : '') : cls === 'approved' ? ', אושר' : '') + '">' +
    '<span class="chip">' + '<span class="val">' + valueHtml(f) + '</span>' + mini + '</span></td>';
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
  const td = e.target.closest('td.cell, td.formcol');
  if (td) schedulePop(td, 280);
});
tbody.addEventListener('mouseout', function(e){
  const td = e.target.closest('td.cell, td.formcol');
  if (td && !td.contains(e.relatedTarget)) scheduleClose();
});
tbody.addEventListener('focusin', function(e){
  const td = e.target.closest('td.formcol');
  if (!td) return;
  let keyboard = false;
  try { keyboard = td.matches(':focus-visible'); } catch (err) {}
  if (keyboard) schedulePop(td, 0);
});
tbody.addEventListener('keydown', function(e){
  const td = e.target.closest && e.target.closest('td.formcol');
  if (td && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); closePop(); openRecord(td.dataset.rec); }
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
      '<div class="thumb"><img loading="lazy" alt="" src="' + pageUrl(r) + '/thumb">' + statusBadge(r) +
      (r.from_store ? '<span class="storedtag" title="' + esc(storedText(r)) + '">🗄 מהמערכת</span>' : '') + '</div>' +
      '<div class="body"><h4>' + esc(sender || 'גורם שולח לא זוהה') + '</h4>' +
      (r.template ? '<span class="kind" title="' + esc(r.template) + '">' + esc(r.template) + '</span>' : '') +
      (r.error ? '<span class="tfail">הקריאה נכשלה — פתחו את הטופס כדי לקרוא שוב</span>'
               : '<span class="who">' + esc(name || '—') + '</span>') +
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
  if (b.dataset.act === 'zoom') { const r = recordById(td.dataset.rec); closePop(); if (r) openPageZoom(td, r, FIELDS[0].key); }
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
  if (td.classList.contains('formcol')) return openPagePop(td, r);
  pop.classList.remove('pagepop');
  const key = td.dataset.key, f = r.fields[key];
  const state = f.state === 'needs_review' ? '⚠ לבדיקה' : f.state === 'approved' ? '✓ אושר' : '';
  pop.innerHTML =
    '<div class="lbl"><span>' + esc(isName(key) ? 'שם פרטי ושם משפחה' : fieldLabel(key)) + '</span><span>' + state + '</span></div>' +
    (isName(key) ? namePopCrops(r) :
     perValue(f) ? f.crop_boxes.map(function(b, i){
        return b ? '<div class="crop multi"><span class="cv"><bdi>' + esc(f.values[i]) + '</bdi></span><img alt="תקריב של ' +
          esc(f.values[i]) + '" src="' + cropUrl(r, key, i) + '"></div>' : '';
      }).join('')
     : f.crop_box ? '<div class="crop"><img alt="תקריב של ' + esc(fieldLabel(key)) + '" src="' + cropUrl(r, key) + '"></div>'
                : '<div class="crop"><div class="nocrop">אין מיקום לשדה הזה — אפשר לפתוח את הטופס המלא</div></div>') +
    (isName(key) ? NAME_KEYS.map(function(k){
        return '<div class="val nm' + (k === key ? ' now' : '') + '"><span>' + esc(fieldLabel(k)) + ':</span> ' + valueHtml(r.fields[k]) + '</div>';
      }).join('') : '<div class="val">' + valueHtml(f) + '</div>') +
    (f.state === 'needs_review' && f.reasons.length ? '<div class="why">' + esc(f.reasons.join(' · ')) + '</div>' : '') +
    '<div class="btns"><button class="secondary" data-act="open">פתיחת הטופס</button>' +
    (f.state !== 'approved' && !hasUnknown(f) ? '<button class="primary" data-act="approve">✓ ' + (f.values.length ? 'אשר' : 'אשר כריק') + '</button>' : '') +
    '<button data-act="edit">✎ תקן</button></div>';
  pop.classList.remove('hidden');
  popFor = td;
  placePop(td);
  const img = pop.querySelector('img');
  if (img) img.addEventListener('load', function(){ if (popFor === td) placePop(td); }, { once: true });
}

// בטבלה, על תא שם פרטי או שם משפחה: תקריב אחד של שני השמות יחד (כמו במצב טופס)
function namePopCrops(r){
  const img = function(k, label){
    return '<div class="crop"><img alt="תקריב של ' + esc(label) + '" src="' + cropUrl(r, k) + '"></div>';
  };
  if (r.full_name_crop) return img('full_name', 'שם פרטי ושם משפחה');
  const a = r.fields.first_name.crop_box, b = r.fields.last_name.crop_box;
  if (a && b && a.join() === b.join()) return img('first_name', 'שם פרטי ושם משפחה');
  const parts = NAME_KEYS.filter(function(k){ return r.fields[k].crop_box; }).map(function(k){
    return '<div class="crop multi"><span class="cv">' + esc(fieldLabel(k)) + '</span><img alt="תקריב של ' +
      esc(fieldLabel(k)) + '" src="' + cropUrl(r, k) + '"></div>';
  });
  return parts.length ? parts.join('')
    : '<div class="crop"><div class="nocrop">אין מיקום לשם — אפשר לפתוח את הטופס המלא</div></div>';
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
// "טופס N" בטבלה: הטופס עצמו צף בצד התא — גדול מהתקריב, עם הגדלה ופתיחה
function openPagePop(td, r){
  const num = last.records.indexOf(r) + 1;
  pop.classList.add('pagepop');
  pop.classList.remove('below');
  pop.innerHTML =
    '<div class="phead"><strong>טופס ' + num + '</strong>' + statusBadge(r) + '</div>' +
    '<div class="pmeta" title="' + esc(r.source_name) + '">עמוד ' + r.page_number + ' · <bdi>' + esc(r.source_name) + '</bdi></div>' +
    '<div class="ppage" data-act-zoom="1" title="לחצו להגדלה"><img alt="טופס ' + num + ' — עמוד ' + r.page_number +
      ' של ' + esc(r.source_name) + '" src="' + pageUrl(r) + '"></div>' +
    '<div class="btns"><button class="secondary" data-act="zoom">🔍 הגדלה</button>' +
    '<button class="primary" data-act="open">פתיחת הטופס</button></div>';
  pop.classList.remove('hidden');
  popFor = td;
  placePageBeside(td);
  const img = pop.querySelector('img');
  img.addEventListener('load', function(){ if (popFor === td) placePageBeside(td); }, { once: true });
  pop.querySelector('.ppage').onclick = function(){ closePop(); openPageZoom(td, r, FIELDS[0].key); };
}

// משמאל לתא (העמודה הימנית בטבלה), בגובה התא ובתוך גבולות המסך; החץ מצביע על התא
function placePageBeside(td){
  const cellBox = td.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  let left = cellBox.left - w - 12;
  if (left < 10) left = Math.min(cellBox.right + 12, window.innerWidth - w - 10);
  const middle = cellBox.top + cellBox.height / 2;
  const top = Math.max(10, Math.min(middle - 40, window.innerHeight - h - 10));
  pop.style.left = (left + window.scrollX) + 'px';
  pop.style.top = (top + window.scrollY) + 'px';
  pop.style.setProperty('--arrow-y', Math.max(18, Math.min(h - 18, middle - top)) + 'px');
}
window.addEventListener('scroll', closePop, { passive: true });
window.addEventListener('resize', closePop);

/* ------------------------------------------------------------ חלונית המקור (טבלה) וצופה (טופס) */
function turnButtons(withInk){
  return '<button class="ink-toggle" data-turn="1" title="סיבוב התצוגה" aria-label="סיבוב התצוגה">↻</button>' +
    (withInk ? '<button class="ink-toggle wide' + (inkView ? ' on' : '') + '" data-ink="1" title="הגדלה עם כתב היד בשחור">דיו</button>' : '') +
    '<button class="ink-toggle wide" data-zoom="1" title="הגדלת הטופס על כל המסך">🔍 הגדלה</button>';
}
function boxHtml(f, pair){
  const one = function(b, extra){
    return '<div class="pv-box' + (extra ? ' pair' : '') + '" style="left:' + b[0] * 100 + '%;top:' + b[1] * 100 + '%;width:' +
      (b[2] - b[0]) * 100 + '%;height:' + (b[3] - b[1]) * 100 + '%"></div>';
  };
  const many = perValue(f);
  if (many) return many.map(function(b, i){ return b ? one(b, pair || i > 0) : ''; }).join('');
  const b = f && f.crop_box;
  return b ? one(b, pair) : '';
}

// שם פרטי ושם משפחה מוצגים תמיד יחד: המנוע מתבלבל ביניהם לפעמים, וכך רואים את שניהם
const NAME_KEYS = ['first_name', 'last_name'];
function isName(key){ return NAME_KEYS.indexOf(key) !== -1; }
function boxesHtml(r, key){
  if (!isName(key)) return boxHtml(r.fields[key]);
  const other = key === 'first_name' ? 'last_name' : 'first_name';
  const a = r.fields[key].crop_box, b = r.fields[other].crop_box;
  if (a && b && a.join() === b.join()) return boxHtml(r.fields[key]);
  return boxHtml(r.fields[key]) + boxHtml(r.fields[other], !!a);
}
// התיבה שאליה "לשדה המסומן" מגדיל: השדה, או שני השמות יחד כשהם באותו אזור
function focusBoxOf(r, key){
  const f = r.fields[key];
  if (!isName(key) || !r.full_name_crop) return f && f.crop_box;
  const a = r.fields.first_name.crop_box, b = r.fields.last_name.crop_box;
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
}
function pageHtml(r, key, withInk){
  return '<div class="pv-sec"><div class="pv-lbl">' + turnButtons(withInk) + '</div>' +
    '<div class="pv-pagewrap"><div class="pv-page" role="button" tabindex="0" data-zoom="1" title="לחצו להגדלה"' +
    ' aria-label="הגדלת הטופס על כל המסך"><img src="' + pageUrl(r) + '" alt="עמוד ' + r.page_number +
    ' של ' + esc(r.source_name) + '">' + boxesHtml(r, key) + '<span class="pv-zoomtag" aria-hidden="true">🔍 לחצו להגדלה</span>' +
    '</div></div></div>';
}

document.addEventListener('click', function(e){
  const b = e.target.closest && e.target.closest('[data-turn],[data-ink],[data-zoom]');
  if (!b) return;
  e.stopPropagation();
  const current = view === 'form' ? currentRecord() : null;
  if (!current) return;
  const rec = current.record_id;
  if (b.dataset.zoom) {
    openPageZoom(b);
  } else if (b.dataset.turn) {
    viewTurn[rec] = ((viewTurn[rec] || 0) + 90) % 360;
    applyTurn(b.closest('.frame'), rec);
    renderZoom();
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

/* ------------------------------------------------------------ הגדלת הטופס על כל המסך */
// העמוד נשמר ב-300 DPI, כך שאפשר להגדיל הרבה ועדיין לקרוא. הכול במסך — בלי שרת חדש ובלי מודל.
// pz: w,h = גודל התמונה; W,H = גודלה אחרי סיבוב; z = הגדלה (פיקסל תמונה ← פיקסל מסך); tx,ty = הזזה.
const pz = { open: false, w: 0, h: 0, W: 0, H: 0, deg: 0, z: 1, fit: 1, tx: 0, ty: 0, opener: null, box: null };
const PZ_MAX = 8;   // עד פי 8 מ"מתאים למסך"

function openPageZoom(opener, rec, fieldKey){
  const r = rec || currentRecord();
  if (!r) return;
  const key = fieldKey || FIELDS[cardIndex].key;
  const list = visibleRecords();
  pz.rec = r;
  pz.opener = opener;
  pz.box = focusBoxOf(r, key);
  pz.deg = viewTurn[r.record_id] || 0;
  $('#pzTitle').textContent = 'טופס ' + (list.indexOf(r) + 1) + ' / ' + list.length + ' · ' +
    (isName(key) ? 'שם פרטי ושם משפחה' : fieldLabel(key));
  $('#pzBox').classList.toggle('hidden', !pz.box);
  $('#pzRot').innerHTML = '<img alt="עמוד ' + r.page_number + ' של ' + esc(r.source_name) + ', מוגדל" src="' + pageUrl(r) + '">' + boxesHtml(r, key);
  closePop();
  $('#pgZoom').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  pz.open = true;
  $('#pzView').focus({ preventScroll: true });
  const img = $('#pzRot img');
  const ready = function(){
    pz.w = img.naturalWidth || 1;
    pz.h = img.naturalHeight || 1;
    pzLayout();
    pzFit(false);
  };
  if (img.complete && img.naturalWidth) ready(); else img.addEventListener('load', ready, { once: true });
}

function closePageZoom(){
  if (!pz.open) return;
  pz.open = false;
  $('#pgZoom').classList.add('hidden');
  document.body.style.overflow = '';
  $('#pzRot').innerHTML = '';
  const back = document.body.contains(pz.opener) ? pz.opener : $('#fvFrame .pv-page');
  if (back) back.focus({ preventScroll: true });
}

// מסגרת הסיבוב: התמונה מסתובבת סביב מרכזה בתוך קופסה בגודל אחרי הסיבוב
function pzLayout(){
  const odd = pz.deg % 180 !== 0;
  pz.W = odd ? pz.h : pz.w;
  pz.H = odd ? pz.w : pz.h;
  const pan = $('#pzPan'), rot = $('#pzRot');
  pan.style.width = pz.W + 'px';
  pan.style.height = pz.H + 'px';
  rot.style.width = pz.w + 'px';
  rot.style.height = pz.h + 'px';
  rot.style.left = (pz.W - pz.w) / 2 + 'px';
  rot.style.top = (pz.H - pz.h) / 2 + 'px';
  rot.style.transform = pz.deg ? 'rotate(' + pz.deg + 'deg)' : '';
}

function pzSize(){ const v = $('#pzView'); return { w: v.clientWidth, h: v.clientHeight }; }

// לא נותנים לעמוד לברוח מהמסך: קטן מהמסך ← באמצע; גדול ← הקצוות לא נכנסים פנימה
function pzClamp(){
  const v = pzSize(), cw = pz.W * pz.z, ch = pz.H * pz.z;
  pz.tx = cw <= v.w ? (v.w - cw) / 2 : Math.min(0, Math.max(v.w - cw, pz.tx));
  pz.ty = ch <= v.h ? (v.h - ch) / 2 : Math.min(0, Math.max(v.h - ch, pz.ty));
}

function pzApply(animate){
  pzClamp();
  const pan = $('#pzPan');
  pan.classList.toggle('anim', !!animate && !calm);
  pan.style.transform = 'translate(' + pz.tx + 'px,' + pz.ty + 'px) scale(' + pz.z + ')';
  pan.style.setProperty('--z', pz.z);
  const pct = Math.round(pz.z / pz.fit * 100);
  $('#pzPct').textContent = pct + '%';
  $('#pzOut').disabled = pz.z <= pz.fit * 1.001;
  $('#pzIn').disabled = pz.z >= pz.fit * PZ_MAX * 0.999;
}

function pzFit(animate){
  const v = pzSize();
  pz.fit = Math.min(v.w / pz.W, v.h / pz.H) * 0.96;
  pz.z = pz.fit;
  pzApply(animate);
}

function pzZoomAt(factor, x, y, animate){
  const v = pzSize();
  if (x == null) { x = v.w / 2; y = v.h / 2; }
  const nz = Math.max(pz.fit, Math.min(pz.fit * PZ_MAX, pz.z * factor));
  const px = (x - pz.tx) / pz.z, py = (y - pz.ty) / pz.z;
  pz.tx = x - px * nz;
  pz.ty = y - py * nz;
  pz.z = nz;
  pzApply(animate);
}

// "לשדה המסומן": מגדילים כך שהמלבן הכתום ממלא בערך את רוב המסך, ובמרכזו
function pzFocusBox(){
  const b = pz.box;
  if (!b) return;
  const rad = pz.deg * Math.PI / 180, cos = Math.cos(rad), sin = Math.sin(rad);
  const x = ((b[0] + b[2]) / 2 - 0.5) * pz.w, y = ((b[1] + b[3]) / 2 - 0.5) * pz.h;
  const cx = pz.W / 2 + x * cos - y * sin, cy = pz.H / 2 + x * sin + y * cos;
  const odd = pz.deg % 180 !== 0;
  const bw = (odd ? b[3] - b[1] : b[2] - b[0]) * (odd ? pz.h : pz.w);
  const bh = (odd ? b[2] - b[0] : b[3] - b[1]) * (odd ? pz.w : pz.h);
  const v = pzSize();
  pz.z = Math.max(pz.fit, Math.min(pz.fit * 5, v.w * 0.45 / Math.max(bw, 1), v.h * 0.35 / Math.max(bh, 1)));
  pz.tx = v.w / 2 - cx * pz.z;
  pz.ty = v.h / 2 - cy * pz.z;
  pzApply(true);
}

$('#pzIn').onclick = function(){ pzZoomAt(1.25, null, null, true); };
$('#pzOut').onclick = function(){ pzZoomAt(1 / 1.25, null, null, true); };
$('#pzFit').onclick = function(){ pzFit(true); };
$('#pzBox').onclick = pzFocusBox;
$('#pzClose').onclick = closePageZoom;
$('#pzTurn').onclick = function(){
  const r = pz.rec;
  if (!r) return;
  pz.deg = (pz.deg + 90) % 360;
  viewTurn[r.record_id] = pz.deg;          // גם הטופס הקטן והתקריב מסתובבים יחד
  if (view === 'form') { applyTurn($('#fvFrame'), r.record_id); renderZoom(); }

  pzLayout();
  pzFit(false);
};

(function(){
  const viewEl = $('#pzView');
  const pointers = {};
  let prev = null, pinch = 0;
  const local = function(e){ const r = viewEl.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  viewEl.addEventListener('wheel', function(e){
    e.preventDefault();
    const p = local(e);
    pzZoomAt(Math.exp(-e.deltaY * 0.0015), p.x, p.y, false);
  }, { passive: false });
  viewEl.addEventListener('dblclick', function(e){ const p = local(e); pzZoomAt(2, p.x, p.y, true); });
  viewEl.addEventListener('pointerdown', function(e){
    if (e.button !== 0) return;
    viewEl.setPointerCapture(e.pointerId);
    pointers[e.pointerId] = local(e);
    prev = local(e);
    pinch = 0;
    viewEl.classList.add('drag');
  });
  viewEl.addEventListener('pointermove', function(e){
    if (!pointers[e.pointerId]) return;
    pointers[e.pointerId] = local(e);
    const ids = Object.keys(pointers);
    if (ids.length >= 2) {                     // צביטה בשתי אצבעות
      const a = pointers[ids[0]], b = pointers[ids[1]];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch) pzZoomAt(d / pinch, (a.x + b.x) / 2, (a.y + b.y) / 2, false);
      pinch = d;
      return;
    }
    const p = pointers[e.pointerId];
    pz.tx += p.x - prev.x;
    pz.ty += p.y - prev.y;
    prev = p;
    pzApply(false);
  });
  const up = function(e){
    delete pointers[e.pointerId];
    const ids = Object.keys(pointers);
    pinch = 0;
    if (ids.length) prev = pointers[ids[0]];
    else viewEl.classList.remove('drag');
  };
  viewEl.addEventListener('pointerup', up);
  viewEl.addEventListener('pointercancel', up);
  window.addEventListener('resize', function(){
    if (!pz.open || !pz.w) return;
    const ratio = pz.z / pz.fit;
    const v = pzSize();
    pz.fit = Math.min(v.w / pz.W, v.h / pz.H) * 0.96;
    pz.z = pz.fit * ratio;
    pzApply(false);
  });
  // Enter / רווח על הטופס הקטן פותחים את ההגדלה (ולא מאשרים שדה)
  $('#fvFrame').addEventListener('keydown', function(e){
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('pv-page')) {
      e.preventDefault();
      e.stopPropagation();
      openPageZoom(e.target);
    }
  });
})();

// בתוך ההגדלה: + / − / 0, חיצים מזיזים, Esc סוגר, Tab נשאר בחלון
function pageZoomKeys(e){
  const step = 90;
  if (e.key === 'Escape') { e.preventDefault(); return closePageZoom(); }
  if (e.key === '+' || e.key === '=') { e.preventDefault(); return pzZoomAt(1.25, null, null, true); }
  if (e.key === '-' || e.key === '_') { e.preventDefault(); return pzZoomAt(1 / 1.25, null, null, true); }
  if (e.key === '0') { e.preventDefault(); return pzFit(true); }
  const moves = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
  if (moves[e.key] && !(e.target.closest && e.target.closest('button'))) {
    e.preventDefault();
    pz.tx += moves[e.key][0];
    pz.ty += moves[e.key][1];
    return pzApply(true);
  }
  if (e.key !== 'Tab') return;
  const items = $$('#pgZoom button:not(:disabled):not(.hidden), #pzView');
  const first = items[0], lastItem = items[items.length - 1];
  if (!$('#pgZoom').contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); lastItem.focus(); }
  else if (!e.shiftKey && document.activeElement === lastItem) { e.preventDefault(); first.focus(); }
}

/* ------------------------------------------------------------ מצב טופס אחד בכל פעם */
function currentRecord(){
  const list = visibleRecords();
  if (!list.length) return null;
  formIndex = Math.max(0, Math.min(formIndex, list.length - 1));
  if (view === 'form') pinnedId = list[formIndex].record_id;
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
    $('#formWhere').textContent = 'אין טפסים';
    $('#formDots').innerHTML = '';
    $('#fvFrame').innerHTML = '<div class="ph">—</div>';
    $('#fvCards').innerHTML = '';
    $('#fvApprove').disabled = true;
    $('#formBadge').innerHTML = '';
    $('#fvFailed').classList.add('hidden');
    $('#fvJoined').classList.add('hidden');
    $('#fvStored').classList.add('hidden');
    $('#fvSplit').classList.add('hidden');
    zoomOpen = false;
    renderZoom();
    return;
  }
  cardIndex = Math.max(0, Math.min(cardIndex, FIELDS.length - 1));
  $('#formWhere').textContent = 'טופס ' + (formIndex + 1) + ' / ' + list.length;
  $('#formBadge').innerHTML = statusBadge(r);
  // סוג הטופס, כפי שהקורא זיהה אותו (כללית / לאומית / בית חולים…) — מידע, בלי עלות
  $('#formType').textContent = r.template ? 'סוג טופס: ' + r.template : '';
  $('#formType').title = r.template || '';
  $('#formWhere').title = r.source_name + ' · עמוד ' + r.page_number + (r.error ? ' · הקריאה נכשלה: ' + r.error : '');
  $('#prevForm').disabled = formIndex === 0;
  $('#nextForm').disabled = formIndex >= list.length - 1;
  $('#formDots').innerHTML = list.length <= 60 ? list.map(function(x, i){
    const cls = isApproved(x) ? 'done' : x.review_count ? 'review' : '';
    return '<i class="' + cls + (i === formIndex ? ' now' : '') + '"></i>';
  }).join('') : '';

  const key = FIELDS[cardIndex].key;
  $('#fvFrame').innerHTML = pageHtml(r, key, true);
  applyTurn($('#fvFrame'), r.record_id);

  // ריבוע קטן לכל שדה, כולם בשורה אחת: שם השדה וערך. ✓ ו-✎ מופיעים במעבר עכבר / בשדה הנבחר.
  $('#fvCards').innerHTML = FIELDS.map(function(spec, i){
    const f = r.fields[spec.key];
    const cls = f.state === 'needs_review' ? 'review' : f.state === 'approved' ? 'approved' : '';
    const why = f.state === 'needs_review' && f.reasons.length ? f.reasons.join(' · ') : '';
    return '<div class="fcard ' + cls + (i === cardIndex ? ' now' : '') + '" role="listitem" tabindex="0" data-i="' + i +
      '" data-key="' + spec.key + '"' + (why ? ' title="' + esc(why) + '"' : '') +
      ' aria-label="' + esc(spec.label) + ': ' + esc(f.values.join(' | ') || 'ריק') +
      (cls === 'review' ? ', לבדיקה' + (why ? ': ' + esc(why) : '') : cls === 'approved' ? ', אושר' : '') + '">' +
      '<span class="lbl">' + esc(spec.label) + '</span>' +
      '<span class="val">' + valueHtml(f) + '</span>' +
      '<span class="btns">' +
        (f.state !== 'approved' && !hasUnknown(f) ? '<button class="ok" data-act="approve" title="' + (f.values.length ? 'אשר' : 'אשר כריק') +
          '" aria-label="אשר">✓</button>' : '') +
        '<button data-act="edit" title="תקן" aria-label="תקן">✎</button></span>' +
      '</div>';
  }).join('');

  renderFailbar(r);
  renderJoinbar(r);
  renderStoredbar(r);
  renderSplitbtn(r);
  $('#fvApprove').disabled = !!r.review_count || r.verified;
  $('#fvApprove').textContent = r.verified ? '✓ הטופס אושר' : '✓ אישור הטופס';
  $('#fvApprove').title = r.review_count ? 'נשארו ' + r.review_count + ' שדות לבדיקה' : '';
  renderZoom();
}

/* ------------------------------------------------------------ טופס שנכשל: "קרא שוב" רק אחרי אישור */
// קריאה חוזרת עולה כמו קריאת טופס אחד — לכן אף פעם לא אוטומטית: חלון שואל, ורק "כן" שולח.
let rrFor = null, rrTimer = null, rrOpener = null;

function rereading(r){
  return !!(last && (last.pages || []).some(function(p){ return p.record_id === r.record_id && p.status === 'reading'; }));
}

function renderFailbar(r){
  const bar = $('#fvFailed');
  if (!r.error) { bar.classList.add('hidden'); bar.innerHTML = ''; return; }
  bar.classList.remove('hidden');
  bar.innerHTML = rereading(r)
    ? '<span class="spin" aria-hidden="true"></span><span class="why"><b>קורא את הטופס שוב…</b> זה יכול לקחת כמה דקות. אפשר להמשיך לעבוד על טפסים אחרים.</span>'
    : '<span class="why"><b>✗ הקריאה של הטופס הזה נכשלה.</b> ' + esc(r.error) + '</span>' +
      '<button class="secondary" id="rrOpen">↻ קרא שוב</button>';
  const open = $('#rrOpen');
  if (open) open.onclick = function(){ askReread(r.record_id, open); };
}

/* שני טפסים בעמוד אחד: המזכירה מחליטה לפצל, בכפתור בסרגל הטופס (החלטת נועה, 20/10/2026).
   בחינם — בלי קריאה נוספת: כל ערך עובר לחלק שבו הוא נמצא בעמוד. בחלק שפוצל — "בטל פיצול". */
function renderSplitbtn(r){
  const btn = $('#fvSplit');
  const undo = r.split_from_page && !r.verified;
  const can = !r.split_from_page && !r.verified && !r.error && !r.joined;
  btn.classList.toggle('hidden', !undo && !can);
  btn.disabled = false;
  btn.textContent = undo ? '↺ בטל פיצול' : '✂ פיצול לשני טפסים';
  btn.title = undo ? 'להחזיר את העמוד להיות טופס אחד — בחינם' : 'העמוד מכיל שני טפסים? לפצל — בחינם, בלי קריאה נוספת';
  btn.onclick = undo
    ? function(){ joinAction('/api/records/' + r.record_id + '/unsplit-page', null, btn, '✓ העמוד חזר להיות טופס אחד'); }
    : function(){ openSplitPick(r, btn); };
}

let spFor = null, spOpener = null;
function spAxis(){
  const on = $('.spitem[aria-checked="true"]');
  return on ? on.dataset.axis : 'y';
}
function spChoose(item){
  $$('.spitem').forEach(function(b){ b.setAttribute('aria-checked', b === item ? 'true' : 'false'); });
}
function openSplitPick(r, opener){
  spFor = r; spOpener = opener;
  spChoose($('.spitem[data-axis="y"]'));
  $('#spGo').disabled = false;
  $('#splitPick').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  $('#spCancel').focus();  // ברירת המחדל: לא לפצל
}
function closeSplitPick(){
  $('#splitPick').classList.add('hidden');
  document.body.style.overflow = '';
  spFor = null;
  if (spOpener && document.body.contains(spOpener)) spOpener.focus();
}
async function splitPicked(){
  if (!spFor) return;
  const ok = await joinAction('/api/records/' + spFor.record_id + '/split-page', { axis: spAxis() }, $('#spGo'),
    '✓ העמוד פוצל לשני טפסים — כל השדות לבדיקה. אפשר לבטל פיצול');
  $('#spGo').disabled = false;
  if (ok) { spOpener = null; closeSplitPick(); }
}
function splitPickKeys(e){
  if (e.key === 'Escape') { e.preventDefault(); return closeSplitPick(); }
  const items = $$('.spitem');
  const at = items.indexOf(document.activeElement);
  if (at !== -1 && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].indexOf(e.key) !== -1) {
    e.preventDefault();
    const next = items[(at + 1) % items.length];
    spChoose(next);
    next.focus();
  } else if (e.key === 'Tab') {
    e.preventDefault();  // הפוקוס נשאר בחלון
    const stops = [$('.spitem[aria-checked="true"]'), $('#spCancel'), $('#spGo')];
    const i = stops.indexOf(document.activeElement);
    stops[(i + (e.shiftKey ? stops.length - 1 : 1) + stops.length) % stops.length].focus();
  }
}
$$('.spitem').forEach(function(b){ b.onclick = function(){ spChoose(b); }; });
$('#spCancel').onclick = closeSplitPick;
$('#spGo').onclick = splitPicked;
$('#splitPick').addEventListener('click', function(e){ if (e.target.id === 'splitPick') closeSplitPick(); });

/* טופס שכבר אושר בעבר: הערכים הגיעו מהמערכת ולא מקריאת הטופס. שינוי — רק אחרי אישור בחלון,
   ואז חובה "אישור הטופס" מחדש (השרת מבטל את האישור כשערך משתנה). */
function storedText(r){
  const when = r.from_store ? ' (אושר ב-' + localTime(r.from_store) + ')' : '';
  return r.verified
    ? 'הנתונים מהמערכת, לא מקריאת הטופס' + when
    : 'הנתונים מהמערכת' + when + ' ושונו — יש לאשר את הטופס שוב';
}
function renderStoredbar(r){
  const bar = $('#fvStored');
  if (!r.from_store) { bar.classList.add('hidden'); bar.innerHTML = ''; return; }
  bar.classList.remove('hidden');
  bar.innerHTML = '<span class="why"><b>🗄 ' + esc(storedText(r)) + '.</b> ' +
    (r.verified ? 'הטופס כבר מאושר. אפשר לתקן — כל שינוי יחייב לאשר את הטופס שוב.'
                : 'בדקו את השינוי ולחצו "אישור הטופס".') + '</span>';
}

const storedEditOk = {};  // טפסים שהמזכירה כבר אישרה לשנות — לא שואלים שוב
let stGo = null, stOpener = null;
function askStoredEdit(recordId, go){
  const r = last && last.records.find(function(x){ return x.record_id === recordId; });
  if (!r || !r.from_store || !r.verified || storedEditOk[recordId]) return true;
  stGo = function(){ storedEditOk[recordId] = true; go(); };
  stOpener = document.activeElement;
  $('#storeConfirm').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  $('#stCancel').focus();  // ברירת המחדל: לא לשנות
  return false;
}
function closeStored(){
  $('#storeConfirm').classList.add('hidden');
  document.body.style.overflow = '';
  stGo = null;
  if (stOpener && document.body.contains(stOpener)) stOpener.focus();
}
function storedKeys(e){
  if (e.key === 'Escape') { e.preventDefault(); return closeStored(); }
  if (e.key === 'Tab') {
    e.preventDefault();
    (document.activeElement === $('#stCancel') ? $('#stYes') : $('#stCancel')).focus();
  }
}
$('#stCancel').onclick = closeStored;
$('#storeConfirm').addEventListener('click', function(e){ if (e.target.id === 'storeConfirm') closeStored(); });
$('#stYes').onclick = function(){
  const go = stGo;
  stOpener = null;
  closeStored();
  if (go) go();
};

/* טופס חתוך שהגיע בשני קבצים. הכול בלי קריאה ובחינם:
   טופס מחובר → "הפרד"; חלק שהופרד → "חבר בחזרה"; חלק של טופס חתוך → "חבר עם חלק אחר" (בחירה ידנית). */
const PATIENT_KEYS = ['first_name', 'last_name', 'national_id', 'central_numbers'];

function hasPatient(r){ return PATIENT_KEYS.some(function(k){ return r.fields[k].values.length > 0; }); }
function joinCandidates(r){
  return (last ? last.records : []).filter(function(x){ return x.can_join && x.record_id !== r.record_id; });
}

function renderJoinbar(r){
  const bar = $('#fvJoined');
  const canPick = r.can_join && joinCandidates(r).length > 0;
  let text = '', buttons = '';
  if (r.joined) {
    text = '<b>⧉ שני חלקים חוברו לטופס אחד.</b> ' + esc(r.straightened || '');
    if (!r.verified) buttons = '<button class="secondary" data-join="split">הפרד לשני טפסים</button>';
  } else if (r.split_from_joined && !r.verified) {
    text = '<b>⧉ הטופס הזה הופרד מטופס מחובר.</b> אם ההפרדה הייתה בטעות — אפשר לחבר בחזרה, בלי עלות.';
    buttons = '<button class="secondary" data-join="rejoin">⧉ חבר בחזרה</button>' +
      (canPick ? '<button class="secondary" data-join="pick">חבר עם חלק אחר</button>' : '');
  } else if (canPick) {
    text = '<b>ייתכן שזה רק חלק מטופס.</b> אם החלק השני הועלה בקובץ נפרד — אפשר לחבר ביניהם, בלי עלות.';
    buttons = '<button class="secondary" data-join="pick">⧉ חבר עם חלק אחר</button>';
  }
  if (!text) { bar.classList.add('hidden'); bar.innerHTML = ''; return; }
  bar.classList.remove('hidden');
  bar.innerHTML = '<span class="why">' + text + '</span>' + buttons;
  bar.querySelectorAll('button[data-join]').forEach(function(btn){
    btn.onclick = function(){
      const act = btn.dataset.join;
      if (act === 'pick') return openJoinPick(r, btn);
      joinAction('/api/records/' + r.record_id + '/' + act, null, btn,
        act === 'split' ? '✓ הטופס הופרד לשני טפסים — אפשר לחבר בחזרה' : '✓ הטופס חובר בחזרה');
    };
  });
}

async function joinAction(url, body, btn, done){
  if (btn) btn.disabled = true;
  const res = await fetch(url, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : { method: 'POST' });
  const d = await res.json().catch(function(){ return {}; });
  if (!res.ok) { if (btn) btn.disabled = false; toast(d.detail || 'הפעולה לא הצליחה'); return false; }
  // נשארים על הטופס שנוצר (או על החלק העליון אחרי הפרדה)
  last = d;
  const at = visibleRecords().findIndex(function(x){ return x.record_id === d.focus; });
  if (at >= 0) formIndex = at;
  lastSignature = '';
  render(d, true);
  toast(done);
  return true;
}

// חלון הבחירה: החלקים האחרים כתמונות ממוזערות; חצים + Enter בוחרים, Esc סוגר
let jpFor = null, jpOther = null, jpOpener = null;

function openJoinPick(r, opener){
  jpFor = r; jpOther = null; jpOpener = opener;
  $('#jpList').innerHTML = joinCandidates(r).map(function(x){
    const who = [x.fields.first_name.values[0], x.fields.last_name.values[0]].filter(Boolean).join(' ') ||
      (x.fields.commitment_numbers.values[0] ? 'התחייבות ' + x.fields.commitment_numbers.values[0] : '');
    return '<button class="jpitem" role="option" aria-selected="false" data-rec="' + x.record_id + '">' +
      '<img alt="" src="' + pageUrl(x) + '/thumb"><span><b>טופס ' + (last.records.indexOf(x) + 1) + '</b>' +
      '<bdi>' + esc(x.source_name) + '</bdi>' + (who ? '<small><bdi>' + esc(who) + '</bdi></small>' : '') + '</span></button>';
  }).join('');
  $$('#jpList .jpitem').forEach(function(b){ b.onclick = function(){ pickPart(b); }; });
  $('#jpAbove').disabled = $('#jpBelow').disabled = true;
  $('#joinPick').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  const first = $('#jpList .jpitem');
  if (first) first.focus();
}

function pickPart(item){
  $$('#jpList .jpitem').forEach(function(b){ b.setAttribute('aria-selected', b === item ? 'true' : 'false'); });
  jpOther = (last.records || []).find(function(x){ return x.record_id === item.dataset.rec; }) || null;
  $('#jpAbove').disabled = $('#jpBelow').disabled = !jpOther;
  if (!jpOther) return;
  // ברירת המחדל: החלק עם פרטי המטופל למעלה
  const thisOnTop = hasPatient(jpFor) || !hasPatient(jpOther);
  $('#jpAbove').className = thisOnTop ? 'primary' : 'secondary';
  $('#jpBelow').className = thisOnTop ? 'secondary' : 'primary';
  (thisOnTop ? $('#jpAbove') : $('#jpBelow')).focus();
}

function closeJoinPick(){
  $('#joinPick').classList.add('hidden');
  document.body.style.overflow = '';
  jpFor = jpOther = null;
  if (jpOpener && document.body.contains(jpOpener)) jpOpener.focus();
}

async function joinPicked(thisOnTop){
  if (!jpFor || !jpOther) return;
  const top = thisOnTop ? jpFor.record_id : jpOther.record_id;
  const ok = await joinAction('/api/records/' + jpFor.record_id + '/join',
    { other: jpOther.record_id, top: top }, thisOnTop ? $('#jpAbove') : $('#jpBelow'), '✓ שני החלקים חוברו לטופס אחד');
  $('#jpAbove').disabled = $('#jpBelow').disabled = false;
  if (ok) { jpOpener = null; closeJoinPick(); }
}

function joinPickKeys(e){
  if (e.key === 'Escape') { e.preventDefault(); return closeJoinPick(); }
  const items = $$('#jpList .jpitem');
  const at = items.indexOf(document.activeElement);
  if (at !== -1 && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].indexOf(e.key) !== -1) {
    e.preventDefault();
    // RTL: חץ שמאלה = הבא
    const step = e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? 1 : -1;
    items[(at + step + items.length) % items.length].focus();
  } else if (at !== -1 && (e.key === 'Enter' || e.key === ' ')) {
    e.preventDefault();
    pickPart(items[at]);
  }
}
$('#jpCancel').onclick = closeJoinPick;
$('#jpAbove').onclick = function(){ joinPicked(true); };
$('#jpBelow').onclick = function(){ joinPicked(false); };
$('#joinPick').addEventListener('click', function(e){ if (e.target.id === 'joinPick') closeJoinPick(); });

function askReread(recordId, opener){
  rrFor = recordId;
  rrOpener = opener;
  $('#rrConfirm').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  $('#rrCancel').focus();                    // ברירת המחדל: לא להוציא כסף
}
function closeReread(){
  $('#rrConfirm').classList.add('hidden');
  document.body.style.overflow = '';
  rrFor = null;
  if (rrOpener && document.body.contains(rrOpener)) rrOpener.focus();
}
function rereadKeys(e){
  if (e.key === 'Escape') { e.preventDefault(); return closeReread(); }
  if (e.key === 'Tab') {
    e.preventDefault();
    (document.activeElement === $('#rrCancel') ? $('#rrYes') : $('#rrCancel')).focus();
  }
}
$('#rrCancel').onclick = closeReread;
$('#rrConfirm').addEventListener('click', function(e){ if (e.target.id === 'rrConfirm') closeReread(); });
$('#rrYes').onclick = async function(){
  const id = rrFor;
  if (!id) return;
  $('#rrYes').disabled = true;
  const res = await fetch('/api/records/' + id + '/reread', { method: 'POST' });
  $('#rrYes').disabled = false;
  closeReread();
  const d = await res.json().catch(function(){ return {}; });
  if (!res.ok) return toast(d.detail || 'לא הצלחנו להתחיל קריאה חוזרת');
  lastSignature = '';
  render(d, true);
  watchReread(id);
};

// עד שהקריאה החוזרת נגמרת: בודקים כל 2 שניות, ואז מציירים את הטופס החדש
function watchReread(id){
  clearInterval(rrTimer);
  const me = batch;
  rrTimer = setInterval(async function(){
    if (me !== batch) return clearInterval(rrTimer);
    const res = await fetch('/api/batches/' + me);
    if (!res.ok) return clearInterval(rrTimer);
    const d = await res.json();
    const still = (d.pages || []).some(function(p){ return p.record_id === id && p.status === 'reading'; });
    if (still) return;
    clearInterval(rrTimer);
    lastSignature = '';
    render(d, true);
    const r = d.records.find(function(x){ return x.record_id === id; });
    toast(r && !r.error ? '✓ הטופס נקרא שוב' : 'גם הקריאה החוזרת נכשלה');
  }, 2000);
}

/* תקריב ליד הטופס: הטופס המלא מימין, השדה הנבחר מוגדל משמאל. לא צף — חלק מהמסך.
   התקריב נחתך מקומית מתמונת העמוד (בלי מודל ובלי עלות). */
let zoomShown = '';
function renderZoom(force){
  const panel = $('#fvZoom');
  const viewer = panel.parentNode;
  const r = view === 'form' && zoomOpen ? currentRecord() : null;
  if (!r) {
    zoomShown = '';
    panel.classList.add('hidden');
    panel.innerHTML = '';
    viewer.classList.remove('zoomed');
    return;
  }
  const spec = FIELDS[cardIndex], f = r.fields[spec.key];
  const keys = isName(spec.key) ? NAME_KEYS : [spec.key];
  const deg = viewTurn[r.record_id] || 0;
  const sig = [r.record_id, spec.key, inkView, deg].concat(keys.map(function(k){
    return r.fields[k].state + r.fields[k].values.join('|');
  })).join('~');
  if (!force && sig === zoomShown) return;
  zoomShown = sig;
  const why = f.state === 'needs_review' && f.reasons.length ? f.reasons.join(' · ') : '';
  const tag = f.state === 'needs_review' ? '<span class="badge review">לבדיקה</span>'
            : f.state === 'approved' ? '<span class="badge ok">✓ אושר</span>' : '';
  const title = isName(spec.key) ? 'שם פרטי ושם משפחה' : spec.label;
  panel.innerHTML =
    '<div class="zhead"><h3>' + esc(title) + '</h3>' + tag + '<span class="grow"></span>' +
      '<button class="closebtn" data-zoomclose="1" aria-label="סגירת התקריב" title="סגירה (Esc)">✕</button></div>' +
    (why ? '<p class="zwhy">' + esc(why) + '</p>' : '') +
    zoomCrops(r, spec.key) +
    '<div class="zval">' + keys.map(function(k){
      return '<div class="zrow' + (k === spec.key && keys.length > 1 ? ' now' : '') + '"><span>' +
        (keys.length > 1 ? esc(fieldLabel(k)) + ' — המנוע קרא:' : 'המנוע קרא:') + '</span> <b>' + valueHtml(r.fields[k]) + '</b></div>';
    }).join('') + '</div>';
  panel.classList.remove('hidden');
  viewer.classList.add('zoomed');
  if (deg) panel.querySelectorAll('.zcrop img').forEach(function(img){ fitTurned(img, img.parentNode, img, deg, 0.5); });
  else setupPans(panel);
}

// התמונה בתקריב: חיתוך אחד, או לשמות — חיתוך משותף כשהם באותו אזור, אחרת שניים זה מתחת לזה
function zoomCrops(r, key){
  // בדף ישר — חלון שאפשר לגרור על העמוד המלא; בדף מסובב — התמונה החתוכה, כמו קודם
  const turned = viewTurn[r.record_id] || 0;
  const box = function(k){
    if (k === 'full_name') {
      const a = r.fields.first_name.crop_box, b = r.fields.last_name.crop_box;
      return a && b ? [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])] : (a || b);
    }
    return r.fields[k].crop_box;
  };
  const crop = function(k, label, i){
    const b = i != null ? r.fields[k].crop_boxes[i] : box(k);
    if (!turned && b) return panHtml(r, b, label);
    return '<div class="zcrop pv-cropwrap"><img alt="תקריב של ' + esc(label) + '" src="' + cropUrl(r, k, i) + '"></div>';
  };
  if (isName(key)) {
    if (r.full_name_crop) return crop('full_name', 'שם פרטי ושם משפחה') + placeNote(r, NAME_KEYS);
    const withBox = NAME_KEYS.filter(function(k){ return r.fields[k].crop_box; });
    const a = r.fields.first_name.crop_box, b = r.fields.last_name.crop_box;
    if (withBox.length === 2 && a.join() === b.join()) return crop(key, fieldLabel(key)) + placeNote(r, NAME_KEYS);
    if (withBox.length) return withBox.map(function(k){
      return '<p class="zsub">' + esc(fieldLabel(k)) + '</p>' + crop(k, fieldLabel(k));
    }).join('') + placeNote(r, withBox);
  } else if (perValue(r.fields[key])) {
    const f = r.fields[key];
    return f.crop_boxes.map(function(b, i){
      return b ? '<p class="zsub">' + esc(fieldLabel(key)) + ' — <bdi>' + esc(f.values[i]) + '</bdi></p>' +
        crop(key, f.values[i], i) : '';
    }).join('') + placeNote(r, [key]);
  } else if (r.fields[key].crop_box) {
    return crop(key, fieldLabel(key)) + placeNote(r, [key]);
  }
  return '<div class="zcrop none">אין מיקום לשדה הזה בטופס — בדקו בעמוד המלא שמימין</div>';
}

/* ------------------------------------------------------------ תקריב שגוררים (רק במצב טופס) */
// התקריב הוא חלון על העמוד המלא: בהתחלה בדיוק על השדה, ואפשר לגרור ולראות מה מסביב.
// המסגרת הכתומה על הטופס שמימין זזה יחד איתו — תמיד רואים איפה עומדים.
function pageSrc(r){ return pageUrl(r) + (inkView ? '?ink=1' : ''); }

function panHtml(r, b, label){
  return '<div class="zcrop zpan" tabindex="0" role="img" data-box="' + b.join(',') + '" aria-label="תקריב של ' +
    esc(label) + ' — גררו כדי לזוז במסמך, Home חוזר לשדה"><img alt="" draggable="false" src="' + pageSrc(r) + '">' +
    '<button class="zhome hidden" type="button" tabindex="-1" title="חזרה לשדה (Home)">↺ חזרה לשדה</button></div>';
}

function setupPans(panel){
  panel.querySelectorAll('.zpan').forEach(function(view){
    const img = view.querySelector('img');
    const ready = function(){ startPan(view, img); };
    if (img.complete && img.naturalWidth) ready(); else img.addEventListener('load', ready, { once: true });
  });
}

function startPan(view, img){
  const W = img.naturalWidth, H = img.naturalHeight;
  const b = view.dataset.box.split(',').map(Number);
  const rw = (b[2] - b[0]) * W, rh = (b[3] - b[1]) * H;
  const vw = view.clientWidth;
  const vh = Math.max(110, Math.min(vw * rh / rw, window.innerHeight * 0.36));
  view.style.height = vh + 'px';
  const z = Math.min(vw / rw, vh / rh);
  img.style.width = W * z + 'px';
  img.style.height = H * z + 'px';
  const st = { tx: 0, ty: 0 };
  const home = function(){
    st.tx = vw / 2 - (b[0] + b[2]) / 2 * W * z;
    st.ty = vh / 2 - (b[1] + b[3]) / 2 * H * z;
    place(false);
    view.querySelector('.zhome').classList.add('hidden');
    const page = $('#fvFrame .pv-page'), r = currentRecord();  // המסגרות המקוריות חוזרות לטופס
    if (page && r) {
      page.querySelectorAll('.pv-box').forEach(function(old){ old.remove(); });
      page.insertAdjacentHTML('beforeend', boxesHtml(r, FIELDS[cardIndex].key));
    }
  };
  const place = function(moved){
    st.tx = Math.min(0, Math.max(vw - W * z, st.tx));
    st.ty = Math.min(0, Math.max(vh - H * z, st.ty));
    img.style.transform = 'translate(' + st.tx + 'px,' + st.ty + 'px)';
    if (!moved) return;
    view.querySelector('.zhome').classList.remove('hidden');
    followOnPage([-st.tx / (W * z), -st.ty / (H * z), (vw - st.tx) / (W * z), (vh - st.ty) / (H * z)]);
  };
  view._home = home;
  home();

  let last = null;
  view.onpointerdown = function(e){
    if (e.button !== 0 || e.target.closest('.zhome')) return;
    view.setPointerCapture(e.pointerId);
    last = { x: e.clientX, y: e.clientY };
    view.classList.add('drag');
  };
  view.onpointermove = function(e){
    if (!last) return;
    st.tx += e.clientX - last.x;
    st.ty += e.clientY - last.y;
    last = { x: e.clientX, y: e.clientY };
    place(true);
  };
  view.onpointerup = view.onpointercancel = function(){ last = null; view.classList.remove('drag'); };
  view.querySelector('.zhome').onclick = function(e){ e.stopPropagation(); home(); view.focus({ preventScroll: true }); };
  view.onkeydown = function(e){
    const step = 40;
    const moves = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
    if (e.key === 'Home') { e.preventDefault(); e.stopPropagation(); return home(); }
    if (!moves[e.key]) return;
    e.preventDefault();
    e.stopPropagation();  // החצים מזיזים את התקריב, לא עוברים שדה/טופס
    st.tx += moves[e.key][0];
    st.ty += moves[e.key][1];
    place(true);
  };
}

// המסגרת על הטופס שמימין = בדיוק מה שמוצג עכשיו בתקריב
function followOnPage(v){
  const page = $('#fvFrame .pv-page');
  if (!page) return;
  page.querySelectorAll('.pv-box').forEach(function(old){ old.remove(); });
  page.insertAdjacentHTML('beforeend', '<div class="pv-box" style="left:' + v[0] * 100 + '%;top:' + v[1] * 100 +
    '%;width:' + (v[2] - v[0]) * 100 + '%;height:' + (v[3] - v[1]) * 100 + '%;transition:none"></div>');
}

// מה ידוע על המיקום: אומת מול הטופס, תוקן אוטומטית, או רק לפי המנוע
function placeNote(r, keys){
  const notes = keys.map(function(k){ return r.fields[k].region_note; });
  const text = notes.every(function(n){ return n === 'verified'; }) ? 'המיקום אומת מול הטופס'
    : notes.every(function(n){ return n === 'verified' || n === 'fixed'; }) ? 'המיקום תוקן אוטומטית לפי הטופס'
    : 'מיקום לפי המנוע — ייתכן שאינו מדויק. אם הערך לא נראה, השתמשו ב-🔍 הגדלה';
  return '<p class="znote">האזור המסומן בכתום בטופס · ' + text + '</p>';
}
function closeZoom(){
  if (!zoomOpen) return;
  zoomOpen = false;
  renderZoom();
  const card = document.querySelector('#fvCards .fcard[data-i="' + cardIndex + '"]');
  if (card) card.focus({ preventScroll: true });
}
$('#fvZoom').addEventListener('click', function(e){
  if (e.target.closest('[data-zoomclose]')) closeZoom();
});

function focusCard(i, noScroll){
  cardIndex = i;
  $$('#fvCards .fcard').forEach(function(c){ c.classList.toggle('now', Number(c.dataset.i) === i); });
  const r = currentRecord();
  if (!r) return;
  const box = $('#fvFrame .pv-page');
  if (box) {
    box.querySelectorAll('.pv-box').forEach(function(old){ old.remove(); });
    box.insertAdjacentHTML('beforeend', boxesHtml(r, FIELDS[i].key));
  }
  const card = document.querySelector('#fvCards .fcard[data-i="' + i + '"]');
  if (card) {
    card.focus({ preventScroll: true });
    if (!noScroll) card.scrollIntoView({ block: 'nearest', behavior: calm ? 'auto' : 'smooth' });
  }
  renderZoom();
}

function goForm(delta){
  const list = visibleRecords();
  const next = formIndex + delta;
  if (!delta || next < 0 || next >= list.length) return;
  // הטופס היעד נבחר לפי הרשימה של עכשיו; הטופס שעוזבים עשוי לצאת מהסינון — אז מחשבים מחדש את מקומו
  const target = list[next];
  pinnedId = target.record_id;
  formIndex = Math.max(0, visibleRecords().indexOf(target));
  zoomOpen = false;
  cardIndex = firstOpenCard(target);
  renderForm();
  focusCard(cardIndex, true);
  $('#formView').scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
  remember(true);  // מעבר בין טפסים מחליף את הרשומה בהיסטוריה — "אחורה" יוצא מהטופס, לא עובר טופס-טופס
}
$('#prevForm').onclick = function(){ goForm(-1); };
$('#nextForm').onclick = function(){ goForm(1); };
$('#formDots').addEventListener('click', function(e){
  const dots = $$('#formDots i');
  const i = dots.indexOf(e.target);
  if (i >= 0) goForm(i - formIndex);
});
$('#fvApprove').onclick = function(){ const r = currentRecord(); if (r) verify(r.record_id); };
// החזרה מהטופס: למקום שממנו פתחו אותו — לטבלה או לאריחים — ישר לטופס הזה
let formFrom = 'tiles';
$('#backToTiles').onclick = function(){
  if (formFrom === 'records') return showRecordsPage(openedKey);  // חזרה לפאנל, לשורה שנפתחה
  const r = currentRecord();
  setView(formFrom);
  const sel = formFrom === 'table' ? 'td.formcol[data-rec="' : '.ftile[data-rec="';
  const t = r && document.querySelector(sel + r.record_id + '"]');
  if (t) { t.scrollIntoView({ block: 'center' }); t.focus({ preventScroll: true }); }
};

$('#fvCards').addEventListener('click', function(e){
  const card = e.target.closest('.fcard');
  if (!card || e.target.closest('input')) return;
  const r = currentRecord();
  const b = e.target.closest('button');
  if (b && b.dataset.act === 'approve') return approveField(r.record_id, card.dataset.key, true);
  zoomOpen = true;
  focusCard(Number(card.dataset.i), true);
  if (b && b.dataset.act === 'edit') editCard(card);
});

function openRecord(recordId, from){
  filter = 'all';
  senderFilter = '';
  query = '';
  $('#q').value = '';
  $('#q2').value = '';
  $$('.filters button').forEach(function(x){ x.classList.toggle('on', x.dataset.filter === 'all'); });
  $('#senderFilter').value = '';
  if (from) formFrom = from;
  else if (view !== 'form') formFrom = view === 'table' ? 'table' : 'tiles';
  $('#backToTiles').textContent = formFrom === 'records' ? '→ חזרה לרשומות במערכת'
    : formFrom === 'table' ? '→ חזרה לטבלה' : '→ כל הטפסים';
  view = 'form';
  zoomOpen = false;
  pinnedId = recordId;
  const list = visibleRecords();
  formIndex = Math.max(0, list.findIndex(function(r){ return r.record_id === recordId; }));
  cardIndex = list[formIndex] ? firstOpenCard(list[formIndex]) : 0;
  lastSignature = '';
  render(last, true);
  focusCard(cardIndex, true);
  $('#formView').scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
  remember();
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
  if (!askStoredEdit(rec, function(){
    const again = document.querySelector('td.cell[data-rec="' + rec + '"][data-key="' + key + '"]');
    if (again) edit(again);
  })) return;
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
  if (LTR_KEYS.indexOf(key) !== -1) input.dir = 'ltr';  // מספרים ותאריכים — משמאל לימין גם בעריכה
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
  if (r && !askStoredEdit(r.record_id, function(){
    const again = document.querySelector('#fvCards .fcard[data-key="' + key + '"]');
    if (again) editCard(again);
  })) return;
  if (cancelEditor()) {
    lastSignature = '';
    render(last, true);
    card = document.querySelector('#fvCards .fcard[data-key="' + key + '"]');
    if (!card) return;
  }
  if (!zoomOpen) { zoomOpen = true; renderZoom(); }
  const raw = r.fields[key].values.join(' | ');
  const box = document.createElement('div');
  box.className = 'cardEdit';
  const input = document.createElement('input');
  input.value = raw;
  if (LTR_KEYS.indexOf(key) !== -1) input.dir = 'ltr';  // מספרים ותאריכים — משמאל לימין גם בעריכה
  input.setAttribute('aria-label', 'תיקון ' + fieldLabel(key) + ' — Enter לשמירה, Esc לביטול');
  box.appendChild(input);
  card.classList.add('editing');
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
// הגרסה שהמסך ראה: אם הטופס השתנה בינתיים (חלון אחר), השרת מסרב במקום לדרוס
function versionOf(recordId){
  const r = last && last.records.find(function(x){ return x.record_id === recordId; });
  return r ? r.version : undefined;
}
async function refuseStale(res, fallback){
  const d = await res.json().catch(function(){ return {}; });
  toast(d.detail || fallback || 'הפעולה לא נשמרה — נסו שוב');
  // רק כשהטופס השתנה בחלון אחר — מביאים את המצב העדכני
  if (res.status === 409 && /השתנה/.test(d.detail || '') && batch) {
    const fresh = await fetch('/api/batches/' + batch);
    if (fresh.ok) { lastSignature = ''; render(await fresh.json(), true); }
  }
}

async function patch(recordId, body){
  const res = await fetch('/api/records/' + recordId, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ version: versionOf(recordId) }, body))
  });
  if (!res.ok) { await refuseStale(res); return null; }
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
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ field: key, version: versionOf(recordId) })
  });
  if (!res.ok) return toast('אין מה לבטל');
  lastSignature = '';
  render(await res.json(), true);
  toast('↶ ' + fieldLabel(key) + ' — בוטל');
}

async function verify(recordId){
  const res = await fetch('/api/records/' + recordId + '/verify', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version: versionOf(recordId) })
  });
  if (!res.ok) return refuseStale(res, 'עוד לא אפשר לאשר את הטופס');
  const d = await res.json();
  approvedHere = true;
  lastSignature = '';
  render(d, true);
  toast('✓ הטופס אושר ונשמר כמאומת');
  // לטופס הבא — אם יש. בטופס האחרון נשארים, והחגיגה וחלונית הייצוא נפתחות מעצמן
  if (view === 'form' && formIndex < visibleRecords().length - 1) goForm(1);
}

/* ------------------------------------------------------------ מקלדת במצב טופס */
document.addEventListener('keydown', function(e){
  if (!$('#xlPreview').classList.contains('hidden')) return previewKeys(e);
  if (pz.open) return pageZoomKeys(e);
  if (!$('#rrConfirm').classList.contains('hidden')) return rereadKeys(e);
  if (!$('#joinPick').classList.contains('hidden')) return joinPickKeys(e);
  if (!$('#splitPick').classList.contains('hidden')) return splitPickKeys(e);
  if (!$('#delConfirm').classList.contains('hidden')) return deleteKeys(e);
  if (!$('#storeConfirm').classList.contains('hidden')) return storedKeys(e);
  if (e.key === 'Escape') { closePop(); if (!$('#tour').classList.contains('hidden')) closeTour(); }
  if (view !== 'form' || $('#results').classList.contains('hidden') || !$('#tour').classList.contains('hidden')) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
  if (e.key === 'Escape' && zoomOpen) { e.preventDefault(); return closeZoom(); }
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

/* ------------------------------------------------------------ ייצוא: תצוגה מקדימה ואז הורדה */
// החלון מציג בדיוק את השורות שהשרת יכתוב לקובץ (אותו קוד בשרת בונה את שניהם).
let xlOpener = null;

function plural(n, one, many){ return n === 1 ? one : n + ' ' + many; }

async function openExportPreview(opener){
  if ($('#download').disabled || !batch) return;
  xlOpener = opener || $('#download');
  $('#xlDone').classList.add('hidden');
  $('#xlNew').classList.add('hidden');
  $('#xlDownload').classList.remove('hidden');
  $('#xlCancel').textContent = 'ביטול';
  const res = await fetch('/api/batches/' + batch + '/export/preview');
  if (!res.ok) return toast('לא הצלחנו להכין תצוגה מקדימה — נסו שוב');
  const d = await res.json();
  const perForm = {};
  d.rows.forEach(function(r){ perForm[r.form] = (perForm[r.form] || 0) + 1; });
  $('#xlSummary').textContent = plural(d.forms, 'טופס אחד', 'טפסים') + ' · ' + plural(d.row_count, 'שורה אחת', 'שורות');
  $('#xlSources').textContent = d.sources === 1 ? 'מקובץ אחד' : 'מתוך ' + d.sources + ' קבצים';
  $('#xlFile').textContent = 'commitments-' + batch + '.xlsx';
  $('#xlHead').innerHTML = '<th class="rn" scope="col">#</th>' + d.headers.map(function(h){
    return '<th scope="col">' + esc(h) + '</th>';
  }).join('');
  $('#xlBody').innerHTML = d.rows.map(function(r, i){
    const split = perForm[r.form] > 1;
    return '<tr class="band' + (r.form % 2) + (split ? ' split' : '') + '"><td class="rn">' + (i + 1) + '</td>' +
      r.cells.map(function(c, col){
        return '<td' + (split && col === d.commitment_column ? ' class="hl"' : '') +
          (c.length > 30 ? ' title="' + esc(c) + '"' : '') + '><bdi>' + esc(c) + '</bdi></td>';
      }).join('') + '</tr>';
  }).join('');
  closePop();
  $('#xlPreview').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  $('.xlwrap').scrollTop = 0;
  $('#xlDownload').focus();
}

function closeExportPreview(){
  $('#xlPreview').classList.add('hidden');
  document.body.style.overflow = '';
  if (xlOpener && !xlOpener.disabled) xlOpener.focus();
}

// בתוך החלון: Esc סוגר, ו-Tab נשאר בין הכפתורים של החלון
function previewKeys(e){
  if (e.key === 'Escape') { e.preventDefault(); closeExportPreview(); return; }
  if (e.key !== 'Tab') return;
  const items = $$('#xlPreview button:not(.hidden), #xlPreview [tabindex="0"]');
  const first = items[0], lastItem = items[items.length - 1];
  if (!$('#xlPreview').contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); lastItem.focus(); }
  else if (!e.shiftKey && document.activeElement === lastItem) { e.preventDefault(); first.focus(); }
}

$('#download').onclick = function(){ openExportPreview($('#download')); };
$('#fvExport').onclick = function(){ openExportPreview($('#fvExport')); };
$('#xlClose').onclick = closeExportPreview;
$('#xlCancel').onclick = closeExportPreview;
$('#xlPreview').addEventListener('click', function(e){ if (e.target.id === 'xlPreview') closeExportPreview(); });
$('#xlDownload').onclick = async function(){
  $('#xlDownload').disabled = true;
  const ok = await downloadExcel();
  $('#xlDownload').disabled = false;
  if (!ok) return;
  $('#xlDone').classList.remove('hidden');
  $('#xlDownload').classList.add('hidden');
  $('#xlNew').classList.remove('hidden');
  $('#xlCancel').textContent = 'סגירה';
  $('#xlNew').focus();
};

async function downloadExcel(){
  if ($('#download').disabled) return false;
  const res = await fetch('/api/batches/' + batch + '/export', { method: 'POST' });
  if (!res.ok) {
    const e = await res.json().catch(function(){ return {}; });
    toast(e.detail || 'הייצוא נכשל — נסו שוב');
    return false;
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
  return true;
}

/* ------------------------------------------------------------ קליטה חדשה — מכל מקום */
// "קליטה חדשה" — לחיצה אחת פותחת את מסך הגרירה (20/10/2026). טפסים שעוד לא אושרו לא הולכים לאיבוד:
// resetToUpload שומר אותם כ"ממתינים לאישור", ולכן אין צורך בחלון שאלה שדרש לחיצה נוספת.
function startNewIntake(){
  const open = !processing && last && last.records ? last.records.filter(function(r){ return !r.verified; }).length : 0;
  resetToUpload();
  if (open) toast((open === 1 ? 'טופס אחד שלא אושר נשמר' : open + ' טפסים שלא אושרו נשמרו') +
    ' ב"רשומות במערכת" תחת "ממתינים לאישור"', { ms: 5000 });
}
$('#fvNew').onclick = function(){ startNewIntake(); };
$('#xlNew').onclick = function(){ closeExportPreview(); resetToUpload(); };

$('#again').onclick = function(){ startNewIntake(); };

function resetToUpload(){
  parkNow();  // "קליטה חדשה" לפני שסיימו — מה שלא אושר נשמר כממתין
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
  view = load('view', 'tiles') === 'table' ? 'table' : 'tiles';
  window.scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' });
  remember();
}

/* ------------------------------------------------------------ רשומות במערכת
   הטפסים שאושרו ב"אישור הטופס", שורה לכל מספר התחייבות — כמו האקסל. המסך מבקש תמיד טווח
   של תאריכי אישור — ברירת המחדל היום, לפי השעון של המחשב — ולכן אף פעם לא נטען כל המאגר. */
const REC = { mode: 'approved', range: 'today', from: '', to: '', rfrom: '', rto: '', q: '', sender: '' };
const REC_RANGES = { today: 'היום', yesterday: 'אתמול', week: 'ב-7 הימים האחרונים', month: 'החודש' };
let recTimer = null, recLoaded = false, delFor = null, delOpener = null;

function isoDay(d){
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
function dayOf(iso){ const p = iso.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
function heDay(iso){ const p = iso.split('-'); return p[2] + '/' + p[1] + '/' + p[0]; }
function shift(days){ const d = new Date(); d.setDate(d.getDate() + days); return isoDay(d); }

// הטווח שנבחר, כימים מקומיים → רגעים מדויקים ב-UTC (השרת שומר UTC). "עד" כולל את כל היום.
function recDays(){
  const today = isoDay(new Date());
  if (REC.range === 'yesterday') return [shift(-1), shift(-1)];
  if (REC.range === 'week') return [shift(-6), today];
  if (REC.range === 'month') return [today.slice(0, 8) + '01', today];
  if (REC.range === 'custom') {
    let from = REC.from || today, to = REC.to || from;
    if (to < from) { const t = from; from = to; to = t; }
    return [from, to];
  }
  return [today, today];
}
function recQuery(extra){
  const days = recDays(), end = dayOf(days[1]);
  end.setDate(end.getDate() + 1);
  const p = new URLSearchParams({ start: dayOf(days[0]).toISOString(), end: end.toISOString() });
  if (REC.q) p.set('q', REC.q);
  if (REC.sender) p.set('sender', REC.sender);
  if (REC.rfrom) p.set('received_from', REC.rfrom);
  if (REC.rto) p.set('received_to', REC.rto);
  Object.keys(extra || {}).forEach(function(k){ p.set(k, extra[k]); });
  return p.toString();
}
function recWhen(){
  if (REC.range !== 'custom') return REC_RANGES[REC.range];
  const days = recDays();
  return days[0] === days[1] ? 'ב-' + heDay(days[0]) : 'בין ' + heDay(days[0]) + ' ל-' + heDay(days[1]);
}

// focusKey: חזרה מטופס שנפתח מכאן — השורה שלו מסומנת ובמרכז המסך, הסינונים נשארים כמו שהיו
function showRecordsPage(focusKey){
  closePop();
  $('#uploadStrip').classList.add('hidden');
  $('#results').classList.add('hidden');
  $('#recordsPage').classList.remove('hidden');
  setNav('#navRecords');
  $('#pageTitle').textContent = 'רשומות במערכת';
  $('#pageSub').textContent = 'הטפסים שאושרו נשמרים כאן — שורה לכל מספר התחייבות, כמו בקובץ לאקסל.';
  remember();
  refreshPendingCount();
  const back = typeof focusKey === 'string' && focusKey ? focusKey : '';
  if (!back) window.scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' });
  return loadRecords().then(function(){
    const tr = back && document.querySelector('#recBody tr[data-key="' + back + '"]');
    if (tr) { tr.scrollIntoView({ block: 'center' }); tr.focus({ preventScroll: true }); }
  });
}

function showIntake(){
  if ($('#recordsPage').classList.contains('hidden')) return false;
  $('#recordsPage').classList.add('hidden');
  $('#uploadStrip').classList.remove('hidden');
  if (last) $('#results').classList.remove('hidden');
  setNav('#navUpload');
  $('#pageTitle').textContent = 'קליטת טפסי התחייבות';
  $('#pageSub').textContent = 'מעלים טפסים, בודקים, מייצאים לאקסל.';
  renderCrumbs();
  return true;
}

function setNav(sel){
  ['#navUpload', '#navReview', '#navExport', '#navRecords'].forEach(function(s){
    $(s).classList.toggle('on', s === sel);
    if (s === sel) $(s).setAttribute('aria-current', 'page'); else $(s).removeAttribute('aria-current');
  });
}

async function loadRecords(){
  const pending = REC.mode === 'pending';
  const asked = pending ? 'pending?' + pendingQuery() : 'saved?' + recQuery();
  if (!recLoaded) $('#recBody').innerHTML = skeletonRows();
  $('#recWrap').classList.add('loading');
  const res = await fetch('/api/' + asked).catch(function(){ return null; });
  if (asked !== (REC.mode === 'pending' ? 'pending?' + pendingQuery() : 'saved?' + recQuery())) return;  // הסינון השתנה בינתיים
  $('#recWrap').classList.remove('loading');
  const d = res ? await res.json().catch(function(){ return {}; }) : {};
  if (!res || !res.ok) return toast(d.detail || 'לא הצלחנו לטעון את הטפסים');
  recLoaded = true;
  if (pending) renderPending(d); else renderRecords(d);
}

/* ממתינים לאישור: טפסים שנקראו ולא אושרו. נשמרים רק כשצריך — הדף נסגר, המחשב נכבה, או העלאה
   חדשה לפני שסיימו. מוצגים כולם (זו רשימת המשימות), הישן ביותר למעלה; לחיצה פותחת לבדיקה. */
function pendingQuery(){
  const p = new URLSearchParams();
  if (REC.q) p.set('q', REC.q);
  if (REC.sender) p.set('sender', REC.sender);
  return p.toString();
}

async function refreshPendingCount(){
  try {
    const res = await fetch('/api/pending/count');
    if (res.ok) $('#pendingN').textContent = (await res.json()).count || '';
  } catch (e) { /* לא חיוני */ }
}

function setRecMode(mode){
  REC.mode = mode;
  const pending = mode === 'pending';
  $$('#recMode button').forEach(function(b){ b.setAttribute('aria-selected', String(b.dataset.mode === mode)); });
  $('#recRangeField').classList.toggle('hidden', pending);
  $('#recCustom').classList.toggle('hidden', pending || REC.range !== 'custom');
  $('#recMore').classList.toggle('hidden', pending);
  if (pending) { $('#recAdvanced').classList.add('hidden'); $('#recMore').setAttribute('aria-expanded', 'false'); }
  $('.recexport').classList.toggle('hidden', pending);
  $('#recWhenHead').textContent = pending ? 'נשמר' : 'אושר';
  $('.recpanel').setAttribute('aria-label', pending ? 'סינון הטפסים הממתינים' : 'סינון הטפסים המאושרים');
  recLoaded = false;
  refreshPendingCount();
  loadRecords();
}
$$('#recMode button').forEach(function(b){ b.onclick = function(){ setRecMode(b.dataset.mode); }; });

const PENDING_STATE = { review: ['review', 'לבדיקה'], ready: ['ready', 'מוכן לאישור'], failed: ['fail', '✗ הקריאה נכשלה'] };

function renderPending(d){
  const sel = $('#recSender');
  const senders = (d.senders || []).slice();
  if (REC.sender && senders.indexOf(REC.sender) === -1) senders.unshift(REC.sender);
  const options = '<option value="">כל הגורמים</option>' + senders.map(function(s){
    return '<option' + (s === REC.sender ? ' selected' : '') + '>' + esc(s) + '</option>';
  }).join('');
  if (sel.dataset.options !== options) { sel.innerHTML = options; sel.dataset.options = options; }

  const rows = d.rows || [];
  $('#pendingN').textContent = d.all || '';
  $('#recSummary').innerHTML = '<b>' + (d.total || 0) + '</b> טפסים ממתינים לאישור<span class="sep">·</span>הישן ביותר למעלה' +
    (d.truncated ? '<span class="sep">·</span>מוצגים ' + rows.length + ' הראשונים' : '');
  $('#recEmptyTitle').textContent = REC.q || REC.sender ? 'אין טפסים ממתינים שמתאימים לסינון' : 'אין טפסים שממתינים לאישור';
  $('#recEmpty').classList.toggle('hidden', rows.length > 0);
  renderChips();
  $('#recBody').innerHTML = rows.map(function(row){
    const st = PENDING_STATE[row.status] || ['', row.status];
    const label = row.status === 'review' ? '<b>' + row.review_count + '</b> ' + st[1] : st[1];
    const cell = function(v, extra){ return '<td class="' + (extra || '') + '">' + bdiText(v) + '</td>'; };
    return '<tr class="grp" tabindex="0" data-key="' + esc(row.record_id) + '" data-pending="1" aria-label="פתיחת הטופס לבדיקה">' +
      cell(row.first_name) + cell(row.last_name) + cell(row.national_id, 'num') + cell(row.central_numbers, 'num') +
      cell(row.sample_received_date) + cell(row.sending_entity) + cell((row.commitment_numbers || []).join(' | '), 'num') +
      '<td class="src" title="' + esc(row.source_name) + '"><span class="badge ' + st[0] + '">' + label + '</span><bdi>' +
        esc(row.source_name) + '</bdi> · עמ׳ ' + row.page_number + '</td>' +
      '<td class="when">' + esc(localTime(row.updated_at)) + '</td><td class="acts"></td></tr>';
  }).join('');
}

// פתיחת טופס ממתין במסך הבדיקה — גם אחרי הפעלה מחדש, בלי קריאה חוזרת. אחרי "אישור הטופס" הוא יוצא מהממתינים.
async function openPending(recordId){
  const res = await fetch('/api/pending/' + encodeURIComponent(recordId) + '/open', { method: 'POST' });
  const d = await res.json().catch(function(){ return {}; });
  if (!res.ok) { toast(d.detail || 'לא הצלחנו לפתוח את הטופס'); return loadRecords(); }
  showIntake();
  adoptBatch(d, true);
  openedKey = recordId;
  openedPending = true;
  openRecord(d.focus, 'records');
}

// הדף נסגר / מוסתר (סגירת דפדפן, כיבוי, מעבר לאתר אחר) — הטפסים שלא אושרו נשמרים כממתינים
function parkNow(){
  if (!batch || !last || processing || !last.records.some(function(r){ return !r.verified; })) return;
  try { navigator.sendBeacon('/api/batches/' + batch + '/park'); } catch (e) { /* הדפדפן נסגר */ }
}
window.addEventListener('pagehide', parkNow);
document.addEventListener('visibilitychange', function(){ if (document.visibilityState === 'hidden') parkNow(); });

function skeletonRows(){
  const row = '<tr class="skel">' + new Array(10).join('<td><span></span></td>') + '<td></td></tr>';
  return new Array(5).join(row);
}

function renderRecords(d){
  const sel = $('#recSender');
  const senders = (d.senders || []).slice();
  if (REC.sender && senders.indexOf(REC.sender) === -1) senders.unshift(REC.sender);
  const options = '<option value="">כל הגורמים</option>' + senders.map(function(s){
    return '<option' + (s === REC.sender ? ' selected' : '') + '>' + esc(s) + '</option>';
  }).join('');
  if (sel.dataset.options !== options) { sel.innerHTML = options; sel.dataset.options = options; }

  const rows = d.rows || [];
  $('#recSummary').innerHTML = '<b>' + (d.forms || 0) + '</b> טפסים<span class="sep">·</span><b>' +
    (d.total_rows || 0) + '</b> שורות<span class="sep">·</span>אושרו ' + esc(recWhen()) +
    (d.truncated ? '<span class="sep">·</span>מוצגות ' + rows.length + ' הראשונות — הייצוא כולל הכול' : '');
  $('#recExcel').disabled = $('#recExportMore').disabled = !rows.length;
  $('#recEmptyTitle').textContent = REC.q || REC.sender || REC.rfrom || REC.rto
    ? 'אין טפסים מאושרים שמתאימים לסינון' : 'אין טפסים מאושרים ' + recWhen();
  $('#recEmpty').classList.toggle('hidden', rows.length > 0);
  renderChips();

  let previous = '';
  $('#recBody').innerHTML = rows.map(function(row){
    const first = row.form_key !== previous;
    previous = row.form_key;
    const cls = [first ? 'grp' : 'cont', row.can_open ? '' : 'old'].filter(Boolean).join(' ');
    const rep = function(v, extra){ return '<td class="rep' + (extra ? ' ' + extra : '') + '">' + bdiText(v) + '</td>'; };
    const dupes = row.duplicates || [];
    const dup = dupes.length ? '<span class="badge dup" title="' + esc(dupTitle(dupes)) + '">⧉ ייתכן כפול</span>' : '';
    const old = row.can_open ? '' : '<span class="oldtag" title="אושר לפני שנוספה השמירה המלאה — לצפייה ולייצוא בלבד">לצפייה</span>';
    return '<tr class="' + cls + '" tabindex="0" data-key="' + esc(row.form_key) + '" data-open="' + (row.can_open ? 1 : 0) + '"' +
      ' aria-label="' + (row.can_open ? 'פתיחת הטופס לבדיקה' : 'טופס ישן — לצפייה בלבד') + '">' +
      rep(row.first_name) + rep(row.last_name) + rep(row.national_id, 'num') + rep(row.central_numbers, 'num') +
      rep(row.sample_received_date) + rep(row.sending_entity) +
      '<td class="num">' + bdiText(row.commitment_number) + dup + '</td>' +
      '<td class="src" title="' + esc(row.source_name) + '"><bdi>' + esc(row.source_name) + '</bdi> · עמ׳ ' + row.page_number + old + '</td>' +
      '<td class="when">' + esc(localTime(row.approved_at)) + '</td>' +
      '<td class="acts">' + (first ? '<button class="delbtn" data-del="' + esc(row.form_key) +
        '" aria-label="מחיקת הטופס" title="מחיקת הטופס">🗑</button>' : '') + '</td></tr>';
  }).join('');
}

// שבבים לסינונים הפעילים — כל אחד נסגר לבד, ו"נקה הכול" מחזיר להיום
function renderChips(){
  const chips = [];
  if (REC.q) chips.push(['q', 'חיפוש', REC.q]);
  if (REC.sender) chips.push(['sender', 'גורם שולח', REC.sender]);
  if (REC.rfrom || REC.rto) chips.push(['received', 'קבלת הדגימה',
    (REC.rfrom ? 'מ-' + heDay(REC.rfrom) : '') + (REC.rfrom && REC.rto ? ' ' : '') + (REC.rto ? 'עד ' + heDay(REC.rto) : '')]);
  const box = $('#recChips');
  box.classList.toggle('hidden', !chips.length);
  box.innerHTML = chips.map(function(c){
    return '<button class="recchip" data-chip="' + c[0] + '" aria-label="הסרת הסינון ' + esc(c[1]) + '">' +
      c[1] + ': <b><bdi>' + esc(c[2]) + '</bdi></b><i aria-hidden="true">✕</i></button>';
  }).join('') + (chips.length ? '<button class="recclear" id="recClear">נקה הכול</button>' : '');
}

function localTime(iso){
  const d = new Date(iso);
  if (isNaN(d)) return iso || '';
  return heDay(isoDay(d)) + ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}
function dupTitle(dupes){
  return 'אותו מספר התחייבות קיים בטופס מאושר אחר: ' + dupes.map(function(x){
    return x.source_name + ', אושר ' + localTime(x.approved_at);
  }).join(' · ');
}

// פתיחת טופס מאושר במסך הבדיקה — גם אחרי הפעלה מחדש של השרת, בלי קריאה חוזרת
async function openSaved(key){
  const res = await fetch('/api/saved/' + encodeURIComponent(key) + '/open', { method: 'POST' });
  const d = await res.json().catch(function(){ return {}; });
  if (!res.ok) return toast(d.detail || 'לא הצלחנו לפתוח את הטופס');
  showIntake();
  adoptBatch(d);
  openedKey = key;
  openedPending = false;
  openRecord(d.focus, 'records');
}

// העלאה שכבר נקראה (מהרשומות, או ב"אחורה"/"קדימה") מוצגת במסך הבדיקה — בלי קריאה חוזרת.
// waiting: טפסים ממתינים — כשהמזכירה מאשרת את האחרון שבהם, החגיגה וחלונית האקסל כרגיל.
function adoptBatch(d, waiting){
  clearInterval(timer);
  clearNotices();
  batch = d.batch_id;
  processing = false;
  finishing = false;
  celebrated = !waiting;  // טפסים שכבר אושרו — בלי חגיגה ובלי חלונית ייצוא שנפתחת לבד
  approvedHere = false;
  drop.classList.add('hidden');
  $('#progress').classList.add('hidden');
  last = d;
  lastSignature = '';
  render(d, true);
}

$('#navRecords').onclick = showRecordsPage;
// מהרשומות חזרה לקליטה: הלשונית "קליטת טפסים" רק חוזרת למסך, לא פותחת קליטה חדשה
$('#navUpload').addEventListener('click', function(e){
  if (showIntake()) { e.stopImmediatePropagation(); remember(); }
}, true);
['#navReview', '#navExport'].forEach(function(sel){
  $(sel).addEventListener('click', function(){ if (showIntake()) remember(); }, true);
});

$('#recBody').addEventListener('click', function(e){
  const del = e.target.closest('button[data-del]');
  if (del) return askDelete(del.dataset.del, del);
  const tr = e.target.closest('tr[data-key]');
  if (!tr) return;
  if (tr.dataset.pending === '1') openPending(tr.dataset.key);
  else if (tr.dataset.open === '1') openSaved(tr.dataset.key);
  else toast('הטופס הזה אושר לפני שנוספה השמירה המלאה — אפשר לראות ולייצא אותו, לא לפתוח');
});
$('#recBody').addEventListener('keydown', function(e){
  if (e.key !== 'Enter' || e.target.tagName !== 'TR') return;
  e.preventDefault();
  e.target.click();
});

// בחירת טווח: פקד מקטעים עם חצים (כמו קבוצת כפתורי רדיו)
const rangeButtons = $$('#recRange button');
function pickRange(button, focus){
  REC.range = button.dataset.range;
  rangeButtons.forEach(function(b){
    const on = b === button;
    b.setAttribute('aria-checked', String(on));
    b.tabIndex = on ? 0 : -1;
  });
  if (focus) button.focus();
  $('#recCustom').classList.toggle('hidden', REC.range !== 'custom');
  if (REC.range === 'custom' && !$('#recFrom').value) $('#recFrom').value = REC.from = isoDay(new Date());
  loadRecords();
}
rangeButtons.forEach(function(b){ b.onclick = function(){ pickRange(b); }; });
$('#recRange').addEventListener('keydown', function(e){
  const at = rangeButtons.indexOf(document.activeElement);
  if (at === -1) return;
  const step = e.key === 'ArrowLeft' ? 1 : e.key === 'ArrowRight' ? -1 : 0;  // RTL: שמאלה = הבא
  if (!step) return;
  e.preventDefault();
  pickRange(rangeButtons[(at + step + rangeButtons.length) % rangeButtons.length], true);
});

$('#recMore').onclick = function(){
  const open = $('#recAdvanced').classList.toggle('hidden') === false;
  $('#recMore').setAttribute('aria-expanded', String(open));
  if (open) $('#recRFrom').focus();
};

function recChanged(){ clearTimeout(recTimer); recTimer = setTimeout(loadRecords, 250); }
$('#recQ').addEventListener('input', function(e){ REC.q = e.target.value.trim(); recChanged(); });
$('#recSender').onchange = function(e){ REC.sender = e.target.value; loadRecords(); };
[['#recFrom', 'from'], ['#recTo', 'to'], ['#recRFrom', 'rfrom'], ['#recRTo', 'rto']].forEach(function(pair){
  $(pair[0]).addEventListener('change', function(e){ REC[pair[1]] = e.target.value; loadRecords(); });
});

$('#recChips').addEventListener('click', function(e){
  const chip = e.target.closest('button');
  if (!chip) return;
  const which = chip.id === 'recClear' ? 'all' : chip.dataset.chip;
  if (which === 'q' || which === 'all') { REC.q = ''; $('#recQ').value = ''; }
  if (which === 'sender' || which === 'all') { REC.sender = ''; $('#recSender').value = ''; }
  if (which === 'received' || which === 'all') { REC.rfrom = REC.rto = ''; $('#recRFrom').value = $('#recRTo').value = ''; }
  if (which === 'all') pickRange(rangeButtons[0]); else loadRecords();
  $('#recQ').focus();
});

// ייצוא: Excel בכפתור הראשי, CSV/JSON בתפריט. הדפדפן מוריד ישירות; offset — שעון מקומי בקובץ
function exportSaved(format){
  window.location.href = '/api/saved/export?' + recQuery({ format: format, offset: new Date().getTimezoneOffset() });
}
function closeExportMenu(){
  $('#recExportMenu').classList.add('hidden');
  $('#recExportMore').setAttribute('aria-expanded', 'false');
}
$('#recExcel').onclick = function(){ exportSaved('xlsx'); };
$('#recExportMore').onclick = function(e){
  e.stopPropagation();
  const open = $('#recExportMenu').classList.toggle('hidden') === false;
  $('#recExportMore').setAttribute('aria-expanded', String(open));
  if (open) $('#recExportMenu button').focus();
};
$$('#recExportMenu button').forEach(function(b){
  b.onclick = function(){ closeExportMenu(); exportSaved(b.dataset.export); };
});
$('#recExportMenu').addEventListener('keydown', function(e){
  const items = $$('#recExportMenu button'), at = items.indexOf(document.activeElement);
  if (e.key === 'Escape') { e.preventDefault(); closeExportMenu(); $('#recExportMore').focus(); }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    items[(at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
  }
});
document.addEventListener('click', function(e){ if (!e.target.closest('.recexport')) closeExportMenu(); });

function askDelete(key, opener){
  delFor = key;
  delOpener = opener;
  $('#delConfirm').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  $('#delCancel').focus();  // ברירת המחדל: לא למחוק
}
function closeDelete(){
  $('#delConfirm').classList.add('hidden');
  document.body.style.overflow = '';
  delFor = null;
  if (delOpener && document.body.contains(delOpener)) delOpener.focus();
}
function deleteKeys(e){
  if (e.key === 'Escape') { e.preventDefault(); return closeDelete(); }
  if (e.key === 'Tab') {
    e.preventDefault();
    (document.activeElement === $('#delCancel') ? $('#delYes') : $('#delCancel')).focus();
  }
}
$('#delCancel').onclick = closeDelete;
$('#delConfirm').addEventListener('click', function(e){ if (e.target.id === 'delConfirm') closeDelete(); });
$('#delYes').onclick = async function(){
  const key = delFor;
  if (!key) return;
  $('#delYes').disabled = true;
  const res = await fetch('/api/saved/' + encodeURIComponent(key), { method: 'DELETE' });
  $('#delYes').disabled = false;
  const d = await res.json().catch(function(){ return {}; });
  delOpener = null;
  closeDelete();
  if (!res.ok) return toast(d.detail || 'המחיקה לא הצליחה');
  if (d.batch && d.batch.batch_id === batch) { last = d.batch; lastSignature = ''; }
  toast('🗑 הטופס נמחק');
  loadRecords();
};

/* ------------------------------------------------------------ ניווט: "אחורה" של הדפדפן ופירורי לחם
   כל המערכת היא עמוד אחד. כל מעבר בין מסכים נרשם בהיסטוריה של הדפדפן (רק סימן אחרי #), כך
   ש"אחורה"/"קדימה" (וגם Alt+←) נשארים בתוך המערכת ומחזירים למסך הקודם עם הסינונים שלו. */
let restoring = false, openedKey = '', openedPending = false;

function screenNow(){
  if (!$('#recordsPage').classList.contains('hidden')) return { page: 'records' };
  if (!last || $('#results').classList.contains('hidden')) return { page: 'intake', view: 'upload' };
  if (view === 'form') {
    const r = currentRecord();
    return { page: 'intake', view: 'form', batch: batch, rec: r ? r.record_id : '', from: formFrom,
             key: formFrom === 'records' ? openedKey : '', pending: formFrom === 'records' && openedPending };
  }
  return { page: 'intake', view: view, batch: batch };
}

function remember(replace){
  renderCrumbs();
  if (restoring) return;
  const s = screenNow();
  if (history.state && JSON.stringify(history.state) === JSON.stringify(s)) return;
  const hash = '#' + (s.page === 'records' ? 'records' : s.view);
  if (replace || !history.state) history.replaceState(s, '', hash);
  else history.pushState(s, '', hash);
}

async function goTo(s){
  closePop();
  if (s.page === 'records') return showRecordsPage(s.key || openedKey);
  showIntake();
  if (s.view === 'upload') { if (last) resetToUpload(); return; }
  if (s.batch && s.batch !== batch) {
    if (s.key) { await (s.pending ? openPending(s.key) : openSaved(s.key)); return; }
    const res = await fetch('/api/batches/' + s.batch);
    if (!res.ok) { toast('ההעלאה הזו כבר לא זמינה (ייתכן שהשרת הופעל מחדש)'); return; }
    adoptBatch(await res.json());
  }
  if (!last) return;
  if (s.view === 'form' && s.rec) { if (s.key) openedKey = s.key; openRecord(s.rec, s.from); }
  else { view = s.view; lastSignature = ''; render(last, true); }
}

window.addEventListener('popstate', function(e){
  if (!e.state) return;
  if (processing) {  // באמצע קריאה לא עוזבים את מסך ההתקדמות
    history.pushState(screenNow(), '', '#upload');
    return toast('אפשר לחזור אחורה אחרי שהקריאה מסתיימת');
  }
  restoring = true;
  Promise.resolve(goTo(e.state)).catch(function(){}).then(function(){ restoring = false; renderCrumbs(); });
});

// פירורי הלחם: המסך הנוכחי, ובטופס פתוח גם "טופס X מתוך Y". השלבים הקודמים לחיצים.
function renderCrumbs(){
  const ol = $('#crumbs');
  $$('#crumbs li.dyn').forEach(function(li){ li.remove(); });
  const onRecords = !$('#recordsPage').classList.contains('hidden');
  const inForm = !onRecords && view === 'form' && last && !$('#results').classList.contains('hidden');
  const parts = [];
  if (onRecords) parts.push(['רשומות במערכת']);
  else if (inForm) {
    parts.push(formFrom === 'records' ? ['רשומות במערכת', 'records'] : ['קליטת טפסי התחייבות', 'forms']);
    parts.push(['טופס ' + (formIndex + 1) + ' מתוך ' + visibleRecords().length]);
  } else parts.push(['קליטת טפסי התחייבות']);
  parts.forEach(function(p, i){
    const li = document.createElement('li');
    li.className = 'dyn';
    if (i === parts.length - 1) { li.setAttribute('aria-current', 'page'); li.textContent = p[0]; }
    else {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'crumblink';
      b.dataset.go = p[1];
      b.textContent = p[0];
      li.appendChild(b);
    }
    ol.appendChild(li);
  });
}
$('#crumbs').addEventListener('click', function(e){
  const b = e.target.closest('button[data-go]');
  if (!b) return;
  if (b.dataset.go === 'records') showRecordsPage(openedKey);
  else $('#backToTiles').click();
});

history.replaceState({ page: 'intake', view: 'upload' }, '', location.pathname + location.search);

/* ------------------------------------------------------------ רגע הצלחה */
function unverifiedCount(d){
  return d.unverified != null ? d.unverified : d.records.filter(function(r){ return !r.verified; }).length;
}

// רק אחרי שהטופס האחרון אושר ב"אישור הטופס": חגיגה, ואחריה נפתחת חלונית הייצוא לבד
const EXPORT_AFTER_PARTY = 2000;
function maybeCelebrate(d){
  if (!approvedHere || processing || celebrated || !d.records.length || d.review_total !== 0 || unverifiedCount(d) !== 0) return;
  if (!d.records.some(function(r){ return !r.error; })) return;
  celebrated = true;
  $('#download').classList.add('ready');
  toast('🎉 כל ' + d.records.length + ' הטפסים אושרו — מוכנים לייצוא', { big: true, ms: 5000 });
  const me = batch;
  setTimeout(function(){
    if (me === batch && !$('#download').disabled && $('#xlPreview').classList.contains('hidden')) openExportPreview($('#download'));
  }, calm ? 300 : EXPORT_AFTER_PARTY);
  if (calm) return;
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
    text: 'גוררים קבצי PDF או תמונות — כמה שרוצים. כל טופס נקרא לבד ומופיע ברגע שהוא מוכן. ' },
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