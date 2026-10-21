/* המסך כולו — אותו מבנה כמו index.html הקודם: פס gov.il, כותרת משנה, פירורי לחם, קליטה ובדיקה,
 * רשומות במערכת, כותרת תחתונה, הודעות וחלונות. */
import { useEffect, useLayoutEffect } from 'react';
import { S, flushAfterRender, useStore } from './store';
import {
  NEUTRAL_MARK, applyLook, approveField, backFromForm, chips, closeExportPreview, closeHelpAsk, closeModal, closePageZoom,
  closePop, closeTour, closeZoom, currentRecord, dismissToast, focusCard, helpPane,
  goForm, loadConfig, navExport, navReview, navUpload, onPopState, openTour, parkNow, refreshPendingCount, setSize,
  showRecordsPage,
  startEdit, startHelpWatch, toggleTheme, typeQuery, visibleRecords,
} from './actions';
import { Btn } from './components/Bits';
import { UploadStrip } from './components/Upload';
import { Results } from './components/Results';
import { RecordsPage } from './components/Records';
import { Pop } from './components/Pop';
import { Modals } from './components/Modals';
import { PageZoom } from './components/PageZoom';

let started = false;

export function App() {
  useStore();
  useLayoutEffect(() => { flushAfterRender(); });

  useEffect(() => {
    if (started) return;
    started = true;
    applyLook();
    loadConfig();
    startHelpWatch();
    refreshPendingCount();  // "טפסים לבדיקה" פתוח גם כשיש רק ממתינים מהעלאות קודמות
    history.replaceState({ page: 'intake', view: 'upload' }, '', location.pathname + location.search);
    window.addEventListener('popstate', onPopState);
    // הדף נסגר / מוסתר — הטפסים שלא אושרו נשמרים כממתינים
    window.addEventListener('pagehide', parkNow);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') parkNow(); });
    window.addEventListener('scroll', closePop, { passive: true });
    window.addEventListener('resize', closePop);
    document.addEventListener('keydown', formKeys);
    // כפתור "חזרה לראש הדף" — מוסתר כשפס הקודם/הבא על המסך, כדי לא לכסות את "הטופס הבא"
    let navInView = false;
    const top = () => {
      const show = window.scrollY > 500 && !navInView;
      if (show !== S.backTop) { S.backTop = show; document.getElementById('backToTop')?.classList.toggle('show', show); }
    };
    window.addEventListener('scroll', top, { passive: true });
    const nav = document.querySelector('#formView .formnav');
    if (nav && 'IntersectionObserver' in window) {
      new IntersectionObserver((entries) => { navInView = entries.some((e) => e.isIntersecting); top(); }).observe(nav);
    }
  }, []);

  const neutral = S.brand === 'neutral';
  const onRecords = S.page === 'records';
  // "טפסים לבדיקה" מסומן כשעומדים על הטפסים (אריחים, טבלה או טופס, בכל סינון) — שם העמוד.
  // "קליטת טפסים" — רק במסך ההעלאה ובזמן הקליטה (נועה, 22/10/2026)
  const onReview = !onRecords && S.resultsVisible && !S.processing;
  const onIntake = !onRecords && !onReview;
  return <>
    <a className="skip" href="#main">דילוג לתוכן</a>

    {/* פס עליון כחול, בסגנון gov.il */}
    <div className="govbar">
      <div className="inner">
        <span className="sysname">מערכת קליטת טפסי התחייבות</span>
        <span className="grow"></span>
        <label className="govsearch" htmlFor="q">
          <input id="q" type="search" placeholder="חיפוש טופס, שם או מספר" autoComplete="off"
            aria-label="חיפוש בטפסים שנקלטו לפי שם, מספר או גורם" value={S.queryText}
            onChange={(e) => typeQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && S.last) document.getElementById('results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }} />
          <span aria-hidden="true">⌕</span>
        </label>
        <div className="tools" role="group" aria-label="תצוגה">
          <Btn className="tool" id="sizeDown" title="טקסט קטן יותר" aria-label="טקסט קטן יותר" onClick={() => setSize(-1)}>A−</Btn>
          <Btn className="tool" id="sizeUp" title="טקסט גדול יותר" aria-label="טקסט גדול יותר" onClick={() => setSize(1)}>A+</Btn>
          <Btn className="tool" id="themeBtn" title="מצב כהה / בהיר" aria-label="מצב כהה או בהיר" onClick={toggleTheme}>◐</Btn>
          <Btn className="tool" id="helpBtn" title="הסבר קצר על המסך" aria-label="הסבר קצר על המסך" onClick={openTour}>?</Btn>
        </div>
      </div>
    </div>

    {/* כותרת משנה: לוגו עגול, שם המשרד והיחידה, תפריט גלולות */}
    <header className="subhead">
      <div className="inner">
        <div className="brand" id="brand">
          <span className="mark"><img src={neutral ? NEUTRAL_MARK : '/static/brand/moh-office.png'} alt="" /></span>
          <span className="names">
            <span className="org" id="brandOrg">{neutral ? 'טופס למידע' : 'משרד הבריאות'}</span>
            <span className="unit" id="brandUnit">{neutral ? 'קליטת טפסי התחייבות' : 'המעבדה המרכזית לנגיפים'}</span>
          </span>
        </div>
        <nav className="pills" aria-label="ניווט במערכת">
          <Btn className={'pill' + (onIntake ? ' on' : '')} id="navUpload" aria-current={onIntake ? 'page' : undefined} onClick={navUpload}>קליטת טפסים</Btn>
          <Btn className={'pill' + (onReview ? ' on' : '')} id="navReview" aria-current={onReview ? 'page' : undefined} disabled={chips.navReviewDisabled && !S.prevBatch && !S.rec.pendingN}
            title={chips.navReviewDisabled ? (S.prevBatch ? 'חזרה להעלאה הקודמת' : S.rec.pendingN ? 'טפסים שממתינים לאישור' : '') : undefined}
            onClick={navReview}>טפסים לבדיקה <b id="navReviewN">{chips.navReviewDisabled && !S.prevBatch ? S.rec.pendingN : chips.navReviewN}</b></Btn>
          <Btn className="pill" id="navExport" disabled={!chips.exportReady} title={S.last ? chips.exportWhy || 'הורדת כל הטפסים לקובץ Excel' : undefined}
            onClick={(e) => navExport(e.currentTarget)}>ייצוא לאקסל</Btn>
          <Btn className={'pill' + (onRecords ? ' on' : '')} id="navRecords" aria-current={onRecords ? 'page' : undefined}
            title="הטפסים שאושרו — מוצגים אלה שאושרו היום" onClick={() => showRecordsPage()}>רשומות במערכת</Btn>
        </nav>
      </div>
    </header>

    <main id="main">
      <Crumbs />

      <div className="pagehead">
        <h1 id="pageTitle">{onRecords ? 'רשומות במערכת' : 'קליטת טפסי התחייבות'}</h1>
        <p id="pageSub">{onRecords ? 'הטפסים שאושרו נשמרים כאן — שורה לכל מספר התחייבות, כמו בקובץ לאקסל.' : 'מעלים טפסים, בודקים, מייצאים לאקסל.'}</p>
      </div>

      <div id="notices">
        {S.notices.map((n) => (
          <div key={n.id} className={'notice ' + n.kind} role={n.kind === 'err' ? 'alert' : 'status'}><b>{n.title}</b>{n.text}</div>
        ))}
      </div>

      <UploadStrip />
      <Results />
      <RecordsPage />
    </main>

    <footer className="sitefoot">
      <div className="inner">
        <div className="fbrand">
          <img src="/static/brand/moh-office.png" alt="" id="footMark" className={neutral ? 'hidden' : undefined} />
          <span><strong id="footOrg">{neutral ? 'טופס למידע' : 'משרד הבריאות'}</strong><small id="footUnit">{neutral ? 'קליטת טפסי התחייבות' : 'המעבדה המרכזית לנגיפים'}</small></span>
        </div>
        <p className="fnote">כל שדה מסומן נבדק על ידי אדם לפני ייצוא.</p>
        <ul className="legend">
          <li><span className="sw review"></span> לבדיקה</li>
          <li><span className="sw approved"></span> מאושר</li>
        </ul>
      </div>
    </footer>

    <Btn id="backToTop" className={'totop' + (S.backTop ? ' show' : '')} aria-label="חזרה לראש הדף" title="חזרה לראש הדף"
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
      <svg viewBox="0 0 19 20" aria-hidden="true"><path d="M1 18.9 9.5 10.9 18 18.9M1 9.7 9.5 1.7 18 9.7" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" /></svg>
    </Btn>

    {/* תקריב צף על תא */}
    <Pop />

    {/* הודעות קופצות */}
    <div id="toasts" className="toasts" aria-live="polite" aria-atomic="false">
      {S.toasts.map((t) => (
        <div key={t.id} className={'toast' + (t.big ? ' big' : '') + (t.out ? ' out' : '')}>
          <span>{t.text}</span>
          {t.undo ? <button onClick={() => { const u = t.undo!; dismissToast(t.id); u(); }}>בטל</button> : null}
        </div>
      ))}
    </div>

    <Modals />
    <PageZoom />

    {/* רגע הצלחה */}
    <div id="party" className={'party' + (S.partyPieces.length ? '' : ' hidden')} aria-hidden="true">
      {S.partyPieces.map((p, i) => <i key={i} style={{ left: p.left + '%', background: p.color, animationDelay: p.delay + 's' }}></i>)}
    </div>
  </>;
}

// פירורי הלחם: המסך הנוכחי, ובטופס פתוח גם "טופס X מתוך Y". השלבים הקודמים לחיצים.
function Crumbs() {
  const onRecords = S.page === 'records';
  const inForm = !onRecords && S.view === 'form' && !!S.last && S.resultsVisible;
  const parts: [string, string?][] = [];
  if (onRecords) parts.push(['רשומות במערכת']);
  else if (inForm) {
    parts.push(S.formFrom === 'records' ? ['רשומות במערכת', 'records'] : ['קליטת טפסי התחייבות', 'forms']);
    parts.push(['טופס ' + (S.formIndex + 1) + ' מתוך ' + visibleRecords().length]);
  } else parts.push(['קליטת טפסי התחייבות']);
  return (
    <nav className="crumbs" aria-label="מיקום באתר">
      <ol id="crumbs">
        <li className="moh-only">משרד הבריאות</li>
        <li className="moh-only">המעבדה המרכזית לנגיפים</li>
        {parts.map((p, i) => (
          <li key={i} className="dyn" aria-current={i === parts.length - 1 ? 'page' : undefined}>
            {i === parts.length - 1 ? p[0] : <button type="button" className="crumblink" data-go={p[1]}
              onClick={() => (p[1] === 'records' ? showRecordsPage(S.openedKey) : backFromForm())}>{p[0]}</button>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

function escapeModal() {
  if (S.modal === 'xl') return closeExportPreview();
  if (S.modal === 'pz') return closePageZoom();
  if (S.modal === 'tour') return closeTour();
  if (S.modal === 'help') return S.help.pane && !S.help.done ? helpPane('') : closeHelpAsk();
  closeModal();
}

/* ------------------------------------------------------------ מקלדת במצב טופס */
function formKeys(e: KeyboardEvent) {
  // חלון פתוח מטפל במקשים שלו. Esc כשהפוקוס מחוץ לחלון — סוגר אותו, כמו קודם
  if (S.modal) { if (e.key === 'Escape') escapeModal(); return; }
  if (e.key === 'Escape') closePop();
  if (S.view !== 'form' || !S.resultsVisible || S.page === 'records') return;
  const target = e.target as HTMLElement;
  const tag = (target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
  if (e.key === 'Escape' && S.zoomOpen) { e.preventDefault(); return closeZoom(); }
  if (target.closest && target.closest('header')) return;
  const r = currentRecord();
  if (!r) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); goForm(1); }              // מימין לשמאל: שמאלה = הבא
  else if (e.key === 'ArrowRight') { e.preventDefault(); goForm(-1); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); focusCard(Math.min(S.fields.length - 1, S.cardIndex + 1)); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); focusCard(Math.max(0, S.cardIndex - 1)); }
  else if (e.key === 'Enter' && !(target.closest && target.closest('button'))) {
    e.preventDefault();
    const key = S.fields[S.cardIndex].key;
    if (r.fields[key].state !== 'approved') approveField(r.record_id, key, true);
  } else if (e.code === 'KeyE') {
    e.preventDefault();
    startEdit('card', r.record_id, S.fields[S.cardIndex].key);
  }
}
