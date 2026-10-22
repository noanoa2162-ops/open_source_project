/* הגדלת הטופס על כל המסך: + / −, גרירה, גלגלת, צביטה וסיבוב. העמוד נשמר ב-300 DPI — מקומי, בלי מודל.
 * pz: w,h = גודל התמונה; W,H = אחרי סיבוב; z = הגדלה; tx,ty = הזזה. אותם חישובים כמו קודם. */
import { useEffect, useRef, useState } from 'react';
import Modal from '@mui/material/Modal';
import { calm, isName } from '../lib';
import { S, emit, fieldLabel, pageUrl } from '../store';
import { closePageZoom, focusCard, pzReq, visibleRecords } from '../actions';
import { Btn } from './Bits';
import { Boxes } from './FormView';

const PZ_MAX = 8;   // עד פי 8 מ"מתאים למסך"

export function PageZoom() {
  const open = S.modal === 'pz';
  const r = open ? pzReq.rec : null;
  const view = useRef<HTMLDivElement | null>(null);
  const [viewEl, setViewEl] = useState<HTMLDivElement | null>(null);
  const pan = useRef<HTMLDivElement>(null);
  const rot = useRef<HTMLDivElement>(null);
  const pct = useRef<HTMLSpanElement>(null);
  const inBtn = useRef<HTMLButtonElement>(null);
  const outBtn = useRef<HTMLButtonElement>(null);
  const pz = useRef({ w: 0, h: 0, W: 0, H: 0, deg: 0, z: 1, fit: 1, tx: 0, ty: 0, box: null as number[] | null });

  const P = pz.current;
  const size = () => ({ w: view.current!.clientWidth, h: view.current!.clientHeight });
  const layout = () => {
    const odd = P.deg % 180 !== 0;
    P.W = odd ? P.h : P.w;
    P.H = odd ? P.w : P.h;
    Object.assign(pan.current!.style, { width: P.W + 'px', height: P.H + 'px' });
    Object.assign(rot.current!.style, { width: P.w + 'px', height: P.h + 'px', left: (P.W - P.w) / 2 + 'px',
      top: (P.H - P.h) / 2 + 'px', transform: P.deg ? 'rotate(' + P.deg + 'deg)' : '' });
  };
  // לא נותנים לעמוד לברוח מהמסך: קטן מהמסך ← באמצע; גדול ← הקצוות לא נכנסים פנימה
  const clamp = () => {
    const v = size(), cw = P.W * P.z, ch = P.H * P.z;
    P.tx = cw <= v.w ? (v.w - cw) / 2 : Math.min(0, Math.max(v.w - cw, P.tx));
    P.ty = ch <= v.h ? (v.h - ch) / 2 : Math.min(0, Math.max(v.h - ch, P.ty));
  };
  const apply = (animate: boolean) => {
    clamp();
    const el = pan.current!;
    el.classList.toggle('anim', !!animate && !calm);
    el.style.transform = 'translate(' + P.tx + 'px,' + P.ty + 'px) scale(' + P.z + ')';
    el.style.setProperty('--z', String(P.z));
    pct.current!.textContent = Math.round(P.z / P.fit * 100) + '%';
    outBtn.current!.disabled = P.z <= P.fit * 1.001;
    inBtn.current!.disabled = P.z >= P.fit * PZ_MAX * 0.999;
  };
  const fit = (animate: boolean) => {
    const v = size();
    P.fit = Math.min(v.w / P.W, v.h / P.H) * 0.96;
    P.z = P.fit;
    apply(animate);
  };
  const zoomAt = (factor: number, x: number | null, y: number | null, animate: boolean) => {
    const v = size();
    if (x == null || y == null) { x = v.w / 2; y = v.h / 2; }
    const nz = Math.max(P.fit, Math.min(P.fit * PZ_MAX, P.z * factor));
    const px = (x - P.tx) / P.z, py = (y - P.ty) / P.z;
    P.tx = x - px * nz;
    P.ty = y - py * nz;
    P.z = nz;
    apply(animate);
  };
  // "לשדה המסומן": מגדילים כך שהמלבן הכתום ממלא בערך את רוב המסך, ובמרכזו
  const focusBox = () => {
    const b = P.box;
    if (!b) return;
    const rad = P.deg * Math.PI / 180, cos = Math.cos(rad), sin = Math.sin(rad);
    const x = ((b[0] + b[2]) / 2 - 0.5) * P.w, y = ((b[1] + b[3]) / 2 - 0.5) * P.h;
    const cx = P.W / 2 + x * cos - y * sin, cy = P.H / 2 + x * sin + y * cos;
    const odd = P.deg % 180 !== 0;
    const bw = (odd ? b[3] - b[1] : b[2] - b[0]) * (odd ? P.h : P.w);
    const bh = (odd ? b[2] - b[0] : b[3] - b[1]) * (odd ? P.w : P.h);
    const v = size();
    P.z = Math.max(P.fit, Math.min(P.fit * 5, v.w * 0.45 / Math.max(bw, 1), v.h * 0.35 / Math.max(bh, 1)));
    P.tx = v.w / 2 - cx * P.z;
    P.ty = v.h / 2 - cy * P.z;
    apply(true);
  };

  // פתיחה: התמונה נטענת, ואז "מתאים למסך"
  useEffect(() => {
    if (!open || !r) return;
    P.deg = S.viewTurn[r.record_id] || 0;
    P.box = focusBoxOf();
    view.current?.focus({ preventScroll: true });
    const img = rot.current!.querySelector('img')!;
    const ready = () => { P.w = img.naturalWidth || 1; P.h = img.naturalHeight || 1; layout(); fit(false); };
    if (img.complete && img.naturalWidth) ready(); else img.addEventListener('load', ready, { once: true });
  }, [open, r]);

  // גרירה, גלגלת, לחיצה כפולה, צביטה; שינוי גודל החלון
  useEffect(() => {
    const el = viewEl;
    if (!el) return;
    const pointers: Record<number, { x: number; y: number }> = {};
    let prev: { x: number; y: number } | null = null, pinch = 0;
    const local = (e: { clientX: number; clientY: number }) => { const b = el.getBoundingClientRect(); return { x: e.clientX - b.left, y: e.clientY - b.top }; };
    const wheel = (e: WheelEvent) => { e.preventDefault(); const p = local(e); zoomAt(Math.exp(-e.deltaY * 0.0015), p.x, p.y, false); };
    const dbl = (e: MouseEvent) => { const p = local(e); zoomAt(2, p.x, p.y, true); };
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return;
      el.setPointerCapture(e.pointerId);
      pointers[e.pointerId] = local(e);
      prev = local(e);
      pinch = 0;
      el.classList.add('drag');
    };
    const move = (e: PointerEvent) => {
      if (!pointers[e.pointerId]) return;
      pointers[e.pointerId] = local(e);
      const ids = Object.keys(pointers);
      if (ids.length >= 2) {                     // צביטה בשתי אצבעות
        const a = pointers[+ids[0]], b = pointers[+ids[1]];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinch) zoomAt(d / pinch, (a.x + b.x) / 2, (a.y + b.y) / 2, false);
        pinch = d;
        return;
      }
      const p = pointers[e.pointerId];
      P.tx += p.x - prev!.x;
      P.ty += p.y - prev!.y;
      prev = p;
      apply(false);
    };
    const up = (e: PointerEvent) => {
      delete pointers[e.pointerId];
      const ids = Object.keys(pointers);
      pinch = 0;
      if (ids.length) prev = pointers[+ids[0]]; else el.classList.remove('drag');
    };
    const resize = () => {
      if (S.modal !== 'pz' || !P.w) return;
      const ratio = P.z / P.fit, v = size();
      P.fit = Math.min(v.w / P.W, v.h / P.H) * 0.96;
      P.z = P.fit * ratio;
      apply(false);
    };
    el.addEventListener('wheel', wheel, { passive: false });
    el.addEventListener('dblclick', dbl);
    el.addEventListener('pointerdown', down);
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    window.addEventListener('resize', resize);
    return () => {
      el.removeEventListener('wheel', wheel);
      el.removeEventListener('dblclick', dbl);
      el.removeEventListener('pointerdown', down);
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', up);
      window.removeEventListener('resize', resize);
    };
  }, [viewEl]);

  // בתוך ההגדלה: + / − / 0, חיצים מזיזים (Esc — דרך ה-Modal)
  const keys = (e: React.KeyboardEvent) => {
    const step = 90;
    if (e.key === '+' || e.key === '=') { e.preventDefault(); return zoomAt(1.25, null, null, true); }
    if (e.key === '-' || e.key === '_') { e.preventDefault(); return zoomAt(1 / 1.25, null, null, true); }
    if (e.key === '0') { e.preventDefault(); return fit(true); }
    const moves: Record<string, number[]> = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
    if (moves[e.key] && !(e.target as HTMLElement).closest('button')) {
      e.preventDefault();
      P.tx += moves[e.key][0];
      P.ty += moves[e.key][1];
      apply(true);
    }
  };
  const turn = () => {
    if (!r) return;
    P.deg = (P.deg + 90) % 360;
    S.viewTurn[r.record_id] = P.deg;   // גם הטופס הקטן והתקריב מסתובבים יחד
    if (S.view === 'form') focusCard(S.cardIndex, true);
    emit();
    layout();
    fit(false);
  };

  const list = r ? visibleRecords() : [];
  const key = pzReq.key;
  const box = r ? focusBoxOf() : null;
  return (
    <Modal open={open} onClose={closePageZoom} keepMounted disablePortal disableAutoFocus disableRestoreFocus disableScrollLock hideBackdrop>
      <div id="pgZoom" className={'pzmodal' + (open ? '' : ' hidden')} role="dialog" aria-modal="true" aria-labelledby="pzTitle" onKeyDown={keys}>
        <div className="pzbar">
          <strong id="pzTitle">{r ? 'טופס ' + (list.indexOf(r) + 1) + ' / ' + list.length + ' · ' + (isName(key) ? 'שם פרטי ושם משפחה' : fieldLabel(key)) : 'הגדלת הטופס'}</strong>
          <span className="grow"></span>
          <div className="pzgroup" role="group" aria-label="הגדלה">
            <Btn className="pzbtn" id="pzOut" aria-label="הקטנה" title="הקטנה (−)" ref={outBtn} onClick={() => zoomAt(1 / 1.25, null, null, true)}>−</Btn>
            <span className="pzpct" id="pzPct" aria-live="polite" ref={pct}>100%</span>
            <Btn className="pzbtn" id="pzIn" aria-label="הגדלה" title="הגדלה (+)" ref={inBtn} onClick={() => zoomAt(1.25, null, null, true)}>+</Btn>
          </div>
          <Btn className="pzwide" id="pzFit" title="התאמה למסך (0)" onClick={() => fit(true)}>התאמה למסך</Btn>
          <Btn className={'pzwide' + (box ? '' : ' hidden')} id="pzBox" title="הגדלת האזור המסומן בכתום" onClick={focusBox}>לשדה המסומן</Btn>
          <Btn className="pzbtn" id="pzTurn" aria-label="סיבוב" title="סיבוב" onClick={turn}>↻</Btn>
          <Btn className="pzbtn" id="pzClose" aria-label="סגירת ההגדלה" title="סגירה (Esc)" onClick={closePageZoom}>✕</Btn>
        </div>
        <div className="pzview" id="pzView" tabIndex={0} aria-label="העמוד המוגדל — גררו כדי לזוז, גלגלת או +/− להגדלה"
          ref={(el) => { view.current = el; if (el !== viewEl) setViewEl(el); }}>
          <div className="pzpan" id="pzPan" ref={pan}><div className="pzrot" id="pzRot" ref={rot}>
            {r ? <><img alt={'עמוד ' + r.page_number + ' של ' + r.source_name + ', מוגדל'} src={pageUrl(r)} /><Boxes r={r} fieldKey={key} /></> : null}
          </div></div>
        </div>
        <p className="pzhint">גררו כדי לזוז · גלגלת או לחיצה כפולה להגדלה · Esc לסגירה</p>
      </div>
    </Modal>
  );
}

// התיבה שאליה "לשדה המסומן" מגדיל: השדה, או שני השמות יחד כשהם באותו אזור
function focusBoxOf(): number[] | null {
  const r = pzReq.rec;
  if (!r) return null;
  const f = r.fields[pzReq.key];
  if (!isName(pzReq.key) || !r.full_name_crop) return f && f.crop_box;
  const a = r.fields.first_name.crop_box!, b = r.fields.last_name.crop_box!;
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
}
