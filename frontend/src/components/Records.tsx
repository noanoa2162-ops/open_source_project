/* אזור: רשומות במערכת — הטפסים שאושרו, שורה לכל מספר התחייבות (כמו האקסל), וטפסים שממתינים לאישור.
 * נטען רק טווח תאריכי האישור שנבחר — ברירת מחדל: היום. אף פעם לא כל המאגר. */
import { useEffect } from 'react';
import { S, emit } from '../store';
import { heDay, isoDay, localTime } from '../lib';
import {
  askDelete, clearChip, exportSaved, loadRecords, openPending, openSaved, pickRange, recTyped, recWhen, setRecMode, toast,
} from '../actions';
import { BdiText, Btn } from './Bits';

const RANGES = [['today', 'היום'], ['yesterday', 'אתמול'], ['week', '7 ימים'], ['month', 'החודש'], ['custom', 'טווח…']];
const PENDING_STATE: Record<string, [string, string]> = { review: ['review', 'לבדיקה'], ready: ['ready', 'מוכן לאישור'], failed: ['fail', '✗ הקריאה נכשלה'] };

export function RecordsPage() {
  const R = S.rec;
  const pending = R.mode === 'pending';
  const d = R.data && R.data.pending === pending ? R.data : null;
  const rows: any[] = d ? d.rows || [] : [];

  // לחיצה מחוץ לתפריט הייצוא סוגרת אותו
  useEffect(() => {
    const close = (e: MouseEvent) => { if (S.rec.menu && !(e.target as HTMLElement).closest('.recexport')) { S.rec.menu = false; emit(); } };
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, []);

  const senders: string[] = d ? (d.senders || []).slice() : [];
  if (R.sender && !senders.includes(R.sender)) senders.unshift(R.sender);
  const chipList: [string, string, string][] = [];
  if (R.q) chipList.push(['q', 'חיפוש', R.q]);
  if (R.sender) chipList.push(['sender', 'גורם שולח', R.sender]);
  if (R.rfrom || R.rto) chipList.push(['received', 'קבלת הדגימה',
    (R.rfrom ? 'מ-' + heDay(R.rfrom) : '') + (R.rfrom && R.rto ? ' ' : '') + (R.rto ? 'עד ' + heDay(R.rto) : '')]);
  const filtered = pending ? !!(R.q || R.sender) : !!(R.q || R.sender || R.rfrom || R.rto);
  const setField = (k: 'from' | 'to' | 'rfrom' | 'rto' | 'sender', v: string) => { (R as any)[k] = v; loadRecords(); };

  return (
    <section id="recordsPage" className={'strip' + (S.page === 'records' ? '' : ' hidden')} aria-labelledby="recTitle">
      <h2 className="sr-only" id="recTitle">רשומות במערכת</h2>

      {/* מאושרים | ממתינים לאישור */}
      <div className="recmode" id="recMode" role="tablist" aria-label="סוג הטפסים">
        <Btn role="tab" aria-selected={String(!pending) as 'true' | 'false'} data-mode="approved" id="modeApproved" onClick={() => setRecMode('approved')}>✓ מאושרים</Btn>
        <Btn role="tab" aria-selected={String(pending) as 'true' | 'false'} data-mode="pending" id="modePending" onClick={() => setRecMode('pending')}>⏳ ממתינים לאישור <b id="pendingN">{R.pendingN}</b></Btn>
      </div>

      <div className="card recpanel" role="search" aria-label={pending ? 'סינון הטפסים הממתינים' : 'סינון הטפסים המאושרים'}>
        <div className="recbar">
          <div className="recsearch">
            <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" /><path d="M13 13l4.5 4.5" /></svg>
            <input id="recQ" type="search" placeholder="חיפוש לפי שם, ת״ז או מספר" autoComplete="off" aria-label="חיפוש לפי שם, תעודת זהות או מספר"
              value={(R as any).qText ?? R.q} onChange={(e) => recTyped(e.target.value)} />
          </div>
          <label className="recfield">
            <span>גורם שולח</span>
            <select id="recSender" value={R.sender} onChange={(e) => setField('sender', e.target.value)}>
              <option value="">כל הגורמים</option>
              {senders.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <div className={'recfield' + (pending ? ' hidden' : '')} id="recRangeField">
            <span id="recRangeLabel">תאריך אישור</span>
            <div className="recseg" id="recRange" role="radiogroup" aria-labelledby="recRangeLabel"
              onKeyDown={(e) => {
                const at = RANGES.findIndex(([r]) => r === R.range);
                const step = e.key === 'ArrowLeft' ? 1 : e.key === 'ArrowRight' ? -1 : 0;  // RTL: שמאלה = הבא
                if (!step) return;
                e.preventDefault();
                pickRange(RANGES[(at + step + RANGES.length) % RANGES.length][0], true);
              }}>
              {RANGES.map(([r, label]) => (
                <Btn key={r} role="radio" aria-checked={String(R.range === r) as 'true' | 'false'} tabIndex={R.range === r ? 0 : -1}
                  data-range={r} onClick={() => pickRange(r)}>{label}</Btn>
              ))}
            </div>
          </div>
          <Btn className={'recmore' + (pending ? ' hidden' : '')} id="recMore" aria-expanded={String(R.more && !pending) as 'true' | 'false'} aria-controls="recAdvanced"
            onClick={() => { R.more = !R.more; emit(); if (R.more) setTimeout(() => document.getElementById('recRFrom')?.focus()); }}>
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h14M6 10h8M8.5 15h3" /></svg>
            סינון מתקדם
          </Btn>
        </div>

        <div className={'recrow' + (!pending && R.range === 'custom' ? '' : ' hidden')} id="recCustom">
          <span className="rowlabel">אושר בין</span>
          <label className="recdate"><span>מתאריך</span><input type="date" id="recFrom" value={R.from} onChange={(e) => setField('from', e.target.value)} /></label>
          <label className="recdate"><span>עד תאריך</span><input type="date" id="recTo" value={R.to} onChange={(e) => setField('to', e.target.value)} /></label>
        </div>
        <div className={'recrow' + (R.more && !pending ? '' : ' hidden')} id="recAdvanced">
          <span className="rowlabel">תאריך קבלת הדגימה</span>
          <label className="recdate"><span>מתאריך</span><input type="date" id="recRFrom" value={R.rfrom} onChange={(e) => setField('rfrom', e.target.value)} /></label>
          <label className="recdate"><span>עד תאריך</span><input type="date" id="recRTo" value={R.rto} onChange={(e) => setField('rto', e.target.value)} /></label>
        </div>
        {/* שבבים לסינונים הפעילים — כל אחד נסגר לבד, ו"נקה הכול" מחזיר להיום */}
        <div className={'recchips' + (chipList.length ? '' : ' hidden')} id="recChips" aria-live="polite">
          {chipList.map(([k, label, value]) => (
            <Btn key={k} className="recchip" data-chip={k} aria-label={'הסרת הסינון ' + label} onClick={() => clearChip(k)}>
              {label}: <b><bdi>{value}</bdi></b><i aria-hidden="true">✕</i></Btn>
          ))}
          {chipList.length ? <Btn className="recclear" id="recClear" onClick={() => clearChip('all')}>נקה הכול</Btn> : null}
        </div>
      </div>

      <div className="rechead">
        <p className="recsummary" id="recSummary" aria-live="polite">{d ? (pending
          ? <><b>{d.total || 0}</b> טפסים ממתינים לאישור<span className="sep">·</span>הישן ביותר למעלה
              {d.truncated ? <><span className="sep">·</span>מוצגים {rows.length} הראשונים</> : null}</>
          : <><b>{d.forms || 0}</b> טפסים<span className="sep">·</span><b>{d.total_rows || 0}</b> שורות<span className="sep">·</span>אושרו {recWhen()}
              {d.truncated ? <><span className="sep">·</span>מוצגות {rows.length} הראשונות — הייצוא כולל הכול</> : null}</>) : null}</p>
        <span className="grow"></span>
        <div className={'recexport' + (pending ? ' hidden' : '')}>
          <Btn className="primary" id="recExcel" disabled={!rows.length} onClick={() => exportSaved('xlsx')}>⬇ ייצוא ל-Excel</Btn>
          <Btn className="secondary recexportmore" id="recExportMore" aria-haspopup="menu" aria-expanded={String(R.menu) as 'true' | 'false'}
            aria-controls="recExportMenu" aria-label="פורמטים נוספים לייצוא" disabled={!rows.length}
            onClick={(e) => {
              e.stopPropagation();
              R.menu = !R.menu;
              emit();
              if (R.menu) setTimeout(() => (document.querySelector('#recExportMenu button') as HTMLElement | null)?.focus());
            }}>▾</Btn>
          <div className={'recmenu' + (R.menu ? '' : ' hidden')} id="recExportMenu" role="menu"
            onKeyDown={(e) => {
              const items = Array.from(document.querySelectorAll('#recExportMenu button')) as HTMLElement[];
              const at = items.indexOf(document.activeElement as HTMLElement);
              if (e.key === 'Escape') { e.preventDefault(); R.menu = false; emit(); document.getElementById('recExportMore')?.focus(); }
              if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                items[(at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
              }
            }}>
            <Btn role="menuitem" data-export="csv" onClick={() => { R.menu = false; emit(); exportSaved('csv'); }}>CSV — טקסט מופרד בפסיקים</Btn>
            <Btn role="menuitem" data-export="json" onClick={() => { R.menu = false; emit(); exportSaved('json'); }}>JSON — למערכות אחרות</Btn>
          </div>
        </div>
      </div>

      <div className={'card rectablewrap' + (R.loading ? ' loading' : '')} id="recWrap">
        <table className="rectable">
          <thead><tr>
            <th scope="col">שם פרטי</th><th scope="col">שם משפחה</th><th scope="col">ת״ז</th>
            <th scope="col">מספר מרכזי</th><th scope="col">תאריך קבלה</th><th scope="col">גורם שולח</th>
            <th scope="col">מספר התחייבות</th><th scope="col">מקור</th><th scope="col" id="recWhenHead">{pending ? 'נשמר' : 'אושר'}</th>
            <th scope="col"><span className="sr-only">פעולות</span></th>
          </tr></thead>
          <tbody id="recBody"
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'TR') { e.preventDefault(); (e.target as HTMLElement).click(); } }}>
            {!R.loaded ? <Skeleton /> : pending ? rows.map((row) => <PendingRow key={row.record_id} row={row} />) : <ApprovedRows rows={rows} />}
          </tbody>
        </table>
        <div className={'recempty' + (R.loaded && d && !rows.length ? '' : ' hidden')} id="recEmpty">
          <svg viewBox="0 0 48 48" aria-hidden="true"><rect x="10" y="6" width="28" height="36" rx="4" /><path d="M17 17h14M17 24h14M17 31h8" /></svg>
          <b id="recEmptyTitle">{pending ? (filtered ? 'אין טפסים ממתינים שמתאימים לסינון' : 'אין טפסים שממתינים לאישור')
            : filtered ? 'אין טפסים מאושרים שמתאימים לסינון' : 'אין טפסים מאושרים ' + recWhen()}</b>
          <span>טופס נכנס לכאן אחרי "אישור הטופס". אפשר לבחור טווח אחר או לנקות את הסינון.</span>
        </div>
      </div>
    </section>
  );
}

