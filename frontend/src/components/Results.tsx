/* אזור: טפסים שנקלטו — פאנל הסינון, ראש התוצאות, טבלה ואריחים. מצב טופס ב-FormView. */
import { useEffect, useRef } from 'react';
import type { FieldSpec, Rec } from '../lib';
import { LTR_KEYS, canApprove, firstValue, senderOf, whyOf } from '../lib';
import { S, fieldLabel, pageUrl } from '../store';
import {
  approveField, cancelPopTimer, chips, closePop, downloadReady, finishEdit, openExportPreview, openPop, openRecord,
  schedulePop, scheduleClose, setFilter, setSenderFilter, setView, startEdit, startNewIntake, typeQuery, verify,
  visibleRecords, waitingForJoin,
} from '../actions';
import { Btn, StatusBadge, ValueView, storedText } from './Bits';
import { FormView } from './FormView';

export function Results() {
  const formMode = S.view === 'form';
  return (
    <section id="results" className={'strip' + (S.resultsVisible && S.page !== 'records' ? '' : ' hidden') + (formMode ? ' formmode' : '')
      + (S.processing ? ' early' : '')}
      aria-labelledby="resultsTitle">
      <h2 className="sr-only" id="resultsTitle">טפסים שנקלטו</h2>

      {/* סינון, בסגנון פאנל הסינון של דף השירותים */}
      <div className="filterbar card" role="search" aria-label="סינון הטפסים">
        <div className="float">
          <input id="q2" type="search" placeholder=" " autoComplete="off" value={S.queryText}
            onChange={(e) => typeQuery(e.target.value)} />
          <label htmlFor="q2">חיפוש חופשי</label>
        </div>
        <div className="float">
          <select id="senderFilter" aria-label="סינון לפי גורם שולח" value={S.senderFilter}
            onChange={(e) => setSenderFilter(e.target.value)}>
            <option value="">כל הגורמים</option>
            {chips.senders.map((s) => <option key={s}>{s}</option>)}
          </select>
          <label htmlFor="senderFilter" className="up">גורם שולח</label>
        </div>
        <div className="statuses filters" role="group" aria-label="סינון לפי מצב">
          {([['all', 'הכול', chips.nAll], ['review', 'לבדיקה', chips.nReview], ['approved', 'מאושרים', chips.nApproved]] as const)
            .map(([f, label, n]) => (
              <Btn key={f} data-filter={f} className={S.filter === f ? 'on' : undefined} onClick={() => setFilter(f)}>
                {label} <b id={f === 'all' ? 'nAll' : f === 'review' ? 'nReview' : 'nApproved'}>{n}</b>
              </Btn>
            ))}
        </div>
        <span className="grow"></span>
        <Btn className="secondary" id="again" onClick={() => startNewIntake()}>קליטה חדשה</Btn>
        <span className="exporthint" id="exportHint" aria-live="polite">{chips.exportWhy}</span>
        <Btn className={'primary' + (downloadReady ? ' ready' : '')} id="download" disabled={!chips.exportReady}
          title={chips.exportWhy || 'הורדת כל הטפסים לקובץ Excel'}
          onClick={(e) => openExportPreview(e.currentTarget)}>⬇ הורדת קובץ Excel</Btn>
      </div>

      {/* ראש התוצאות: "N טפסים" + מתג תצוגה */}
      <div className="resulthead">
        <h3 id="resultCount"><b id="cRecords">{chips.records}</b> טפסים</h3>
        <span id="cReviewChip" className={'badge review' + (chips.review === 0 ? ' hidden' : '')}><b id="cReview">{chips.review}</b> שדות לבדיקה</span>
        <span className="grow"></span>
        <div className="seg" role="tablist" aria-label="מצב תצוגה">
          <Btn role="tab" id="viewTiles" aria-selected={String(S.view !== 'table') as 'true' | 'false'} onClick={() => setView('tiles')}>▦ אריחים</Btn>
          <Btn role="tab" id="viewTable" aria-selected={String(S.view === 'table') as 'true' | 'false'} onClick={() => setView('table')}>▤ טבלה</Btn>
        </div>
      </div>

      <div id="tableView" className={S.view === 'table' ? undefined : 'hidden'}>
        <div className="card tablewrap">
          <table>
            <thead><tr id="head">
              <th scope="col">טופס</th>
              {S.fields.map((f) => <th key={f.key} scope="col" title={f.hint}>{f.label}</th>)}
              <th scope="col"><span className="sr-only">אישור</span></th>
            </tr></thead>
            <TableBody />
          </table>
          <div className={'empty-filter' + (S.view === 'table' && visibleRecords().length === 0 ? '' : ' hidden')} id="emptyFilter">אין טפסים שמתאימים לסינון הזה.</div>
        </div>
      </div>

      <div id="tilesView" className={S.view === 'tiles' ? undefined : 'hidden'}>
        <ul className="ftiles" id="tileGrid" aria-label="הטפסים">
          {S.view === 'tiles' ? visibleRecords().map((r) => <TileCard key={r.record_id} r={r} />) : null}
          {S.view === 'tiles' && S.processing && S.last ? S.last.records.filter(waitingForJoin).map((r) => (
            <li key={r.record_id} className="ftile" aria-disabled="true" style={{ cursor: 'default', opacity: 0.7 }}
              aria-label={'עמוד ' + r.page_number + ' — ממתין לחיבור, ייפתח בסוף הקריאה'}>
              <div className="thumb"><HideBroken src={pageUrl(r) + '/thumb'} /><span className="badge ready">⏳ ממתין לחיבור</span></div>
              <div className="body"><h4>ייתכן שזה חלק תחתון של טופס</h4>
                <span className="who">ייפתח בסוף הקריאה, אחרי חיבור החצאים</span>
                <span className="metaline"><span>עמוד {r.page_number}</span><span className="file" title={r.source_name}>{r.source_name}</span></span></div>
            </li>
          )) : null}
        </ul>
        <div className={'empty-filter' + (S.view === 'tiles' && visibleRecords().length === 0 ? '' : ' hidden')} id="tilesEmpty">אין טפסים שמתאימים לסינון הזה.</div>
      </div>

      <FormView />
    </section>
  );
}

