/* Shared code: API requests, translations, messages, app dialogs, person suggestions,
   drawing plan elements, zooming and panning. */
'use strict';

const PREFIX = document.querySelector('meta[name=prefix]').content;
const CSRF = document.querySelector('meta[name=csrf-token]').content;
const SVGNS = 'http://www.w3.org/2000/svg';

/* Translation: t('Booked: {place}', { place }) - texts from locales/<language>.json. */
function t(text, params) {
  let out = (window.I18N && window.I18N[text]) || text;
  if (params) for (const [k, v] of Object.entries(params)) out = out.split('{' + k + '}').join(v);
  return out;
}

async function api(url, opts = {}) {
  const o = { headers: { 'X-CSRF-Token': CSRF }, ...opts };
  if (o.body && !(o.body instanceof FormData)) {
    o.headers['Content-Type'] = 'application/json';
    o.body = JSON.stringify(o.body);
  }
  const r = await fetch(PREFIX + url, o);
  let data = {};
  try { data = await r.json(); } catch (e) { /* not a JSON response */ }
  if (r.status === 401) { location.href = PREFIX + '/login?next=' + encodeURIComponent(location.pathname + location.search); }
  return { status: r.status, ok: r.ok, ...data };
}

let _toastTimer;
function toast(text, isError = false) {
  let el = document.querySelector('.toast');
  if (!el) { el = document.createElement('div'); document.body.appendChild(el); }
  el.className = 'toast' + (isError ? ' error' : '');
  el.textContent = text;
  el.style.display = 'block';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.style.display = 'none'; }, isError ? 5000 : 2800);
}

const seats = n => n === 1 ? t('1 seat') : t('{n} seats', { n });

