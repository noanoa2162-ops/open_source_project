/* מצב טופס אחד בכל פעם: הטופס המלא, תקריב השדה ליד, שורת ריבועי השדות, ופסי ההודעה. */
import { Fragment, useEffect, useLayoutEffect, useRef } from 'react';
import type { Field, Rec } from '../lib';
import { NAME_KEYS, canApprove, canAskHelp, isName, perValue, whyOf } from '../lib';
import { S, cropUrl, fieldLabel, isApproved, pageSrc, pageUrl, viewKey, zoomViews } from '../store';
import {
  approveField, askReread, backFromForm, chips, clickCard, closeZoom, currentRecord, goForm, helpAction, joinAction,
  joinCandidates, openExportPreview, openJoinPick, openPageZoom, openSplitPick, rereading, startEdit, startNewIntake,
  toggleInk, turnView, verify, visibleRecords,
} from '../actions';
import { Btn, HelpMark, StatusBadge, ValueView, storedText } from './Bits';
import { EditInput } from './Results';

// בסיבוב של 90° הרוחב והגובה מתחלפים: מקטינים כך שייכנס, ומגדילים את המסגרת לגובה החדש.
export function fitTurned(el: HTMLElement, wrap: HTMLElement, img: HTMLImageElement, deg: number, heightShare: number) {
  const odd = deg % 180 !== 0;
  const tallest = window.innerHeight * heightShare;
  el.style.transform = deg ? 'rotate(' + deg + 'deg)' : '';
  img.style.maxHeight = odd ? Math.min(wrap.clientWidth || 380, tallest) + 'px' : '';
  img.style.maxWidth = odd ? tallest + 'px' : '';
  const size = () => { wrap.style.height = odd ? el.offsetWidth + 'px' : ''; };
  if (img.complete) size(); else img.addEventListener('load', size, { once: true });
}

/* ------------------------------------------------------------ המסגרות על הטופס */
function Box({ b, pair }: { b: number[]; pair?: boolean }) {
  return <div className={'pv-box' + (pair ? ' pair' : '')} style={{
    left: b[0] * 100 + '%', top: b[1] * 100 + '%', width: (b[2] - b[0]) * 100 + '%', height: (b[3] - b[1]) * 100 + '%',
  }}></div>;
}
function FieldBoxes({ f, pair }: { f: Field | undefined; pair?: boolean }) {
  const many = perValue(f);
  if (many) return <>{many.map((b, i) => (b ? <Box key={i} b={b} pair={pair || i > 0} /> : null))}</>;
  const b = f && f.crop_box;
  return b ? <Box b={b} pair={pair} /> : null;
}
// שם פרטי ושם משפחה מוצגים תמיד יחד: המנוע מתבלבל ביניהם לפעמים, וכך רואים את שניהם
export function Boxes({ r, fieldKey }: { r: Rec; fieldKey: string }) {
  if (!isName(fieldKey)) return <FieldBoxes f={r.fields[fieldKey]} />;
  const other = fieldKey === 'first_name' ? 'last_name' : 'first_name';
  const a = r.fields[fieldKey].crop_box, b = r.fields[other].crop_box;
  if (a && b && a.join() === b.join()) return <FieldBoxes f={r.fields[fieldKey]} />;
  return <><FieldBoxes f={r.fields[fieldKey]} /><FieldBoxes f={r.fields[other]} pair={!!a} /></>;
}