/* ------------------------------------------------------------ מצב טבלה */
function TableBody() {
  if (S.view !== 'table') return <tbody id="body"></tbody>;
  const rows = visibleRecords();
  const span = S.fields.length + 2;
  const keyboard = (td: HTMLElement) => { try { return td.matches(':focus-visible'); } catch { return false; } };
  return (
    <tbody id="body"
      onMouseOver={(e) => {
        const td = (e.target as HTMLElement).closest('td.cell, td.formcol') as HTMLElement | null;
        if (td) schedulePop(td.classList.contains('formcol') ? 'page' : 'cell', td.dataset.rec!, td.dataset.key || '', 280);
      }}
      onMouseOut={(e) => {
        const td = (e.target as HTMLElement).closest('td.cell, td.formcol');
        if (td && !td.contains(e.relatedTarget as Node)) scheduleClose();
      }}
      onFocus={(e) => {
        const td = (e.target as HTMLElement).closest('td.cell, td.formcol') as HTMLElement | null;
        if (!td) return;
        if (td.classList.contains('cell')) S.focusedCell = { rec: td.dataset.rec!, key: td.dataset.key! };
        // תקריב מיידי רק במעבר מקלדת (Tab); בעכבר הוא נפתח במעבר מעל התא
        if (e.target === td && keyboard(td)) schedulePop(td.classList.contains('formcol') ? 'page' : 'cell', td.dataset.rec!, td.dataset.key || '', 0);
      }}
      onBlur={(e) => {
        const pop = document.getElementById('pop');
        if (!e.relatedTarget || !pop || !pop.contains(e.relatedTarget as Node)) scheduleClose();
      }}>
      {rows.map((r) => {
        const notes: string[] = [];
        if (r.low_resolution) notes.push('רזולוציה נמוכה — כתב יד עלול לא להיקרא. מומלץ להעלות את הסריקה המקורית.');
        if (r.from_store) notes.push(storedText(r));
        if (r.straightened) notes.push(r.straightened);
        if (r.error) notes.push('הקריאה נכשלה: ' + r.error);
        else if (r.note) notes.push('הערה: ' + r.note);
        const open = r.review_count;
        const num = S.last!.records.indexOf(r) + 1;
        return [
          <tr key={r.record_id} data-rec={r.record_id}>
            <td className="formcol" tabIndex={0} data-rec={r.record_id}
              aria-label={'טופס ' + num + ' — מעבר מציג את הטופס, Enter פותח אותו'}
              onClick={() => openRecord(r.record_id)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); closePop(); openRecord(r.record_id); } }}>
              <div className="fc"><img loading="lazy" alt="" src={pageUrl(r) + '/thumb'} />
                <span className="t"><b>טופס {num}</b><StatusBadge r={r} />
                  {r.template ? <small className="kind" title={r.template}>{r.template}</small> : null}</span></div>
            </td>
            {S.fields.map((f) => <Cell key={f.key} r={r} spec={f} />)}
            <td className="acts">{r.verified
              ? <span className="verified" title="הטופס אושר">✓</span>
              : <Btn className="approveRow" data-rec={r.record_id} disabled={!!open || S.processing} aria-label="אישור הטופס"
                  title={S.processing ? 'הקריאה עוד נמשכת — אפשר לעיין ולתקן, ואת הטופס מאשרים בסיום' : open ? 'נשארו ' + open + ' שדות לבדיקה' : 'אישור כל הטופס'}
                  onClick={(e) => { e.stopPropagation(); verify(r.record_id); }}>✓</Btn>}</td>
          </tr>,
          notes.length ? <tr key={r.record_id + '-n'} className="noterow"><td colSpan={span}>{notes.join(' · ')}</td></tr> : null,
        ];
      })}
    </tbody>
  );
}

