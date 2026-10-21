/* כל החלונות. כל חלון יושב בתוך Modal של MUI — הפוקוס נשאר בפנים, Esc סוגר — והמבנה שבפנים
 * זהה למה שהיה (אותן מחלקות, אותו עיצוב). חלון סגור נשאר בדף עם "hidden", כמו קודם. */
import { Fragment, useState } from 'react';
import type { KeyboardEvent, ReactElement } from 'react';
import Modal from '@mui/material/Modal';
import { S, emit, fieldLabel, pageSrc, pageUrl, recordById } from '../store';
import { LTR_KEYS, plural } from '../lib';
import {
  closeExportPreview, closeHelpAsk, closeModal, closeTour, deleteYes, downloadExcel, helpPane, helpSend, helpStep, helpViews,
  joinCandidates, joinPicked, joinThisOnTopDefault, pickPart, rereadYes, resetToUpload, splitPicked, storedYes,
} from '../actions';
import { Btn } from './Bits';

function Dialog({ open, onClose, children }: { open: boolean; onClose: (why: string) => void; children: ReactElement }) {
  return (
    <Modal open={open} onClose={(_e, why) => onClose(why)} keepMounted disablePortal disableAutoFocus disableRestoreFocus
      disableScrollLock hideBackdrop>
      {children}
    </Modal>
  );
}
const outside = (close: () => void) => (e: React.MouseEvent) => { if (e.target === e.currentTarget) close(); };

export function Modals() {
  return <>
    <ExcelPreview />
    <StoreConfirm />
    <DeleteConfirm />
    <SplitPick />
    <HelpAsk />
    <JoinPick />
    <RereadConfirm />
    <Tour />
  </>;
}

