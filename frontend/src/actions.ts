/* הלוגיקה של המסך — הועתקה מהמסך הקודם (app.js, שמור בהיסטוריה של Git) פונקציה מול פונקציה. במקום לבנות HTML, כל פעולה משנה את S
 * וקוראת ל-emit(); הרכיבים מציירים לפי S. שום פעולה כאן לא קוראת למודל: הכול מול השרת המקומי. */
import type { Batch, Rec } from './lib';
import {
  EMAIL_RE, calm, dayOf, hasPatient, heDay, isName, isoDay, load, localTime, perValue, recent, remember5, save,
  senderOf, shift, splitValues, waNumber,
} from './lib';
import { S, afterRender, emit, fieldLabel, isApproved, recordById, viewKey, zoomViews } from './store';

const $ = (s: string) => document.querySelector(s) as HTMLElement | null;

/* ------------------------------------------------------------ מראה */
export function applyLook() {
  const root = document.documentElement;
  root.dataset.theme = S.theme;
  root.dataset.size = S.size;
  root.dataset.brand = S.brand;
}
export function toggleTheme() {
  S.theme = S.theme === 'dark' ? 'light' : 'dark';
  save('theme', S.theme);
  applyLook();
  emit();
}
export function setSize(delta: number) {
  S.size = String(Math.max(0, Math.min(3, Number(S.size || 1) + delta)));
  save('size', S.size);
  applyLook();
  closePop();
}

/* ------------------------------------------------------------ הגדרות מהשרת */
export const NEUTRAL_MARK = 'data:image/svg+xml;utf8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect x="9" y="5" width="22" height="30" rx="3" fill="#fff" stroke="#186cb4" stroke-width="2.5"/>' +
  '<path d="M14 13h12M14 18h9" stroke="#9db4c8" stroke-width="2.5" stroke-linecap="round"/>' +
  '<path d="M14 26l4 4 8-9" fill="none" stroke="#00a8cc" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>');

export function loadConfig() {
  fetch('/api/config').then((r) => r.json()).then((c) => {
    S.config = c;
    S.fields = c.fields;
    // moh = עיצוב משרד הבריאות (ברירת המחדל); neutral = הסמל והשם שלנו, בלי שם המשרד ובלי הלוגו
    S.brand = c.brand === 'neutral' ? 'neutral' : 'moh';
    applyLook();
    if (S.brand === 'neutral') {
      const icon = document.querySelector('link[rel="icon"]') as HTMLLinkElement | null;
      if (icon) icon.href = NEUTRAL_MARK;
      document.title = 'קליטת טפסי התחייבות — טופס למידע';
    }
    if (!c.has_key) notice('err', 'אין מפתח API', 'לא נמצא ANTHROPIC_API_KEY בקובץ .env. הוסיפו והפעילו מחדש את השרת.');
    if (load('toured', '') !== '1') openTour();
    emit();
  });
}

export function setStep(n: number) { S.step = n; }

/* ------------------------------------------------------------ הודעות */
let noticeId = 0, toastId = 0;
export function notice(kind: string, title: string, text: string) {
  S.notices.push({ id: ++noticeId, kind, title, text });
  emit();
}
export function clearNotices() { S.notices = []; }

export function toast(text: string, opts: { big?: boolean; undo?: () => void; ms?: number } = {}) {
  const t = { id: ++toastId, text, big: !!opts.big, out: false, undo: opts.undo };
  S.toasts.push(t);
  const live = S.toasts.filter((x) => !x.out);
  live.slice(0, Math.max(0, live.length - 3)).forEach((old) => dismissToast(old.id));
  const timer = setTimeout(() => dismissToast(t.id), opts.ms || (opts.undo ? 6500 : 3500));
  (t as any).timer = timer;
  emit();
}
export function dismissToast(id: number) {
  const t = S.toasts.find((x) => x.id === id);
  if (!t || t.out) return;
  clearTimeout((t as any).timer);
  t.out = true;
  emit();
  setTimeout(() => { S.toasts = S.toasts.filter((x) => x.id !== id); emit(); }, 260);
}

/* ------------------------------------------------------------ העלאה */
function showProgress(title: string, names: string[]) {
  clearNotices();
  setStep(2);
  S.dropVisible = false;
  S.resultsVisible = false;
  S.progressVisible = true;
  S.pTitle = title;
  S.pCount = '';
  resetRing();
  S.tiles = [];
  tilesBuilt = {};
  S.pFiles = names.map((n) => ({ name: n, error: '' }));
  emit();
}

export async function upload(fileList: FileList | File[] | null) {
  const files = Array.prototype.slice.call(fileList || []) as File[];
  if (!files.length) return;
  showProgress('קולט את הטפסים…', files.map((f) => f.name));
  const body = new FormData();
  files.forEach((f) => body.append('files', f));
  try {
    // בענן (Supabase): הקבצים עולים ישר לאחסון הפרטי, ורק אז מתחילה העבודה (מהגרסה של קודקס)
    if (S.config && S.config.cloud_uploads) {
      const prepared = await fetch('/api/uploads', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files: files.map((f) => ({ name: f.name, size: f.size })) }),
      });
      const tickets = await prepared.json();
      if (!prepared.ok) throw new Error(typeof tickets.detail === 'string' ? tickets.detail : 'פרטי הקבצים אינם תקינים');
      for (let i = 0; i < files.length; i++) {
        const data = new FormData();
        data.append('cacheControl', '0');
        data.append('file', files[i]);
        const sent = await fetch(tickets.files[i].url, { method: 'PUT', body: data, credentials: 'omit' });
        if (!sent.ok) throw new Error('העלאת הקובץ לאחסון נכשלה. אפשר לבחור אותו מחדש.');
      }
      const ready = await fetch('/api/uploads/complete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: tickets.files.map((f: any) => f.id) }),
      });
      const info = await ready.json();
      if (!ready.ok) throw new Error(info.detail || 'לא ניתן להתחיל בעיבוד');
      begin(info);
      return;
    }
    const res = await fetch('/api/batches', { method: 'POST', body });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || 'ההעלאה נכשלה');
    }
    begin(await res.json());
  } catch (err) {
    fail((err as Error).message);
  }
}

let timer: ReturnType<typeof setInterval> | undefined;

export function begin(info: { batch_id: string; rejected?: number }) {
  S.batch = info.batch_id;
  S.last = null;
  S.finishing = false;
  S.view = 'tiles';
  S.filter = 'all';  // העלאה חדשה מתחילה תמיד ב"הכול" — גם אם קודם עמדו על "לבדיקה"
  S.senderFilter = '';
  lastSignature = '';
  S.celebrated = false;
  S.approvedHere = false;
  S.formIndex = 0;
  S.pinnedId = null;
  S.cardIndex = 0;
  S.processing = true;
  if (info.rejected)
    notice('warn', info.rejected + ' קבצים לא התקבלו', 'הם מופיעים ברשימה עם הסיבה. שאר הקבצים מעובדים כרגיל.');
  S.pTitle = 'קולט את הטפסים…';
  clearInterval(timer);
  timer = setInterval(tick, 900);
  emit();
  tick();
}

function fail(msg: string) {
  clearInterval(timer);
  S.processing = false;
  S.progressVisible = false;
  S.pFiles = [];
  S.dropVisible = true;
  setStep(1);
  notice('err', 'לא הצלחנו לעבד את הקבצים', msg);
}

/* ------------------------------------------------------------ התקדמות חיה */
// גלגל אחד באחוזים, מההעלאה ועד הסוף. הכנת העמודים שווה 20%, הקריאה 80%; טופס באמצע קריאה נספר
// כחצי. האחוז אף פעם לא יורד, ו-100% רק כשהכול גמור.
export const RING_C = 2 * Math.PI * 52;
// קצב התצוגה בלבד (לא עבודה): האחוז עולה 1% כל 12ms, טופס מוכן מופיע אחרי 150ms (היה 40/500 — נמדד 22/10/2026:
// כ-3.4 שניות המתנה מלאכותית לטופס אחד)
const PCT_STEP = 12, TILE_STEP = 150, PREPARE_SHARE = 20;
let ringTimer: ReturnType<typeof setTimeout> | null = null;
let revealQueue: string[] = [], revealTimer: ReturnType<typeof setInterval> | null = null;
let tilesBuilt: Record<string, { dataSrc: string; tile: (typeof S.tiles)[number] }> = {};