/* ------------------------------------------------------------ הטופס */
export function FormView() {
  const visible = S.view === 'form';
  const list = visible ? visibleRecords() : [];
  const r = visible ? currentRecord() : null;
  return (
    <div id="formView" className={visible ? undefined : 'hidden'}>
      <div className="formtop">
        <Btn className="secondary back" id="backToTiles" onClick={backFromForm}>
          {S.formFrom === 'records' ? '→ חזרה לרשומות במערכת' : S.formFrom === 'table' ? '→ חזרה לטבלה' : '→ כל הטפסים'}
        </Btn>
        <strong id="formWhere" title={r ? r.source_name + ' · עמוד ' + r.page_number + (r.error ? ' · הקריאה נכשלה: ' + r.error : '') : undefined}>
          {r ? 'טופס ' + (S.formIndex + 1) + ' / ' + list.length : visible ? 'אין טפסים' : '1 / 1'}
        </strong>
        <span id="formBadge">{r ? <StatusBadge r={r} /> : null}</span>
        {/* סוג הטופס, כפי שהקורא זיהה אותו — מידע, בלי עלות */}
        <span className="formtype" id="formType" title={r ? r.template || '' : undefined}>{r && r.template ? 'סוג טופס: ' + r.template : ''}</span>
        <span className="grow"></span>
        <SplitButton r={r} />
        <Btn className="iconbtn" id="prevForm" aria-label="הטופס הקודם" title="הטופס הקודם" disabled={!r || S.formIndex === 0}
          onClick={() => goForm(-1)}>→</Btn>
        <Btn className="iconbtn" id="nextForm" aria-label="הטופס הבא"
          title={S.processing && r && S.formIndex >= list.length - 1 ? 'הטופס הבא עוד נקרא…' : 'הטופס הבא'}
          disabled={!r || S.formIndex >= list.length - 1}
          onClick={() => goForm(1)}>←</Btn>
      </div>
      {/* טופס שהקריאה שלו נכשלה: הסיבה, וכפתור "קרא שוב" שקודם שואל */}
      <FailBar r={r} />
      {/* טופס חתוך: "הפרד" / "חבר בחזרה" / "חבר עם חלק אחר" (בחינם) */}
      <JoinBar r={r} />
      {/* טופס שכבר אושר בעבר: הערכים מהמערכת, לא מקריאת הטופס */}
      <StoredBar r={r} />
      <div className={'viewer' + (r && S.zoomOpen ? ' zoomed' : '')}>
        <Frame r={r} />
        {/* תקריב קבוע ליד הטופס: נפתח בלחיצה על ריבוע שדה */}
        {r && S.zoomOpen ? <ZoomPanel key={zoomSig(r)} r={r} /> : <div className="zoompanel hidden" id="fvZoom" aria-live="polite"></div>}
      </div>
      <Cards r={r} />
      <div className="formnav">
        <div className="dots" id="formDots" aria-hidden="true"
          onClick={(e) => { const i = Array.from(e.currentTarget.children).indexOf(e.target as Element); if (i >= 0) goForm(i - S.formIndex); }}>
          {r && list.length <= 60 ? list.map((x, i) => {
            const cls = isApproved(x) ? 'done' : x.review_count ? 'review' : '';
            return <i key={x.record_id} className={(cls + (i === S.formIndex ? ' now' : '')) || undefined}></i>;
          }) : null}
        </div>
        <span className="fvacts">
          <Btn className="secondary" id="fvNew" onClick={() => startNewIntake()}>⬆ קליטה חדשה</Btn>
          <Btn className="secondary" id="fvExport" disabled={!chips.exportReady} title={chips.exportWhy || 'הורדת כל הטפסים לקובץ Excel'}
            onClick={(e) => openExportPreview(e.currentTarget)}>⬇ ייצוא לאקסל</Btn>
          <Btn className="primary" id="fvApprove" disabled={!r || !!r.review_count || r.verified || S.processing}
            title={S.processing && r && !r.verified ? 'הקריאה עוד נמשכת — אפשר לעיין ולתקן, ואת הטופס מאשרים בסיום' : r && r.review_count ? 'נשארו ' + r.review_count + ' שדות לבדיקה' : ''}
            onClick={() => { const c = currentRecord(); if (c) verify(c.record_id); }}>
            {r && r.verified ? '✓ הטופס אושר' : '✓ אישור הטופס'}
          </Btn>
        </span>
      </div>
    </div>
  );
}

/* שני טפסים בעמוד אחד: המזכירה מחליטה לפצל — בחינם, בלי קריאה נוספת. בחלק שפוצל — "בטל פיצול". */
function SplitButton({ r }: { r: Rec | null }) {
  if (!r || S.processing) return <Btn className="secondary splitbtn hidden" id="fvSplit"></Btn>;
  const undo = r.split_from_page && !r.verified;
  const can = !r.split_from_page && !r.verified && !r.error && !r.joined;
  return (
    <Btn className={'secondary splitbtn' + (!undo && !can ? ' hidden' : '')} id="fvSplit"
      title={undo ? 'להחזיר את העמוד להיות טופס אחד — בחינם' : 'העמוד מכיל שני טפסים? לפצל — בחינם, בלי קריאה נוספת'}
      onClick={() => (undo
        ? joinAction('/api/records/' + r.record_id + '/unsplit-page', null, '✓ העמוד חזר להיות טופס אחד')
        : openSplitPick(r))}>
      {undo ? '↺ בטל פיצול' : '✂ פיצול לשני טפסים'}
    </Btn>
  );
}