function Skeleton() {
  return <>{Array.from({ length: 4 }, (_, i) => (
    <tr key={i} className="skel">{Array.from({ length: 9 }, (_, j) => <td key={j}><span></span></td>)}<td></td></tr>
  ))}</>;
}

function ApprovedRows({ rows }: { rows: any[] }) {
  let previous = '';
  return <>{rows.map((row) => {
    const first = row.form_key !== previous;
    previous = row.form_key;
    const cls = [first ? 'grp' : 'cont', row.can_open ? '' : 'old'].filter(Boolean).join(' ');
    const rep = (v: string, extra?: string) => <td className={'rep' + (extra ? ' ' + extra : '')}><BdiText v={v} /></td>;
    const dupes = row.duplicates || [];
    return (
      <tr key={row.row_id} className={cls} tabIndex={0} data-key={row.form_key} data-open={row.can_open ? 1 : 0}
        aria-label={row.can_open ? 'פתיחת הטופס לבדיקה' : 'טופס ישן — לצפייה בלבד'}
        onClick={(e) => {
          if ((e.target as HTMLElement).closest('button[data-del]')) return;
          if (row.can_open) openSaved(row.form_key);
          else toast('הטופס הזה אושר לפני שנוספה השמירה המלאה — אפשר לראות ולייצא אותו, לא לפתוח');
        }}>
        {rep(row.first_name)}{rep(row.last_name)}{rep(row.national_id, 'num')}{rep(row.central_numbers, 'num')}
        {rep(row.sample_received_date)}{rep(row.sending_entity)}
        <td className="num"><BdiText v={row.commitment_number} />
          {dupes.length ? <span className="badge dup" title={dupTitle(dupes)}>⧉ ייתכן כפול</span> : null}
          {row.breaks_rules ? <span className="badge rulebad" title='אושר לפני שנוספו כללי המערכת ואינו עומד בהם (למשל שתי ת"ז). לא נמחק ולא שונה — בפתיחה, השדה יסומן לתיקון.'>⚠ לא עומד בכללים</span> : null}</td>
        <td className="src" title={row.source_name}><bdi>{row.source_name}</bdi> · עמ׳ {row.page_number}
          {row.can_open ? null : <span className="oldtag" title="אושר לפני שנוספה השמירה המלאה — לצפייה ולייצוא בלבד">לצפייה</span>}</td>
        <td className="when">{localTime(row.approved_at)}</td>
        <td className="acts">{first ? <Btn className="delbtn" data-del={row.form_key} aria-label="מחיקת הטופס" title="מחיקת הטופס"
          onClick={(e) => { e.stopPropagation(); askDelete(row.form_key); }}>🗑</Btn> : null}</td>
      </tr>
    );
  })}</>;
}