/* תצוגה מקדימה של קובץ האקסל: בדיוק השורות שייכנסו לקובץ, לפני ההורדה */
function ExcelPreview() {
  const open = S.modal === 'xl';
  const xl = S.xl;
  const d = xl && xl.data;
  const perForm: Record<string, number> = {};
  if (d) d.rows.forEach((r: any) => { perForm[r.form] = (perForm[r.form] || 0) + 1; });
  const done = !!(xl && xl.done);
  return (
    <Dialog open={open} onClose={closeExportPreview}>
      <div id="xlPreview" className={'modal' + (open ? '' : ' hidden')} role="dialog" aria-modal="true" aria-labelledby="xlTitle"
        aria-describedby="xlNote" onClick={outside(closeExportPreview)}>
        <div className="modalbox">
          <div className="modalhead">
            <div>
              <h2 id="xlTitle">תצוגה מקדימה של קובץ האקסל</h2>
              <div className="xlsum">
                <span className="badge ready" id="xlSummary">{d ? plural(d.forms, 'טופס אחד', 'טפסים') + ' · ' + plural(d.row_count, 'שורה אחת', 'שורות') : ''}</span>
                <span className="sub" id="xlSources">{d ? (d.sources === 1 ? 'מקובץ אחד' : 'מתוך ' + d.sources + ' קבצים') : ''}</span>
              </div>
              <p className="sub" id="xlNote">שורה לכל מספר התחייבות. טופס עם כמה מספרים מופיע בכמה שורות, מסומן בפס כחול.</p>
            </div>
            <Btn className="closebtn" id="xlClose" aria-label="סגירת התצוגה המקדימה" title="סגירה" onClick={closeExportPreview}>✕</Btn>
          </div>
          <div className="xlwrap" tabIndex={0} aria-label="שורות הקובץ">
            <table className="xltable">
              <thead><tr id="xlHead">{d ? <><th className="rn" scope="col">#</th>{d.headers.map((h: string, i: number) => <th key={i} scope="col">{h}</th>)}</> : null}</tr></thead>
              <tbody id="xlBody">{d ? d.rows.map((r: any, i: number) => {
                const split = perForm[r.form] > 1;
                return <tr key={i} className={'band' + (r.form % 2) + (split ? ' split' : '')}><td className="rn">{i + 1}</td>
                  {r.cells.map((c: string, col: number) => (
                    <td key={col} className={split && col === d.commitment_column ? 'hl' : undefined} title={c.length > 30 ? c : undefined}><bdi>{c}</bdi></td>
                  ))}</tr>;
              }) : null}</tbody>
            </table>
          </div>
          <div className="modalfoot">
            <span className="xlfile" id="xlFile">{d ? 'commitments-' + S.batch + '.xlsx' : ''}</span>
            <span className="grow"></span>
            <span className={'xldone' + (done ? '' : ' hidden')} id="xlDone">✓ הקובץ ירד</span>
            <Btn className="secondary" id="xlCancel" onClick={closeExportPreview}>{done ? 'סגירה' : 'ביטול'}</Btn>
            <Btn className={'primary' + (done ? ' hidden' : '')} id="xlDownload" onClick={async (e) => {
              const b = e.currentTarget as HTMLButtonElement;
              b.disabled = true;
              const ok = await downloadExcel();
              b.disabled = false;
              if (!ok || !S.xl) return;
              S.xl.done = true;
              emit();
              setTimeout(() => document.getElementById('xlNew')?.focus());
            }}>⬇ הורדת הקובץ</Btn>
            <Btn className={'primary' + (done ? '' : ' hidden')} id="xlNew" onClick={() => { closeExportPreview(); resetToUpload(); }}>⬆ קליטה חדשה</Btn>
          </div>
        </div>
      </div>
    </Dialog>
  );
}

/* שינוי בטופס שהגיע מהמערכת: שואלים קודם. ברירת המחדל: ביטול */
function Confirm({ id, name, title, text, yes, yesClass, onYes, cancelId, yesId, titleId, textId }: {
  id: string; name: typeof S.modal; title: string; text: string; yes: string; yesClass: string; onYes: () => void;
  cancelId: string; yesId: string; titleId: string; textId: string;
}) {
  const open = S.modal === name;
  const close = () => closeModal();
  return (
    <Dialog open={open} onClose={close}>
      <div id={id} className={'modal' + (open ? '' : ' hidden')} role="alertdialog" aria-modal="true" aria-labelledby={titleId}
        aria-describedby={textId} onClick={outside(close)}>
        <div className="modalbox rrbox">
          <div className="modalhead"><div>
            <h2 id={titleId}>{title}</h2>
            <p className="sub" id={textId}>{text}</p>
          </div></div>
          <div className="modalfoot">
            <span className="grow"></span>
            <Btn className="secondary" id={cancelId} onClick={close}>ביטול</Btn>
            <Btn className={yesClass} id={yesId} disabled={name === 'reread' && S.rereadBusy} onClick={onYes}>{yes}</Btn>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
function StoreConfirm() {
  return <Confirm id="storeConfirm" name="stored" titleId="stTitle" textId="stText" cancelId="stCancel" yesId="stYes"
    title="לשנות נתונים מהמערכת?" yesClass="primary" yes="✎ כן, לשנות" onYes={storedYes}
    text="הנתונים בטופס הזה הגיעו מהמערכת — הם כבר נבדקו ואושרו בעבר. אם תשני אותם, יהיה צריך לאשר את הטופס שוב. להמשיך?" />;
}
function DeleteConfirm() {
  return <Confirm id="delConfirm" name="del" titleId="delTitle" textId="delText" cancelId="delCancel" yesId="delYes"
    title="למחוק את הטופס המאושר?" yesClass="primary danger" yes="🗑 כן, מחק" onYes={deleteYes}
    text="כל השורות של הטופס, תמונת הטופס והתקריבים שלו יימחקו לצמיתות. אי אפשר לבטל." />;
}
// קריאה חוזרת עולה כמו קריאת טופס אחד — לכן אף פעם לא אוטומטית: חלון שואל, ורק "כן" שולח
function RereadConfirm() {
  return <Confirm id="rrConfirm" name="reread" titleId="rrTitle" textId="rrText" cancelId="rrCancel" yesId="rrYes"
    title="לקרוא את הטופס שוב?" yesClass="primary" yes="↻ כן, קרא שוב" onYes={rereadYes}
    text="קריאה חוזרת של הטופס הזה שולחת אותו שוב ל-Claude ועולה כמו קריאת טופס אחד. להמשיך?" />;
}

/* פיצול עמוד לשני טפסים — בחינם, בלי קריאה נוספת */
function SplitPick() {
  const open = S.modal === 'split';
  const keys = (e: KeyboardEvent) => {
    const items = Array.from(document.querySelectorAll('.spitem')) as HTMLElement[];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (at !== -1 && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
      e.preventDefault();
      const next = items[(at + 1) % items.length];
      S.split.axis = next.dataset.axis!;
      emit();
      next.focus();
    }
  };
  return (
    <Dialog open={open} onClose={closeModal}>
      <div id="splitPick" className={'modal' + (open ? '' : ' hidden')} role="dialog" aria-modal="true" aria-labelledby="spTitle"
        aria-describedby="spText" onClick={outside(closeModal)} onKeyDown={keys}>
        <div className="modalbox spbox">
          <div className="modalhead"><div>
            <h2 id="spTitle">לפצל את העמוד לשני טפסים?</h2>
            <p className="sub" id="spText">בוחרים איך שני הטפסים מסודרים בעמוד. בחינם, בלי קריאה נוספת: כל ערך עובר לטופס שבו הוא נמצא בעמוד, וכל השדות יסומנו לבדיקה. אפשר לבטל את הפיצול אחר כך.</p>
          </div></div>
          <div className="splist" role="radiogroup" aria-label="סידור הטפסים בעמוד">
            {([['y', 'אחד מעל השני'], ['x', 'זה לצד זה']] as const).map(([axis, label]) => (
              <Btn key={axis} className="spitem" role="radio" aria-checked={S.split.axis === axis ? 'true' : 'false'} data-axis={axis}
                onClick={() => { S.split.axis = axis; emit(); }}>
                <span className={'spicon spicon-' + axis} aria-hidden="true"><i></i><i></i></span>{label}</Btn>
            ))}
          </div>
          <div className="modalfoot">
            <span className="grow"></span>
            <Btn className="secondary" id="spCancel" onClick={closeModal}>ביטול</Btn>
            <Btn className="primary" id="spGo" onClick={async (e) => {
              const b = e.currentTarget as HTMLButtonElement;
              b.disabled = true;
              await splitPicked();
              b.disabled = false;
            }}>✂ פצל לשני טפסים</Btn>
          </div>
        </div>
      </div>
    </Dialog>
  );
}

/* חיבור ידני של שני חלקים של טופס חתוך: בוחרים את החלק השני, ואז מי למעלה. בלי עלות. */
function JoinPick() {
  const open = S.modal === 'join';
  const { rec, other } = S.join;
  const list = rec ? joinCandidates(rec) : [];
  const onTop = joinThisOnTopDefault();
  const keys = (e: KeyboardEvent) => {
    const items = Array.from(document.querySelectorAll('#jpList .jpitem')) as HTMLElement[];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (at !== -1 && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
      e.preventDefault();
      // RTL: חץ שמאלה = הבא
      const step = e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? 1 : -1;
      items[(at + step + items.length) % items.length].focus();
    } else if (at !== -1 && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      choose(items[at].dataset.rec!);
    }
  };
  const choose = (id: string) => {
    pickPart(id);
    // ברירת המחדל: החלק עם פרטי המטופל למעלה
    setTimeout(() => document.getElementById(joinThisOnTopDefault() ? 'jpAbove' : 'jpBelow')?.focus());
  };
  return (
    <Dialog open={open} onClose={closeModal}>
      <div id="joinPick" className={'modal' + (open ? '' : ' hidden')} role="dialog" aria-modal="true" aria-labelledby="jpTitle"
        aria-describedby="jpText" onClick={outside(closeModal)} onKeyDown={keys}>
        <div className="modalbox jpbox">
          <div className="modalhead"><div>
            <h2 id="jpTitle">לחבר עם איזה חלק?</h2>
            <p className="sub" id="jpText">בוחרים את החלק השני של אותו טופס. מוצגים רק חלקים של טפסים חתוכים מההעלאה הזו. החיבור לא עולה כסף, ואפשר להפריד אחר כך.</p>
          </div></div>
          <div className="jplist" id="jpList" role="listbox" aria-label="חלקים לחיבור">
            {list.map((x) => {
              const who = [x.fields.first_name.values[0], x.fields.last_name.values[0]].filter(Boolean).join(' ') ||
                (x.fields.commitment_numbers.values[0] ? 'התחייבות ' + x.fields.commitment_numbers.values[0] : '');
              return (
                <Btn key={x.record_id} className="jpitem" role="option" aria-selected={other && other.record_id === x.record_id ? 'true' : 'false'}
                  data-rec={x.record_id} onClick={() => choose(x.record_id)}>
                  <img alt="" src={pageUrl(x) + '/thumb'} /><span><b>טופס {S.last!.records.indexOf(x) + 1}</b>
                    <bdi>{x.source_name}</bdi>{who ? <small><bdi>{who}</bdi></small> : null}</span>
                </Btn>
              );
            })}
          </div>
          <div className="modalfoot">
            <span className="grow"></span>
            <Btn className="secondary" id="jpCancel" onClick={closeModal}>ביטול</Btn>
            <Btn className={other && !onTop ? 'primary' : 'secondary'} id="jpBelow" disabled={!other} onClick={() => joinPicked(false)}>⧉ חבר — הטופס הזה למטה</Btn>
            <Btn className={other && onTop ? 'primary' : 'secondary'} id="jpAbove" disabled={!other} onClick={() => joinPicked(true)}>⧉ חבר — הטופס הזה למעלה</Btn>
          </div>
        </div>
      </div>
    </Dialog>
  );
}

/* 🙋 עזרה: קודם בוחרים דרך (מייל / וואטסאפ / קישור), ואז מסך נפרד לכל אחת. בהודעה אין פרטי מטופל. */
// אזור שהמזכירה בחרה בגרירה — חלון על העמוד המלא, באותם יחסים שהממונה יקבל
function ViewCrop({ src, box, alt }: { src: string; box: number[]; alt: string }) {
  const [ratio, setRatio] = useState(0);
  const w = box[2] - box[0], h = box[3] - box[1];
  return (
    <div className="haview" role="img" aria-label={alt}
      style={ratio ? { aspectRatio: String(ratio), height: 'auto', width: 'min(100%, calc(9rem * ' + ratio + '))' } : undefined}>
      <img alt="" src={src} onLoad={(e) => setRatio(w * e.currentTarget.naturalWidth / (h * e.currentTarget.naturalHeight))}
        style={{ width: 100 / w + '%', height: 100 / h + '%', left: -box[0] / w * 100 + '%', top: -box[1] / h * 100 + '%' }} />
    </div>
  );
}

function HelpAsk() {
  const open = S.modal === 'help';
  const H = S.help;
  const r = H.rec ? recordById(H.rec) : null;
  const f = r ? r.fields[H.key] : null;
  const local = /^(localhost|127\.|\[::1\])/.test(location.hostname);
  const set = (k: 'question' | 'asker' | 'to' | 'phone', v: string) => { (H as any)[k] = v; emit(); };
  const keys = (e: KeyboardEvent) => {
    const tiles = Array.from(document.querySelectorAll('#haPick .hachan')) as HTMLElement[];
    const at = tiles.indexOf(document.activeElement as HTMLElement);
    if (at !== -1 && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
      e.preventDefault();
      const step = e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? 1 : -1;  // RTL: שמאלה = הבא
      tiles[(at + step + tiles.length) % tiles.length].focus();
    }
  };
  // Esc במסך של דרך — חזרה לבחירה; בבחירה — סגירה
  const onClose = () => (H.pane && !H.done ? helpPane('') : closeHelpAsk());
  // מה שהממונה יראה: התקריב כפי שהמזכירה הזיזה אותו, ואם לא הזיזה — התקריב על השדה.
  // בקשה שכבר נשלחה — בדיוק התקריבים שנשלחו
  const live = f && f.help && f.help.status === 'open' && !f.help.expired ? f.help : null;
  const views = r && f && !live ? helpViews(r, H.key) : [];
  const shown = (i: number | undefined, alt: string) => {
    const v = views[i ?? 0];
    return v ? <ViewCrop key={i ?? 0} src={pageSrc(r!)} box={v} alt={alt + ' (כפי שהזזת)'} />
      : <img key={i ?? 0} alt={alt} src={cropSrc(i)} />;
  };
  const crops = live ? (live.crops ? Array.from({ length: live.crops }, (_, i) => (
      <img key={i} alt={'התקריב שנשלח לממונה'} src={'/api/help/' + encodeURIComponent(live.token) + '/crops/' + i} />)) : null)
    : f ? (f.crop_boxes || []).length
    ? f.crop_boxes.map((b, i) => (b ? shown(i, 'תקריב של ' + f.values[i]) : null))
    : f.crop_box ? shown(undefined, 'תקריב של ' + fieldLabel(H.key)) : null : null;
  function cropSrc(i?: number) {
    const q: string[] = [];
    if (S.inkView) q.push('ink=1');
    if (i != null) q.push('i=' + i);
    return pageUrl(r!) + '/crops/' + H.key + (q.length ? '?' + q.join('&') : '');
  }
  const ltr = LTR_KEYS.includes(H.key) ? 'ltr' : 'rtl';
  const busy = (c: string) => H.busy === c;
  return (
    <Dialog open={open} onClose={onClose}>
      <div id="helpAsk" className={'modal' + (open ? '' : ' hidden')} role="dialog" aria-modal="true" aria-labelledby="haTitle"
        aria-describedby="haText" onClick={outside(closeHelpAsk)} onKeyDown={keys}>
        <div className="modalbox habox">
          <div className="modalhead"><div>
            <h2 id="haTitle">🙋 בקשת עזרה{H.key ? ' — ' + fieldLabel(H.key) : ''}</h2>
            <p className="sub" id="haText">נשלח קישור לממונה. <b>בהודעה עצמה אין פרטי מטופל</b> — רק הקישור. מי שפותח אותו רואה רק את התקריב של השדה הזה ועונה. עד שתגיע תשובה (או שתבטלי את הבקשה) השדה לא יאושר, וההחלטה נשארת שלך.</p>
          </div></div>
          <div className={'habody' + (H.done ? ' hidden' : '')} id="haForm">
            <div className="hacrops" id="haCrops">{crops || <p className="sub">אין תקריב לשדה הזה — הממונה יראה רק את השאלה והערכים.</p>}</div>
            <p className="havalues" id="haValues">{f ? (f.values.length
              ? <>הממונה יראה את {f.values.length > 1 ? 'הערכים' : 'הערך'}: {f.values.map((v, i) => <Fragment key={i}>{i ? ' · ' : ''}<b><bdi dir={ltr}>{v}</bdi></b></Fragment>)}</>
              : 'השדה ריק — הממונה יתבקש לכתוב את הערך.') : null}</p>
            <label className="halbl" htmlFor="haQuestion">השאלה</label>
            <textarea id="haQuestion" rows={2} maxLength={500} value={H.question} disabled={H.sent} onChange={(e) => set('question', e.target.value)} />
            <small className="hahint">בלי פרטים מזהים — הממונה רואה את התקריב.</small>
            <label className="halbl" htmlFor="haAsker">השם שלך (יופיע בהודעה)</label>
            <input id="haAsker" maxLength={60} autoComplete="name" value={H.asker} disabled={H.sent} onChange={(e) => set('asker', e.target.value)} />

            {/* איך לשלוח: קודם בוחרים דרך, ואז מסך נפרד לכל אחת (החלטת נועה, 21/10/2026) */}
            <div className="hato">
              <div id="haPick" className={H.pane ? 'hidden' : undefined}>
                <span className="halbl" id="haPickLbl">איך לשלוח?</span>
                <div className="hapick" role="group" aria-labelledby="haPickLbl">
                  <Btn className="hachan" data-pane="mail" onClick={() => helpPane('mail')}><span className="haicon" aria-hidden="true">✉</span>מייל</Btn>
                  <Btn className="hachan" data-pane="wa" onClick={() => helpPane('wa')}><span className="haicon wa" aria-hidden="true">✆</span>וואטסאפ</Btn>
                  <Btn className="hachan" data-pane="link" onClick={() => helpPane('link')}><span className="haicon" aria-hidden="true">🔗</span>קישור</Btn>
                </div>
              </div>

              <div className={'hapane' + (H.pane === 'mail' ? '' : ' hidden')} id="haPaneMail" role="group" aria-label="שליחה במייל">
                <Btn className="haback" data-pane="" onClick={() => helpPane('')}>→ חזרה</Btn>
                <label className="halbl" htmlFor="haTo">למי לשלוח? — מייל של הממונה</label>
                <input id="haTo" type="email" dir="ltr" list="haToList" maxLength={120} placeholder="name@example.com" autoComplete="email"
                  value={H.to} onChange={(e) => set('to', e.target.value)} />
                <datalist id="haToList">{recentList('helpTo')}</datalist>
                <div className="hasend">
                  <span className="hasendlbl">פתיחת מייל מוכן ב:</span>
                  <Btn className="primary" id="haGmail" disabled={busy('gmail')} onClick={() => helpSend('gmail')}>Gmail</Btn>
                  <Btn className="primary" id="haOutlook" disabled={busy('outlook')} onClick={() => helpSend('outlook')}>Outlook</Btn>
                  <Btn className="secondary" id="haMail" disabled={busy('mail')} title="תוכנת המייל שמותקנת במחשב — רק אם יש כזו" onClick={() => helpSend('mail')}>תוכנת מייל</Btn>
                </div>
              </div>

              <div className={'hapane' + (H.pane === 'wa' ? '' : ' hidden')} id="haPaneWa" role="group" aria-label="שליחה בוואטסאפ">
                <Btn className="haback" data-pane="" onClick={() => helpPane('')}>→ חזרה</Btn>
                <label className="halbl" htmlFor="haPhone">מספר הנייד של הממונה</label>
                <input id="haPhone" type="tel" dir="ltr" list="haPhoneList" maxLength={20} placeholder="05X-XXXXXXX" autoComplete="tel"
                  value={H.phone} onChange={(e) => set('phone', e.target.value)} />
                <datalist id="haPhoneList">{recentList('helpPhone')}</datalist>
                <small className="hahint">בלי מספר — וואטסאפ יבקש לבחור איש קשר.</small>
                <div className="hasend">
                  <Btn className="primary" id="haWhatsapp" disabled={busy('whatsapp')} onClick={() => helpSend('whatsapp')}>פתיחת וואטסאפ</Btn>
                </div>
              </div>

              <div className={'hapane' + (H.pane === 'link' ? '' : ' hidden')} id="haPaneLink" role="group" aria-label="קישור">
                <Btn className="haback" data-pane="" onClick={() => helpPane('')}>→ חזרה</Btn>
                <p className="hahint">יוצרים קישור ומעתיקים אותו — ואז מדביקים אותו בכל הודעה (Teams, SMS…).</p>
                <div className="hasend">
                  <Btn className="primary" id="haCopy" disabled={busy('copy')} onClick={() => helpSend('copy')}>🔗 העתקת קישור</Btn>
                </div>
              </div>
              <p className={'haerr' + (H.err ? '' : ' hidden')} id="haErr" role="alert">{H.err}</p>
            </div>
            <p className={'halocal' + (local ? '' : ' hidden')} id="haLocal">⚠ המערכת רצה עכשיו על המחשב הזה (localhost) — הקישור ייפתח רק במחשב הזה. בשרת המשותף הוא ייפתח מכל מקום.</p>
          </div>

          {/* אחרי השליחה: החלון לא נסגר — אומרים בדיוק מה קרה ומה נשאר לעשות */}
          <div className={'habody hadone' + (H.done ? '' : ' hidden')} id="haDone" role="status" aria-live="polite">
            <p className="hadonemain" id="haDoneText"><DoneText /></p>
            <p className="hadonealt" id="haDoneAlt"><DoneAlt /></p>
            <p className="sub">השדה מסומן ⏳ ממתין לתשובה. כשהממונה יענה, התשובה תופיע כאן לבד.</p>
          </div>
          <div className="modalfoot">
            <Btn className={'secondary' + (H.done ? ' hidden' : '')} id="haCancel" onClick={closeHelpAsk}>ביטול</Btn>
            <Btn className={'secondary' + (H.done ? '' : ' hidden')} id="haAgain" onClick={() => helpStep(false)}>שליחה שוב</Btn>
            <span className="grow"></span>
            <Btn className={'primary' + (H.done ? '' : ' hidden')} id="haFinish" onClick={closeHelpAsk}>סיום</Btn>
          </div>
        </div>
      </div>
    </Dialog>
  );
}

function recentList(key: string) {
  let list: string[] = [];
  try { list = JSON.parse(localStorage.getItem(key) || '[]'); } catch { /* דפדפן פרטי */ }
  return list.map((v) => <option key={v} value={v} />);
}

// מה קרה אחרי השליחה — אותו נוסח כמו קודם
function DoneText() {
  const t = S.help.doneText;
  if (!t) return null;
  if (t === 'copied') return <>✓ <b>הקישור הועתק.</b> הדביקי אותו בהודעה לממונה (Ctrl+V).</>;
  if (t === 'copyit') return <b>העתיקי את הקישור:</b>;
  const [channel, who] = [t.slice(0, t.indexOf(':')), t.slice(t.indexOf(':') + 1)];
  if (channel === 'mail') return <>✓ <b>ביקשנו מהמחשב לפתוח את תוכנת המייל</b> עם הודעה אל <bdi dir="ltr">{who}</bdi>. לחצי שם "שליחה".</>;
  const where = channel === 'gmail' ? 'ב-Gmail' : channel === 'outlook' ? 'ב-Outlook' : 'בוואטסאפ';
  const target = channel === 'whatsapp'
    ? (who ? <>צ'אט עם <bdi dir="ltr">{who}</bdi></> : <>וואטסאפ — בחרי שם את איש הקשר</>)
    : <>מייל אל <bdi dir="ltr">{who}</bdi></>;
  return <>✓ <b>נפתח {target}</b> {where}, בלשונית חדשה. ההודעה כבר כתובה — עברי ללשונית ולחצי "שליחה".</>;
}
function DoneAlt() {
  const H = S.help;
  if (H.linkBox) return <input className="halink" readOnly dir="ltr" value={H.linkBox} onClick={(e) => e.currentTarget.select()} />;
  if (H.doneAlt === 'nomailapp') return <>לא נפתחה תוכנה? כנראה שאין תוכנת מייל מוגדרת במחשב — חזרי ובחרי Gmail או Outlook.</>;
  // אם הדפדפן חסם את הלשונית — אותו קישור מופיע בחלון, ללחיצה
  if (H.doneAlt) return <> <a href={H.doneAlt} target="_blank" rel="noopener">לא נפתח? לחצי כאן</a></>;
  return null;
}

/* ------------------------------------------------------------ סיור קצר */
const TOUR = [
  { art: <>📄 ➜ 📊</>, title: 'גוררים טפסים',
    text: 'גוררים קבצי PDF או תמונות — כמה שרוצים. כל טופס נקרא לבד ומופיע ברגע שהוא מוכן. ' },
  { art: <><span className="demo-cell n">939376</span><span className="demo-cell r">00112?344</span><span className="demo-cell g">✓ מאוחדת</span></>,
    title: 'כתום = לבדוק', text: 'כתום — המנוע לא בטוח או שבדיקה אוטומטית נכשלה, והסיבה כתובה. ירוק — אדם בדק. תא רגיל — לא נמצא ספק.' },
  { art: <>🔍</>, title: 'מעבר על תא = תקריב',
    text: 'בטבלה: עכבר או Tab על תא מציגים תקריב של השדה. באריחים: לחיצה פותחת את הטופס — התמונה למעלה, השדות מתחת, וחצים לטופס הבא. ✓ מאשר, ✎ מתקן, ולכל פעולה יש "בטל". הייצוא לאקסל נפתח כשאין יותר שדות לבדיקה.' },
];

function Tour() {
  const open = S.modal === 'tour';
  const s = TOUR[S.tourStep] || TOUR[0];
  const next = () => { if (S.tourStep < TOUR.length - 1) { S.tourStep += 1; emit(); } else closeTour(); };
  return (
    <Dialog open={open} onClose={closeTour}>
      <div id="tour" className={'tour' + (open ? '' : ' hidden')} role="dialog" aria-modal="true" aria-labelledby="tourTitle"
        onClick={outside(closeTour)}>
        <div className="tourbox card">
          <div className="tourart" id="tourArt" aria-hidden="true">{s.art}</div>
          <h2 id="tourTitle">{s.title}</h2>
          <p id="tourText">{s.text}</p>
          <div className="tourfoot">
            <span className="tourdots" id="tourDots" aria-hidden="true">{TOUR.map((_, i) => <i key={i} className={i === S.tourStep ? 'on' : undefined}></i>)}</span>
            <Btn className="secondary" id="tourSkip" onClick={closeTour}>דלג</Btn>
            <Btn className="primary" id="tourNext" onClick={next}>{S.tourStep === TOUR.length - 1 ? 'מתחילים' : 'הבא'}</Btn>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