function FailBar({ r }: { r: Rec | null }) {
  if (!r || !r.error) return <div className="failbar hidden" id="fvFailed" role="status" aria-live="polite"></div>;
  return (
    <div className="failbar" id="fvFailed" role="status" aria-live="polite">
      {rereading(r)
        ? <><span className="spin" aria-hidden="true"></span><span className="why"><b>קורא את הטופס שוב…</b> זה יכול לקחת כמה דקות. אפשר להמשיך לעבוד על טפסים אחרים.</span></>
        : <><span className="why"><b>✗ הקריאה של הטופס הזה נכשלה.</b> {r.error}</span>
            <Btn className="secondary" id="rrOpen" onClick={() => askReread(r.record_id)}>↻ קרא שוב</Btn></>}
    </div>
  );
}

/* טופס חתוך שהגיע בשני קבצים. הכול בלי קריאה ובחינם. */
function JoinBar({ r }: { r: Rec | null }) {
  const empty = <div className="failbar joinbar hidden" id="fvJoined" role="status" aria-live="polite"></div>;
  if (!r || S.processing) return empty;
  const canPick = r.can_join && joinCandidates(r).length > 0;
  const act = (a: string) => joinAction('/api/records/' + r.record_id + '/' + a, null,
    a === 'split' ? '✓ הטופס הופרד לשני טפסים — אפשר לחבר בחזרה' : '✓ הטופס חובר בחזרה');
  let text = null, buttons = null;
  if (r.joined) {
    text = <><b>⧉ שני חלקים חוברו לטופס אחד.</b> {r.straightened || ''}</>;
    if (!r.verified) buttons = <Btn className="secondary" data-join="split" onClick={() => act('split')}>הפרד לשני טפסים</Btn>;
  } else if (r.split_from_joined && !r.verified) {
    text = <><b>⧉ הטופס הזה הופרד מטופס מחובר.</b> אם ההפרדה הייתה בטעות — אפשר לחבר בחזרה, בלי עלות.</>;
    buttons = <><Btn className="secondary" data-join="rejoin" onClick={() => act('rejoin')}>⧉ חבר בחזרה</Btn>
      {canPick ? <Btn className="secondary" data-join="pick" onClick={() => openJoinPick(r)}>חבר עם חלק אחר</Btn> : null}</>;
  } else if (canPick) {
    text = <><b>ייתכן שזה רק חלק מטופס.</b> אם החלק השני הועלה בקובץ נפרד — אפשר לחבר ביניהם, בלי עלות.</>;
    buttons = <Btn className="secondary" data-join="pick" onClick={() => openJoinPick(r)}>⧉ חבר עם חלק אחר</Btn>;
  }
  if (!text) return empty;
  return <div className="failbar joinbar" id="fvJoined" role="status" aria-live="polite"><span className="why">{text}</span>{buttons}</div>;
}

function StoredBar({ r }: { r: Rec | null }) {
  if (!r || !r.from_store) return <div className="failbar joinbar hidden" id="fvStored" role="status" aria-live="polite"></div>;
  return (
    <div className="failbar joinbar" id="fvStored" role="status" aria-live="polite">
      <span className="why"><b>🗄 {storedText(r)}.</b> {r.verified ? 'הטופס כבר מאושר. אפשר לתקן — כל שינוי יחייב לאשר את הטופס שוב.'
        : 'בדקו את השינוי ולחצו "אישור הטופס".'}</span>
    </div>
  );
}

