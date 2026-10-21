/* הדף שהממונה פותח מהקישור: רק השדה ששאלו עליו — לא הטופס, לא שם המטופל, לא שדות אחרים.
 * אותו מבנה ואותו עיצוב כמו קודם; השרת מחליט מה מותר להציג (אחרי תשובה / 48 שעות — כלום). */
import { useEffect, useState } from 'react';
import ButtonBase from '@mui/material/ButtonBase';

type Req = {
  field: string; label: string; question: string; asker: string; options: string[]; crops: number;
  status: string; answered_by: string; answered_at: string;
};

const LTR = ['national_id', 'central_numbers', 'commitment_numbers', 'sample_received_date'];
const token = location.pathname.split('/').filter(Boolean).pop() || '';

function load(key: string) { try { return localStorage.getItem(key) || ''; } catch { return ''; } }

export function HelpPage() {
  const [req, setReq] = useState<Req | null>(null);
  const [missing, setMissing] = useState(false);
  const [chosen, setChosen] = useState('');
  const [other, setOther] = useState('');
  const [note, setNote] = useState('');
  const [name, setName] = useState(load('helpName'));
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<{ value: string; note: string } | null>(null);

  useEffect(() => {
    fetch('/api/help/' + encodeURIComponent(token))
      .then((res) => { if (!res.ok) throw new Error(); return res.json(); })
      .then(setReq)
      .catch(() => setMissing(true));
  }, []);

  const dir = req && LTR.includes(req.field) ? 'ltr' : 'rtl';
  const bdi = (v: string) => <bdi dir={dir}>{v}</bdi>;

  async function send() {
    const value = chosen || other.trim();
    const text = note.trim();
    setErr('');
    if (!value && !text) { setErr('יש לבחור ערך, לכתוב ערך אחר, או לכתוב הערה.'); return; }
    try { localStorage.setItem('helpName', name.trim()); } catch { /* דפדפן פרטי */ }
    setBusy(true);
    const res = await fetch('/api/help/' + encodeURIComponent(token) + '/answer', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value, note: text, name: name.trim() }),
    });
    const d = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) { setErr(d.detail || 'השליחה לא הצליחה — נסו שוב.'); return; }
    setSent({ value, note: text });  // הערך שנשלח מוצג רק מיד אחרי השליחה — השרת כבר לא מחזיר אותו
    setReq(d);
  }

  const open = req && req.status === 'open';
  return (
    <>
      <div className="govbar"><div className="inner">מערכת קליטת טפסי התחייבות · בקשת עזרה</div></div>
      <main>
        <h1 id="title">{missing ? 'הבקשה לא נמצאה' : req ? 'עזרה בשדה: ' + req.label : 'בקשת עזרה'}</h1>
        <p className="sub" id="who">
          {missing ? 'ייתכן שהקישור לא שלם.' : req ? (req.asker || 'המזכירה') + ' מתלבטת בשדה הזה בטופס התחייבות, ומבקשת את עזרתך.' : 'טוען…'}
        </p>

        <section className={'card' + (missing ? ' hidden' : '')} id="ask" aria-labelledby="title">
          <div id="question">
            {req && req.question ? <><span className="lbl">השאלה</span><p className="q">{req.question}</p></> : null}
          </div>
          <div id="crops">
            {req && req.crops > 0 ? <>
              <span className="lbl">מה כתוב בטופס</span>
              {Array.from({ length: req.crops }, (_, i) => (
                <img key={i} className="crop" alt={'תקריב של השדה ' + req.label}
                  src={'/api/help/' + encodeURIComponent(token) + '/crops/' + i} />
              ))}
            </> : null}
          </div>
        </section>

        <section className={'card' + (open ? '' : ' hidden')} id="form" aria-label="התשובה">
          {req ? <>
            <span className="lbl" id="optsLabel">
              {req.options.length > 1 ? 'איזה מהערכים נכון?' : req.options.length ? 'האם זה הערך הנכון?' : 'מה הערך הנכון?'}
            </span>
            <div className="opts" id="opts" role="radiogroup" aria-labelledby="optsLabel">
              {req.options.map((v, i) => (
                <ButtonBase key={i} className="opt" role="radio" aria-checked={chosen === v ? 'true' : 'false'} data-i={i}
                  onClick={() => { const next = chosen === v ? '' : v; setChosen(next); if (next) setOther(''); }}>
                  {bdi(v)}
                </ButtonBase>
              ))}
            </div>
            <label className="lbl" htmlFor="other">ערך אחר (אם אף אחד מהם לא נכון)</label>
            <input id="other" autoComplete="off" dir={dir === 'ltr' ? 'ltr' : undefined} value={other}
              onChange={(e) => { setOther(e.target.value); if (e.target.value.trim()) setChosen(''); }} />
            <label className="lbl" htmlFor="note">הערה (לא חובה)</label>
            <textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} />
            <label className="lbl" htmlFor="name">השם שלך</label>
            <input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
            <ButtonBase className="send" id="send" disabled={busy} onClick={send}>שליחת התשובה</ButtonBase>
            <div className={'err' + (err ? '' : ' hidden')} id="err" role="alert">{err}</div>
          </> : null}
        </section>

        <section className={'card' + (req && !open ? '' : ' hidden')} id="answered" role="status" aria-live="polite">
          {req && !open ? <Answered req={req} sent={sent} bdi={bdi} /> : null}
        </section>

        <p className="note">מוצג כאן רק השדה ששאלו עליו. ההחלטה הסופית נשארת אצל מי ששאלה. נא לא להעביר את הקישור הלאה.</p>
      </main>
    </>
  );
}

function Answered({ req, sent, bdi }: { req: Req; sent: { value: string; note: string } | null; bdi: (v: string) => JSX.Element }) {
  if (req.status === 'expired') {
    return <><b>פג תוקף הקישור</b> (הוא עובד 48 שעות). אם עדיין צריך עזרה — בקשו מ{req.asker || 'המזכירה'} לשלוח קישור חדש.</>;
  }
  if (req.status === 'canceled' || (req.status === 'closed' && !req.answered_at)) {
    return <><b>הבקשה כבר לא פעילה.</b> אין צורך לענות.</>;
  }
  return <>
    <div className="done">✓ התשובה נשלחה{req.asker ? ' ל' + req.asker : ''}. תודה!</div>
    {sent && sent.value ? <p>הערך: <b>{bdi(sent.value)}</b></p> : null}
    {sent && sent.note ? <p>הערה: {sent.note}</p> : null}
    {req.answered_by ? <p className="note">ענה/תה: {req.answered_by}</p> : null}
  </>;
}