function resetRing() {
  if (ringTimer) clearTimeout(ringTimer);
  ringTimer = null;
  if (revealTimer) clearInterval(revealTimer);
  revealTimer = null;
  revealQueue = [];
  S.ring = { cls: 'ring pct', shown: 0, target: 0, num: '0', of: 'הושלמו', arcOffset: '', arcOpacity: '0' };
}

function progressOf(d: Batch) {
  const total = d.total_pages || 0;
  const expected = total || d.expected_pages || d.found_pages || 0;
  if (!expected) return 0;
  const prepared = total ? 1 : Math.min(1, (d.found_pages || 0) / expected);
  const reading = (d.pages || []).filter((p) => p.status === 'reading').length;
  const read = total ? Math.min(1, ((d.processed_pages || 0) + reading / 2) / total) : 0;
  return Math.min(99, Math.floor(PREPARE_SHARE * prepared + (100 - PREPARE_SHARE) * read));
}

function drawRing() {
  S.ring.arcOffset = String(RING_C * (1 - Math.min(1, S.ring.shown / 100)));
  S.ring.arcOpacity = S.ring.shown ? '' : '0';   // בלי נקודה תקועה למעלה כשעוד אין התקדמות
  S.ring.num = String(S.ring.shown);
  emit();
}

function countTo(pct: number) {
  S.ring.target = Math.max(S.ring.target, pct);
  if (calm) { S.ring.shown = S.ring.target; drawRing(); return; }
  if (ringTimer) return;
  const step = () => {
    if (S.ring.shown >= S.ring.target) { drawRing(); ringTimer = null; return; }
    S.ring.shown += 1;
    drawRing();
    ringTimer = setTimeout(step, PCT_STEP);
  };
  step();
}

function startReveal() {
  if (revealTimer) return;
  revealTimer = setInterval(() => { if (revealQueue.length) revealTile(revealQueue.shift()!); }, calm ? 60 : TILE_STEP);
}

function whenCounted(me: string, cb: () => void) {
  const wait = () => {
    if (me !== S.batch) return;
    if (ringTimer || S.ring.shown < 100 || revealQueue.length) { setTimeout(wait, 120); return; }
    if (revealTimer) clearInterval(revealTimer);
    revealTimer = null;
    cb();
  };
  wait();
}

async function tick() {
  const me = S.batch;
  const res = await fetch('/api/batches/' + me);
  if (me !== S.batch || S.finishing) return;
  if (!res.ok) { clearInterval(timer); return fail('האצווה לא נמצאה'); }
  const d: Batch = await res.json();
  if (me !== S.batch || S.finishing) return;
  const total = d.total_pages || 0;
  const finished = ['completed', 'partial', 'failed'].includes(d.status);

  updateTiles(d);
  startReveal();
  S.pTitle = 'קולט את הטפסים…';
  const got = (d.pages || []).length;
  S.pCount = !got ? '' : got === 1 ? 'הטופס התקבל — המנוע קורא אותו' : got + ' טפסים התקבלו — המנוע קורא אותם';
  S.pFiles = (d.sources || []).map((s) => ({ name: s.filename, error: s.error }));
  countTo(progressOf(d));
  if (!finished) {
    // טפסים שכבר נקראו מופיעים כבר עכשיו, מתחת לטבעת — אפשר לעיין ולתקן. אישור הטופס רק בסוף,
    // כי חיבור החצאים וזיהוי טפסים מהמערכת רצים אחרי שכל העמודים נקראו (החלטת נועה, 21/10/2026).
    if (d.records.length) {
      S.pCount = 'אפשר כבר לעיין בטפסים שמוכנים ולתקן אותם — למטה';
      render(d);
    }
    emit();
    return;
  }

  // הכול נקרא: האחוז מגיע ל-100, ✓, ואז עוברים לאריחים
  clearInterval(timer);
  S.finishing = true;
  if (d.status === 'failed') { S.finishing = false; return fail(d.error || 'העיבוד נכשל'); }
  const n = d.records.length;
  countTo(100);
  whenCounted(me!, () => finish(me!, d, n, total));
}