/* ------------------------------------------------------------ הטופס המלא (מימין) */
function Frame({ r }: { r: Rec | null }) {
  const frame = useRef<HTMLDivElement>(null);
  const deg = r ? S.viewTurn[r.record_id] || 0 : 0;
  useLayoutEffect(() => {
    const page = frame.current && frame.current.querySelector('.pv-page') as HTMLElement | null;
    if (page) fitTurned(page, page.parentNode as HTMLElement, page.querySelector('img')!, deg, 0.62);
  });
  if (!r) return <div className="frame" id="fvFrame"><div className="ph">—</div></div>;
  const key = S.fields[S.cardIndex] ? S.fields[S.cardIndex].key : '';
  return (
    <div className="frame" id="fvFrame" ref={frame}>
      <div className="pv-sec"><div className="pv-lbl">
        <Btn className="ink-toggle" data-turn="1" title="סיבוב התצוגה" aria-label="סיבוב התצוגה" onClick={turnView}>↻</Btn>
        <Btn className={'ink-toggle wide' + (S.inkView ? ' on' : '')} data-ink="1" title="הגדלה עם כתב היד בשחור" onClick={toggleInk}>דיו</Btn>
        <Btn className="ink-toggle wide" data-zoom="1" title="הגדלת הטופס על כל המסך" onClick={(e) => openPageZoom(e.currentTarget)}>🔍 הגדלה</Btn>
      </div>
      <div className="pv-pagewrap"><div className="pv-page" role="button" tabIndex={0} data-zoom="1" title="לחצו להגדלה"
        aria-label="הגדלת הטופס על כל המסך"
        onClick={(e) => openPageZoom(e.currentTarget)}
        onKeyDown={(e) => {
          // Enter / רווח על הטופס הקטן פותחים את ההגדלה (ולא מאשרים שדה)
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); openPageZoom(e.currentTarget); }
        }}>
        <img src={pageUrl(r)} alt={'עמוד ' + r.page_number + ' של ' + r.source_name} />
        {key ? <Boxes r={r} fieldKey={key} /> : null}
        <div className="pv-box pv-follow" style={{ display: 'none', transition: 'none' }}></div>
        <span className="pv-zoomtag" aria-hidden="true">🔍 לחצו להגדלה</span>
      </div></div></div>
    </div>
  );
}

/* ------------------------------------------------------------ ריבועי השדות */
function Cards({ r }: { r: Rec | null }) {
  return (
    <div className="cards" id="fvCards" role="list" aria-label="שדות הטופס" style={{ ['--n' as string]: S.fields.length }}>
      {r ? S.fields.map((spec, i) => {
        const f = r.fields[spec.key];
        const cls = f.state === 'needs_review' ? 'review' : f.state === 'approved' ? 'approved' : '';
        const why = whyOf(f);
        const ed = S.editor;
        const editing = !!ed && ed.kind === 'card' && ed.rec === r.record_id && ed.key === spec.key;
        return (
          <div key={spec.key} className={'fcard ' + cls + (i === S.cardIndex ? ' now' : '') + (editing ? ' editing' : '')} role="listitem"
            tabIndex={0} data-i={i} data-key={spec.key} title={why || undefined}
            aria-label={spec.label + ': ' + (f.values.join(' | ') || 'ריק') +
              (cls === 'review' ? ', לבדיקה' + (why ? ': ' + why : '') : cls === 'approved' ? ', אושר' : '')}
            onClick={(e) => {
              if ((e.target as HTMLElement).closest('input')) return;
              const b = (e.target as HTMLElement).closest('button') as HTMLElement | null;
              if (b && b.dataset.act === 'approve') { approveField(r.record_id, spec.key, true); return; }
              clickCard(i);
              if (b && b.dataset.act === 'edit') startEdit('card', r.record_id, spec.key);
            }}>
            <span className="lbl">{spec.label}<HelpMark f={f} /></span>
            <span className="val"><ValueView f={f} /></span>
            <span className="btns">
              {canApprove(f) ? <Btn className="ok" data-act="approve" title={f.values.length ? 'אשר' : 'אשר כריק'} aria-label="אשר">✓</Btn> : null}
              <Btn data-act="edit" title="תקן" aria-label="תקן">✎</Btn></span>
            {editing ? <div className="cardEdit"><EditInput keyName={spec.key} stopKeys
              label={'תיקון ' + fieldLabel(spec.key) + ' — Enter לשמירה, Esc לביטול'} /></div> : null}
          </div>
        );
      }) : null}
    </div>
  );
}

/* ------------------------------------------------------------ תקריב ליד הטופס */
function zoomSig(r: Rec) {
  const spec = S.fields[S.cardIndex];
  const keys = isName(spec.key) ? NAME_KEYS : [spec.key];
  return [r.record_id, spec.key, S.inkView, S.viewTurn[r.record_id] || 0].concat(keys.map((k) => {
    const f = r.fields[k];
    return f.state + f.values.join('|') + (f.help ? f.help.status : '');
  })).join('~');
}