// תא בטבלה: ריבוע מעוגל אחד עם הערך. הסיבה בתקריב הצף (ובכותרת), לא כטקסט בתוך הטבלה.
function Cell({ r, spec }: { r: Rec; spec: FieldSpec }) {
  const f = r.fields[spec.key];
  const cls = f.state === 'needs_review' ? 'review' : f.state === 'approved' ? 'approved' : '';
  const why = whyOf(f);
  const ed = S.editor;
  const editing = !!ed && ed.kind === 'cell' && ed.rec === r.record_id && ed.key === spec.key;
  return (
    <td className={'cell ' + cls} tabIndex={0} data-key={spec.key} data-rec={r.record_id} data-raw={f.values.join(' | ')}
      title={why || undefined}
      aria-label={spec.label + ': ' + (f.values.join(' | ') || 'ריק') +
        (cls === 'review' ? ', לבדיקה' + (why ? ': ' + why : '') : cls === 'approved' ? ', אושר' : '')}
      onClick={(e) => {
        if ((e.target as HTMLElement).closest('button, input')) return;
        cancelPopTimer();
        openPop('cell', r.record_id, spec.key);
      }}
      onDoubleClick={(e) => { if (!(e.target as HTMLElement).closest('button, input')) startEdit('cell', r.record_id, spec.key); }}
      onKeyDown={(e) => {
        if ((e.target as HTMLElement).tagName === 'INPUT') return;
        if (e.key === 'Enter') { e.preventDefault(); openPop('cell', r.record_id, spec.key); }
        else if (e.key === 'F2' || e.code === 'KeyE') { e.preventDefault(); startEdit('cell', r.record_id, spec.key); }
        else if (e.code === 'KeyA') { e.preventDefault(); approveField(r.record_id, spec.key); }
      }}>
      {editing ? <EditInput keyName={spec.key} label={'תיקון ' + fieldLabel(spec.key) + ' — Enter לשמירה, Esc לביטול. כמה ערכים: להפריד ב-|'} />
        : <span className="chip"><span className="val"><ValueView f={f} /></span><span className="mini">
          {canApprove(f) ? <Btn className="ok" data-act="approve" tabIndex={-1} title={f.values.length ? 'אשר' : 'אשר כריק'} aria-label="אשר"
            onClick={(e) => { e.stopPropagation(); approveField(r.record_id, spec.key); }}>✓</Btn> : null}
          <Btn data-act="edit" tabIndex={-1} title="תקן" aria-label="תקן"
            onClick={(e) => { e.stopPropagation(); startEdit('cell', r.record_id, spec.key); }}>✎</Btn></span></span>}
    </td>
  );
}