function isoToday(d = new Date()) {
  const z = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function el(tag, attrs = {}, parent = null) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

/* ── App dialogs instead of the browser's confirm / prompt ─────────────────
   dialog({ title, text, button, cancel, danger, field: { type, label, autocomplete, placeholder },
            run: async value => null | 'error message' })
   -> Promise: the field value (or true), null when cancelled. When `run` returns a text,
   the dialog stays open and shows the error (e.g. a wrong password). */
function dialog(o = {}) {
  return new Promise(resolve => {
    const backdrop = document.createElement('div');
    backdrop.className = 'dialog-backdrop';
    backdrop.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
        <h3 id="dialog-title">${esc(o.title || t('Confirm'))}</h3>
        ${o.text ? `<p class="dialog-text">${esc(o.text)}</p>` : ''}
        ${o.field ? `<label class="f">${esc(o.field.label || '')}<input type="${esc(o.field.type || 'text')}"
            autocomplete="${esc(o.field.autocomplete || 'off')}" placeholder="${esc(o.field.placeholder || '')}"></label>` : ''}
        <p class="dialog-error" hidden></p>
        <div class="dialog-buttons">
          <button type="button" class="btn" data-d="no">${esc(o.cancel || t('Cancel'))}</button>
          <button type="button" class="btn ${o.danger ? 'danger-solid' : 'primary'}" data-d="yes">${esc(o.button || 'OK')}</button>
        </div></div>`;
    document.body.appendChild(backdrop);
    const input = backdrop.querySelector('input'), error = backdrop.querySelector('.dialog-error'), yes = backdrop.querySelector('[data-d=yes]');
    const previous = document.activeElement;
    const close = value => { backdrop.remove(); document.removeEventListener('keydown', key, true); if (previous) previous.focus(); resolve(value); };
    const submit = async () => {
      const value = input ? input.value : true;
      if (input && !input.value.trim()) { input.focus(); return; }
      if (o.run) {
        yes.disabled = true;
        const problem = await o.run(value);
        yes.disabled = false;
        if (problem) { error.textContent = problem; error.hidden = false; if (input) { input.select(); input.focus(); } return; }
      }
      close(value);
    };
    const key = ev => {
      if (ev.key === 'Escape') { ev.preventDefault(); close(null); }
      else if (ev.key === 'Enter' && document.activeElement !== backdrop.querySelector('[data-d=no]')) { ev.preventDefault(); submit(); }
      else if (ev.key === 'Tab') {        // focus does not leave the dialog
        const items = [...backdrop.querySelectorAll('input, button')];
        const i = items.indexOf(document.activeElement);
        if (ev.shiftKey && i <= 0) { ev.preventDefault(); items[items.length - 1].focus(); }
        else if (!ev.shiftKey && i === items.length - 1) { ev.preventDefault(); items[0].focus(); }
      }
    };
    document.addEventListener('keydown', key, true);
    backdrop.addEventListener('mousedown', ev => { if (ev.target === backdrop) close(null); });
    backdrop.querySelector('[data-d=no]').onclick = () => close(null);
    yes.onclick = submit;
    (input || yes).focus();
  });
}
const confirmDialog = (text, o = {}) => dialog({ title: t('Confirm'), text, button: t('Yes'), ...o }).then(v => v !== null);

/* Forms and buttons: data-confirm="question" (+ data-confirm-button, data-danger).
   On a button - asks before submitting with that button; on a form - before every submit. */
document.addEventListener('submit', async ev => {
  const form = ev.target, button = ev.submitter;
  const source = (button && button.dataset.confirm) ? button : (form.dataset.confirm ? form : null);
  if (!source || form._confirmed) { form._confirmed = false; return; }
  ev.preventDefault();
  if (await confirmDialog(source.dataset.confirm, { button: source.dataset.confirmButton || t('Yes'),
                                                     danger: source.dataset.danger !== undefined })) {
    form._confirmed = true;
    form.requestSubmit(button || undefined);
  }
});

/* Password reset from lists (users, team members):
   <button data-password-link="email" data-name="…"> - e-mail with a link to set a password,
   <button data-set-password="email" data-name="…">  - an administrator sets a temporary password. */
document.addEventListener('click', async ev => {
  const b = ev.target.closest('[data-password-link], [data-set-password]');
  if (!b) return;
  const name = b.dataset.name || b.dataset.passwordLink || b.dataset.setPassword;
  if (b.dataset.passwordLink !== undefined) {
    const email = b.dataset.passwordLink;
    let result = null;
    const ok = await dialog({
      title: t('Link to set the password'), button: t('Send'),
      text: t('{name} ({email}) will get an e-mail with a link to set a new password. The current password works until it is changed.', { name, email }),
      run: async () => {
        result = await api('/admin/api/account/' + encodeURIComponent(email) + '/password-link', { method: 'POST' });
        return result.ok ? null : (result.error || t('Failed.'));
      } });
    if (ok === null) return;
    if (result.sent) { toast(t('An e-mail with the link has been sent to {email}.', { email })); return; }
    const copy = await dialog({ title: t('Link to set the password'), button: t('Copy the link'), cancel: t('Close'),
      text: t('Mail is not configured, so pass the link on to the person yourself (valid 24-72 h, one-time): {link}', { link: result.link }) });
    if (copy) navigator.clipboard.writeText(result.link).then(() => toast(t('Link copied.')), () => toast(t('Copy the link manually.'), true));
    return;
  }
  const email = b.dataset.setPassword;
  const ok = await dialog({
    title: t('Set password'), button: t('Set password'),
    text: t('Temporary password for: {name} ({email}). It will have to be changed at the first login.', { name, email }),
    field: { type: 'password', label: t('New password (at least 10 characters, letters and digits)'), autocomplete: 'new-password' },
    run: async password => {
      const r = await api('/admin/api/account/' + encodeURIComponent(email) + '/password', { method: 'POST', body: { password } });
      return r.ok ? null : (r.error || t('Failed.'));
    } });
  if (ok !== null) toast(t('A temporary password has been set for {email}.', { email }));
});

/* Field validation messages in the app's style instead of the browser's bubbles. */
document.addEventListener('invalid', ev => {
  const field = ev.target;
  ev.preventDefault();
  field.classList.add('field-error');
  let msg = field.parentNode.querySelector(':scope > .field-error-msg');
  if (!msg) { msg = document.createElement('span'); msg.className = 'field-error-msg'; field.insertAdjacentElement('afterend', msg); }
  msg.textContent = field.validationMessage;
  const form = field.form;
  if (form && form.querySelector(':invalid') === field) field.focus();
}, true);
document.addEventListener('input', ev => {
  const field = ev.target;
  if (!field.classList || !field.classList.contains('field-error') || !field.checkValidity()) return;
  field.classList.remove('field-error');
  const msg = field.parentNode.querySelector(':scope > .field-error-msg');
  if (msg) msg.remove();
});

/* Person suggestions: <input data-people> + optional data-target="hidden_field_name" */
function personPicker(input, onPick) {
  const list = document.createElement('ul');
  list.className = 'search-results';
  list.style.display = 'none';
  list.style.position = 'absolute';
  list.style.background = '#fff';
  list.style.zIndex = 60;
  input.parentNode.style.position = 'relative';
  input.parentNode.appendChild(list);
  let timer;
  input.setAttribute('autocomplete', 'off');
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) { list.style.display = 'none'; return; }
      const r = await api('/api/people?q=' + encodeURIComponent(q));
      list.innerHTML = (r.results || []).map(o =>
        `<li data-email="${esc(o.email)}" data-first="${esc(o.first_name || '')}" data-last="${esc(o.last_name || '')}" data-name="${esc(o.name)}"><b>${esc(o.name)}</b> <span class="muted small">${esc(o.email)}</span></li>`).join('')
        || `<li class="muted">${esc(t('No results'))}</li>`;
      list.style.top = (input.offsetTop + input.offsetHeight + 2) + 'px';
      list.style.left = input.offsetLeft + 'px';
      list.style.width = Math.max(input.offsetWidth, 320) + 'px';
      list.style.display = 'block';
    }, 200);
  });
  list.addEventListener('mousedown', e => {
    const li = e.target.closest('li[data-email]');
    if (!li) return;
    e.preventDefault();
    list.style.display = 'none';
    onPick({ email: li.dataset.email, first_name: li.dataset.first, last_name: li.dataset.last, name: li.dataset.name });
  });
  input.addEventListener('blur', () => setTimeout(() => { list.style.display = 'none'; }, 150));
}

/* data-people - account suggestions. data-target="field_name" - the visible field shows the name
   and the e-mail goes to the hidden field with that name. data-fill - after picking, also fills
   the first_name / last_name fields of the same form (adding a team member). */
document.querySelectorAll('input[data-people]').forEach(input => {
  const target = input.dataset.target ? input.form.querySelector(`[name="${input.dataset.target}"]`) : null;
  personPicker(input, o => {
    if (target) { target.value = o.email; input.value = o.name; } else input.value = o.email;
    if (input.dataset.fill !== undefined) {
      for (const k of ['first_name', 'last_name']) { const f = input.form.querySelector(`[name="${k}"]`); if (f) f.value = o[k] || ''; }
    }
    if (input.dataset.submit !== undefined) input.form.submit();
  });
  if (target) input.addEventListener('input', () => { if (!input.value.trim()) target.value = ''; });
});

/* ── Colours and drawing plan elements ───────────────────────────────────── */
const _cssVar = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
const STATUS_COLORS = {   // from the CSS variables - "mine" follows the main colour chosen in the settings
  free: _cssVar('--free', '#22c55e'), mine: _cssVar('--mine', '#1d5fbf'), taken: _cssVar('--taken', '#ef4444'),
  team: _cssVar('--team', '#a855f7'), unavailable: _cssVar('--unavailable', '#cbd5e1'), assigned: _cssVar('--assigned', '#64748b'),
  assigned_mine: _cssVar('--mine', '#1d5fbf'), partial: _cssVar('--partial', '#06b6d4'),
};
const STATUS_LABELS = {
  free: 'Free', mine: 'Your booking', taken: 'Taken', team: 'Taken by someone from your team',
  unavailable: 'Unavailable', assigned: 'Permanent desk', assigned_mine: 'Your permanent desk',
  partial: 'Part of the day taken',
};
const statusLabel = s => t(STATUS_LABELS[s] || s);

/* Draws one plan element in a <g> group. `fill` - a ready fill colour. */
function drawElement(e, parent, fill) {
  const g = el('g', { class: 'el-' + e.type, 'data-id': e.id }, parent);
  const cx = e.x + e.w / 2, cy = e.y + e.h / 2;
  if (e.rotation) g.setAttribute('transform', `rotate(${e.rotation} ${cx} ${cy})`);
  const min = Math.min(e.w, e.h);
  if (e.type === 'desk') {
    // the chair on the "lower" side of the longer edge
    if (e.w >= e.h) el('rect', { class: 'chair', x: cx - min * 0.28, y: e.y + e.h - 2, width: min * 0.56, height: min * 0.32, rx: 5 }, g);
    else el('rect', { class: 'chair', x: e.x + e.w - 2, y: cy - min * 0.28, width: min * 0.32, height: min * 0.56, rx: 5 }, g);
    el('rect', { class: 'top', x: e.x, y: e.y, width: e.w, height: e.h, rx: 5, fill: fill || e.color || '#94a3b8' }, g);
    const txt = el('text', { x: cx, y: cy, 'text-anchor': 'middle', 'dominant-baseline': 'central', fill: '#fff', 'font-size': Math.max(9, Math.min(15, min * 0.36)) }, g);
    txt.textContent = e.code || '';
    if (e.rotation) txt.setAttribute('transform', `rotate(${-e.rotation} ${cx} ${cy})`);
  } else if (e.type === 'room' || e.type === 'booth') {
    // Room / booth: light inside; when booked a frame and a marker in the status colour.
    const booth = e.type === 'booth';
    el('rect', { class: 'top', x: e.x, y: e.y, width: e.w, height: e.h, rx: booth ? 12 : 6,
                 fill: e.color || (booth ? '#e0f2fe' : '#dbeafe'), 'fill-opacity': 0.85,
                 stroke: fill || (booth ? '#7dd3fc' : '#93c5fd'), 'stroke-width': fill ? 4 : 2 }, g);
    if (fill) el('circle', { cx: e.x + e.w - 12, cy: e.y + 12, r: 7, fill, stroke: '#fff', 'stroke-width': 2 }, g);
    const txt = el('text', { x: cx, y: cy, 'text-anchor': 'middle', 'dominant-baseline': 'central', fill: '#0b2f5b',
                             'font-size': Math.max(10, Math.min(18, min * (booth ? 0.24 : 0.18))), 'font-weight': 600 }, g);
    txt.textContent = e.label || e.code || (booth ? t('Booth') : t('Room'));
    if (e.rotation) txt.setAttribute('transform', `rotate(${-e.rotation} ${cx} ${cy})`);
    if (e.capacity && e.h > 50) {
      const t2 = el('text', { x: cx, y: cy + Math.max(12, min * 0.16), 'text-anchor': 'middle', 'dominant-baseline': 'central',
                              fill: '#47607f', 'font-size': Math.max(9, Math.min(13, min * 0.12)) }, g);
      t2.textContent = seats(e.capacity);
    }
  } else if (e.type === 'wall') {
    el('rect', { x: e.x, y: e.y, width: e.w, height: e.h, fill: e.color || '#475569' }, g);
  } else if (e.type === 'zone') {
    const c = e.color || '#06b6d4';
    el('rect', { x: e.x, y: e.y, width: e.w, height: e.h, rx: 10, fill: c, 'fill-opacity': 0.12, stroke: c }, g);
    const lines = [e.label, (e.team_names || []).join(' + ')].filter(Boolean);
    if (lines.length) {
      const t1 = el('text', { x: e.x + 10, y: e.y + 18, fill: c, 'font-size': 13, 'font-weight': 700 }, g);
      t1.textContent = lines[0];
      if (lines.length > 1 && lines[1] !== lines[0]) {
        const t2 = el('text', { x: e.x + 10, y: e.y + 33, fill: c, 'font-size': 11 }, g);
        t2.textContent = lines[1];
      }
    }
  } else if (e.type === 'label') {
    const fs = Math.max(10, e.h * 0.7);
    el('rect', { x: e.x, y: e.y, width: e.w, height: e.h, fill: 'transparent' }, g);
    const txt = el('text', { x: cx, y: cy, 'text-anchor': 'middle', 'dominant-baseline': 'central', 'font-size': fs, fill: e.color || '#334155' }, g);
    txt.textContent = e.label || t('Label');
  }
  return g;
}

/* ── Panning and zooming the view (viewBox) ─────────────────────────────── */
class View {
  constructor(svg, onChange) {
    this.svg = svg;
    this.onChange = onChange || (() => {});
    this.vb = { x: 0, y: 0, w: 1600, h: 1000 };
    this.panning = null;
    this.panWithLeft = false;   // in the booking view the left button on the background pans the plan
    svg.addEventListener('wheel', e => {
      e.preventDefault();
      const p = this.point(e);
      this.zoom(e.deltaY < 0 ? 0.88 : 1 / 0.88, p);
    }, { passive: false });
    // Two fingers: pinch zoom. All active touch pointers are tracked.
    this.fingers = new Map();
    svg.addEventListener('pointerdown', e => {
      if (e.pointerType === 'touch') {
        this.fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (this.fingers.size === 2) {
          const [p1, p2] = [...this.fingers.values()];
          this.pinch = { dist: Math.hypot(p1.x - p2.x, p1.y - p2.y), vb: { ...this.vb },
                         centre: this.point({ clientX: (p1.x + p2.x) / 2, clientY: (p1.y + p2.y) / 2 }) };
          this.panning = null;
          this.lastPanned = true;
          return;
        }
      }
      this.lastPanned = false;
      const onBackground = !e.target.closest('[data-id]') && !e.target.closest('.handle');
      if (e.button === 1 || e.button === 2 || (e.button === 0 && (this.space || (this.panWithLeft && onBackground)))) {
        e.preventDefault();
        this.panning = { x: e.clientX, y: e.clientY, vb: { ...this.vb }, moved: false };
        svg.setPointerCapture(e.pointerId);
      }
    });
    svg.addEventListener('pointermove', e => {
      if (this.fingers.has(e.pointerId)) this.fingers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.pinch && this.fingers.size === 2) {
        const [p1, p2] = [...this.fingers.values()];
        const k = this.pinch.dist / Math.max(10, Math.hypot(p1.x - p2.x, p1.y - p2.y));
        const v = this.pinch.vb, c = this.pinch.centre;
        const nw = Math.min(20000, Math.max(80, v.w * k)), f = nw / v.w;
        this.vb = { x: c.x - (c.x - v.x) * f, y: c.y - (c.y - v.y) * f, w: nw, h: v.h * f };
        this.apply();
        return;
      }
      if (!this.panning) return;
      const s = this.scale();
      const dx = (e.clientX - this.panning.x) * s, dy = (e.clientY - this.panning.y) * s;
      if (Math.abs(dx) + Math.abs(dy) > 2) this.panning.moved = true;
      this.vb.x = this.panning.vb.x - dx;
      this.vb.y = this.panning.vb.y - dy;
      this.apply();
    });
    const end = e => {
      this.fingers.delete(e.pointerId);
      if (this.fingers.size < 2) this.pinch = null;
      if (this.panning) { this.lastPanned = this.panning.moved; this.panning = null; }
    };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    svg.addEventListener('contextmenu', e => e.preventDefault());
    window.addEventListener('keydown', e => { if (e.code === 'Space' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) { this.space = true; svg.style.cursor = 'grab'; e.preventDefault(); } });
    window.addEventListener('keyup', e => { if (e.code === 'Space') { this.space = false; svg.style.cursor = ''; } });
    window.addEventListener('resize', () => this.apply());
  }
  scale() {
    const r = this.svg.getBoundingClientRect();
    return Math.max(this.vb.w / r.width, this.vb.h / r.height);
  }
  point(e) {
    const pt = this.svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    return pt.matrixTransform(this.svg.getScreenCTM().inverse());
  }
  apply() {
    this.svg.setAttribute('viewBox', `${this.vb.x} ${this.vb.y} ${this.vb.w} ${this.vb.h}`);
    this.onChange();
  }
  zoom(f, p) {
    p = p || { x: this.vb.x + this.vb.w / 2, y: this.vb.y + this.vb.h / 2 };
    const nw = Math.min(20000, Math.max(80, this.vb.w * f));
    const k = nw / this.vb.w;
    this.vb = { x: p.x - (p.x - this.vb.x) * k, y: p.y - (p.y - this.vb.y) * k, w: nw, h: this.vb.h * k };
    this.apply();
  }
  fit(w, h, margin = 30) {
    this.vb = { x: -margin, y: -margin, w: w + 2 * margin, h: h + 2 * margin };
    this.apply();
  }
  center(x, y, width = 500) {
    const r = this.svg.getBoundingClientRect();
    const h = width * r.height / r.width;
    this.vb = { x: x - width / 2, y: y - h / 2, w: width, h };
    this.apply();
  }
}

/* Drop-down menu in the header: close after clicking outside. */
document.addEventListener('click', ev => {
  document.querySelectorAll('details.menu[open]').forEach(m => { if (!m.contains(ev.target)) m.removeAttribute('open'); });
});

/* ☰ menu on a narrow screen. */
(() => {
  const b = document.getElementById('menu-button');
  if (b) b.onclick = () => document.querySelector('.topbar').classList.toggle('menu-open');
})();