function ZoomPanel({ r }: { r: Rec }) {
  const spec = S.fields[S.cardIndex], f = r.fields[spec.key];
  const keys = isName(spec.key) ? NAME_KEYS : [spec.key];
  const deg = S.viewTurn[r.record_id] || 0;
  const panel = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!deg || !panel.current) return;
    panel.current.querySelectorAll('.zcrop img').forEach((img) => fitTurned(img as HTMLElement, img.parentNode as HTMLElement, img as HTMLImageElement, deg, 0.5));
  });
  const why = whyOf(f);
  return (
    <div className="zoompanel" id="fvZoom" aria-live="polite" ref={panel}
      onClick={(e) => {
        const h = (e.target as HTMLElement).closest('[data-help]') as HTMLElement | null;
        if (h) helpAction(h.dataset.help!, r, spec.key, h);
      }}>
      <div className="zhead"><h3>{isName(spec.key) ? 'שם פרטי ושם משפחה' : spec.label}</h3>
        {f.state === 'needs_review' ? <span className="badge review">לבדיקה</span> : f.state === 'approved' ? <span className="badge ok">✓ אושר</span> : null}
        <span className="grow"></span>
        {!S.processing && canAskHelp(r, f) ? <Btn className="secondary helpbtn" data-help="ask" title="מתלבטת? לשאול ממונה — בהודעה אין פרטי מטופל">🙋 עזרה</Btn> : null}
        <Btn className="closebtn" data-zoomclose="1" aria-label="סגירת התקריב" title="סגירה (Esc)" onClick={closeZoom}>✕</Btn></div>
      {why ? <p className="zwhy">{why}</p> : null}
      <HelpStrip f={f} fieldKey={spec.key} />
      <ZoomCrops r={r} fieldKey={spec.key} deg={deg} />
      <div className="zval">{keys.map((k) => (
        <div key={k} className={'zrow' + (k === spec.key && keys.length > 1 ? ' now' : '')}>
          <span>{keys.length > 1 ? fieldLabel(k) + ' — המנוע קרא:' : 'המנוע קרא:'}</span> <b><ValueView f={r.fields[k]} /></b>
        </div>))}</div>
    </div>
  );
}

// התמונה בתקריב: חיתוך אחד, או לשמות — חיתוך משותף כשהם באותו אזור, אחרת שניים זה מתחת לזה
function ZoomCrops({ r, fieldKey, deg }: { r: Rec; fieldKey: string; deg: number }) {
  const box = (k: string) => {
    if (k === 'full_name') {
      const a = r.fields.first_name.crop_box, b = r.fields.last_name.crop_box;
      return a && b ? [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])] : (a || b);
    }
    return r.fields[k].crop_box;
  };
  // בדף ישר — חלון שאפשר לגרור על העמוד המלא; בדף מסובב — התמונה החתוכה
  const crop = (k: string, label: string, i?: number) => {
    const b = i != null ? r.fields[k].crop_boxes[i] : box(k);
    if (!deg && b) return <ZPan r={r} b={b} label={label} vk={viewKey(r.record_id, k, i)} />;
    return <div className="zcrop pv-cropwrap"><img alt={'תקריב של ' + label} src={cropUrl(r, k, i)} /></div>;
  };
  if (isName(fieldKey)) {
    if (r.full_name_crop) return <>{crop('full_name', 'שם פרטי ושם משפחה')}<PlaceNote r={r} keys={NAME_KEYS} /></>;
    const withBox = NAME_KEYS.filter((k) => r.fields[k].crop_box);
    const a = r.fields.first_name.crop_box, b = r.fields.last_name.crop_box;
    if (withBox.length === 2 && a!.join() === b!.join()) return <>{crop(fieldKey, fieldLabel(fieldKey))}<PlaceNote r={r} keys={NAME_KEYS} /></>;
    if (withBox.length) return <>{withBox.map((k) => <Fragment key={k}>
      <p className="zsub">{fieldLabel(k)}</p>{crop(k, fieldLabel(k))}</Fragment>)}<PlaceNote r={r} keys={withBox} /></>;
  } else if (perValue(r.fields[fieldKey])) {
    const f = r.fields[fieldKey];
    return <>{f.crop_boxes.map((b, i) => (b ? <Fragment key={i}>
      <p className="zsub">{fieldLabel(fieldKey)} — <bdi>{f.values[i]}</bdi></p>{crop(fieldKey, f.values[i], i)}</Fragment> : null))}
      <PlaceNote r={r} keys={[fieldKey]} /></>;
  } else if (r.fields[fieldKey].crop_box) {
    return <>{crop(fieldKey, fieldLabel(fieldKey))}<PlaceNote r={r} keys={[fieldKey]} /></>;
  }
  return <div className="zcrop none">אין מיקום לשדה הזה בטופס — בדקו בעמוד המלא שמימין</div>;
}