function finish(me: string, d: Batch, n: number, total: number) {
  S.ring.cls += ' complete';
  S.pTitle = n === 1 ? 'הטופס נקלט' : 'כל ' + n + ' הטפסים נקלטו';
  S.pCount = 'עוברים לבדיקה ולאישור';
  emit();
  setTimeout(() => {
    if (me !== S.batch) return;
    // מי שבאמצע טופס: כרטיס ההתקדמות שמעליו נעלם — מתקנים את הגלילה כך שהטופס נשאר באותו מקום במסך
    const form = S.view === 'form' ? $('#formView') : null;
    const formTop = form ? form.getBoundingClientRect().top : null;
    if (form && formTop != null) afterRender(() => window.scrollBy(0, form.getBoundingClientRect().top - formTop));
    S.finishing = false;
    S.processing = false;
    S.progressVisible = false;
    if (d.status === 'partial') notice('warn', 'חלק מהקבצים לא עובדו במלואם', d.error);
    if (d.skipped_pages)
      notice('warn', 'נחצתה תקרת הטפסים', d.skipped_pages + ' טפסים לא עובדו. עובדו ' + total +
        ' הראשונים. ניתן לשנות את MAX_PAGES_PER_BATCH בקובץ .env.');
    (d.warnings || []).forEach((w) => notice('warn', 'לתשומת לב', w));
    lastSignature = '';
    render(d, true);
    remember();
    if (S.view !== 'form') afterRender(() => $('#results')?.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' }));
    emit();
  }, calm ? 250 : 400);
}

// אריחי הרקע של מסך הטעינה: שלד מהבהב בתור, והטופס עולה כשהוא נקרא. לא לחיצים.
function updateTiles(d: Batch) {
  (d.pages || []).forEach((p) => {
    const id = p.source_index + '-' + p.page_number;
    let built = tilesBuilt[id];
    if (!built) {
      const dataSrc = '/api/batches/' + S.batch + '/sources/' + p.source_index + '/pages/' + p.page_number + '/thumb';
      // העמוד כבר מוכן (נכנס ל-pages רק אחרי ההכנה) — התמונה הממוזערת מוצגת מיד, עוד לפני שהמנוע קרא
      const tile = { id, src: '', cls: '', revealed: false, className: 'tile queued', thumb: dataSrc } as any;
      built = tilesBuilt[id] = { dataSrc, tile };
      S.tiles.push(tile);
    }
    const tile = built.tile as any;
    const record = p.record_id ? d.records.find((r) => r.record_id === p.record_id) : null;
    let cls = p.status === 'reading' ? 'reading' : 'queued';
    if (p.status === 'done') cls = 'shown ' + (record && record.review_count ? 'review' : 'done');
    if (p.status === 'failed') cls = 'shown failed';
    if (p.status === 'skipped') cls = 'shown';
    if (tile.cls === cls) return;
    tile.cls = cls;
    if (cls.indexOf('shown') !== 0 || tile.revealed) { tile.className = 'tile ' + cls; return; }
    // טופס שהסתיים לא קופץ מיד: נכנס לתור, והספירה מעלה אותו בתורו (בינתיים התמונה כבר נטענת)
    if (!tile.src) tile.src = built.dataSrc;
    if (!revealQueue.includes(id)) revealQueue.push(id);
  });
}

function revealTile(id: string) {
  const built = tilesBuilt[id];
  if (!built) return;
  const tile = built.tile as any;
  tile.revealed = true;
  tile.className = 'tile ' + tile.cls;
  emit();
}

/* ------------------------------------------------------------ תצוגה כללית */
export function visibleRecords(): Rec[] {
  if (!S.last) return [];
  // בזמן הקריאה הטפסים מגיעים לפי מי שסיים ראשון — במסך הם לפי סדר העמודים, כך ש"הבא" הולך לפי הסדר
  const records = S.processing
    ? S.last.records.slice().sort((a, b) => a.source_index - b.source_index || a.page_number - b.page_number)
    : S.last.records;
  return records.filter((r) => {
    if (S.view === 'form' && r.record_id === S.pinnedId) return true;
    if (S.processing && waitingForJoin(r)) return false;
    if (S.filter === 'review' && !(r.review_count > 0)) return false;
    if (S.filter === 'approved' && !isApproved(r)) return false;
    if (S.senderFilter && senderOf(r) !== S.senderFilter) return false;
    if (S.query) {
      const text = S.fields.map((f) => r.fields[f.key].values.join(' ')).join(' ') + ' ' + r.source_name;
      if (text.toLowerCase().indexOf(S.query) === -1) return false;
    }
    return true;
  });
}

// בזמן הקריאה: עמוד בלי שם, ת"ז או מרכזי — אולי חצי תחתון של טופס. מוצג כ"ממתין לחיבור" עד הסוף.
export function waitingForJoin(r: Rec) { return S.processing && !r.error && !hasPatient(r); }

let lastSignature = '';
function signature(d: Batch) {
  return JSON.stringify(d.records.map((r) => [r.record_id, r.verified, S.fields.map((f) => {
    const x = r.fields[f.key]; return [x.state, x.values, x.reasons, x.help && x.help.status];
  })]));
}

export type Chips = {
  records: number; review: number; nAll: number; nReview: number; nApproved: number; navReviewN: string;
  navReviewDisabled: boolean; exportReady: boolean; exportWhy: string; senders: string[];
};
export const chips: Chips = {
  records: 0, review: 0, nAll: 0, nReview: 0, nApproved: 0, navReviewN: '', navReviewDisabled: true,
  exportReady: false, exportWhy: '', senders: [],
};
export let downloadReady = false;

export function render(d: Batch, force?: boolean) {
  // תשובה מאוחרת של העלאה שכבר יצאנו ממנה — לא מציירים אותה מחדש
  if (d && d.batch_id && d.batch_id !== S.batch) return;
  S.last = d;
  S.resultsVisible = true;
  updateChips(d);
  setStep(!S.processing && d.records.length && d.review_total === 0 ? 3 : 2);
  const sig = signature(d) + '|' + S.filter + '|' + S.senderFilter + '|' + S.query + '|' + S.view;
  // רענון אוטומטי לא מוחק את מה שהמזכירה מקלידה; אחרי פעולה שלה (force) — תמיד מציירים מחדש
  if (!force && (sig === lastSignature || S.editor)) { emit(); return; }
  lastSignature = sig;
  if (!['table', 'tiles', 'form'].includes(S.view)) S.view = 'tiles';
  closePop();
  S.followBox = null;
  maybeCelebrate(d);
  emit();
}
export function forceRender(d?: Batch | null) { lastSignature = ''; if (d || S.last) render((d || S.last)!, true); }

function unverifiedCount(d: Batch) { return d.unverified != null ? d.unverified : d.records.filter((r) => !r.verified).length; }

function updateChips(d: Batch) {
  const reviewForms = d.records.filter((r) => r.review_count > 0).length;
  const open = unverifiedCount(d);
  // ייצוא רק אחרי שכל שדה כתום טופל וכל טופס אושר ב"אישור הטופס" (השרת אוכף את אותו כלל)
  const ready = !S.processing && d.records.length > 0 && d.review_total === 0 && open === 0;
  const why = !d.records.length ? 'אין טפסים לייצוא' : S.processing ? 'הייצוא ייפתח בסוף הקריאה'
    : d.review_total ? 'נשארו ' + d.review_total + ' שדות לבדיקה לפני ייצוא'
    : open ? (open === 1 ? 'נשאר טופס אחד לאישור לפני ייצוא' : 'נשארו ' + open + ' טפסים לאישור לפני ייצוא') : '';
  Object.assign(chips, {
    records: d.records.length, review: d.review_total, nAll: d.records.length, nReview: reviewForms,
    nApproved: d.records.filter(isApproved).length, navReviewN: reviewForms ? String(reviewForms) : '',
    navReviewDisabled: !d.records.length, exportReady: ready, exportWhy: why,
    senders: Array.from(new Set(d.records.map(senderOf).filter(Boolean))).sort(),
  });
  if (!ready) downloadReady = false;
}

export function setView(v: 'tiles' | 'table' | 'form') {
  S.view = v;
  save('view', v);
  forceRender();
  remember();
}

// חיפוש: התיבה בפס העליון והתיבה בפאנל הסינון מסונכרנות
let queryTimer: ReturnType<typeof setTimeout> | undefined;
export function typeQuery(value: string) {
  S.queryText = value;
  emit();
  clearTimeout(queryTimer);
  queryTimer = setTimeout(() => {
    S.query = value.trim().toLowerCase();
    S.formIndex = 0;
    S.pinnedId = null;
    if (S.last) render(S.last, true);
  }, 180);
}
export function setFilter(f: string) {
  S.filter = f;
  S.formIndex = 0;
  S.pinnedId = null;
  if (S.last) render(S.last, true); else emit();
}
export function setSenderFilter(v: string) {
  S.senderFilter = v;
  S.formIndex = 0;
  S.pinnedId = null;
  if (S.last) render(S.last, true); else emit();
}

/* ------------------------------------------------------------ תפריט הגלולות */
export function navUpload() {
  if (showIntake()) { remember(); emit(); return; }  // מהרשומות — רק חוזרים למסך, לא קליטה חדשה
  if (S.last && S.last.records && S.last.records.length) return startNewIntake();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
// מכל מקום: ההעלאה שבמסך; אחרי "קליטת טפסים" — חזרה להעלאה הקודמת; אחרת — הממתינים לאישור
export async function navReview() {
  if (!S.last || !S.last.records.length) {
    if (S.processing) return toast('הטפסים של ההעלאה הזו יופיעו כאן ברגע שהראשון מוכן');
    if (S.prevBatch && await backToBatch(S.prevBatch)) return;
    S.prevBatch = null;
    S.rec.mode = 'pending';
    S.rec.loaded = false;
    showRecordsPage();
    return;
  }
  if (showIntake()) remember();
  setFilter('review');
  afterRender(() => $('#results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  emit();
}
async function backToBatch(id: string) {
  const res = await fetch('/api/batches/' + id).catch(() => null);
  if (!res || !res.ok) return false;  // השרת הופעל מחדש — הטפסים שלא אושרו מחכים בממתינים
  const d: Batch = await res.json();
  showIntake();
  S.prevBatch = null;
  if (!['completed', 'partial', 'failed'].includes(d.status)) {
    showProgress('קולט את הטפסים…', (d.sources || []).map((s) => s.filename));
    begin({ batch_id: id });
  } else {
    adoptBatch(d, true);
    setFilter('review');
    remember();
  }
  return true;
}
export function navExport(opener: HTMLElement) {
  if (showIntake()) remember();
  openExportPreview(opener);
}

/* ------------------------------------------------------------ תקריב צף (טבלה) */
let popTimer: ReturnType<typeof setTimeout> | undefined;
export function schedulePop(kind: 'cell' | 'page', rec: string, key: string, delay: number) {
  clearTimeout(popTimer);
  if (S.pop && S.pop.rec === rec && S.pop.key === key && S.pop.kind === kind) return;
  popTimer = setTimeout(() => openPop(kind, rec, key), delay);
}
export function scheduleClose() {
  clearTimeout(popTimer);
  popTimer = setTimeout(closePop, 220);
}
export function cancelPopTimer() { clearTimeout(popTimer); }
export function closePop() {
  clearTimeout(popTimer);
  if (!S.pop) return;
  S.pop = null;
  emit();
}
export function openPop(kind: 'cell' | 'page', rec: string, key: string) {
  if (S.editor && S.editor.kind === 'cell' && S.editor.rec === rec && S.editor.key === key) return;
  if (!recordById(rec)) return;
  // המיקום נמדד אחרי שהתוכן מצויר (Pop) — עד אז מוסתר
  S.pop = { kind, rec, key, left: 0, top: 0, below: false, arrow: 0, arrowY: 0, shown: false };
  emit();
}

/* ------------------------------------------------------------ מצב טופס */
export function currentRecord(): Rec | null {
  const list = visibleRecords();
  if (!list.length) return null;
  // הטופס הפתוח נשאר אותו טופס גם כשטפסים חדשים מצטרפים, הסדר מתעדכן בסוף, או החצי שלו חובר
  if (S.view === 'form' && S.pinnedId) {
    let at = list.findIndex((r) => r.record_id === S.pinnedId);
    if (at < 0) at = list.findIndex((r) => (r.joined_from || []).includes(S.pinnedId!));
    if (at >= 0) S.formIndex = at;
  }
  S.formIndex = Math.max(0, Math.min(S.formIndex, list.length - 1));
  if (S.view === 'form') S.pinnedId = list[S.formIndex].record_id;
  return list[S.formIndex];
}
export function firstOpenCard(r: Rec) {
  const i = S.fields.findIndex((f) => r.fields[f.key].state === 'needs_review');
  return i < 0 ? 0 : i;
}

export function focusCard(i: number, noScroll?: boolean) {
  S.cardIndex = i;
  S.followBox = null;
  emit();
  afterRender(() => {
    const card = document.querySelector('#fvCards .fcard[data-i="' + i + '"]') as HTMLElement | null;
    if (card) {
      card.focus({ preventScroll: true });
      if (!noScroll) card.scrollIntoView({ block: 'nearest', behavior: calm ? 'auto' : 'smooth' });
    }
  });
}

export function goForm(delta: number) {
  const list = visibleRecords();
  const next = S.formIndex + delta;
  if (!delta || next < 0 || next >= list.length) return;
  // הטופס היעד נבחר לפי הרשימה של עכשיו; הטופס שעוזבים עשוי לצאת מהסינון
  const target = list[next];
  S.pinnedId = target.record_id;
  S.formIndex = Math.max(0, visibleRecords().indexOf(target));
  S.zoomOpen = false;
  S.cardIndex = firstOpenCard(target);
  focusCard(S.cardIndex, true);
  afterRender(() => $('#formView')?.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' }));
  remember(true);  // מעבר בין טפסים מחליף את הרשומה בהיסטוריה
}

export function backFromForm() {
  if (S.formFrom === 'records') { showRecordsPage(S.openedKey); return; }  // חזרה לפאנל, לשורה שנפתחה
  const r = currentRecord();
  const from = S.formFrom;
  setView(from);
  afterRender(() => {
    const sel = from === 'table' ? 'td.formcol[data-rec="' : '.ftile[data-rec="';
    const t = r && document.querySelector(sel + r.record_id + '"]') as HTMLElement | null;
    if (t) { t.scrollIntoView({ block: 'center' }); t.focus({ preventScroll: true }); }
  });
  emit();
}

export function openRecord(recordId: string, from?: 'tiles' | 'table' | 'records') {
  S.filter = 'all';
  S.senderFilter = '';
  S.query = '';
  S.queryText = '';
  if (from) S.formFrom = from;
  else if (S.view !== 'form') S.formFrom = S.view === 'table' ? 'table' : 'tiles';
  S.view = 'form';
  S.zoomOpen = false;
  S.pinnedId = recordId;
  const list = visibleRecords();
  S.formIndex = Math.max(0, list.findIndex((r) => r.record_id === recordId));
  S.cardIndex = list[S.formIndex] ? firstOpenCard(list[S.formIndex]) : 0;
  forceRender();
  focusCard(S.cardIndex, true);
  afterRender(() => $('#formView')?.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' }));
  remember();
}

export function clickCard(i: number) {
  S.zoomOpen = true;
  focusCard(i, true);
}
export function closeZoom() {
  if (!S.zoomOpen) return;
  S.zoomOpen = false;
  emit();
  afterRender(() => (document.querySelector('#fvCards .fcard[data-i="' + S.cardIndex + '"]') as HTMLElement | null)?.focus({ preventScroll: true }));
}

export function turnView() {
  const r = S.view === 'form' ? currentRecord() : null;
  if (!r) return;
  S.viewTurn[r.record_id] = ((S.viewTurn[r.record_id] || 0) + 90) % 360;
  emit();
}
export function toggleInk() {
  S.inkView = !S.inkView;
  save('inkView', S.inkView ? '1' : '0');
  focusCard(S.cardIndex, true);
}

/* ------------------------------------------------------------ עריכה */
// עורך אחד פתוח בכל רגע. פתיחת עורך חדש סוגרת קודם את הקודם (בלי לשמור).
export function startEdit(kind: 'cell' | 'card', rec: string, key: string) {
  const r = recordById(rec);
  if (!r) return;
  if (S.editor && S.editor.kind === kind && S.editor.rec === rec && S.editor.key === key) return;
  closePop();
  if (!askStoredEdit(rec, () => startEdit(kind, rec, key))) return;
  if (kind === 'card' && !S.zoomOpen) S.zoomOpen = true;
  const raw = r.fields[key].values.join(' | ');
  S.editor = { kind, rec, key, raw, text: raw, invalid: false };
  emit();
}
export function finishEdit(saveIt: boolean, again?: boolean) {
  const ed = S.editor;
  if (!ed) return;
  S.editor = null;
  if (saveIt) {
    saveValues(ed.rec, ed.key, splitValues(ed.text), ed.raw, ed.kind === 'card', again ? () => {
      // Enter על ערך שהשרת דחה (rules.py) — העורך נפתח שוב עם מה שהוקלד, לא נאבד
      S.editor = { ...ed, invalid: true };
      forceRender();
    } : null);
  } else {
    forceRender();
    if (ed.kind === 'card') focusCard(S.cardIndex);
  }
  // בטבלה: הפוקוס חוזר לתא (כמו קודם — Tab ממשיך מאותו מקום)
  if (ed.kind === 'cell') afterRender(() => {
    if (S.editor) return;
    (document.querySelector('td.cell[data-rec="' + ed.rec + '"][data-key="' + ed.key + '"]') as HTMLElement | null)?.focus({ preventScroll: true });
  });
  emit();
}

/* ------------------------------------------------------------ פעולות מול השרת */
// הגרסה שהמסך ראה: אם הטופס השתנה בינתיים (חלון אחר), השרת מסרב במקום לדרוס
function versionOf(recordId: string) { const r = recordById(recordId); return r ? r.version : undefined; }

export async function refuseStale(res: Response, fallback?: string) {
  const d = await res.json().catch(() => ({}));
  toast(d.detail || fallback || 'הפעולה לא נשמרה — נסו שוב');
  if (res.status === 409 && /השתנה/.test(d.detail || '') && S.batch) {
    const fresh = await fetch('/api/batches/' + S.batch);
    if (fresh.ok) forceRender(await fresh.json());
  }
}

let lastRefused = 0;  // 422 = הערך אינו עומד בכללי המערכת (rules.py)
async function patch(recordId: string, body: Record<string, unknown>): Promise<Batch | null> {
  const res = await fetch('/api/records/' + recordId, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ version: versionOf(recordId) }, body)),
  });
  lastRefused = res.ok ? 0 : res.status;
  if (!res.ok) { await refuseStale(res); return null; }
  return res.json();
}

export async function approveField(recordId: string, key: string, fromCard?: boolean) {
  const d = await patch(recordId, { field: key, approve: true });
  if (!d) return;
  forceRender(d);
  closePop();
  toast('✓ ' + fieldLabel(key) + ' אושר', { undo: () => undo(recordId, key) });
  if (fromCard || S.view === 'form') advanceCard();
}

export async function saveValues(recordId: string, key: string, values: string[], before: string, fromCard: boolean,
                                 again?: (() => void) | null) {
  if (values.join(' | ') === before) { forceRender(); return; }
  const d = await patch(recordId, { field: key, values });
  if (!d) {
    if (again && lastRefused === 422) again(); else forceRender();
    return;
  }
  forceRender(d);
  toast('✎ ' + fieldLabel(key) + ' עודכן', { undo: () => undo(recordId, key) });
  if (fromCard || S.view === 'form') advanceCard();
}

// אחרי פעולה במצב טופס — קופצים לשדה הכתום הבא באותו טופס
function advanceCard() {
  if (S.view !== 'form') return;
  const r = currentRecord();
  if (!r) return;
  const order = S.fields.map((_, i) => (S.cardIndex + 1 + i) % S.fields.length);
  const next = order.find((i) => r.fields[S.fields[i].key].state === 'needs_review');
  focusCard(next == null ? S.cardIndex : next);
}

export async function undo(recordId: string, key: string) {
  const res = await fetch('/api/records/' + recordId + '/undo', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ field: key, version: versionOf(recordId) }),
  });
  if (!res.ok) return toast('אין מה לבטל');
  forceRender(await res.json());
  toast('↶ ' + fieldLabel(key) + ' — בוטל');
}

