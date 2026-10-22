/* Floor plan with booking. */
'use strict';
(() => {
  const svg = document.getElementById('plan');
  const selFloor = document.getElementById('floor');
  const inpDate = document.getElementById('date');
  const selFor = document.getElementById('for');
  const panel = document.getElementById('details');
  const view = new View(svg);
  view.panWithLeft = true;

  const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const SHORT_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  let state = null;          // /api/plan response
  let chosen = null;         // element id
  let fitted = null;         // floor id the view was fitted for
  let otherPerson = null;    // admin: booking for any person
  const BOOKABLE = ['desk', 'room', 'booth'];
  const allDay = () => t('all day');

  const bookingFor = () => (selFor && selFor.value === '__other') ? otherPerson : (selFor ? selFor.value : '');

  function updateAddress() {
    const p = new URLSearchParams({ floor: selFloor.value, date: inpDate.value });
    history.replaceState(null, '', location.pathname + '?' + p);
  }

  async function load(highlight) {
    updateAddress();
    const d = new Date(inpDate.value + 'T12:00');
    document.getElementById('weekday').textContent = isNaN(d) ? '' : t(WEEKDAYS[d.getDay()]);
    const q = new URLSearchParams({ floor: selFloor.value, date: inpDate.value });
    if (bookingFor()) q.set('for', bookingFor());
    const r = await api('/api/plan?' + q);
    if (!r.ok) { toast(r.error || t('Could not load the plan'), true); return; }
    state = r;
    draw();
    if (fitted !== state.floor.id) { view.fit(state.floor.width, state.floor.height); fitted = state.floor.id; }
    const desks = state.elements.filter(e => e.type === 'desk');
    const free = desks.filter(e => e.status === 'free').length;
    const rooms = state.elements.filter(e => e.type === 'room' || e.type === 'booth');
    document.getElementById('counter').textContent = t('Free desks: {free} / {total}', { free, total: desks.length })
      + (rooms.length ? ' · ' + t('rooms: {free} / {total}', { free: rooms.filter(e => e.status === 'free' || e.status === 'partial').length, total: rooms.length }) : '');
    if (highlight) highlightElement(highlight);
    drawList();
    showDetails();
  }

  function draw() {
    svg.innerHTML = '';
    const f = state.floor;
    el('rect', { x: 0, y: 0, width: f.width, height: f.height, fill: '#fff', rx: 4 }, svg);
    if (state.background) el('image', { href: state.background, x: 0, y: 0, width: f.width, height: f.height, opacity: f.background_opacity, preserveAspectRatio: 'xMidYMid meet' }, svg);
    // layer order: zones, rooms and booths, walls, desks, labels
    const layer = { zone: 0, room: 1, booth: 1, wall: 2, desk: 3, label: 4 };
    [...state.elements].sort((a, b) => layer[a.type] - layer[b.type]).forEach(e => {
      const g = drawElement(e, svg, BOOKABLE.includes(e.type) ? STATUS_COLORS[e.status] : null);
      if (!BOOKABLE.includes(e.type)) return;
      g.classList.add('bookable', e.status);
      if (e.id === chosen) g.classList.add('selected');
      const title = el('title', {}, g);
      title.textContent = `${e.name} - ${statusLabel(e.status)}${e.who ? ': ' + e.who : ''}`
        + (e.bookings || []).filter(r => r.from).map(r => `\n${r.from}–${r.to} ${r.who}`).join('');
      g.addEventListener('click', () => {
        if (view.lastPanned) return;
        chosen = e.id; draw(); drawList(); showDetails();
      });
    });
  }

  function highlightElement(id) {
    const e = state.elements.find(x => x.id === id);
    if (!e) return;
    chosen = id;
    draw();
    view.center(e.x + e.w / 2, e.y + e.h / 2, 700);
    const g = svg.querySelector(`[data-id="${id}"]`);
    if (g) g.classList.add('highlight');
  }

  /* ── Hours ──────────────────────────────────────────────────────────── */
  const toMin = s => { const [h, m] = s.split(':').map(Number); return h * 60 + m; };
  const toTime = m => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  const H_FROM = toMin(HOURS.from), H_TO = toMin(HOURS.to), STEP = HOURS.step;
  const slots = (a, b) => { const out = []; for (let m = a; m <= b; m += STEP) out.push(toTime(m)); return out; };

  function defaultHours(e) {
    // from the nearest full step (today), rooms for an hour, a desk for half a day
    let start = toMin(e.type === 'desk' ? '08:00' : '09:00');
    if (state.date === isoToday()) {
      const now = new Date(), m = now.getHours() * 60 + now.getMinutes();
      start = Math.max(start, Math.ceil(m / STEP) * STEP);
    }
    start = Math.min(Math.max(start, H_FROM), H_TO - STEP);
    const end = Math.min(start + (e.type === 'desk' ? 240 : 60), H_TO);
    return { from: toTime(start), to: toTime(end) };
  }

  /* Day axis: taken hours on the background of the bookable hours; a click on a free spot sets "from". */
  function dayAxis(e) {
    const range = H_TO - H_FROM;
    const bands = (e.bookings || []).map(r => {
      const a = r.from ? Math.max(toMin(r.from), H_FROM) : H_FROM, b = r.to ? Math.min(toMin(r.to), H_TO) : H_TO;
      return `<div class="band ${r.mine ? 'mine' : ''}" style="left:${(a - H_FROM) / range * 100}%;width:${Math.max(0, b - a) / range * 100}%"
        title="${esc(r.from ? r.from + '–' + r.to : allDay())} · ${esc(r.who)}${r.title ? ' · ' + esc(r.title) : ''}"></div>`;
    }).join('');
    const ticks = slots(H_FROM, H_TO).filter(s => s.endsWith(':00')).map(s =>
      `<span style="left:${(toMin(s) - H_FROM) / range * 100}%">${parseInt(s, 10)}</span>`).join('');
    return `<div class="day-axis" id="day-axis" title="${esc(t('Click a free spot to choose the time'))}">${bands}<div class="pick" id="axis-pick"></div></div>
      <div class="axis-scale">${ticks}</div>`;
  }

  /* ── Details panel ──────────────────────────────────────────────────── */
  function showDetails() {
    const e = state && state.elements.find(x => x.id === chosen);
    if (!e) { panel.innerHTML = `<p class="muted">${esc(t('Click a desk, room or booth on the plan to see the details and book.'))}</p>`; return; }
    const teams = (e.teams || []).map(z => `<span class="tag color" style="background:${esc(z.color)}">${esc(z.name)}</span>`).join(' ')
      || `<span class="muted">${esc(t('everyone'))}</span>`;
    const items = e.bookings || [];
    const assigned = e.status.startsWith('assigned');
    let html = `<h3>${esc(e.name)}${e.capacity ? ` <span class="tag">${esc(seats(e.capacity))}</span>` : ''}</h3>
      <p><span class="status" style="background:${STATUS_COLORS[e.status]}">${esc(statusLabel(e.status))}</span></p>
      ${assigned ? `<p>${esc(t('Owner:'))} <b>${esc(e.who)}</b></p>` : ''}
      <p class="small">${esc(t('For teams:'))} ${teams}${e.from_zone ? `<br><span class="muted">${esc(t('from the zone: {zone}', { zone: e.from_zone }))}</span>` : ''}</p>
      ${e.equipment ? `<p class="small">${esc(t('Equipment:'))} ${esc(e.equipment)}</p>` : ''}
      ${e.reason && e.status === 'unavailable' ? `<p class="small" style="color:var(--err)">${esc(e.reason)}</p>` : ''}
      ${assigned ? '' : `<button type="button" class="btn sm" id="show-schedule" style="width:100%;justify-content:center">
        📅 ${esc(e.type === 'desk' ? t('Who and when - the whole week') : t('All meetings - week / list'))}</button>`}`;
    if (!assigned) {
      html += `<div class="small muted" style="margin-top:8px">${esc(t('{date} - occupancy', { date: state.date }))}</div>${dayAxis(e)}`;
      if (items.length) {
        html += `<ul class="booking-list">${items.map(r => `<li class="${r.mine ? 'mine' : ''}">
            <b>${r.from ? esc(r.from + '–' + r.to) : esc(allDay())}</b> ${esc(r.who)}${r.title ? `<br><span class="small muted">${esc(r.title)}</span>` : ''}
            ${r.can_cancel ? `<button class="btn sm danger" data-cancel="${r.id}" data-desc="${esc((r.from ? r.from + '–' + r.to : allDay()) + ', ' + r.who)}">${esc(t('Cancel'))}</button>` : ''}
          </li>`).join('')}</ul>`;
      }
    }
    const canBook = e.can_book && !assigned;
    if (canBook) {
      const h = defaultHours(e);
      const hourly = e.type !== 'desk';
      const options = (list, sel) => list.map(s => `<option ${s === sel ? 'selected' : ''}>${s}</option>`).join('');
      html += `<div class="card booking-form">
        <b>${esc(t('Book'))}</b>
        <div class="toggle" id="bk-mode">
          <button type="button" data-mode="day" class="${hourly ? '' : 'active'}">${esc(t('All day'))}</button>
          <button type="button" data-mode="hours" class="${hourly ? 'active' : ''}">${esc(t('Hours'))}</button>
        </div>
        <div class="row hours-pick" id="bk-hours" ${hourly ? '' : 'hidden'}>
          <label class="f">${esc(t('From'))}<select id="bk-time-from">${options(slots(H_FROM, H_TO - STEP), h.from)}</select></label>
          <label class="f">${esc(t('To'))}<select id="bk-time-to">${options(slots(H_FROM + STEP, H_TO), h.to)}</select></label>
        </div>
        ${e.type !== 'desk' ? `<label class="f" style="margin-top:8px">${esc(t('Subject (optional)'))}<input type="text" id="bk-title" maxlength="120" placeholder="${esc(t('e.g. Team meeting'))}"></label>` : ''}
        <div class="row" style="gap:8px;margin-top:8px">
          <label class="f" style="flex:1">${esc(t('From day'))}<input type="date" id="bk-from" value="${esc(state.date)}"></label>
          <label class="f" style="flex:1">${esc(t('To day'))}<input type="date" id="bk-to" value="${esc(state.date)}"></label>
        </div>
        <div class="row" style="margin:6px 0;gap:6px">
          <button class="btn sm" data-quick="one">${esc(t('Only this day'))}</button>
          <button class="btn sm" data-quick="this">${esc(t('Until the end of the week'))}</button>
          <button class="btn sm" data-quick="next">${esc(t('Next week'))}</button>
          <button class="btn sm" data-quick="two">${esc(t('2 weeks'))}</button>
        </div>
        <div class="weekdays" id="bk-days" hidden>${SHORT_DAYS.map((n, i) =>
          `<label><input type="checkbox" value="${i}" ${i < 5 ? 'checked' : ''}><span>${esc(t(n))}</span></label>`).join('')}</div>
        <div id="bk-preview" class="day-preview"></div>
        <button class="btn accent2" id="bk-ok" style="width:100%;justify-content:center;margin-top:8px" disabled>…</button>
      </div>`;
    }
    panel.innerHTML = html;
    panel.querySelectorAll('[data-cancel]').forEach(b => b.onclick = () => cancel(parseInt(b.dataset.cancel, 10), `${e.name}, ${state.date} (${b.dataset.desc})`));
    const bs = document.getElementById('show-schedule');
    if (bs) bs.onclick = () => openSchedule(e);
    if (canBook) wireForm(e);
    sheet(true);
  }

  /* ── Booking form (one for a day, hours and a date range) ───────────── */
  const dateOf = s => new Date(s + 'T12:00');
  const plusDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const mondayOf = d => plusDays(d, -((d.getDay() + 6) % 7));

  function wireForm(e) {
    const $ = id => document.getElementById(id);
    const inFrom = $('bk-from'), inTo = $('bk-to'), tFrom = $('bk-time-from'), tTo = $('bk-time-to');
    const btn = $('bk-ok'), preview = $('bk-preview');
    let mode = $('bk-mode').querySelector('.active').dataset.mode;
    if (preset && preset.id === e.id) {
      // came from a click in the schedule: set its day and hours
      mode = 'hours';
      $('bk-mode').querySelectorAll('button').forEach(x => x.classList.toggle('active', x.dataset.mode === 'hours'));
      $('bk-hours').hidden = false;
      tFrom.value = preset.from; tTo.value = preset.to;
      preset = null;
    }
    const weekdays = () => [...document.querySelectorAll('#bk-days input:checked')].map(c => c.value).join(',');
    const data = () => {
      const d = { element_id: e.id, date_from: inFrom.value, date_to: inTo.value };
      if (inFrom.value !== inTo.value) d.weekdays = weekdays();
      if (mode === 'hours') { d.time_from = tFrom.value; d.time_to = tTo.value; }
      const ti = $('bk-title'); if (ti && ti.value.trim()) d.title = ti.value.trim();
      if (bookingFor()) d.for = bookingFor();
      return d;
    };
    function markAxis() {
      const p = $('axis-pick');
      if (!p) return;
      if (mode !== 'hours') { p.style.display = 'none'; return; }
      const a = toMin(tFrom.value), b = toMin(tTo.value);
      p.style.display = 'block';
      p.style.left = (a - H_FROM) / (H_TO - H_FROM) * 100 + '%';
      p.style.width = Math.max(0, b - a) / (H_TO - H_FROM) * 100 + '%';
    }
    let timer;
    function refresh() {
      markAxis();
      $('bk-days').hidden = inFrom.value === inTo.value;
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = new URLSearchParams(Object.entries(data()).filter(([k]) => k !== 'title'));
        const r = await api('/api/availability?' + q);
        if (!r.ok) { preview.innerHTML = `<p class="small" style="color:var(--err)">${esc(r.error || t('Error'))}</p>`; btn.disabled = true; btn.textContent = t('Cannot be booked'); return; }
        const text = { free: t('free'), mine: t('already yours'), taken: t('taken'), other: t('you already have another place'), outside: t('out of range') };
        if (r.days.length === 1) {
          const x = r.days[0];
          preview.innerHTML = x.state === 'free' ? `<p class="small" style="color:var(--ok);margin:4px 0">✓ ${esc(t('Free at the chosen time'))}</p>`
            : `<p class="small" style="color:var(--err);margin:4px 0">✕ ${esc(x.reason || text[x.state])}</p>`;
        } else {
          preview.innerHTML = r.days.map(x => `<span class="day-chip ${x.state}" title="${esc(x.date)}: ${esc(x.reason || text[x.state])}">
              <small>${esc(x.weekday)}</small>${esc(x.date.slice(8, 10))}.${esc(x.date.slice(5, 7))}</span>`).join('')
            + `<p class="small muted" style="margin:6px 0 0">${esc(t('Green days will be booked, the others skipped (hover to see the reason).'))}</p>`;
        }
        const when = mode === 'hours' ? ` ${tFrom.value}–${tTo.value}` : '';
        btn.disabled = !r.free;
        btn.textContent = !r.free ? t('Taken at the chosen time')
          : r.days.length === 1 ? (when ? t('Book') + when : t('Book for the whole day'))
          : t('Book {n} days', { n: r.free }) + when;
      }, 120);
    }
    $('bk-mode').querySelectorAll('button').forEach(b => b.onclick = () => {
      mode = b.dataset.mode;
      $('bk-mode').querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
      $('bk-hours').hidden = mode !== 'hours';
      refresh();
    });
    tFrom.onchange = () => {
      if (tTo.value <= tFrom.value) tTo.value = toTime(Math.min(toMin(tFrom.value) + 60, H_TO));
      refresh();
    };
    tTo.onchange = () => {
      if (tTo.value <= tFrom.value) tFrom.value = toTime(Math.max(toMin(tTo.value) - STEP, H_FROM));
      refresh();
    };
    const axis = $('day-axis');
    if (axis) axis.onclick = ev => {
      const r = axis.getBoundingClientRect();
      const m = H_FROM + Math.floor((ev.clientX - r.left) / r.width * (H_TO - H_FROM) / STEP) * STEP;
      const start = Math.min(Math.max(m, H_FROM), H_TO - STEP);
      mode = 'hours';
      $('bk-mode').querySelectorAll('button').forEach(x => x.classList.toggle('active', x.dataset.mode === 'hours'));
      $('bk-hours').hidden = false;
      tFrom.value = toTime(start); tTo.value = toTime(Math.min(start + 60, H_TO));
      refresh();
    };
    document.querySelectorAll('[data-quick]').forEach(b => b.onclick = () => {
      const d = dateOf(state.date), mon = mondayOf(d);
      const range = { one: [d, d], this: [d, plusDays(mon, 4)], next: [plusDays(mon, 7), plusDays(mon, 11)],
                      two: [d, plusDays(mon, 11)] }[b.dataset.quick];
      inFrom.value = isoToday(range[0]); inTo.value = isoToday(range[1] < range[0] ? range[0] : range[1]);
      refresh();
    });
    [inFrom, inTo].forEach(i => i.onchange = () => { if (inTo.value < inFrom.value) inTo.value = inFrom.value; refresh(); });
    document.querySelectorAll('#bk-days input').forEach(c => c.onchange = refresh);
    btn.onclick = () => book(data());
    refresh();
  }

  async function book(data) {
    const r = await api('/api/book', { method: 'POST', body: data });
    if (!r.ok) { toast(r.error || t('Booking error'), true); return; }
    const failed = r.results.filter(w => !w.ok);
    const when = data.time_from ? ` (${data.time_from}–${data.time_to})` : '';
    if (!failed.length) toast(r.booked === 1 ? t('Booked: {place}', { place: r.place + when }) + '.'
                                              : t('Booked: {place} for {n} days.', { place: r.place + when, n: r.booked }));
    else if (r.booked) toast(t('Booked {n} days; skipped {m}:', { n: r.booked, m: failed.length }) + ' ' + failed.map(b => `${b.date} (${b.error})`).join(', '), true);
    else toast(failed.map(b => (r.results.length > 1 ? b.date + ': ' : '') + b.error).join('; '), true);
    load();
  }

  async function cancel(id, desc) {
    if (!(await confirmDialog(t('Cancel the booking: {desc}?', { desc }), { button: t('Cancel the booking'), cancel: t('Back'), danger: true }))) return;
    const r = await api('/api/cancel', { method: 'POST', body: { id } });
    if (!r.ok) { toast(r.error || t('Error'), true); return; }
    toast(t('Booking cancelled.'));
    load();
  }

  /* ── Schedule of a place (slide-out drawer) ──────────────────────────── */
  let preset = null;                  // {id, from, to} - after clicking a free slot
  const dr = { el: null, from: null, mode: 'week' };
  const $d = id => document.getElementById(id);

  function openSchedule(e) {
    dr.el = e;
    if (window.innerWidth < 760) dr.mode = 'list';   // the week grid is too narrow for a phone
    $d('dr-mode').querySelectorAll('button').forEach(x => x.classList.toggle('active', x.dataset.mode === dr.mode));
    dr.from = dr.mode === 'list' ? state.date : isoToday(mondayOf(dateOf(state.date)));
    $d('dr-title').textContent = e.name;
    $d('drawer').hidden = false; $d('drawer-backdrop').hidden = false;
    requestAnimationFrame(() => { $d('drawer').classList.add('open'); });
    loadSchedule();
  }
  function closeSchedule() {
    $d('drawer').classList.remove('open');
    setTimeout(() => { $d('drawer').hidden = true; $d('drawer-backdrop').hidden = true; }, 200);
  }

  const dm = s => `${s.slice(8, 10)}.${s.slice(5, 7)}`;

  async function loadSchedule() {
    const days = dr.mode === 'week' ? 7 : 14;
    const r = await api(`/api/schedule?element_id=${dr.el.id}&from=${dr.from}&days=${days}`);
    if (!r.ok) { $d('dr-body').innerHTML = `<p class="muted">${esc(r.error || t('Error'))}</p>`; return; }
    $d('dr-range').textContent = `${dm(r.from)} - ${dm(r.to)}`;
    $d('dr-body').innerHTML = dr.mode === 'week' ? weekGrid(r) : meetingList(r);
    document.querySelector('.drawer-footer').hidden = dr.mode !== 'week';
    $d('dr-body').querySelectorAll('[data-cancel]').forEach(b => b.onclick = async ev => {
      ev.stopPropagation();
      await cancel(parseInt(b.dataset.cancel, 10), b.dataset.desc);
      loadSchedule();
    });
    $d('dr-body').querySelectorAll('.wk-col[data-date]').forEach(k => k.onclick = ev => {
      if (ev.target.closest('.wk-block')) return;
      if (k.classList.contains('past')) return;
      const rect = k.getBoundingClientRect();
      const m = H_FROM + Math.floor((ev.clientY - rect.top) / rect.height * (H_TO - H_FROM) / STEP) * STEP;
      const start = Math.min(Math.max(m, H_FROM), H_TO - STEP);
      preset = { id: dr.el.id, from: toTime(start), to: toTime(Math.min(start + (dr.el.type === 'desk' ? 240 : 60), H_TO)) };
      closeSchedule();
      inpDate.value = k.dataset.date;
      chosen = dr.el.id;
      load();
    });
  }

  function weekGrid(r) {
    const weekend = r.bookings.some(x => r.days.find(d => d.date === x.date).weekend);
    const days = r.days.filter(d => weekend || !d.weekend);
    const range = H_TO - H_FROM;
    const hours = slots(H_FROM, H_TO).filter(s => s.endsWith(':00'));
    const pos = (a, b) => `top:${(a - H_FROM) / range * 100}%;height:${Math.max(2, (b - a) / range * 100)}%`;
    const cols = days.map(d => {
      const blocks = r.bookings.filter(x => x.date === d.date).map(x => {
        const a = x.from ? Math.max(toMin(x.from), H_FROM) : H_FROM, b = x.to ? Math.min(toMin(x.to), H_TO) : H_TO;
        const desc = `${x.from ? x.from + '-' + x.to : allDay()} · ${x.who}${x.title ? ' · ' + x.title : ''}`;
        return `<div class="wk-block ${x.mine ? 'mine' : ''}" style="${pos(a, b)}" title="${esc(desc)}">
            <b>${x.from ? esc(x.from + '-' + x.to) : esc(allDay())}</b><span>${esc(x.title || x.who)}</span>${x.title ? `<span class="who">${esc(x.who)}</span>` : ''}
            ${x.can_cancel ? `<button class="wk-x" data-cancel="${x.id}" data-desc="${esc(dr.el.name + ', ' + d.date + ' ' + desc)}" title="${esc(t('Cancel'))}">✕</button>` : ''}</div>`;
      }).join('');
      return `<div class="wk-day ${d.date === r.today ? 'today' : ''}"><div class="wk-head">${esc(d.weekday)} <b>${dm(d.date)}</b></div>
        <div class="wk-col ${d.date < r.today ? 'past' : ''}" data-date="${d.date}">${blocks}</div></div>`;
    }).join('');
    return `<div class="wk-grid" style="--days:${days.length}">
      <div class="wk-hours"><div class="wk-head"></div><div class="wk-scale">${hours.map(s =>
        `<span style="top:${(toMin(s) - H_FROM) / range * 100}%">${s}</span>`).join('')}</div></div>${cols}</div>`;
  }

  function meetingList(r) {
    const h = r.days.map(d => {
      const items = r.bookings.filter(x => x.date === d.date);
      if (!items.length) return '';
      return `<div class="ls-day ${d.date === r.today ? 'today' : ''}"><div class="ls-date">${esc(d.weekday)} ${dm(d.date)}${d.date === r.today ? ' · ' + esc(t('today')) : ''}</div>
        ${items.map(x => `<div class="ls-item ${x.mine ? 'mine' : ''}">
          <span class="time">${x.from ? esc(x.from + '-' + x.to) : esc(allDay())}</span>
          <span class="desc"><b>${esc(x.title || x.who)}</b>${x.title ? `<br><span class="small muted">${esc(x.who)}</span>` : ''}</span>
          ${x.can_cancel ? `<button class="btn sm danger" data-cancel="${x.id}" data-desc="${esc(dr.el.name + ', ' + d.date + ' ' + (x.from ? x.from + '-' + x.to : allDay()))}">${esc(t('Cancel'))}</button>` : ''}
        </div>`).join('')}</div>`;
    }).join('');
    return h || `<p class="empty">${esc(t('No bookings on these days - the place is free.'))}</p>`;
  }

  $d('dr-close').onclick = closeSchedule;
  $d('drawer-backdrop').onclick = closeSchedule;
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !$d('drawer').hidden) closeSchedule(); });
  $d('dr-back').onclick = () => { dr.from = isoToday(plusDays(dateOf(dr.from), dr.mode === 'week' ? -7 : -14)); loadSchedule(); };
  $d('dr-next').onclick = () => { dr.from = isoToday(plusDays(dateOf(dr.from), dr.mode === 'week' ? 7 : 14)); loadSchedule(); };
  $d('dr-mode').querySelectorAll('button').forEach(b => b.onclick = () => {
    dr.mode = b.dataset.mode;
    $d('dr-mode').querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
    if (dr.mode === 'list') { const today = isoToday(); if (dr.from < today) dr.from = today; }
    loadSchedule();
  });

  /* ── List of places (convenient on a phone) ──────────────────────────── */
  let viewMode = 'plan';
  try { viewMode = localStorage.getItem('plan-view') || (window.innerWidth < 760 ? 'list' : 'plan'); } catch (e) { /* no storage */ }

  function setView(mode) {
    viewMode = mode;
    try { localStorage.setItem('plan-view', mode); } catch (e) { /* no storage */ }
    document.querySelectorAll('#view-toggle button').forEach(b => b.classList.toggle('active', b.dataset.view === mode));
    $d('place-list').hidden = mode !== 'list';
    $d('plan-wrap').hidden = mode !== 'plan';
    if (mode === 'plan' && state) view.fit(state.floor.width, state.floor.height);
  }
  document.querySelectorAll('#view-toggle button').forEach(b => b.onclick = () => setView(b.dataset.view));

  function drawList() {
    const places = state.elements.filter(e => BOOKABLE.includes(e.type));
    const groups = new Map();
    const add = (k, e) => { if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); };
    places.filter(e => e.type !== 'desk').forEach(e => add(t('Rooms and booths'), e));
    places.filter(e => e.type === 'desk').forEach(e =>
      add(e.from_zone ? t('Zone {zone}', { zone: e.from_zone }) : (e.teams || []).length ? t('Desks: {teams}', { teams: e.teams.map(z => z.name).join(', ') }) : t('Desks open to everyone'), e));
    const describe = e => {
      const r = e.bookings || [];
      if (e.status.startsWith('assigned')) return t('permanent desk: {who}', { who: e.who });
      const whole = r.find(x => !x.from);
      if (whole) return whole.who;
      if (r.length) return r.map(x => `${x.from}-${x.to}`).join(', ');
      return e.status === 'unavailable' ? (e.reason || t('unavailable')) : t('free all day');
    };
    $d('place-list').innerHTML = [...groups].map(([name, list]) => {
      const free = list.filter(e => e.status === 'free' || e.status === 'partial').length;
      return `<section><h4>${esc(name)} <span class="muted small">${esc(t('free {n} / {total}', { n: free, total: list.length }))}</span></h4>
        <div class="pl-grid">${list.map(e => `<button type="button" class="pl-item ${e.status} ${e.id === chosen ? 'selected' : ''}" data-id="${e.id}">
          <i style="background:${STATUS_COLORS[e.status]}"></i><b>${esc(e.type === 'desk' ? (e.code || e.name) : e.name)}</b>
          ${e.capacity ? `<span class="tag">${esc(seats(e.capacity))}</span>` : ''}
          <span class="pl-desc">${esc(describe(e))}</span></button>`).join('')}</div></section>`;
    }).join('') || `<p class="empty">${esc(t('There are no bookable places on this floor.'))}</p>`;
    $d('place-list').querySelectorAll('.pl-item').forEach(b => b.onclick = () => {
      chosen = parseInt(b.dataset.id, 10); draw(); drawList(); showDetails();
    });
  }

  /* ── The panel as a bottom sheet (phone) ─────────────────────────────── */
  const side = $d('plan-side');
  function sheet(open) {
    if (window.innerWidth > 900) return;
    side.classList.toggle('open', open === undefined ? !side.classList.contains('open') : open);
  }
  $d('sheet-handle').onclick = () => sheet();

  function shiftDay(n) {
    const d = new Date(inpDate.value + 'T12:00');
    d.setDate(d.getDate() + n);
    inpDate.value = isoToday(d);
    load();
  }

  selFloor.onchange = () => { chosen = null; load(); };
  inpDate.onchange = () => load();
  document.getElementById('day-back').onclick = () => shiftDay(-1);
  document.getElementById('day-next').onclick = () => shiftDay(1);
  document.getElementById('today').onclick = () => { inpDate.value = isoToday(); load(); };
  document.getElementById('zoom-in').onclick = () => view.zoom(0.8);
  document.getElementById('zoom-out').onclick = () => view.zoom(1.25);
  document.getElementById('zoom-fit').onclick = () => view.fit(state.floor.width, state.floor.height);
  if (selFor) selFor.onchange = async () => {
    if (selFor.value === '__other') {
      const e = await dialog({ title: t('Booking for another person'), button: t('Next'),
                               field: { type: 'email', label: t('E-mail of the person you are booking for'), autocomplete: 'off' } });
      if (!e) { selFor.value = ''; } else { otherPerson = e.trim().toLowerCase(); }
    }
    load();
  };

  // Find a person
  const search = document.getElementById('search');
  const results = document.getElementById('search-results');
  let st;
  search.addEventListener('input', () => {
    clearTimeout(st);
    st = setTimeout(async () => {
      const q = search.value.trim();
      if (q.length < 2) { results.style.display = 'none'; return; }
      const r = await api('/api/search?' + new URLSearchParams({ q, date: inpDate.value }));
      results.innerHTML = (r.results || []).map(o => `<li data-floor="${o.floor_id || ''}" data-el="${o.element_id || ''}">
          <b>${esc(o.name)}</b><br><span class="small ${o.code ? '' : 'muted'}">${o.code
            ? esc((o.kind === 'fixed' ? t('Permanent desk') : t('Desk')) + ' ' + o.code) : esc(t('no desk on this day'))}</span></li>`).join('')
        || `<li class="muted">${esc(t('No results'))}</li>`;
      results.style.display = 'block';
    }, 250);
  });
  results.addEventListener('click', async ev => {
    const li = ev.target.closest('li[data-el]');
    if (!li || !li.dataset.el) return;
    const fid = li.dataset.floor, eid = parseInt(li.dataset.el, 10);
    if (String(selFloor.value) !== fid) { selFloor.value = fid; await load(eid); }
    else highlightElement(eid);
    showDetails();
  });

  setView(viewMode);
  load();
})();
