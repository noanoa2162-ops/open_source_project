/* חלקים קטנים שחוזרים בכל המסך — אותו HTML בדיוק כמו במסך הקודם (app.js) (statusBadge, valueHtml, bdiText…). */
import { forwardRef } from 'react';
import type { ReactNode } from 'react';
import ButtonBase from '@mui/material/ButtonBase';
import type { ButtonBaseProps } from '@mui/material/ButtonBase';
import type { Field, Rec } from '../lib';
import { isLtr, localTime } from '../lib';
import { isApproved } from '../store';

// כפתור של MUI שנראה בדיוק כמו כפתור רגיל של המערכת (העיצוב מ-app.css)
export const Btn = forwardRef<HTMLButtonElement, ButtonBaseProps & { [k: `data-${string}`]: string | number | undefined }>(
  function Btn(props, ref) { return <ButtonBase type="button" ref={ref} {...props} />; });

// תג מצב בצורת "שירות מקוון" של gov.il. ירוק רק כשאדם אישר.
export function StatusBadge({ r }: { r: Rec }) {
  if (r.error) return <span className="badge fail">✗ הקריאה נכשלה</span>;
  if (isApproved(r)) return <span className="badge ok">✓ {r.verified ? 'מאומת' : 'מאושר'}</span>;
  if (r.review_count) return <span className="badge review"><b>{r.review_count}</b> לבדיקה</span>;
  return <span className="badge ready">מוכן לאישור</span>;
}

// "?" — תו שלא נקרא — מסומן בתוך הערך
export function Unreadable({ text }: { text: string }) {
  const parts = text.split('?');
  const out: ReactNode[] = [];
  parts.forEach((p, i) => {
    if (i) out.push(<mark key={'q' + i} className="q" title="תו שלא נקרא">?</mark>);
    if (p) out.push(p);
  });
  return <>{out}</>;
}

export function ValueView({ f }: { f: Field }) {
  if (f.values.length) {
    const text = f.values.join(' | ');
    return <bdi dir={isLtr(text) ? 'ltr' : 'rtl'}><Unreadable text={text} /></bdi>;
  }
  const notOnForm = (f.reason_codes || []).includes('not_on_form');
  return <span className="empty">{notOnForm ? 'לא בטופס' : 'חסר'}</span>;
}

export function BdiText({ v }: { v: string | null | undefined }) {
  return <bdi dir={v && isLtr(v) ? 'ltr' : 'auto'}>{v || '—'}</bdi>;
}

/* טופס שכבר אושר בעבר: הערכים הגיעו מהמערכת ולא מקריאת הטופס */
export function storedText(r: Rec) {
  const when = r.from_store ? ' (אושר ב-' + localTime(r.from_store) + ')' : '';
  return r.verified
    ? 'הנתונים מהמערכת, לא מקריאת הטופס' + when
    : 'הנתונים מהמערכת' + when + ' ושונו — יש לאשר את הטופס שוב';
}

export function HelpMark({ f }: { f: Field }) {
  if (!f.help) return null;
  return f.help.status === 'open'
    ? <> <span className="helpmark" title="נשלח לעזרה — ממתין לתשובה">⏳</span></>
    : <> <span className="helpmark got" title="התקבלה תשובה">💬</span></>;
}