export async function verify(recordId: string) {
  const res = await fetch('/api/records/' + recordId + '/verify', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version: versionOf(recordId) }),
  });
  if (!res.ok) return refuseStale(res, 'עוד לא אפשר לאשר את הטופס');
  const d = await res.json();
  S.approvedHere = true;
  forceRender(d);
  toast('✓ הטופס אושר ונשמר כמאומת');
  // לטופס הבא — אם יש. בטופס האחרון נשארים, והחגיגה וחלונית הייצוא נפתחות מעצמן
  if (S.view === 'form' && S.formIndex < visibleRecords().length - 1) goForm(1);
}

/* ------------------------------------------------------------ חיבור, הפרדה ופיצול — בחינם */
export async function joinAction(url: string, body: unknown, done: string): Promise<boolean> {
  const res = await fetch(url, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : { method: 'POST' });
  const d = await res.json().catch(() => ({}));
  if (!res.ok) { toast(d.detail || 'הפעולה לא הצליחה'); return false; }
  // נשארים על הטופס שנוצר (או על החלק העליון אחרי הפרדה)
  S.last = d;
  const at = visibleRecords().findIndex((x) => x.record_id === d.focus);
  if (at >= 0) S.formIndex = at;
  forceRender(d);
  toast(done);
  return true;
}
export function joinCandidates(r: Rec) {
  return (S.last ? S.last.records : []).filter((x) => x.can_join && x.record_id !== r.record_id);
}

