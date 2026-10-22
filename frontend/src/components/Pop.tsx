/* תקריב צף על תא בטבלה, ו"טופס N" — הטופס עצמו צף בצד התא. המיקום נמדד אחרי הציור, כמו קודם. */
import { useLayoutEffect, useRef } from 'react';
import { NAME_KEYS, canApprove, canAskHelp, isName, perValue, whyOf } from '../lib';
import type { Rec } from '../lib';
import { S, cropUrl, emit, fieldLabel, pageUrl, recordById } from '../store';
import { approveField, cancelPopTimer, closePop, helpAction, openPageZoom, openRecord, scheduleClose, startEdit } from '../actions';
import { Btn, StatusBadge, ValueView } from './Bits';
import { HelpStrip } from './FormView';

function anchor(): HTMLElement | null {
  const p = S.pop;
  if (!p) return null;
  return document.querySelector(p.kind === 'page' ? 'td.formcol[data-rec="' + p.rec + '"]'
    : 'td.cell[data-rec="' + p.rec + '"][data-key="' + p.key + '"]');
}

export function Pop() {
  const el = useRef<HTMLDivElement>(null);
  const p = S.pop;
  const r = p ? recordById(p.rec) : null;

  const place = () => {
    const pop = el.current, td = anchor();
    if (!pop || !td || !S.pop) return;
    const cellBox = td.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    if (S.pop.kind === 'page') {
      // משמאל לתא (העמודה הימנית בטבלה), בגובה התא ובתוך גבולות המסך; החץ מצביע על התא
      let left = cellBox.left - w - 12;
      if (left < 10) left = Math.min(cellBox.right + 12, window.innerWidth - w - 10);
      const middle = cellBox.top + cellBox.height / 2;
      const top = Math.max(10, Math.min(middle - 40, window.innerHeight - h - 10));
      pop.style.left = (left + window.scrollX) + 'px';
      pop.style.top = (top + window.scrollY) + 'px';
      pop.style.setProperty('--arrow-y', Math.max(18, Math.min(h - 18, middle - top)) + 'px');
      return;
    }
    const below = cellBox.top < h + 90;
    let left = cellBox.left + cellBox.width / 2 - w / 2;
    left = Math.max(10, Math.min(left, window.innerWidth - w - 10));
    const top = below ? cellBox.bottom + 10 : cellBox.top - h - 10;
    pop.classList.toggle('below', below);
    pop.style.left = (left + window.scrollX) + 'px';
    pop.style.top = (top + window.scrollY) + 'px';
    pop.style.setProperty('--arrow', Math.max(14, Math.min(w - 14, cellBox.left + cellBox.width / 2 - left)) + 'px');
  };
  useLayoutEffect(() => { if (S.pop && !anchor()) { S.pop = null; emit(); return; } place(); });

  const cls = 'pop' + (!p || !r ? ' hidden' : '') + (p && p.kind === 'page' ? ' pagepop' : '');
  return (
    <div id="pop" className={cls} role="dialog" aria-label="תקריב השדה" ref={el}
      onMouseEnter={cancelPopTimer} onMouseLeave={scheduleClose}
      onClick={(e) => {
        // כפתורי בקשת העזרה בתוך התקריב (שליחה שוב / ביטול / להשתמש בערך)
        const h = (e.target as HTMLElement).closest('[data-help]') as HTMLElement | null;
        if (h && p && r) { closePop(); helpAction(h.dataset.help!, r, p.key, null); }
      }}>
      {p && r ? (p.kind === 'page' ? <PageContent r={r} onLoad={place} /> : <CellContent r={r} fieldKey={p.key} onLoad={place} />) : null}
    </div>
  );
}

// "טופס N" בטבלה: הטופס עצמו — גדול מהתקריב, עם הגדלה ופתיחה
function PageContent({ r, onLoad }: { r: Rec; onLoad: () => void }) {
  const num = S.last!.records.indexOf(r) + 1;
  const zoom = () => { const td = anchor(); closePop(); openPageZoom(td, r, S.fields[0].key); };
  return <>
    <div className="phead"><strong>טופס {num}</strong><StatusBadge r={r} /></div>
    <div className="pmeta" title={r.source_name}>עמוד {r.page_number} · <bdi>{r.source_name}</bdi></div>
    <div className="ppage" data-act-zoom="1" title="לחצו להגדלה" onClick={zoom}>
      <img alt={'טופס ' + num + ' — עמוד ' + r.page_number + ' של ' + r.source_name} src={pageUrl(r)} onLoad={onLoad} /></div>
    <div className="btns"><Btn className="secondary" data-act="zoom" onClick={zoom}>🔍 הגדלה</Btn>
      <Btn className="primary" data-act="open" onClick={() => { closePop(); openRecord(r.record_id); }}>פתיחת הטופס</Btn></div>
  </>;
}

