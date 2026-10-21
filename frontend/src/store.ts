/* מצב המסך — כל מה שהיה משתנים כלליים במסך הקודם (app.js), במקום אחד. כל שינוי נגמר ב-emit(), ו-React מצייר מחדש.
 * afterRender: פעולות שצריכות את המסך החדש (פוקוס, גלילה, מדידה) — רצות מיד אחרי הציור. */
import { useSyncExternalStore } from 'react';
import type { Batch, FieldSpec, Rec } from './lib';
import { load } from './lib';

export type Notice = { id: number; kind: string; title: string; text: string };
export type Toast = { id: number; text: string; big: boolean; out: boolean; undo?: () => void };
export type Tile = { id: string; src: string; cls: string; revealed: boolean; thumb?: string };
export type Editor = { kind: 'cell' | 'card'; rec: string; key: string; raw: string; text: string; invalid: boolean };
export type PopState = { kind: 'cell' | 'page'; rec: string; key: string; left: number; top: number; below: boolean;
  arrow: number; arrowY: number; shown: boolean } | null;

export const S = {
  config: null as null | Record<string, any>,
  fields: [] as FieldSpec[],
  batch: null as string | null,
  // ההעלאה הקודמת, אחרי "קליטת טפסים" — "טפסים לבדיקה" חוזר אליה (גם באמצע קריאה)
  prevBatch: null as string | null,
  last: null as Batch | null,
  processing: false,
  finishing: false,
  view: (load('view', 'tiles') === 'table' ? 'table' : 'tiles') as 'tiles' | 'table' | 'form',
  filter: 'all',
  senderFilter: '',
  query: '',
  queryText: '',
  formIndex: 0,
  cardIndex: 0,
  // הטופס הפתוח נשאר פתוח עד שעוברים ממנו — גם אם יצא מהסינון
  pinnedId: null as string | null,
  zoomOpen: false,
  celebrated: false,
  // המזכירה עצמה לחצה "אישור הטופס" בהעלאה הזו — רק אז חגיגה וחלונית האקסל
  approvedHere: false,
  viewTurn: {} as Record<string, number>,
  inkView: load('inkView', '0') === '1',
  theme: load('theme', typeof window.matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
  size: load('size', '1'),
  brand: 'moh',
  step: 1,
  notices: [] as Notice[],
  toasts: [] as Toast[],
  // מסכים
  page: 'intake' as 'intake' | 'records',
  dropVisible: true,
  progressVisible: false,
  resultsVisible: false,
  // התקדמות
  ring: { cls: 'ring indeterminate', shown: 0, target: 0, num: '0', of: 'מכין…', arcOffset: '', arcOpacity: '' },
  pTitle: 'מעבד…',
  pCount: '',
  pFiles: [] as { name: string; error: string }[],
  tiles: [] as Tile[],
  // טופס פתוח
  formFrom: 'tiles' as 'tiles' | 'table' | 'records',
  followBox: null as number[] | null,    // המסגרת על הטופס כשגוררים את התקריב
  editor: null as Editor | null,
  focusedCell: null as { rec: string; key: string } | null,
  pop: null as PopState,
  partyPieces: [] as { left: number; color: string; delay: number }[],
  backTop: false,
  storedEditOk: {} as Record<string, boolean>,
  // חלונות
  modal: '' as '' | 'xl' | 'stored' | 'del' | 'split' | 'join' | 'reread' | 'help' | 'pz' | 'tour',
  xl: null as null | { data: any; done: boolean },
  storedGo: null as null | (() => void),
  delFor: '',
  split: { rec: null as Rec | null, axis: 'y' },
  join: { rec: null as Rec | null, other: null as Rec | null },
  rerFor: '',
  rereadBusy: false,
  tourStep: 0,
  help: {
    rec: '', key: '', pane: '' as '' | 'mail' | 'wa' | 'link', done: false, doneText: '', doneAlt: '',
    linkBox: '', question: '', asker: '', to: '', phone: '', err: '', sent: false, busy: '',
  },
  // רשומות במערכת
  rec: { mode: 'approved', range: 'today', from: '', to: '', rfrom: '', rto: '', q: '', sender: '',
         more: false, menu: false, loading: false, loaded: false, data: null as any, pendingN: '' as string | number },
  openedKey: '',
  openedPending: false,
};

let version = 0;
const subs = new Set<() => void>();
export function emit() { version += 1; subs.forEach((f) => f()); }
export function useStore() {
  return useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => version);
}

const queue: (() => void)[] = [];
export function afterRender(fn: () => void) { queue.push(fn); }
export function flushAfterRender() { while (queue.length) queue.shift()!(); }

/* ------------------------------------------------------------ כתובות */
export function pageUrl(r: Rec) { return '/api/batches/' + S.batch + '/sources/' + r.source_index + '/pages/' + r.page_number; }
export function cropUrl(r: Rec, key: string, i?: number) {
  const q: string[] = [];
  if (S.inkView) q.push('ink=1');
  if (i != null) q.push('i=' + i);
  return pageUrl(r) + '/crops/' + key + (q.length ? '?' + q.join('&') : '');
}
export function pageSrc(r: Rec) { return pageUrl(r) + (S.inkView ? '?ink=1' : ''); }

// התקריב כפי שהמזכירה הזיזה אותו (x0,y0,x1,y1 יחסית לעמוד). בקשת עזרה שולחת בדיוק את האזור הזה.
// אין רשומה = התקריב על השדה עצמו (ברירת המחדל)
export const zoomViews = new Map<string, number[]>();
export function viewKey(rec: string, key: string, i?: number) { return rec + '|' + key + '|' + (i ?? ''); }
export function fieldLabel(key: string) { const f = S.fields.find((x) => x.key === key); return f ? f.label : key; }
export function recordById(id: string) { return S.last ? S.last.records.find((r) => r.record_id === id) || null : null; }
export function isApproved(r: Rec) { return r.verified || S.fields.every((f) => r.fields[f.key].state === 'approved'); }