// הפוקוס בפתיחה — כמו קודם: בחלונות "לא הפיך / עולה כסף" ברירת המחדל היא ביטול
const FIRST_FOCUS: Record<string, string> = {
  stored: '#stCancel', del: '#delCancel', reread: '#rrCancel', split: '#spCancel', join: '#jpList .jpitem',
};
let modalOpener: HTMLElement | null = null;
export function openModal(name: typeof S.modal) {
  closePop();
  modalOpener = document.activeElement as HTMLElement | null;
  S.modal = name;
  document.body.style.overflow = 'hidden';
  emit();
  if (FIRST_FOCUS[name]) afterRender(() => $(FIRST_FOCUS[name])?.focus());
}
export function closeModal() {
  S.modal = '';
  document.body.style.overflow = '';
  emit();
  // חזרה לכפתור שפתח את החלון (אם הוא עדיין בדף)
  const o = modalOpener;
  modalOpener = null;
  if (o && o !== document.body) afterRender(() => { if (document.body.contains(o) && !S.modal) o.focus({ preventScroll: true }); });
}

export function openSplitPick(r: Rec) { S.split = { rec: r, axis: 'y' }; openModal('split'); }
export async function splitPicked() {
  const r = S.split.rec;
  if (!r) return false;
  const ok = await joinAction('/api/records/' + r.record_id + '/split-page', { axis: S.split.axis },
    '✓ העמוד פוצל לשני טפסים — כל השדות לבדיקה. אפשר לבטל פיצול');
  if (ok) closeModal();
  return ok;
}

export function openJoinPick(r: Rec) { S.join = { rec: r, other: null }; openModal('join'); }
export function pickPart(id: string) {
  S.join.other = (S.last ? S.last.records : []).find((x) => x.record_id === id) || null;
  emit();
}
export function joinThisOnTopDefault() {
  const { rec, other } = S.join;
  return !!rec && !!other && (hasPatient(rec) || !hasPatient(other));
}
export async function joinPicked(thisOnTop: boolean) {
  const { rec, other } = S.join;
  if (!rec || !other) return;
  const top = thisOnTop ? rec.record_id : other.record_id;
  const ok = await joinAction('/api/records/' + rec.record_id + '/join', { other: other.record_id, top },
    '✓ שני החלקים חוברו לטופס אחד');
  if (ok) closeModal();
}

/* ------------------------------------------------------------ טופס שהגיע מהמערכת: שואלים לפני שינוי */
function askStoredEdit(recordId: string, go: () => void) {
  const r = recordById(recordId);
  if (!r || !r.from_store || !r.verified || S.storedEditOk[recordId]) return true;
  S.storedGo = () => { S.storedEditOk[recordId] = true; go(); };
  openModal('stored');
  return false;
}
export function storedYes() {
  const go = S.storedGo;
  S.storedGo = null;
  closeModal();
  if (go) go();
}

/* ------------------------------------------------------------ קריאה חוזרת — רק אחרי "כן" */
let rrTimer: ReturnType<typeof setInterval> | undefined;
export function askReread(recordId: string) { S.rerFor = recordId; openModal('reread'); }
export async function rereadYes() {
  const id = S.rerFor;
  if (!id) return;
  S.rereadBusy = true;
  emit();
  const res = await fetch('/api/records/' + id + '/reread', { method: 'POST' });
  S.rereadBusy = false;
  closeModal();
  const d = await res.json().catch(() => ({}));
  if (!res.ok) return toast(d.detail || 'לא הצלחנו להתחיל קריאה חוזרת');
  forceRender(d);
  watchReread(id);
}
function watchReread(id: string) {
  clearInterval(rrTimer);
  const me = S.batch;
  rrTimer = setInterval(async () => {
    if (me !== S.batch) return clearInterval(rrTimer);
    const res = await fetch('/api/batches/' + me);
    if (!res.ok) return clearInterval(rrTimer);
    const d: Batch = await res.json();
    if ((d.pages || []).some((p) => p.record_id === id && p.status === 'reading')) return;
    clearInterval(rrTimer);
    forceRender(d);
    const r = d.records.find((x) => x.record_id === id);
    toast(r && !r.error ? '✓ הטופס נקרא שוב' : 'גם הקריאה החוזרת נכשלה');
  }, 2000);
}
export function rereading(r: Rec) {
  return !!(S.last && (S.last.pages || []).some((p) => p.record_id === r.record_id && p.status === 'reading'));
}

/* ------------------------------------------------------------ הגדלת הטופס על כל המסך */
export const pzReq = { rec: null as Rec | null, key: '', opener: null as HTMLElement | null };
export function openPageZoom(opener: HTMLElement | null, rec?: Rec | null, fieldKey?: string) {
  const r = rec || currentRecord();
  if (!r) return;
  pzReq.rec = r;
  pzReq.key = fieldKey || S.fields[S.cardIndex].key;
  pzReq.opener = opener;
  openModal('pz');
}
export function closePageZoom() {
  if (S.modal !== 'pz') return;
  closeModal();
  const back = pzReq.opener && document.body.contains(pzReq.opener) ? pzReq.opener : $('#fvFrame .pv-page');
  afterRender(() => back?.focus({ preventScroll: true }));
}

/* ------------------------------------------------------------ ייצוא: תצוגה מקדימה ואז הורדה */
let xlOpener: HTMLElement | null = null;
export async function openExportPreview(opener?: HTMLElement | null) {
  if (!chips.exportReady || !S.batch) return;
  xlOpener = opener || $('#download');
  const res = await fetch('/api/batches/' + S.batch + '/export/preview');
  if (!res.ok) return toast('לא הצלחנו להכין תצוגה מקדימה — נסו שוב');
  S.xl = { data: await res.json(), done: false };
  openModal('xl');
  afterRender(() => { const w = $('.xlwrap'); if (w) w.scrollTop = 0; $('#xlDownload')?.focus(); });
}
export function closeExportPreview() {
  closeModal();
  if (xlOpener && !(xlOpener as HTMLButtonElement).disabled) { const o = xlOpener; afterRender(() => o.focus()); }
}
export async function downloadExcel() {
  if (!chips.exportReady) return false;
  const res = await fetch('/api/batches/' + S.batch + '/export', { method: 'POST' });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    toast(e.detail || 'הייצוא נכשל — נסו שוב');
    return false;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'commitments-' + S.batch + '.xlsx';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  downloadReady = false;
  toast('⬇ קובץ ה-Excel ירד');
  return true;
}

