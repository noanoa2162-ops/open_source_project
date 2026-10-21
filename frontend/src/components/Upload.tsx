/* אזור: העלאת טפסים — שלבי העבודה, גרירה, והתקדמות חיה (גלגל אחוזים + אריחים). */
import { useRef, useState } from 'react';
import { S } from '../store';
import { upload } from '../actions';
import { Btn } from './Bits';

const STEPS = [['1', 'העלאה'], ['2', 'בדיקה ואישור'], ['3', 'ייצוא לאקסל']];

export function UploadStrip() {
  const file = useRef<HTMLInputElement>(null);
  const early = S.processing && S.resultsVisible;
  const [over, setOver] = useState(false);
  const drag = (on: boolean) => (e: React.DragEvent) => { e.preventDefault(); setOver(on); };
  return (
    <section className={'strip' + (S.page === 'records' ? ' hidden' : '')} id="uploadStrip" aria-labelledby="uploadTitle">
      <h2 className="sr-only" id="uploadTitle">העלאת טפסים</h2>

      {/* שלבי העבודה: פס דק אחד, בלי כרטיסים */}
      <ol className="steps" id="steps" aria-label="שלבי העבודה">
        {STEPS.map(([n, label]) => {
          const step = Number(n);
          const cls = step === S.step ? 'now' : step < S.step ? 'done' : '';
          return <li key={n} data-step={n} className={cls || undefined} aria-current={step === S.step ? 'step' : undefined}>
            <b>{n}</b><span>{label}</span></li>;
        })}
      </ol>

      <section id="drop" className={'card' + (over ? ' over' : '') + (S.dropVisible ? '' : ' hidden')} aria-label="העלאת טפסים"
        onClick={() => file.current?.click()}
        onDragEnter={drag(true)} onDragOver={drag(true)} onDragLeave={drag(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}>
        <HeroArt />
        <h3>גררו לכאן טפסים</h3>
        <p>או</p>
        <div className="actions">
          <Btn className="primary big" id="browse" onClick={(e) => { e.stopPropagation(); file.current?.click(); }}>בחירת קבצים</Btn>
        </div>
        <input type="file" id="file" ref={file} className="hidden" accept=".pdf,.jpg,.jpeg,.png" multiple
          onClick={(e) => e.stopPropagation()} onChange={(e) => upload(e.target.files)} />
        <small id="limits">{S.config ? 'PDF, JPG או PNG · עד ' + S.config.max_pages + ' טפסים בכל פעם' : 'PDF, JPG או PNG'}</small>
      </section>

      {/* התקדמות — בלי אחוזים (החלטת נועה, 21/10/2026). לפני הטופס הראשון: אנימציה של קליטה. מהטופס
          הראשון: פס דק בלי מספר, והאריחים האמיתיים (לחיצים) ישר מתחתיו. */}
      <section id="progress" className={'card' + (S.progressVisible ? '' : ' hidden') + (early ? ' early' : '')}
        aria-label="התקדמות הקליטה">
        {early ? (
          <div className="pbarwrap" role="status" aria-live="polite">
            <div className="pbar" role="progressbar" aria-label="התקדמות הקליטה" aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={S.ring.shown}>
              <span style={{ width: Math.max(4, S.ring.shown) + '%' }}></span>
            </div>
            <p className="pbarnote"><strong id="pTitle">{S.pTitle}</strong> · <span id="pCount">{S.pCount}</span></p>
          </div>
        ) : (
          <div className="intakebox" role="status" aria-live="polite">
            <IntakeArt />
            <strong id="pTitle">{S.pTitle}</strong>
            <span id="pCount">{S.pCount}</span>
            <ul id="pFiles">
              {S.pFiles.map((f, i) => (
                <li key={i}><bdi>{f.name}</bdi>{f.error ? <> — <span className="bad">{f.error}</span></> : null}</li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </section>
  );
}

// בזמן הקליטה: זרם של טפסים. 8 דפים יוצאים מהערימה בזה אחר זה (כל 0.45 שנ׳), כ-3 באוויר בבת
// אחת, כל אחד בקשת קלה אל שורה בטבלה — והשורה מאירה כשהוא נוחת. השורות מתחלפות 1→4 ושוב.
// כתום = שדה לבדיקה, כמו במערכת (בלי ירוק: ירוק = אושר על ידי אדם).
const INTAKE_ROWS = [{ dy: -18, y: 19 }, { dy: 2, y: 39 }, { dy: 22, y: 59 }, { dy: 41, y: 79 }];
const INTAKE_SHEETS = 8;
const INTAKE_GAP = 0.45;  // שניות בין דף לדף; מחזור שלם = 8 × 0.45 = 3.6 שנ׳ (כמו ב-app.css)
const INTAKE_LAND = 0.4 * INTAKE_SHEETS * INTAKE_GAP;  // הדף נוחת ב-40% מהמחזור
function IntakeArt() {
  const cycle = INTAKE_ROWS.length * INTAKE_GAP;  // כל שורה מקבלת דף פעם ב-1.8 שנ׳
  return (
    <svg className="hero intake" viewBox="0 0 320 120" aria-hidden="true">
      <rect x="10" y="30" width="62" height="80" rx="6" className="sheet s3" />
      <rect x="18" y="26" width="62" height="80" rx="6" className="sheet s3" />
      <rect x="26" y="20" width="62" height="80" rx="6" className="sheet s2" />
      <rect x="34" y="13" width="62" height="80" rx="6" className="sheet s2" />
      <path d="M128 56h56" className="arrow" />
      <path d="M176 46l12 10-12 10" className="arrow" />
      <g className="grid">
        <rect x="206" y="18" width="98" height="78" rx="7" className="tablebox" />
        {INTAKE_ROWS.map((row, i) => (
          <g key={i} className="irow" style={{ animationDelay: ((i * INTAKE_GAP + INTAKE_LAND) % cycle) + 's' }}>
            <rect x="207" y={row.y} width="32" height="17" className="icell" />
            <rect x="241" y={row.y} width="30" height="17" className={i % 2 ? 'icell' : 'hl'} />
            <rect x="273" y={row.y} width="30" height={i === 3 ? 16 : 17} className={i === 2 ? 'hl' : 'icell'} />
          </g>
        ))}
        <path d="M206 38h98M206 58h98M206 78h98M240 18v78M272 18v78" className="gridline" />
      </g>
      {Array.from({ length: INTAKE_SHEETS }, (_, i) => (
        <g key={i} className="fly"
          style={{ animationDelay: i * INTAKE_GAP + 's', ['--dy' as string]: INTAKE_ROWS[i % INTAKE_ROWS.length].dy + 'px' }}>
          <rect x="42" y="6" width="62" height="80" rx="6" className="sheet s1" />
          <path d="M54 26h38M54 38h30M54 50h38M54 62h22" className="ink" />
          <path d="M60 74h30" className="ink hand" />
        </g>
      ))}
    </svg>
  );
}

// האיור: מסמכים ← חץ ← טבלה. במסך הגרירה הוא עומד; בזמן הקליטה — IntakeArt.
function HeroArt({ className = 'hero' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 320 120" aria-hidden="true">
      <g className="sheets">
        <rect x="18" y="22" width="62" height="80" rx="6" className="sheet s3" />
        <rect x="30" y="14" width="62" height="80" rx="6" className="sheet s2" />
        <g className="s1g">
          <rect x="42" y="6" width="62" height="80" rx="6" className="sheet s1" />
          <path d="M54 26h38M54 38h30M54 50h38M54 62h22" className="ink" />
          <path d="M60 74h30" className="ink hand" />
        </g>
      </g>
      <path d="M128 56h56" className="arrow" />
      <path d="M176 46l12 10-12 10" className="arrow" />
      <g className="grid">
        <rect x="206" y="18" width="98" height="78" rx="7" className="tablebox" />
        <path d="M206 38h98M206 58h98M206 78h98M240 18v78M272 18v78" className="gridline" />
        <rect x="241" y="39" width="30" height="18" className="hl" />
        <rect x="273" y="59" width="30" height="18" className="ok" />
      </g>
    </svg>
  );
}