function CellContent({ r, fieldKey, onLoad }: { r: Rec; fieldKey: string; onLoad: () => void }) {
  const f = r.fields[fieldKey];
  const state = f.state === 'needs_review' ? '⚠ לבדיקה' : f.state === 'approved' ? '✓ אושר' : '';
  const why = whyOf(f);
  const many = perValue(f);
  let crops;
  if (isName(fieldKey)) crops = <NameCrops r={r} onLoad={onLoad} />;
  else if (many) crops = many.map((b, i) => (b ? <div key={i} className="crop multi"><span className="cv"><bdi>{f.values[i]}</bdi></span>
    <img alt={'תקריב של ' + f.values[i]} src={cropUrl(r, fieldKey, i)} onLoad={onLoad} /></div> : null));
  else crops = f.crop_box
    ? <div className="crop"><img alt={'תקריב של ' + fieldLabel(fieldKey)} src={cropUrl(r, fieldKey)} onLoad={onLoad} /></div>
    : <div className="crop"><div className="nocrop">אין מיקום לשדה הזה — אפשר לפתוח את הטופס המלא</div></div>;
  return (
    <>
      <div className="lbl"><span>{isName(fieldKey) ? 'שם פרטי ושם משפחה' : fieldLabel(fieldKey)}</span><span>{state}</span></div>
      {crops}
      {isName(fieldKey) ? NAME_KEYS.map((k) => (
        <div key={k} className={'val nm' + (k === fieldKey ? ' now' : '')}><span>{fieldLabel(k)}:</span> <ValueView f={r.fields[k]} /></div>
      )) : <div className="val"><ValueView f={f} /></div>}
      {why ? <div className="why">{why}</div> : null}
      <HelpStrip f={f} fieldKey={fieldKey} />
      <div className="btns"><Btn className="secondary" data-act="open" onClick={() => { closePop(); openRecord(r.record_id); }}>פתיחת הטופס</Btn>
        {!S.processing && canAskHelp(r, f) ? <Btn className="secondary" data-act="help" onClick={() => { closePop(); helpAction('ask', r, fieldKey, null); }}>🙋 עזרה</Btn> : null}
        {canApprove(f) ? <Btn className="primary" data-act="approve" onClick={() => approveField(r.record_id, fieldKey)}>✓ {f.values.length ? 'אשר' : 'אשר כריק'}</Btn> : null}
        <Btn data-act="edit" onClick={() => { closePop(); startEdit('cell', r.record_id, fieldKey); }}>✎ תקן</Btn></div>
    </>
  );
}

// בטבלה, על תא שם פרטי או שם משפחה: תקריב אחד של שני השמות יחד (כמו במצב טופס)
function NameCrops({ r, onLoad }: { r: Rec; onLoad: () => void }) {
  const img = (k: string, label: string) => <div className="crop"><img alt={'תקריב של ' + label} src={cropUrl(r, k)} onLoad={onLoad} /></div>;
  if (r.full_name_crop) return img('full_name', 'שם פרטי ושם משפחה');
  const a = r.fields.first_name.crop_box, b = r.fields.last_name.crop_box;
  if (a && b && a.join() === b.join()) return img('first_name', 'שם פרטי ושם משפחה');
  const parts = NAME_KEYS.filter((k) => r.fields[k].crop_box);
  return parts.length ? <>{parts.map((k) => (
    <div key={k} className="crop multi"><span className="cv">{fieldLabel(k)}</span>
      <img alt={'תקריב של ' + fieldLabel(k)} src={cropUrl(r, k)} onLoad={onLoad} /></div>))}</>
    : <div className="crop"><div className="nocrop">אין מיקום לשם — אפשר לפתוח את הטופס המלא</div></div>;
}
