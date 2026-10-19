'use strict';
/* מסך "רשומות במערכת": כל טופס שנקלט, מהמסד — חיפוש, סינון, מה אושר ומה ממתין, ייצוא.
   שורה לכל טופס לוגי; כמה מספרים מרכזיים / התחייבויות מוצגים יחד באותו תא, כל אחד בנפרד. */
(function(){
  const $ = function(s){ return document.querySelector(s); };
  const esc = window.TL.esc;
  const STATUS = {
    approved: ['ok', '✓ מאושר'],
    ready: ['ready', 'ממתין לאישור'],
    review: ['review', '⚠ לבדיקה'],
    failed: ['fail', '✗ נכשל']
  };
  const COLS = [
    ['first_name', 'שם פרטי'], ['last_name', 'שם משפחה'], ['national_id', 'ת״ז'],
    ['central_numbers', 'מספר מרכזי'], ['sample_received_date', 'תאריך קבלה'],
    ['sending_entity', 'גורם שולח'], ['commitment_numbers', 'מספר התחייבות']
  ];
  const state = { q: '', status: '', sender: '', from: '', to: '' };
  let timer = null, open = false, rows = [];

  $('#recHead').innerHTML = '<th scope="col">מצב</th>' +
    COLS.map(function(c){ return '<th scope="col">' + c[1] + '</th>'; }).join('') +
    '<th scope="col">מקור</th><th scope="col">נקלט</th>';

  function params(){
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    if (state.status) p.set('status', state.status);
    if (state.sender) p.set('sender', state.sender);
    if (state.from) p.set('date_from', state.from);
    if (state.to) p.set('date_to', state.to);
    return p;
  }

  function cellValue(v){
    if (Array.isArray(v)) {
      if (!v.length) return '<span class="none">—</span>';
      return v.map(function(x){ return '<span class="num"><bdi>' + mark(x) + '</bdi></span>'; }).join('');
    }
    return v ? '<bdi>' + mark(v) + '</bdi>' : '<span class="none">—</span>';
  }
  function mark(v){ return esc(v).replace(/\?/g, '<mark class="q" title="תו שלא נקרא">?</mark>'); }
  function when(iso){
    try {
      const d = new Date(iso);
      return d.toLocaleDateString('he-IL') + ' ' + d.toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' });
    } catch (e) { return ''; }
  }

  async function refresh(){
    const res = await fetch('/api/records?' + params().toString());
    if (!res.ok) return;
    const d = await res.json();
    rows = d.records;
    Object.keys(STATUS).concat(['all']).forEach(function(k){
      const el = document.querySelector('#recStatuses b[data-n="' + k + '"]');
      if (el) el.textContent = d.counts[k] || 0;
    });
    $('#navRecordsN').textContent = d.counts.all || '';
    const sel = $('#recSender'), keep = sel.value;
    sel.innerHTML = '<option value="">כל הגורמים</option>' + d.senders.map(function(s){
      return '<option' + (s === keep ? ' selected' : '') + '>' + esc(s) + '</option>';
    }).join('');

    $('#recBody').innerHTML = rows.map(function(r){
      const st = STATUS[r.status] || ['ready', r.status];
      return '<tr tabindex="0" data-rec="' + esc(r.record_id) + '" data-batch="' + esc(r.batch_id) + '" class="st-' + esc(r.status) + '">' +
        '<td><span class="badge ' + st[0] + '">' + st[1] + (r.review_count ? ' · ' + r.review_count : '') + '</span></td>' +
        COLS.map(function(c){ return '<td>' + cellValue(r[c[0]]) + '</td>'; }).join('') +
        '<td class="src"><bdi>' + esc(r.source_name) + '</bdi> · עמ׳ ' + r.page_number + '</td>' +
        '<td class="when">' + esc(when(r.created_at)) + '</td></tr>';
    }).join('');

    const filtered = state.q || state.status || state.sender || state.from || state.to;
    $('#recEmpty').classList.toggle('hidden', rows.length > 0);
    $('#recEmpty').textContent = filtered ? 'אין רשומות שמתאימות לחיפוש ולסינון.'
      : 'עוד אין רשומות. טפסים שתקלטו יופיעו כאן ויישמרו.';
    $('#recSum').textContent = rows.length + (filtered ? ' רשומות תואמות' : ' רשומות') +
      ' · ' + (d.counts.approved || 0) + ' מאושרות · ' + ((d.counts.review || 0) + (d.counts.ready || 0)) + ' ממתינות';
    const qs = params().toString();
    $('#recXlsx').href = '/api/records/export?format=xlsx' + (qs ? '&' + qs : '');
    $('#recCsv').href = '/api/records/export?format=csv' + (qs ? '&' + qs : '');
    $('#recJson').href = '/api/records/export?format=json' + (qs ? '&' + qs : '');
  }
  function later(){ clearTimeout(timer); timer = setTimeout(refresh, 220); }

  $('#recQ').addEventListener('input', function(e){ state.q = e.target.value.trim(); later(); });
  $('#recSender').addEventListener('change', function(e){ state.sender = e.target.value; refresh(); });
  $('#recFrom').addEventListener('change', function(e){ state.from = e.target.value; refresh(); });
  $('#recTo').addEventListener('change', function(e){ state.to = e.target.value; refresh(); });
  $('#recStatuses').addEventListener('click', function(e){
    const b = e.target.closest('button'); if (!b) return;
    state.status = b.dataset.status;
    Array.prototype.forEach.call(this.querySelectorAll('button'), function(x){
      x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b ? 'true' : 'false');
    });
    refresh();
  });

  async function openRow(tr){
    if (!tr) return;
    show(false);
    const ok = await window.TL.openSavedBatch(tr.dataset.batch, tr.dataset.rec);
    if (!ok) { window.TL.toast('הרשומה כבר לא קיימת'); show(true); }
  }
  $('#recBody').addEventListener('click', function(e){ openRow(e.target.closest('tr')); });
  $('#recBody').addEventListener('keydown', function(e){
    const tr = e.target.closest('tr'); if (!tr) return;
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRow(tr); }
    if (e.key === 'ArrowDown' && tr.nextElementSibling) { e.preventDefault(); tr.nextElementSibling.focus(); }
    if (e.key === 'ArrowUp' && tr.previousElementSibling) { e.preventDefault(); tr.previousElementSibling.focus(); }
  });

  // מעבר בין המסכים: רשומות ↔ קליטה ובדיקה
  const MAIN = ['#uploadStrip', '#results'];
  let hiddenByUs = [];
  function show(on){
    open = on;
    $('#recordsView').classList.toggle('hidden', !on);
    $('#navRecords').classList.toggle('on', on);
    if (on) $('#navRecords').setAttribute('aria-current', 'page'); else $('#navRecords').removeAttribute('aria-current');
    $('#navUpload').classList.toggle('on', !on);
    if (on) {
      hiddenByUs = MAIN.filter(function(s){ return !$(s).classList.contains('hidden'); });
      hiddenByUs.forEach(function(s){ $(s).classList.add('hidden'); });
      $('#pageTitle').textContent = 'רשומות במערכת';
      $('#pageSub').textContent = 'כל טופס שנקלט נשמר כאן, גם אחרי סגירת הדפדפן. לחיצה על שורה פותחת אותו לבדיקה.';
      refresh();
      $('#recQ').focus();
    } else {
      hiddenByUs.forEach(function(s){ $(s).classList.remove('hidden'); });
      hiddenByUs = [];
      $('#pageTitle').textContent = 'קליטת טפסי התחייבות';
      $('#pageSub').textContent = 'מעלים טפסים, בודקים, מייצאים לאקסל.';
    }
  }
  $('#navRecords').addEventListener('click', function(){ show(!open); });
  ['#navUpload', '#navReview', '#navExport'].forEach(function(sel){
    $(sel).addEventListener('click', function(){ if (open) show(false); }, true);
  });

  refresh();
  window.TL.showRecords = show;
})();