// מה ידוע על המיקום: אומת מול הטופס, תוקן אוטומטית, או רק לפי המנוע
function PlaceNote({ r, keys }: { r: Rec; keys: string[] }) {
  const notes = keys.map((k) => r.fields[k].region_note);
  const text = notes.every((n) => n === 'verified') ? 'המיקום אומת מול הטופס'
    : notes.every((n) => n === 'verified' || n === 'fixed') ? 'המיקום תוקן אוטומטית לפי הטופס'
    : 'מיקום לפי המנוע — ייתכן שאינו מדויק. אם הערך לא נראה, השתמשו ב-🔍 הגדלה';
  return <p className="znote">האזור המסומן בכתום בטופס · {text}</p>;
}

/* ------------------------------------------------------------ תקריב שגוררים (רק במצב טופס) */
// התקריב הוא חלון על העמוד המלא: בהתחלה בדיוק על השדה, ואפשר לגרור ולראות מה מסביב.
// המסגרת הכתומה על הטופס שמימין זזה יחד איתו — תמיד רואים איפה עומדים.
// המיקום נשמר ב-zoomViews: חוזרים לשדה — התקריב במקום שהשאירו אותו, ו-🙋 עזרה שולחת בדיוק אותו.
function ZPan({ r, b, label, vk }: { r: Rec; b: number[]; label: string; vk: string }) {
  const view = useRef<HTMLDivElement>(null);
  const img = useRef<HTMLImageElement>(null);
  const homeBtn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const v = view.current!, im = img.current!;
    let home = () => {};
    const ready = () => {
      const W = im.naturalWidth, H = im.naturalHeight;
      const rw = (b[2] - b[0]) * W, rh = (b[3] - b[1]) * H;
      const vw = v.clientWidth;
      const vh = Math.max(110, Math.min(vw * rh / rw, window.innerHeight * 0.36));
      v.style.height = vh + 'px';
      const z = Math.min(vw / rw, vh / rh);
      im.style.width = W * z + 'px';
      im.style.height = H * z + 'px';
      const st = { tx: 0, ty: 0 };
      const page = () => document.querySelector('#fvFrame .pv-page');
      const follow = (box: number[] | null) => {
        const p = page();
        if (!p) return;
        p.querySelectorAll('.pv-box:not(.pv-follow)').forEach((x) => { (x as HTMLElement).style.display = box ? 'none' : ''; });
        const f = p.querySelector('.pv-follow') as HTMLElement | null;
        if (!f) return;
        f.style.display = box ? '' : 'none';
        if (box) Object.assign(f.style, { left: box[0] * 100 + '%', top: box[1] * 100 + '%',
          width: (box[2] - box[0]) * 100 + '%', height: (box[3] - box[1]) * 100 + '%' });
      };
      const place = (moved: boolean) => {
        st.tx = Math.min(0, Math.max(vw - W * z, st.tx));
        st.ty = Math.min(0, Math.max(vh - H * z, st.ty));
        im.style.transform = 'translate(' + st.tx + 'px,' + st.ty + 'px)';
        if (!moved) return;
        homeBtn.current?.classList.remove('hidden');
        const box = [-st.tx / (W * z), -st.ty / (H * z), Math.min(1, (vw - st.tx) / (W * z)), Math.min(1, (vh - st.ty) / (H * z))];
        zoomViews.set(vk, box);
        follow(box);
      };
      home = () => {
        st.tx = vw / 2 - (b[0] + b[2]) / 2 * W * z;
        st.ty = vh / 2 - (b[1] + b[3]) / 2 * H * z;
        place(false);
        zoomViews.delete(vk);
        homeBtn.current?.classList.add('hidden');
        follow(null);  // המסגרות המקוריות חוזרות לטופס
      };
      const kept = zoomViews.get(vk);
      if (kept) {
        st.tx = -kept[0] * W * z;
        st.ty = -kept[1] * H * z;
        place(true);
      } else {
        home();
      }
      let last: { x: number; y: number } | null = null;
      v.onpointerdown = (e) => {
        if (e.button !== 0 || (e.target as HTMLElement).closest('.zhome')) return;
        v.setPointerCapture(e.pointerId);
        last = { x: e.clientX, y: e.clientY };
        v.classList.add('drag');
      };
      v.onpointermove = (e) => {
        if (!last) return;
        st.tx += e.clientX - last.x;
        st.ty += e.clientY - last.y;
        last = { x: e.clientX, y: e.clientY };
        place(true);
      };
      v.onpointerup = v.onpointercancel = () => { last = null; v.classList.remove('drag'); };
      homeBtn.current!.onclick = (e) => { e.stopPropagation(); home(); v.focus({ preventScroll: true }); };
      v.onkeydown = (e) => {
        const step = 40;
        const moves: Record<string, number[]> = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
        if (e.key === 'Home') { e.preventDefault(); e.stopPropagation(); return home(); }
        if (!moves[e.key]) return;
        e.preventDefault();
        e.stopPropagation();  // החצים מזיזים את התקריב, לא עוברים שדה/טופס
        st.tx += moves[e.key][0];
        st.ty += moves[e.key][1];
        place(true);
      };
    };
    if (im.complete && im.naturalWidth) ready(); else im.addEventListener('load', ready, { once: true });
    return () => {
      const p = document.querySelector('#fvFrame .pv-page');
      if (p) {
        p.querySelectorAll('.pv-box:not(.pv-follow)').forEach((x) => { (x as HTMLElement).style.display = ''; });
        const f = p.querySelector('.pv-follow') as HTMLElement | null;
        if (f) f.style.display = 'none';
      }
    };
  }, []);
  return (
    <div className="zcrop zpan" tabIndex={0} role="img" ref={view} data-box={b.join(',')}
      aria-label={'תקריב של ' + label + ' — גררו כדי לזוז במסמך, Home חוזר לשדה'}>
      <img alt="" draggable={false} src={pageSrc(r)} ref={img} />
      <button className="zhome hidden" type="button" tabIndex={-1} title="חזרה לשדה (Home)" ref={homeBtn}>↺ חזרה לשדה</button>
    </div>
  );
}