/* ------------------------------------------------------------ קליטה חדשה */
// לחיצה אחת פותחת את מסך הגרירה. טפסים שלא אושרו נשמרים כ"ממתינים לאישור".
export function startNewIntake() {
  const open = !S.processing && S.last && S.last.records ? S.last.records.filter((r) => !r.verified).length : 0;
  resetToUpload();
  if (open) toast((open === 1 ? 'טופס אחד שלא אושר נשמר' : open + ' טפסים שלא אושרו נשמרו') +
    ' ב"רשומות במערכת" תחת "ממתינים לאישור"', { ms: 5000 });
}
export function resetToUpload() {
  parkNow();
  clearInterval(timer);
  if (S.batch) S.prevBatch = S.batch;
  chips.navReviewDisabled = true;
  chips.navReviewN = '';
  S.batch = null;
  S.last = null;
  S.processing = false;
  S.finishing = false;
  S.editor = null;
  closePop();
  clearNotices();
  S.resultsVisible = false;
  S.progressVisible = false;
  downloadReady = false;
  const file = $('#file') as HTMLInputElement | null;
  if (file) file.value = '';
  S.dropVisible = true;
  setStep(1);
  S.view = load('view', 'tiles') === 'table' ? 'table' : 'tiles';
  window.scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' });
  remember();
  emit();
  setTimeout(refreshPendingCount, 800);  // אחרי שהטפסים שלא אושרו נשמרו כממתינים
}

// הדף נסגר / מוסתר — הטפסים שלא אושרו נשמרים כממתינים
export function parkNow() {
  if (!S.batch || !S.last || S.processing || !S.last.records.some((r) => !r.verified)) return;
  try { navigator.sendBeacon('/api/batches/' + S.batch + '/park'); } catch { /* הדפדפן נסגר */ }
}

/* ------------------------------------------------------------ רגע הצלחה */
const EXPORT_AFTER_PARTY = 2000;
function maybeCelebrate(d: Batch) {
  if (!S.approvedHere || S.processing || S.celebrated || !d.records.length || d.review_total !== 0 || unverifiedCount(d) !== 0) return;
  if (!d.records.some((r) => !r.error)) return;
  S.celebrated = true;
  downloadReady = true;
  toast('🎉 כל ' + d.records.length + ' הטפסים אושרו — מוכנים לייצוא', { big: true, ms: 5000 });
  const me = S.batch;
  setTimeout(() => {
    if (me === S.batch && chips.exportReady && S.modal !== 'xl') openExportPreview($('#download'));
  }, calm ? 300 : EXPORT_AFTER_PARTY);
  if (calm) return;
  const colors = ['#0068f5', '#0c306a', '#00a8cc', '#c2d96b', '#48c0d8'];
  S.partyPieces = Array.from({ length: 46 }, (_, i) => ({
    left: Math.random() * 100, color: colors[i % colors.length], delay: Math.random() * 0.6,
  }));
  setTimeout(() => { S.partyPieces = []; emit(); }, 3400);
}

/* ------------------------------------------------------------ סיור קצר */
export function openTour() { S.tourStep = 0; S.modal = 'tour'; emit(); afterRender(() => $('#tourNext')?.focus()); }
export function closeTour() { if (S.modal === 'tour') S.modal = ''; save('toured', '1'); emit(); }

/* ------------------------------------------------------------ רשומות במערכת */
const REC_RANGES: Record<string, string> = { today: 'היום', yesterday: 'אתמול', week: 'ב-7 הימים האחרונים', month: 'החודש' };
let recTimer: ReturnType<typeof setTimeout> | undefined;

export function recDays(): [string, string] {
  const R = S.rec, today = isoDay(new Date());
  if (R.range === 'yesterday') return [shift(-1), shift(-1)];
  if (R.range === 'week') return [shift(-6), today];
  if (R.range === 'month') return [today.slice(0, 8) + '01', today];
  if (R.range === 'custom') {
    let from = R.from || today, to = R.to || from;
    if (to < from) { const t = from; from = to; to = t; }
    return [from, to];
  }
  return [today, today];
}
export function recQuery(extra?: Record<string, string | number>) {
  const R = S.rec, days = recDays(), end = dayOf(days[1]);
  end.setDate(end.getDate() + 1);
  const p = new URLSearchParams({ start: dayOf(days[0]).toISOString(), end: end.toISOString() });
  if (R.q) p.set('q', R.q);
  if (R.sender) p.set('sender', R.sender);
  if (R.rfrom) p.set('received_from', R.rfrom);
  if (R.rto) p.set('received_to', R.rto);
  Object.keys(extra || {}).forEach((k) => p.set(k, String(extra![k])));
  return p.toString();
}
export function recWhen() {
  if (S.rec.range !== 'custom') return REC_RANGES[S.rec.range];
  const days = recDays();
  return days[0] === days[1] ? 'ב-' + heDay(days[0]) : 'בין ' + heDay(days[0]) + ' ל-' + heDay(days[1]);
}
function pendingQuery() {
  const p = new URLSearchParams();
  if (S.rec.q) p.set('q', S.rec.q);
  if (S.rec.sender) p.set('sender', S.rec.sender);
  return p.toString();
}

// focusKey: חזרה מטופס שנפתח מכאן — השורה שלו מסומנת ובמרכז המסך
export function showRecordsPage(focusKey?: string) {
  closePop();
  S.page = 'records';
  remember();
  refreshPendingCount();
  const back = typeof focusKey === 'string' && focusKey ? focusKey : '';
  if (!back) window.scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' });
  emit();
  return loadRecords().then(() => {
    afterRender(() => {
      const tr = back && document.querySelector('#recBody tr[data-key="' + back + '"]') as HTMLElement | null;
      if (tr) { tr.scrollIntoView({ block: 'center' }); tr.focus({ preventScroll: true }); }
    });
    emit();
  });
}
export function showIntake() {
  if (S.page !== 'records') return false;
  S.page = 'intake';
  return true;
}

export async function loadRecords() {
  const pending = S.rec.mode === 'pending';
  const asked = pending ? 'pending?' + pendingQuery() : 'saved?' + recQuery();
  S.rec.loading = true;
  emit();
  const res = await fetch('/api/' + asked).catch(() => null);
  if (asked !== (S.rec.mode === 'pending' ? 'pending?' + pendingQuery() : 'saved?' + recQuery())) return;  // הסינון השתנה בינתיים
  S.rec.loading = false;
  const d = res ? await res.json().catch(() => ({})) : {};
  if (!res || !res.ok) { emit(); return toast(d.detail || 'לא הצלחנו לטעון את הטפסים'); }
  S.rec.loaded = true;
  S.rec.data = { ...d, pending };
  if (pending) S.rec.pendingN = d.all || '';
  emit();
}
export async function refreshPendingCount() {
  try {
    const res = await fetch('/api/pending/count');
    if (res.ok) { S.rec.pendingN = (await res.json()).count || ''; emit(); }
  } catch { /* לא חיוני */ }
}
export function setRecMode(mode: string) {
  S.rec.mode = mode;
  if (mode === 'pending') S.rec.more = false;
  S.rec.loaded = false;
  refreshPendingCount();
  loadRecords();
}
export function pickRange(range: string, focus?: boolean) {
  S.rec.range = range;
  if (range === 'custom' && !S.rec.from) S.rec.from = isoDay(new Date());
  if (focus) afterRender(() => (document.querySelector('#recRange button[data-range="' + range + '"]') as HTMLElement | null)?.focus());
  loadRecords();
}
export function recTyped(q: string) {
  S.rec.q = q.trim();
  (S.rec as any).qText = q;
  emit();
  clearTimeout(recTimer);
  recTimer = setTimeout(loadRecords, 250);
}
export function clearChip(which: string) {
  if (which === 'q' || which === 'all') { S.rec.q = ''; (S.rec as any).qText = ''; }
  if (which === 'sender' || which === 'all') S.rec.sender = '';
  if (which === 'received' || which === 'all') { S.rec.rfrom = ''; S.rec.rto = ''; }
  if (which === 'all') pickRange('today'); else loadRecords();
  afterRender(() => $('#recQ')?.focus());
}
// ייצוא: Excel בכפתור הראשי, CSV/JSON בתפריט. offset — שעון מקומי בקובץ
export function exportSaved(format: string) {
  window.location.href = '/api/saved/export?' + recQuery({ format, offset: new Date().getTimezoneOffset() });
}