function PendingRow({ row }: { row: any }) {
  const st = PENDING_STATE[row.status] || ['', row.status];
  const cell = (v: string, extra?: string) => <td className={extra || ''}><BdiText v={v} /></td>;
  return (
    <tr className="grp" tabIndex={0} data-key={row.record_id} data-pending="1" aria-label="פתיחת הטופס לבדיקה"
      onClick={() => openPending(row.record_id)}>
      {cell(row.first_name)}{cell(row.last_name)}{cell(row.national_id, 'num')}{cell(row.central_numbers, 'num')}
      {cell(row.sample_received_date)}{cell(row.sending_entity)}{cell((row.commitment_numbers || []).join(' | '), 'num')}
      <td className="src" title={row.source_name}><span className={'badge ' + st[0]}>{row.status === 'review' ? <><b>{row.review_count}</b> {st[1]}</> : st[1]}</span><bdi>{row.source_name}</bdi> · עמ׳ {row.page_number}</td>
      <td className="when">{localTime(row.updated_at)}</td><td className="acts"></td>
    </tr>
  );
}

function dupTitle(dupes: any[]) {
  return 'אותו מספר התחייבות קיים בטופס מאושר אחר: ' + dupes.map((x) => x.source_name + ', אושר ' + localTime(x.approved_at)).join(' · ');
}

export { isoDay };