/* ------------------------------------------------------------ 🙋 עזרה: מצב הבקשה ליד השדה */
export function HelpStrip({ f, fieldKey }: { f: Field; fieldKey: string }) {
  const h = f.help;
  if (!h) return null;
  if (h.status === 'open') {
    return (
      <div className="zhelp wait" role="status">
        {h.expired
          ? <span>⌛ <b>הקישור פג תוקף</b> (48 שעות) ולא התקבלה תשובה. אפשר לשלוח קישור חדש.</span>
          : <span>⏳ <b>נשלח לעזרה</b> — ממתין לתשובה. עד אז השדה לא יאושר.</span>}
        <span className="zhelpbtns"><Btn className="secondary" data-help="share">שליחה שוב</Btn>
          <Btn className="secondary" data-help="cancel">ביטול הבקשה</Btn></span>
      </div>
    );
  }
  const ltr = ['national_id', 'central_numbers', 'sample_received_date', 'commitment_numbers'].includes(fieldKey) ? 'ltr' : 'rtl';
  return (
    <div className="zhelp got" role="status">
      <span>💬 <b>{h.answered_by || 'התקבלה תשובה'}:</b> {h.answer_value ? <bdi dir={ltr} className="hv">{h.answer_value}</bdi> : null}
        {h.answer_note ? (h.answer_value ? ' · ' : '') + h.answer_note : null}</span>
      {h.answer_value ? <span className="zhelpbtns"><Btn className="primary" data-help="use">✓ להשתמש בערך הזה</Btn></span> : null}
      <small className="zhelpnote">ההחלטה שלך: אפשר להשתמש בערך, לתקן בעצמך, או לאשר את מה שיש.</small>
    </div>
  );
}