// תיבת העריכה — בתא בטבלה או בריבוע השדה. Enter שומר, Esc מבטל, יציאה מהתיבה שומרת.
export function EditInput({ keyName, label, stopKeys }: { keyName: string; label: string; stopKeys?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  const closed = useRef(false);
  const ed = S.editor!;
  useEffect(() => { ref.current?.focus(); ref.current?.select(); }, []);
  const finish = (saveIt: boolean, again?: boolean) => {
    if (closed.current) return;
    closed.current = true;
    finishEdit(saveIt, again);
  };
  return (
    <input ref={ref} defaultValue={ed.text} dir={LTR_KEYS.includes(keyName) ? 'ltr' : undefined} aria-label={label}
      aria-invalid={ed.invalid ? 'true' : undefined}
      onChange={(e) => { if (S.editor) S.editor.text = e.target.value; }}
      onBlur={() => finish(true)}
      onKeyDown={(e) => {
        if (stopKeys) e.stopPropagation();
        if (e.key === 'Enter') { e.preventDefault(); finish(true, true); }
        if (e.key === 'Escape') { e.preventDefault(); finish(false); }
      }} />
  );
}

/* ------------------------------------------------------------ מצב אריחים */
// אריח לכל טופס, כמו אריחי השירותים באתר: תמונה, תג מצב, גורם שולח, שם ומספרים.
function TileCard({ r }: { r: Rec }) {
  const name = [firstValue(r, 'first_name'), firstValue(r, 'last_name')].filter(Boolean).join(' ');
  const commit = r.fields.commitment_numbers ? r.fields.commitment_numbers.values.join(', ') : '';
  const sender = senderOf(r);
  return (
    <li className="ftile" tabIndex={0} role="button" data-rec={r.record_id}
      aria-label={(sender || 'גורם לא זוהה') + ', ' + (name || 'שם לא זוהה') + '. פתיחת הטופס'}
      onClick={() => openRecord(r.record_id)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRecord(r.record_id); } }}>
      <div className="thumb"><HideBroken src={pageUrl(r) + '/thumb'} /><StatusBadge r={r} />
        {r.from_store ? <span className="storedtag" title={storedText(r)}>🗄 מהמערכת</span> : null}</div>
      <div className="body"><h4>{sender || 'גורם שולח לא זוהה'}</h4>
        {r.template ? <span className="kind" title={r.template}>{r.template}</span> : null}
        {r.error ? <span className="tfail">הקריאה נכשלה — פתחו את הטופס כדי לקרוא שוב</span>
                 : <span className="who">{name || '—'}</span>}
        <span className="nums">מספר התחייבות: <b>{commit || '—'}</b></span>
        <span className="metaline"><span>עמוד {r.page_number}</span><span className="file" title={r.source_name}>{r.source_name}</span></span>
      </div>
    </li>
  );
}

function HideBroken({ src }: { src: string }) {
  return <img loading="lazy" alt="" src={src} onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }} />;
}