export function askDelete(key: string) { S.delFor = key; openModal('del'); }
export async function deleteYes() {
  const key = S.delFor;
  if (!key) return;
  const res = await fetch('/api/saved/' + encodeURIComponent(key), { method: 'DELETE' });
  const d = await res.json().catch(() => ({}));
  S.delFor = '';
  closeModal();
  if (!res.ok) return toast(d.detail || 'המחיקה לא הצליחה');
  if (d.batch && d.batch.batch_id === S.batch) { S.last = d.batch; lastSignature = ''; }
  toast('🗑 הטופס נמחק');
  loadRecords();
}

// פתיחת טופס מאושר במסך הבדיקה — גם אחרי הפעלה מחדש של השרת, בלי קריאה חוזרת
export async function openSaved(key: string) {
  const res = await fetch('/api/saved/' + encodeURIComponent(key) + '/open', { method: 'POST' });
  const d = await res.json().catch(() => ({}));
  if (!res.ok) return toast(d.detail || 'לא הצלחנו לפתוח את הטופס');
  showIntake();
  adoptBatch(d);
  S.openedKey = key;
  S.openedPending = false;
  openRecord(d.focus, 'records');
}
// פתיחת טופס ממתין — אחרי "אישור הטופס" הוא יוצא מהממתינים
export async function openPending(recordId: string) {
  const res = await fetch('/api/pending/' + encodeURIComponent(recordId) + '/open', { method: 'POST' });
  const d = await res.json().catch(() => ({}));
  if (!res.ok) { toast(d.detail || 'לא הצלחנו לפתוח את הטופס'); return loadRecords(); }
  showIntake();
  adoptBatch(d, true);
  S.openedKey = recordId;
  S.openedPending = true;
  openRecord(d.focus, 'records');
}
// העלאה שכבר נקראה מוצגת במסך הבדיקה — בלי קריאה חוזרת. waiting: טפסים ממתינים — חגיגה כרגיל.
function adoptBatch(d: Batch, waiting?: boolean) {
  clearInterval(timer);
  clearNotices();
  S.batch = d.batch_id;
  S.processing = false;
  S.finishing = false;
  S.celebrated = !waiting;
  S.approvedHere = false;
  S.dropVisible = false;
  S.progressVisible = false;
  S.last = d;
  forceRender(d);
}

/* ------------------------------------------------------------ ניווט: "אחורה" של הדפדפן ופירורי לחם */
let restoring = false;

function screenNow() {
  if (S.page === 'records') return { page: 'records' };
  if (!S.last || !S.resultsVisible) return { page: 'intake', view: 'upload' };
  if (S.view === 'form') {
    const r = currentRecord();
    return { page: 'intake', view: 'form', batch: S.batch, rec: r ? r.record_id : '', from: S.formFrom,
             key: S.formFrom === 'records' ? S.openedKey : '', pending: S.formFrom === 'records' && S.openedPending };
  }
  return { page: 'intake', view: S.view, batch: S.batch };
}

export function remember(replace?: boolean) {
  if (restoring) return;
  const s = screenNow() as any;
  if (history.state && JSON.stringify(history.state) === JSON.stringify(s)) return;
  const hash = '#' + (s.page === 'records' ? 'records' : s.view);
  if (replace || !history.state) history.replaceState(s, '', hash);
  else history.pushState(s, '', hash);
}

async function goTo(s: any) {
  closePop();
  if (s.page === 'records') return showRecordsPage(s.key || S.openedKey);
  showIntake();
  emit();
  if (s.view === 'upload') { if (S.last) resetToUpload(); return; }
  if (s.batch && s.batch !== S.batch) {
    if (s.key) { await (s.pending ? openPending(s.key) : openSaved(s.key)); return; }
    const res = await fetch('/api/batches/' + s.batch);
    if (!res.ok) { toast('ההעלאה הזו כבר לא זמינה (ייתכן שהשרת הופעל מחדש)'); return; }
    adoptBatch(await res.json());
  }
  if (!S.last) return;
  if (s.view === 'form' && s.rec) { if (s.key) S.openedKey = s.key; openRecord(s.rec, s.from); }
  else { S.view = s.view; forceRender(); }
}

export function onPopState(e: PopStateEvent) {
  if (!e.state) return;
  if (S.processing) {  // באמצע קריאה לא עוזבים את מסך ההתקדמות
    history.pushState(screenNow(), '', '#upload');
    return toast('אפשר לחזור אחורה אחרי שהקריאה מסתיימת');
  }
  restoring = true;
  Promise.resolve(goTo(e.state)).catch(() => {}).then(() => { restoring = false; emit(); });
}

/* ------------------------------------------------------------ 🙋 עזרה: שאלה לממונה (החלטת נועה, 21/10/2026)
   בהודעה עצמה אין פרטי מטופל — רק הקישור. השדה לא מאושר עד שמגיעה תשובה, וההחלטה נשארת אצלה. */
const HELP_SUBJECT = 'בקשת עזרה בבדיקת טופס';
let haOpener: HTMLElement | null = null;

export async function helpAction(act: string, r: Rec, key: string, opener: HTMLElement | null) {
  const f = r.fields[key];
  if (act === 'ask' || act === 'share') return openHelpAsk(r, key, opener);
  if (act === 'cancel') {
    const res = await fetch('/api/records/' + r.record_id + '/help/cancel', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ field: key }),
    });
    if (!res.ok) return refuseStale(res);
    forceRender(await res.json());
    toast('הבקשה בוטלה — אפשר לתקן או לאשר את השדה');
  }
  if (act === 'use' && f.help && f.help.answer_value) {
    saveValues(r.record_id, key, [f.help.answer_value], f.values.join(' | '), S.view === 'form');
  }
}

function openHelpAsk(r: Rec, key: string, opener: HTMLElement | null) {
  const f = r.fields[key];
  haOpener = opener || (document.activeElement as HTMLElement);
  const sent = !!f.help;
  const to = recent('helpTo'), phone = recent('helpPhone');
  S.help = {
    rec: r.record_id, key, pane: '', done: false, doneText: '', doneAlt: '', linkBox: '',
    question: sent ? (f.help!.question || '') : (f.values.length > 1 ? 'איזה מהערכים נכון?' : 'מה כתוב כאן?'),
    asker: sent ? (f.help!.asker || '') : load('helpAsker', ''),
    to: S.help.to || to[0] || '', phone: S.help.phone || phone[0] || '', err: '', sent, busy: '',
  };
  openModal('help');
  afterRender(() => (sent ? $('#haPick .hachan') : $('#haQuestion'))?.focus());
}
export function closeHelpAsk() {
  closeModal();
  const o = haOpener;
  if (o && document.body.contains(o)) afterRender(() => o.focus());
}
// קודם בוחרים דרך (מייל / וואטסאפ / קישור), ואז רואים רק את המסך שלה. '' = חזרה לבחירה
export function helpPane(name: '' | 'mail' | 'wa' | 'link') {
  S.help.pane = name;
  S.help.err = '';
  emit();
  afterRender(() => {
    const ids: Record<string, string> = { mail: '#haPaneMail', wa: '#haPaneWa', link: '#haPaneLink' };
    const first = name ? document.querySelector(ids[name] + ' input, ' + ids[name] + ' .primary') as HTMLElement | null
      : $('#haPick .hachan');
    first?.focus();
  });
}
export function helpStep(done: boolean, text = '', alt = '', linkBox = '') {
  S.help.done = done;
  S.help.doneText = text;
  S.help.doneAlt = alt;
  S.help.linkBox = linkBox;
  S.help.err = '';
  if (!done) return helpPane('');
  emit();
  afterRender(() => $('#haFinish')?.focus());
}

