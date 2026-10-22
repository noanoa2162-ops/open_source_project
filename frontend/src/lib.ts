/* כלים קטנים בלי מצב — אותם כללים כמו במסך הקודם (app.js), שורה מול שורה. */

export type FieldSpec = { key: string; label: string; hint: string };
export type Help = {
  token: string; status: string; question: string; asker: string; created_at: string; expired: boolean;
  answer_value: string; answer_note: string; answered_by: string; answered_at: string; crops: number;
};
export type Field = {
  values: string[]; state: string; reasons: string[]; reason_codes: string[];
  crop_box: number[] | null; crop_boxes: (number[] | null)[]; region_note: string; can_undo: boolean;
  blocked: string[]; help: Help | null;
};
export type Rec = {
  record_id: string; source_index: number; source_name: string; page_number: number; error: string; note: string;
  review_count: number; template: string; low_resolution: boolean; verified: boolean; straightened: string;
  joined: boolean; joined_from?: string[]; split_from_joined: boolean; can_join: boolean; version: number; from_store: string;
  split_from_page: boolean; full_name_crop: boolean; fields: Record<string, Field>;
};
export type Page = { source_index: number; page_number: number; status: string; record_id: string | null };
export type Source = { index: number; filename: string; error: string };
export type Batch = {
  batch_id: string; status: string; error: string; total_pages: number; processed_pages: number;
  skipped_pages: number; found_pages: number; expected_pages: number; records: Rec[]; pages: Page[];
  sources: Source[]; review_total: number; unverified?: number; warnings: string[]; focus?: string; help_token?: string;
};

/* ------------------------------------------------------------ זיכרון קטן בדפדפן */
export function load(key: string, fallback: string): string {
  try { const v = localStorage.getItem(key); return v == null ? fallback : v; } catch { return fallback; }
}
export function save(key: string, value: string) { try { localStorage.setItem(key, value); } catch { /* דפדפן פרטי */ } }

export const calm = !!(typeof window.matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);

// ערך בלי אותיות עבריות (מספר, תאריך, "X132767") — תמיד משמאל לימין. בלי זה הדפדפן מסדר מחדש
// את החלקים שמסביב ל-"?" או ל-"/" בתוך הדף העברי, ו-"1?007" נראה "007?1".
export function isLtr(text: string | null | undefined) { return !/[֐-׿]/.test(String(text || '')); }
export const LTR_KEYS = ['national_id', 'central_numbers', 'sample_received_date', 'commitment_numbers'];
export const NAME_KEYS = ['first_name', 'last_name'];
export const PATIENT_KEYS = ['first_name', 'last_name', 'national_id', 'central_numbers'];
export function isName(key: string) { return NAME_KEYS.includes(key); }

// ערך עם "?" (תו שלא נקרא) — אי אפשר לאשר אותו, רק לתקן (גם השרת מסרב)
export function hasUnknown(f: Field | undefined) { return !!f && f.values.some((v) => v.includes('?')); }
// ערך שאינו עומד בכללי המערכת (rules.py): אין ✓ בכלל — רק תיקון, או ריק ("חסר")
export function isBlocked(f: Field | undefined) { return !!(f && f.blocked && f.blocked.length); }
export function helpWaiting(f: Field | undefined) { return !!(f && f.help && f.help.status === 'open'); }
export function canApprove(f: Field) { return f.state !== 'approved' && !hasUnknown(f) && !isBlocked(f) && !helpWaiting(f); }
// "🙋 עזרה": שדה כתום שעוד לא אושר, ושאין לו בקשה פעילה
export function canAskHelp(r: Rec, f: Field) { return !r.verified && !r.error && f.state === 'needs_review' && !f.help; }
export function whyOf(f: Field) {
  const why = (f.blocked || []).map((b) => '⛔ ' + b);
  // סיבות שהכלל כבר אומר במילים שלו — לא חוזרים עליהן
  const same = why.length ? ['not_a_value', 'breaks_rule', 'unknown_char'] : [];
  if (f.state === 'needs_review') f.reasons.forEach((x, i) => {
    if (!same.includes((f.reason_codes || [])[i])) why.push(x);
  });
  return why.join(' · ');
}
// כשיש כמה ערכים עם מיקום לכל אחד (למשל שני מספרים מרכזיים) — תקריב לכל ערך
export function perValue(f: Field | undefined) { return f && f.crop_boxes && f.crop_boxes.length > 1 ? f.crop_boxes : null; }
export function senderOf(r: Rec) { return (r.fields.sending_entity.values[0] || '').trim(); }
export function firstValue(r: Rec, key: string) { return (r.fields[key] && r.fields[key].values[0]) || ''; }
export function hasPatient(r: Rec) { return PATIENT_KEYS.some((k) => r.fields[k].values.length > 0); }
export function splitValues(text: string) { return text.split('|').map((s) => s.trim()).filter(Boolean); }
export function plural(n: number, one: string, many: string) { return n === 1 ? one : n + ' ' + many; }

/* ------------------------------------------------------------ תאריכים */
export function isoDay(d: Date) {
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
export function dayOf(iso: string) { const p = iso.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
export function heDay(iso: string) { const p = iso.split('-'); return p[2] + '/' + p[1] + '/' + p[0]; }
export function shift(days: number) { const d = new Date(); d.setDate(d.getDate() + days); return isoDay(d); }
export function localTime(iso: string) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso || '';
  return heDay(isoDay(d)) + ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

/* ------------------------------------------------------------ עזרה: נמענים ונייד */
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// הנמענים האחרונים — נשמרים רק בדפדפן הזה, לבחירה מהירה בפעם הבאה
export function recent(key: string): string[] {
  try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch { return []; }
}
export function remember5(key: string, value: string) {
  if (!value) return;
  const list = [value].concat(recent(key).filter((x) => x !== value)).slice(0, 5);
  save(key, JSON.stringify(list));
}
// נייד ישראלי → המספר הבינלאומי שוואטסאפ צריך: 050-1234567 → 972501234567
export function waNumber(text: string) {
  let digits = (text || '').replace(/\D/g, '');
  if (digits.startsWith('00')) digits = digits.slice(2);
  if (digits.startsWith('0')) digits = '972' + digits.slice(1);
  return digits.length >= 9 ? digits : '';
}