// התקריבים שהמזכירה הזיזה, לפי סדר התקריבים של השדה (null = התקריב על השדה עצמו).
// בשמות התקריב המשותף נשמר כ-full_name.
export function helpViews(r: Rec, key: string): (number[] | null)[] {
  const f = r.fields[key];
  if (perValue(f)) return f.crop_boxes.map((_, i) => zoomViews.get(viewKey(r.record_id, key, i)) || null);
  return [zoomViews.get(viewKey(r.record_id, key))
    || (isName(key) ? zoomViews.get(viewKey(r.record_id, 'full_name')) : null) || null];
}

// יוצר את הבקשה (פעם אחת לשדה) ומחזיר את הקישור; שליחה שוב — אותו קישור (אלא אם פג)
export function helpOrigin() {
  // בענן הכתובת מגיעה מהשרת (APP_ORIGIN), כדי שמייל לעולם לא יקבל localhost
  // או כתובת Preview. מקומית לא שולחים בכלל: הטוקן המקומי אינו קיים במסד שבענן.
  const configured = String(S.config?.public_origin || '').replace(/\/+$/, '');
  if (configured) return configured;
  return /^(localhost|127\.|\[::1\])/.test(location.hostname) ? '' : location.origin.replace(/\/+$/, '');
}
async function helpLink() {
  const origin = helpOrigin();
  if (!origin) {
    helpError('כדי לשלוח קישור פעיל, פתחי את המערכת באתר שבענן.', '#haPick');
    return null;
  }
  const r = recordById(S.help.rec);
  const f = r && r.fields[S.help.key];
  if (!f) return null;
  if (f.help && !f.help.expired) return origin + '/help/' + encodeURIComponent(f.help.token);
  const asker = S.help.asker.trim();
  save('helpAsker', asker);
  const res = await fetch('/api/records/' + S.help.rec + '/help', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ field: S.help.key, question: S.help.question.trim(), asker, views: helpViews(r!, S.help.key) }),
  });
  if (!res.ok) { await refuseStale(res); return null; }
  const d = await res.json();
  forceRender(d);
  return origin + '/help/' + encodeURIComponent(d.help_token);
}

// ההודעה עצמה: מי שואלת, על איזה שדה ומה השאלה, וקישור. בלי ערכים, בלי שם מטופל, בלי ת"ז.
// טקסט בלבד: Gmail / Outlook / וואטסאפ לא מעבירים תמונה או עיצוב. וואטסאפ מציג *כך* כמודגש.
export function helpMessage(url: string, forWhatsapp?: boolean) {
  const asker = S.help.asker.trim();
  const question = S.help.question.trim();
  const b = (text: string) => (forWhatsapp ? '*' + text + '*' : text);
  return [
    'שלום,',
    '',
    (asker || 'המזכירה') + ' מבקשת את עזרתך בבדיקת טופס התחייבות.',
    '',
    b('השדה:') + ' ' + fieldLabel(S.help.key),
    question ? b('השאלה:') + ' ' + question : '',
    '',
    'לצפייה בתקריב ולתשובה בלחיצה אחת:',
    url,
    'התשובה נכנסת ישר למערכת. הקישור בתוקף 48 שעות.',
    '',
    'מטעמי פרטיות, פרטי המטופל מוצגים רק בקישור — לא בהודעה.',
    '',
    'תודה,',
    'מערכת קליטת טפסי התחייבות · המעבדה המרכזית לנגיפים',
  ].filter((line, i, all) => line !== '' || all[i - 1] !== '').join('\n');
}
function helpSubject() { return HELP_SUBJECT + ' — שדה "' + fieldLabel(S.help.key) + '"'; }

function helpError(text: string, focus: string) {
  S.help.err = text;
  emit();
  afterRender(() => $(focus)?.focus());
}

// שליחה ישירה מהשרת (mailer.py), בלי לפתוח את Gmail: השרת כותב את ההודעה, הדפדפן רק אומר למי
async function helpDirect(to: string) {
  if (!EMAIL_RE.test(to)) return helpError('כתבי למי לשלוח — כתובת המייל של הממונה.', '#haTo');
  S.help.busy = 'direct';
  emit();
  const url = await helpLink();
  if (!url) { S.help.busy = ''; return emit(); }
  const res = await fetch('/api/records/' + S.help.rec + '/help/mail', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ field: S.help.key, to }),
  }).catch(() => null);
  S.help.busy = '';
  const d = res ? await res.json().catch(() => ({})) : {};
  if (!res || !res.ok) return helpError(d.detail || 'המייל לא נשלח. נסי שוב, או פתחי מייל מוכן ב-Gmail.', '#haDirect');
  remember5('helpTo', to);
  helpStep(true, 'sent:' + to);
  toast('🙋 המייל נשלח — השדה ממתין לתשובה');
}

export async function helpSend(channel: 'direct' | 'gmail' | 'outlook' | 'mail' | 'whatsapp' | 'copy') {
  if (channel === 'direct') return helpDirect(S.help.to.trim());
  if (!S.help.rec) return;
  S.help.err = '';
  const to = S.help.to.trim();
  const phoneText = S.help.phone.trim();
  const phone = waNumber(phoneText);
  const byMail = channel === 'gmail' || channel === 'outlook' || channel === 'mail';
  if (byMail && !EMAIL_RE.test(to)) return helpError('כתבי למי לשלוח — כתובת המייל של הממונה.', '#haTo');
  if (channel === 'whatsapp' && phoneText && !phone) return helpError('מספר הנייד לא נראה תקין.', '#haPhone');
  S.help.busy = channel;
  emit();
  const url = await helpLink();
  S.help.busy = '';
  emit();
  if (!url) return;
  const subject = encodeURIComponent(helpSubject()), body = encodeURIComponent(helpMessage(url)), toq = encodeURIComponent(to);
  const target = {
    gmail: 'https://mail.google.com/mail/?view=cm&fs=1&to=' + toq + '&su=' + subject + '&body=' + body,
    outlook: 'https://outlook.office.com/mail/deeplink/compose?to=' + toq + '&subject=' + subject + '&body=' + body,
    mail: 'mailto:' + to + '?subject=' + subject + '&body=' + body,
    whatsapp: 'https://wa.me/' + phone + '?text=' + encodeURIComponent(helpMessage(url, true)),
    copy: '',
  }[channel];
  if (byMail) remember5('helpTo', to);
  if (channel === 'whatsapp' && phone) remember5('helpPhone', phoneText);
  if (channel === 'copy') {
    let copied = true;
    try { await navigator.clipboard.writeText(url); } catch { copied = false; }
    helpStep(true, copied ? 'copied' : 'copyit', '', url);
    return;
  }
  if (channel === 'mail') {
    const a = document.createElement('a');
    a.href = target;
    a.click();
    helpStep(true, 'mail:' + to, 'nomailapp');
  } else {
    window.open(target, '_blank', 'noopener');
    helpStep(true, channel + ':' + (byMail ? to : phone ? phoneText : ''), target);
  }
  toast('🙋 נשלח לעזרה — השדה ממתין לתשובה');
}

/* ------------------------------------------------------------ טיימרים כלליים */
export function startHelpWatch() {
  // כשיש שדה שממתין לתשובה — בודקים כל כמה שניות אם ענו. בלי מודל ובלי עלות.
  setInterval(async () => {
    if (!S.last || !S.batch || S.processing || document.hidden) return;
    const waiting = S.last.records.some((r) => S.fields.some((s) => {
      const f = r.fields[s.key]; return !!(f && f.help && f.help.status === 'open');
    }));
    if (!waiting) return;
    const res = await fetch('/api/batches/' + S.batch).catch(() => null);
    if (!res || !res.ok) return;
    const d: Batch = await res.json();
    d.records.forEach((r) => {
      S.fields.forEach((s) => {
        const before = recordById(r.record_id), h = r.fields[s.key].help;
        const was = before && before.fields[s.key].help;
        if (was && was.status === 'open' && h && h.status === 'answered') {
          toast('💬 התקבלה תשובה בשדה ' + s.label + (h.answered_by ? ' · ' + h.answered_by : ''));
        }
      });
    });
    render(d);
  }, 5000);
}

export { localTime };
