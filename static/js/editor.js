/* Floor plan editor: drawing, selecting, moving, resizing, rotating, undo. */
'use strict';
(() => {
  const svg = document.getElementById('plan');
  const panel = document.getElementById('props');
  const statusEl = document.getElementById('status');
  const view = new View(svg, () => drawOverlay());

  const DEFAULTS = {
    desk: { w: 80, h: 50 }, room: { w: 200, h: 140 }, booth: { w: 60, h: 60 }, wall: { w: 200, h: 10 },
    zone: { w: 300, h: 200 }, label: { w: 140, h: 30 },
  };
  const NAMES = { desk: 'Desk', room: 'Room', booth: 'Booth', wall: 'Wall', zone: 'Zone', label: 'Label' };
  const LAYER = { zone: 0, room: 1, booth: 1, wall: 2, desk: 3, label: 4 };
  const teamById = Object.fromEntries(TEAMS.map(z => [z.id, z]));

  let floor = null, background = null;
  let elements = [];
  let sel = new Set();
  let tool = 'select';
  let gridOn = true;
  let op = null;
  let undoStack = [], redoStack = [];
  let dirty = false;
  let newCounter = 0;
  let clipboard = [];
  let gEl, gOver;

  // ── Helpers ───────────────────────────────────────────────────────────────
  const grid = () => floor.grid || 20;
  const snap = (v, alt) => (gridOn && !alt) ? Math.round(v / grid()) * grid() : Math.round(v);
  const find = id => elements.find(e => String(e.id) === String(id));
  const selected = () => elements.filter(e => sel.has(e.id));
  const newId = () => 'n' + (++newCounter);
  const snapshot = () => JSON.stringify(elements);

  function remember(before) {
    if (before === snapshot()) return;
    undoStack.push(before);
    if (undoStack.length > 150) undoStack.shift();
    redoStack = [];
    markDirty();
  }

  function markDirty(v = true) {
    dirty = v;
    statusEl.textContent = v ? t('Unsaved changes') : t('Saved');
    statusEl.classList.toggle('dirty', v);
  }

  function rotate(px, py, cx, cy, angle) {
    const r = angle * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    return { x: cx + (px - cx) * c - (py - cy) * s, y: cy + (px - cx) * s + (py - cy) * c };
  }

  /* Bounding box (after rotation) - for marquee selection. */
  function bbox(e) {
    const cx = e.x + e.w / 2, cy = e.y + e.h / 2;
    const pts = [[e.x, e.y], [e.x + e.w, e.y], [e.x, e.y + e.h], [e.x + e.w, e.y + e.h]].map(([x, y]) => rotate(x, y, cx, cy, e.rotation || 0));
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    return { x1: Math.min(...xs), y1: Math.min(...ys), x2: Math.max(...xs), y2: Math.max(...ys) };
  }

  /* Next desk number: the most common prefix + (max number + 1), zero-padded. */
  function nextCode(forcedPrefix) {
    const codes = elements.filter(e => e.type === 'desk' && e.code).map(e => e.code);
    const used = new Set(codes);
    let prefix = forcedPrefix, len = 2;
    if (prefix === undefined) {
      const count = {};
      codes.forEach(k => { const m = k.match(/^(.*?)(\d+)$/); if (m) count[m[1]] = (count[m[1]] || 0) + 1; });
      prefix = Object.keys(count).sort((a, b) => count[b] - count[a])[0] ?? 'A-';
    }
    let max = 0;
    codes.forEach(k => {
      const m = k.match(/^(.*?)(\d+)$/);
      if (m && m[1] === prefix) { max = Math.max(max, parseInt(m[2], 10)); len = Math.max(len, m[2].length); }
    });
    let n = max + 1, code;
    do { code = prefix + String(n++).padStart(len, '0'); } while (used.has(code));
    return code;
  }

  function newElement(type, x, y, w, h) {
    const e = {
      id: newId(), type, x, y, w: w ?? DEFAULTS[type].w, h: h ?? DEFAULTS[type].h, rotation: 0,
      code: null, label: null, color: null, bookable: 1, assigned_email: null,
      assigned_name: null, equipment: null, teams: [],
    };
    if (type === 'desk') e.code = nextCode();
    if (type === 'room') { e.label = t('Room') + ' ' + (elements.filter(x => x.type === 'room').length + 1); e.capacity = 6; }
    if (type === 'booth') { e.label = t('Booth') + ' ' + (elements.filter(x => x.type === 'booth').length + 1); e.capacity = 1; }
    if (type === 'zone') { e.label = t('Zone'); e.color = '#06b6d4'; }
    if (type === 'label') e.label = t('Label');
    return e;
  }

  /* Teams of a desk: its own, and when it has none - from the zones it stands in (as on the server). */
  function effectiveTeams(b) {
    if (b.teams && b.teams.length) return { teams: b.teams, zones: [] };
    const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
    const ids = new Set(), zones = [];
    elements.forEach(z => {
      if (z.type !== 'zone' || !z.teams || !z.teams.length) return;
      const p = rotate(cx, cy, z.x + z.w / 2, z.y + z.h / 2, -(z.rotation || 0));
      if (p.x >= z.x && p.x <= z.x + z.w && p.y >= z.y && p.y <= z.y + z.h) {
        z.teams.forEach(id => ids.add(id)); zones.push(z.label || t('zone'));
      }
    });
    return { teams: [...ids], zones };
  }

  function deskColor(e) {
    if (!e.bookable) return '#cbd5e1';
    if (e.assigned_email) return '#334155';
    const teams = effectiveTeams(e).teams;
    if (teams.length && teamById[teams[0]]) return teamById[teams[0]].color;
    return '#94a3b8';   // open to everyone - neutral, so it is not mistaken for a team colour
  }

  const teamNames = ids => (ids || []).map(i => teamById[i] && teamById[i].name).filter(Boolean);

  // ── Drawing ───────────────────────────────────────────────────────────────
  function drawAll() {
    svg.innerHTML = '';
    const defs = el('defs', {}, svg);
    const pat = el('pattern', { id: 'grid', width: grid(), height: grid(), patternUnits: 'userSpaceOnUse' }, defs);
    el('path', { d: `M ${grid()} 0 L 0 0 0 ${grid()}`, fill: 'none', stroke: '#e5e9f0', 'stroke-width': 1 }, pat);
    el('rect', { x: 0, y: 0, width: floor.width, height: floor.height, fill: '#fff' }, svg);
    if (background) el('image', { href: background, x: 0, y: 0, width: floor.width, height: floor.height, opacity: floor.background_opacity, preserveAspectRatio: 'xMidYMid meet', 'pointer-events': 'none' }, svg);
    el('rect', { x: 0, y: 0, width: floor.width, height: floor.height, fill: 'url(#grid)', 'pointer-events': 'none', opacity: gridOn ? 1 : 0.35 }, svg);
    el('rect', { x: 0, y: 0, width: floor.width, height: floor.height, fill: 'none', stroke: '#94a3b8', 'stroke-width': 2, 'pointer-events': 'none' }, svg);
    gEl = el('g', {}, svg);
    gOver = el('g', {}, svg);
    drawElements();
  }

  function drawElements() {
    gEl.innerHTML = '';
    [...elements].sort((a, b) => LAYER[a.type] - LAYER[b.type]).forEach(e => {
      const data = e.type === 'zone' ? { ...e, team_names: teamNames(e.teams),
        color: e.color || (e.teams.length && teamById[e.teams[0]] ? teamById[e.teams[0]].color : null) } : e;
      const g = drawElement(data, gEl, e.type === 'desk' ? deskColor(e) : null);
      g.style.cursor = tool === 'select' ? 'move' : 'crosshair';
      const eff = e.type === 'desk' ? effectiveTeams(e).teams : [];
      if (eff.length > 1) {
        // several teams - dots in the corner of the desk
        eff.slice(1, 4).forEach((tid, i) => {
          const z = teamById[tid];
          if (z) el('circle', { cx: e.x + 7 + i * 9, cy: e.y + 7, r: 3.5, fill: z.color, stroke: '#fff' }, g);
        });
      }
    });
    drawOverlay();
    summary();
  }

  function drawOverlay() {
    if (!gOver) return;
    gOver.innerHTML = '';
    const s = view.scale();
    selected().forEach(e => {
      const cx = e.x + e.w / 2, cy = e.y + e.h / 2;
      const g = el('g', { transform: e.rotation ? `rotate(${e.rotation} ${cx} ${cy})` : null }, gOver);
      const m = 3 * s;
      el('rect', { class: 'ed-sel-outline', x: e.x - m, y: e.y - m, width: e.w + 2 * m, height: e.h + 2 * m, 'stroke-width': 1.5 * s }, g);
      if (sel.size === 1 && tool === 'select') {
        const r = 5 * s;
        const handles = { nw: [e.x, e.y], n: [cx, e.y], ne: [e.x + e.w, e.y], e: [e.x + e.w, cy],
                          se: [e.x + e.w, e.y + e.h], s: [cx, e.y + e.h], sw: [e.x, e.y + e.h], w: [e.x, cy] };
        const cursors = { nw: 'nwse', se: 'nwse', ne: 'nesw', sw: 'nesw', n: 'ns', s: 'ns', e: 'ew', w: 'ew' };
        for (const [k, [x, y]] of Object.entries(handles)) {
          if (e.type === 'wall' && (e.w > e.h ? ['n', 's', 'nw', 'ne', 'sw', 'se'] : ['e', 'w', 'nw', 'ne', 'sw', 'se']).includes(k)) continue;
          const u = el('rect', { class: 'handle', 'data-handle': k, x: x - r, y: y - r, width: 2 * r, height: 2 * r, 'stroke-width': 1.5 * s }, g);
          u.style.cursor = cursors[k] + '-resize';
        }
        el('line', { x1: cx, y1: e.y, x2: cx, y2: e.y - 24 * s, stroke: '#2563eb', 'stroke-width': 1.2 * s }, g);
        el('circle', { class: 'handle rotate', 'data-handle': 'rotate', cx, cy: e.y - 24 * s, r: 6 * s, 'stroke-width': 1.5 * s }, g);
      }
    });
    if (op && op.type === 'marquee') {
      const r = op.box;
      el('rect', { class: 'marquee', x: Math.min(r.x1, r.x2), y: Math.min(r.y1, r.y2), width: Math.abs(r.x2 - r.x1), height: Math.abs(r.y2 - r.y1), 'stroke-width': s }, gOver);
    }
    if (op && op.type === 'draw' && op.preview) {
      const p = op.preview;
      el('rect', { x: p.x, y: p.y, width: p.w, height: p.h, fill: 'rgba(37,99,235,.15)', stroke: '#2563eb', 'stroke-width': 1.5 * s, 'stroke-dasharray': `${4 * s} ${3 * s}` }, gOver);
    }
  }

  function summary() {
    const p = document.getElementById('summary');
    if (!p) return;
    const d = elements.filter(e => e.type === 'desk');
    p.innerHTML = esc(t('Desks: {n} (bookable {bookable}, permanent {fixed}, with a team {team})', {
      n: d.length, bookable: d.filter(e => e.bookable && !e.assigned_email).length,
      fixed: d.filter(e => e.assigned_email).length, team: d.filter(e => e.teams.length).length })) + '<br>'
      + esc(t('Rooms: {rooms} · Walls: {walls} · Zones: {zones}', { rooms: elements.filter(e => e.type === 'room').length,
        walls: elements.filter(e => e.type === 'wall').length, zones: elements.filter(e => e.type === 'zone').length }));
  }

  // ── Properties panel ──────────────────────────────────────────────────────
  let beforeEdit = null;
  panel.addEventListener('focusin', () => { beforeEdit = snapshot(); });
  panel.addEventListener('change', () => { if (beforeEdit) { remember(beforeEdit); beforeEdit = snapshot(); } });

  function field(label, html) { return `<label class="f">${esc(label)}${html}</label>`; }

  function teamList(checked, cls = 'team-cb') {
    if (!TEAMS.length) return `<p class="small muted">${esc(t('No teams - create them in the Teams tab.'))}</p>`;
    return `<div class="team-list">${TEAMS.map(z => `<label><input type="checkbox" class="${cls}" value="${z.id}" ${checked.includes(z.id) ? 'checked' : ''}>
      <span class="chip"><i style="background:${esc(z.color)}"></i>${esc(z.name)}</span></label>`).join('')}</div>
      <p class="small muted" style="margin:4px 0 0">${esc(t('Nothing ticked - open to everyone.'))}</p>`;
  }

  function showPanel() {
    const w = selected();
    if (op && op.type === 'generator') return;
    if (!w.length) {
      panel.innerHTML = document.getElementById('tpl-floor').innerHTML;
      wireFloorPanel();
      summary();
      return;
    }
    if (w.length > 1) { multiPanel(w); return; }
    const e = w[0];
    let h = `<div class="row"><h3 style="margin:0">${esc(t(NAMES[e.type]))}</h3><span class="spacer"></span>
      <button class="btn sm" data-action="rot-" title="${esc(t('Rotate left'))} (Q)">⟲</button>
      <button class="btn sm" data-action="rot+" title="${esc(t('Rotate right'))} (E)">⟳</button>
      <button class="btn sm" data-action="duplicate" title="${esc(t('Duplicate'))} (Ctrl+D)">⧉</button>
      <button class="btn sm danger" data-action="delete" title="${esc(t('Delete'))} (Del)">✕</button></div><br>`;
    if (e.type === 'desk') {
      h += field(t('Desk number'), `<input type="text" data-p="code" value="${esc(e.code || '')}">`);
    }
    if (e.type !== 'wall' && e.type !== 'desk') {
      h += field(t('Description / name'), `<input type="text" data-p="label" value="${esc(e.label || '')}">`);
    }
    h += `<div class="two">
      ${field('X', `<input type="number" data-p="x" data-num value="${e.x}">`)}
      ${field('Y', `<input type="number" data-p="y" data-num value="${e.y}">`)}
      ${field(t('Width'), `<input type="number" data-p="w" data-num min="2" value="${e.w}">`)}
      ${field(t('Height'), `<input type="number" data-p="h" data-num min="2" value="${e.h}">`)}
    </div>`;
    h += field(t('Rotation (°)'), `<input type="number" data-p="rotation" data-num step="15" value="${e.rotation || 0}">`);
    if (e.type !== 'desk') {
      h += field(t('Colour'), `<input type="color" data-p="color" value="${esc(e.color || { room: '#dbeafe', booth: '#e0f2fe', wall: '#475569', zone: '#06b6d4', label: '#334155' }[e.type])}">`);
    }
    if (e.type === 'room' || e.type === 'booth') {
      h += field(t('Number of seats'), `<input type="number" data-p="capacity" data-num min="1" value="${e.capacity || ''}">`);
      h += field(t('Equipment'), `<input type="text" data-p="equipment" placeholder="${esc(t('e.g. screen, camera, whiteboard'))}" value="${esc(e.equipment || '')}">`);
      h += `<label class="row" style="margin:6px 0 10px"><input type="checkbox" data-p="bookable" ${e.bookable ? 'checked' : ''}> ${esc(t('Bookable (by the hour)'))}</label>`;
      h += `<h3>${esc(t('Available to teams'))}</h3>${teamList(e.teams)}`;
    }
    if (e.type === 'desk') {
      h += `<label class="row" style="margin:6px 0 10px"><input type="checkbox" data-p="bookable" ${e.bookable ? 'checked' : ''}> ${esc(t('Bookable'))}</label>`;
      h += field(t('Permanent desk - assigned to'), `<input type="text" id="assigned" placeholder="${esc(t('search for a person…'))}" value="${esc(e.assigned_name || e.assigned_email || '')}">`);
      if (e.assigned_email) h += `<p class="small" style="margin-top:-6px">${esc(e.assigned_email)} <a href="#" id="unassign">${esc(t('unassign'))}</a></p>`;
      h += field(t('Equipment'), `<input type="text" data-p="equipment" placeholder="${esc(t('e.g. 2 monitors, docking station'))}" value="${esc(e.equipment || '')}">`);
      h += `<h3>${esc(t('Available to teams'))}</h3>${teamList(e.teams)}`;
      const eff = effectiveTeams(e);
      if (!e.teams.length && eff.zones.length) {
        h += `<p class="small" style="margin-top:6px">${esc(t('Now inherits from the zone {zone}: {teams}. Ticking teams here overrides the zone.', { zone: eff.zones.join(', '), teams: teamNames(eff.teams).join(', ') }))}</p>`;
      }
    }
    if (e.type === 'zone') {
      h += `<h3>${esc(t('Space shared by teams'))}</h3>${teamList(e.teams)}
        <p class="small muted" style="margin-top:4px">${esc(t('Desks in the zone without their own teams are available to all teams ticked here.'))}</p>`;
    }
    panel.innerHTML = h;

    panel.querySelectorAll('[data-p]').forEach(inp => {
      inp.addEventListener('input', () => {
        const k = inp.dataset.p;
        let v = inp.type === 'checkbox' ? (inp.checked ? 1 : 0) : inp.value;
        if (inp.dataset.num !== undefined) { v = parseFloat(v); if (isNaN(v)) return; if ((k === 'w' || k === 'h') && v < 2) return; }
        e[k] = v === '' ? null : v;
        drawElements();
      });
    });
    panel.querySelectorAll('.team-cb').forEach(cb => cb.addEventListener('change', () => {
      e.teams = [...panel.querySelectorAll('.team-cb:checked')].map(c => parseInt(c.value, 10));
      drawElements();
    }));
    panel.querySelectorAll('[data-action]').forEach(b => b.addEventListener('click', () => action(b.dataset.action)));
    const inpA = document.getElementById('assigned');
    if (inpA) {
      personPicker(inpA, o => {
        const before = snapshot();
        e.assigned_email = o.email; e.assigned_name = o.name;
        remember(before); drawElements(); showPanel();
      });
      const un = document.getElementById('unassign');
      if (un) un.onclick = ev => { ev.preventDefault(); const before = snapshot(); e.assigned_email = null; e.assigned_name = null; remember(before); drawElements(); showPanel(); };
    }
  }

  function multiPanel(w) {
    const desks = w.filter(e => e.type === 'desk');
    let h = `<div class="row"><h3 style="margin:0">${esc(t('Selected: {n}', { n: w.length }))}</h3><span class="spacer"></span>
      <button class="btn sm" data-action="rot-">⟲</button><button class="btn sm" data-action="rot+">⟳</button>
      <button class="btn sm" data-action="duplicate">⧉</button><button class="btn sm danger" data-action="delete">✕</button></div>
      <h3>${esc(t('Align'))}</h3><div class="row">
        <button class="btn sm" data-align="left">⇤ ${esc(t('left'))}</button><button class="btn sm" data-align="top">⤒ ${esc(t('top'))}</button>
        <button class="btn sm" data-align="middleX">↔ ${esc(t('middle'))}</button><button class="btn sm" data-align="middleY">↕ ${esc(t('middle'))}</button>
        <button class="btn sm" data-align="spreadX">${esc(t('spread horizontally'))}</button><button class="btn sm" data-align="spreadY">${esc(t('spread vertically'))}</button>
      </div>`;
    if (desks.length) {
      const common = TEAMS.filter(z => desks.every(b => b.teams.includes(z.id))).map(z => z.id);
      h += `<h3>${esc(t('Desks ({n}) - teams', { n: desks.length }))}</h3>
        ${TEAMS.length ? `<div class="team-list">${TEAMS.map(z => {
          const count = desks.filter(b => b.teams.includes(z.id)).length;
          return `<label><input type="checkbox" class="team-multi" value="${z.id}" ${common.includes(z.id) ? 'checked' : ''} ${count && count < desks.length ? 'data-partial="1"' : ''}>
            <span class="chip"><i style="background:${esc(z.color)}"></i>${esc(z.name)}</span>
            ${count && count < desks.length ? `<span class="small muted">(${count}/${desks.length})</span>` : ''}</label>`;
        }).join('')}</div>` : `<p class="small muted">${esc(t('No teams.'))}</p>`}
        <div class="row" style="margin-top:8px">
          <button class="btn sm" data-action="book1">${esc(t('Bookable'))}</button>
          <button class="btn sm" data-action="book0">${esc(t('Not bookable'))}</button>
          <button class="btn sm" data-action="noteams">${esc(t('Open to everyone'))}</button></div>
        <h3>${esc(t('Numbering'))}</h3>
        <div class="two">${field(t('Prefix'), `<input type="text" id="num-prefix" value="${esc((desks[0].code || 'A-').replace(/\d+$/, ''))}">`)}
          ${field(t('From number'), `<input type="number" id="num-start" value="1" min="0">`)}</div>
        <div class="row"><select id="num-dir"><option value="rows">${esc(t('by rows (left→right)'))}</option><option value="cols">${esc(t('by columns (top→bottom)'))}</option></select>
          <button class="btn sm blue" data-action="number">${esc(t('Number'))}</button></div>`;
    }
    panel.innerHTML = h;
    panel.querySelectorAll('[data-action]').forEach(b => b.addEventListener('click', () => action(b.dataset.action)));
    panel.querySelectorAll('[data-align]').forEach(b => b.addEventListener('click', () => align(b.dataset.align)));
    panel.querySelectorAll('.team-multi').forEach(cb => {
      if (cb.dataset.partial) cb.indeterminate = true;
      cb.addEventListener('change', () => {
        const tid = parseInt(cb.value, 10);
        desks.forEach(b => {
          b.teams = b.teams.filter(x => x !== tid);
          if (cb.checked) b.teams.push(tid);
        });
        drawElements();
      });
    });
  }

  function wireFloorPanel() {
    const file = document.getElementById('bg-file');
    const opacity = document.getElementById('bg-opacity');
    const floorForm = document.getElementById('floor-form');
    floorForm.addEventListener('submit', async ev => {
      if (!dirty) return;
      ev.preventDefault();
      if (await confirmDialog(t('You have unsaved plan changes that will be lost. Continue?'), { button: t('Continue'), danger: true })) {
        dirty = false;
        floorForm.submit();
      }
    });
    file.onchange = async () => {
      if (!file.files[0]) return;
      const fd = new FormData();
      fd.append('file', file.files[0]);
      const r = await api(`/admin/floors/${FLOOR_ID}/background`, { method: 'POST', body: fd });
      if (!r.ok) { toast(r.error || t('Could not upload the file'), true); return; }
      background = r.background; drawAll(); toast(t('Background uploaded.'));
    };
    let timer;
    opacity.oninput = () => {
      floor.background_opacity = parseFloat(opacity.value);
      const img = svg.querySelector('image');
      if (img) img.setAttribute('opacity', floor.background_opacity);
      clearTimeout(timer);
      timer = setTimeout(() => { const fd = new FormData(); fd.append('opacity', opacity.value); api(`/admin/floors/${FLOOR_ID}/background`, { method: 'POST', body: fd }); }, 400);
    };
    document.getElementById('bg-remove').onclick = async () => {
      if (!(await confirmDialog(t('Remove the plan background?'), { button: t('Remove'), danger: true }))) return;
      const fd = new FormData(); fd.append('remove', '1');
      await api(`/admin/floors/${FLOOR_ID}/background`, { method: 'POST', body: fd });
      background = null; drawAll();
    };
  }

  // ── Desk block generator ──────────────────────────────────────────────────
  function generator(x, y) {
    op = { type: 'generator' };
    panel.innerHTML = `<h3 style="margin-top:0">${esc(t('Block of desks'))}</h3>
      <div class="two">
        ${field(t('Rows'), '<input type="number" id="g-rows" value="2" min="1" max="30">')}
        ${field(t('Desks per row'), '<input type="number" id="g-cols" value="4" min="1" max="40">')}
        ${field(t('Desk width'), '<input type="number" id="g-w" value="80" min="10">')}
        ${field(t('Desk depth'), '<input type="number" id="g-h" value="60" min="10">')}
        ${field(t('Gap in a row'), '<input type="number" id="g-dx" value="0" min="0">')}
        ${field(t('Gap between rows'), '<input type="number" id="g-dy" value="100" min="0">')}
      </div>
      <label class="row" style="margin:6px 0"><input type="checkbox" id="g-facing" checked> ${esc(t('Rows in pairs "face to face" (islands)'))}</label>
      <div class="two">${field(t('Number prefix'), `<input type="text" id="g-prefix" value="${esc(nextCode().replace(/\d+$/, ''))}">`)}
        ${field(t('First number'), `<input type="number" id="g-start" value="${parseInt((nextCode().match(/\d+$/) || ['1'])[0], 10)}">`)}</div>
      <h3>${esc(t('Teams'))}</h3>${teamList([], 'g-team')}
      <label class="row" style="margin:8px 0"><input type="checkbox" id="g-zone"> ${esc(t('Surround the block with a zone and assign the teams to the zone'))}</label>
      ${field(t('Zone name'), `<input type="text" id="g-zone-name" placeholder="${esc(t('e.g. Row 1 - Purchasing'))}">`)}
      <div class="row" style="margin-top:12px"><button class="btn primary" id="g-ok">${esc(t('Insert'))}</button><button class="btn" id="g-cancel">${esc(t('Cancel'))}</button></div>`;
    const v = id => parseFloat(document.getElementById(id).value) || 0;
    document.getElementById('g-cancel').onclick = () => { op = null; setTool('select'); showPanel(); };
    document.getElementById('g-ok').onclick = () => {
      const before = snapshot();
      const rows = Math.max(1, v('g-rows')), cols = Math.max(1, v('g-cols')), w = v('g-w'), h = v('g-h');
      const dx = v('g-dx'), dy = v('g-dy'), facing = document.getElementById('g-facing').checked;
      const prefix = document.getElementById('g-prefix').value;
      let nr = v('g-start');
      const teams = [...panel.querySelectorAll('.g-team:checked')].map(c => parseInt(c.value, 10));
      const len = Math.max(2, String(nr + rows * cols).length);
      const used = new Set(elements.map(e => e.code).filter(Boolean));
      const created = [];
      let yy = snap(y);
      for (let r = 0; r < rows; r++) {
        for (let k = 0; k < cols; k++) {
          const e = newElement('desk', snap(x) + k * (w + dx), yy, w, h);
          let code;
          do { code = prefix + String(nr++).padStart(len, '0'); } while (used.has(code));
          used.add(code);
          e.code = code; e.teams = [...teams];
          // In a pair of rows the upper one is turned around (chair at the top) - the desks touch with their tops.
          if (facing && r % 2 === 0 && r + 1 < rows) e.rotation = 180;
          elements.push(e); created.push(e.id);
        }
        yy += h + ((facing && r % 2 === 0) ? 0 : dy);
      }
      if (document.getElementById('g-zone').checked) {
        // teams on the zone, desks without their own - they inherit
        // margin for the chairs (on both sides of an island) and for the zone label at the top
        const bl = created.map(find), m = 16, chair = Math.min(w, h) * 0.32;
        const x1 = Math.min(...bl.map(b => b.x)) - m, y1 = Math.min(...bl.map(b => b.y)) - chair - m - 30;
        const x2 = Math.max(...bl.map(b => b.x + b.w)) + m, y2 = Math.max(...bl.map(b => b.y + b.h)) + chair + m;
        const z = newElement('zone', x1, y1, x2 - x1, y2 - y1);
        z.label = document.getElementById('g-zone-name').value || teamNames(teams).join(' + ') || t('Zone');
        z.color = teams.length && teamById[teams[0]] ? teamById[teams[0]].color : '#06b6d4';
        z.teams = [...teams];
        bl.forEach(b => { b.teams = []; });
        elements.push(z);
      }
      sel = new Set(created);
      op = null;
      remember(before);
      setTool('select');
      drawElements(); showPanel();
    };
  }

  // ── Actions ───────────────────────────────────────────────────────────────
  function action(a) {
    const before = snapshot();
    const w = selected();
    if (a === 'delete') {
      elements = elements.filter(e => !sel.has(e.id)); sel.clear();
    } else if (a === 'rot+' || a === 'rot-') {
      w.forEach(e => { e.rotation = ((e.rotation || 0) + (a === 'rot+' ? 90 : -90) + 360) % 360; });
    } else if (a === 'duplicate') {
      paste(w.map(e => ({ ...e })), grid() * 2);
      return remember(before);
    } else if (a === 'book1' || a === 'book0') {
      w.filter(e => e.type === 'desk').forEach(e => { e.bookable = a === 'book1' ? 1 : 0; });
    } else if (a === 'noteams') {
      w.filter(e => e.type === 'desk').forEach(e => { e.teams = []; });
    } else if (a === 'number') {
      const prefix = document.getElementById('num-prefix').value;
      let nr = parseInt(document.getElementById('num-start').value, 10) || 1;
      const dir = document.getElementById('num-dir').value;
      const d = w.filter(e => e.type === 'desk');
      const tol = 20;  // desks in the same row may differ by a few px
      d.sort((p, q) => dir === 'rows'
        ? (Math.abs(p.y - q.y) > tol ? p.y - q.y : p.x - q.x)
        : (Math.abs(p.x - q.x) > tol ? p.x - q.x : p.y - q.y));
      const len = Math.max(2, String(nr + d.length).length);
      const ids = new Set(d.map(e => e.id));
      const used = new Set(elements.filter(e => !ids.has(e.id)).map(e => e.code).filter(Boolean));
      d.forEach(e => { let code; do { code = prefix + String(nr++).padStart(len, '0'); } while (used.has(code)); e.code = code; });
    }
    remember(before);
    drawElements(); showPanel();
  }

  function align(mode) {
    const before = snapshot();
    const w = selected();
    if (mode === 'left') { const m = Math.min(...w.map(e => e.x)); w.forEach(e => { e.x = m; }); }
    if (mode === 'top') { const m = Math.min(...w.map(e => e.y)); w.forEach(e => { e.y = m; }); }
    if (mode === 'middleX') { const m = w.reduce((s, e) => s + e.y + e.h / 2, 0) / w.length; w.forEach(e => { e.y = Math.round(m - e.h / 2); }); }
    if (mode === 'middleY') { const m = w.reduce((s, e) => s + e.x + e.w / 2, 0) / w.length; w.forEach(e => { e.x = Math.round(m - e.w / 2); }); }
    if (mode === 'spreadX' && w.length > 2) {
      w.sort((a, b) => a.x - b.x);
      const sum = w.reduce((s, e) => s + e.w, 0), gap = (w[w.length - 1].x + w[w.length - 1].w - w[0].x - sum) / (w.length - 1);
      let x = w[0].x; w.forEach(e => { e.x = Math.round(x); x += e.w + gap; });
    }
    if (mode === 'spreadY' && w.length > 2) {
      w.sort((a, b) => a.y - b.y);
      const sum = w.reduce((s, e) => s + e.h, 0), gap = (w[w.length - 1].y + w[w.length - 1].h - w[0].y - sum) / (w.length - 1);
      let y = w[0].y; w.forEach(e => { e.y = Math.round(y); y += e.h + gap; });
    }
    remember(before);
    drawElements();
  }

  function paste(copies, shift) {
    const created = [];
    copies.forEach(k => {
      const e = { ...k, id: newId(), x: k.x + shift, y: k.y + shift, teams: [...(k.teams || [])] };
      if (e.type === 'desk') { e.assigned_email = null; e.assigned_name = null; e.code = nextCode((k.code || '').replace(/\d+$/, '')); }
      elements.push(e); created.push(e.id);
    });
    sel = new Set(created);
    drawElements(); showPanel();
  }

  function setTool(n) {
    tool = n;
    document.querySelectorAll('#tools [data-tool]').forEach(b => b.classList.toggle('active', b.dataset.tool === n));
    svg.style.cursor = n === 'select' ? '' : 'crosshair';
    if (n !== 'select') { sel.clear(); showPanel(); }
    drawElements();
  }

  // ── Mouse ─────────────────────────────────────────────────────────────────
  svg.addEventListener('pointerdown', ev => {
    if (view.panning || ev.button !== 0) return;
    svg.focus();
    const p = view.point(ev);
    const handle = ev.target.closest('[data-handle]');
    const gEl_ = ev.target.closest('[data-id]');

    if (handle && sel.size === 1) {
      const e = selected()[0];
      op = { type: handle.dataset.handle === 'rotate' ? 'rotate' : 'resize', dir: handle.dataset.handle, e, start: { ...e }, before: snapshot() };
    } else if (tool === 'block') {
      generator(p.x, p.y);
      return;
    } else if (tool !== 'select') {
      const x = snap(p.x, ev.altKey), y = snap(p.y, ev.altKey);
      op = { type: 'draw', x0: x, y0: y, preview: null };
    } else if (gEl_) {
      const id = find(gEl_.dataset.id).id;
      if (ev.shiftKey) { sel.has(id) ? sel.delete(id) : sel.add(id); }
      else if (!sel.has(id)) sel = new Set([id]);
      op = { type: 'move', p0: p, start: selected().map(e => ({ e, x: e.x, y: e.y })), before: snapshot(), moved: false };
      showPanel();
    } else {
      if (!ev.shiftKey) sel.clear();
      op = { type: 'marquee', box: { x1: p.x, y1: p.y, x2: p.x, y2: p.y }, was: new Set(sel) };
      showPanel();
    }
    svg.setPointerCapture(ev.pointerId);
    drawOverlay();
  });

  svg.addEventListener('pointermove', ev => {
    if (!op || view.panning) return;
    const p = view.point(ev);
    const o = op;
    if (o.type === 'move') {
      const d0 = o.start[0];
      let dx = p.x - o.p0.x, dy = p.y - o.p0.y;
      if (!o.moved && Math.hypot(dx, dy) < 3 * view.scale()) return;
      o.moved = true;
      dx = snap(d0.x + dx, ev.altKey) - d0.x;
      dy = snap(d0.y + dy, ev.altKey) - d0.y;
      o.start.forEach(s => { s.e.x = s.x + dx; s.e.y = s.y + dy; });
      drawElements();
    } else if (o.type === 'marquee') {
      o.box.x2 = p.x; o.box.y2 = p.y;
      const r = { x1: Math.min(o.box.x1, p.x), y1: Math.min(o.box.y1, p.y), x2: Math.max(o.box.x1, p.x), y2: Math.max(o.box.y1, p.y) };
      sel = new Set(o.was);
      elements.forEach(e => {
        const b = bbox(e);
        // zones (large backgrounds) only when entirely inside, the rest - when they intersect
        const inside = e.type === 'zone'
          ? (b.x1 >= r.x1 && b.x2 <= r.x2 && b.y1 >= r.y1 && b.y2 <= r.y2)
          : !(b.x2 < r.x1 || b.x1 > r.x2 || b.y2 < r.y1 || b.y1 > r.y2);
        if (inside) sel.add(e.id);
      });
      drawOverlay();
    } else if (o.type === 'draw') {
      const x = snap(p.x, ev.altKey), y = snap(p.y, ev.altKey);
      o.preview = { x: Math.min(x, o.x0), y: Math.min(y, o.y0), w: Math.abs(x - o.x0), h: Math.abs(y - o.y0) };
      drawOverlay();
    } else if (o.type === 'resize') {
      const s = o.start, e = o.e;
      const cx = s.x + s.w / 2, cy = s.y + s.h / 2;
      const lp = rotate(p.x, p.y, cx, cy, -(s.rotation || 0));     // the cursor in the element's coordinates
      let l = s.x, tp = s.y, r = s.x + s.w, b = s.y + s.h;
      const sn = v => snap(v, ev.altKey || (s.rotation % 90 !== 0));
      if (o.dir.includes('w')) l = Math.min(sn(lp.x), r - 2);
      if (o.dir.includes('e')) r = Math.max(sn(lp.x), l + 2);
      if (o.dir.includes('n')) tp = Math.min(sn(lp.y), b - 2);
      if (o.dir.includes('s')) b = Math.max(sn(lp.y), tp + 2);
      // the new centre from local coordinates back to the plan - the opposite edge stays in place
      const nc = rotate((l + r) / 2, (tp + b) / 2, cx, cy, s.rotation || 0);
      e.w = r - l; e.h = b - tp;
      e.x = Math.round((nc.x - e.w / 2) * 10) / 10; e.y = Math.round((nc.y - e.h / 2) * 10) / 10;
      drawElements();
    } else if (o.type === 'rotate') {
      const e = o.e, cx = e.x + e.w / 2, cy = e.y + e.h / 2;
      let angle = Math.atan2(p.y - cy, p.x - cx) * 180 / Math.PI + 90;
      angle = ev.shiftKey ? Math.round(angle) : Math.round(angle / 15) * 15;
      e.rotation = (angle + 360) % 360;
      drawElements();
    }
  });

  const end = () => {
    const o = op;
    if (!o || o.type === 'generator') return;
    op = null;
    if (o.type === 'draw') {
      const before = snapshot();
      const type = tool;
      let e;
      const pv = o.preview;
      if (type === 'wall' && pv && Math.max(pv.w, pv.h) >= 6) {
        // a wall is drawn like a line: fixed thickness, direction from the longer side
        const th = DEFAULTS.wall.h;
        e = pv.w >= pv.h ? newElement(type, pv.x, o.y0 - th / 2, pv.w, th) : newElement(type, o.x0 - th / 2, pv.y, th, pv.h);
      } else if (pv && pv.w >= 6 && pv.h >= 6) e = newElement(type, pv.x, pv.y, pv.w, pv.h);
      else e = newElement(type, o.x0 - snap(DEFAULTS[type].w / 2), o.y0 - snap(DEFAULTS[type].h / 2));
      elements.push(e);
      sel = new Set([e.id]);
      remember(before);
      // Desks are placed in series - the tool stays; everything else one at a time.
      if (type !== 'desk') setTool('select');
      drawElements(); showPanel();
    } else if (o.before) {
      remember(o.before);
      drawElements(); showPanel();
    } else {
      drawOverlay(); showPanel();
    }
  };
  svg.addEventListener('pointerup', end);
  svg.addEventListener('pointercancel', end);

  // ── Keyboard ──────────────────────────────────────────────────────────────
  window.addEventListener('keydown', ev => {
    if (document.querySelector('.dialog-backdrop')) return;
    const inField = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
    const ctrl = ev.ctrlKey || ev.metaKey;
    if (ctrl && ev.key.toLowerCase() === 's') { ev.preventDefault(); save(); return; }
    if (inField) return;
    const k = ev.key.toLowerCase();
    if (ctrl && k === 'z') { ev.preventDefault(); ev.shiftKey ? doRedo() : doUndo(); return; }
    if (ctrl && k === 'y') { ev.preventDefault(); doRedo(); return; }
    if (ctrl && k === 'a') { ev.preventDefault(); sel = new Set(elements.map(e => e.id)); drawOverlay(); showPanel(); return; }
    if (ctrl && k === 'c') { clipboard = selected().map(e => JSON.parse(JSON.stringify(e))); toast(t('Copied: {n}', { n: clipboard.length })); return; }
    if (ctrl && k === 'v') { if (clipboard.length) { const before = snapshot(); paste(clipboard, grid() * 2); clipboard = clipboard.map(e => ({ ...e, x: e.x + grid() * 2, y: e.y + grid() * 2 })); remember(before); } return; }
    if (ctrl && k === 'd') { ev.preventDefault(); if (sel.size) action('duplicate'); return; }
    if (ctrl) return;
    if (k === 'delete' || k === 'backspace') { if (sel.size) { ev.preventDefault(); action('delete'); } return; }
    if (k === 'escape') { op = null; sel.clear(); setTool('select'); showPanel(); return; }
    if (k.startsWith('arrow') && sel.size) {
      ev.preventDefault();
      const step = (ev.altKey ? 1 : grid()) * (ev.shiftKey ? 5 : 1);
      const before = snapshot();
      selected().forEach(e => {
        if (k === 'arrowleft') e.x -= step; if (k === 'arrowright') e.x += step;
        if (k === 'arrowup') e.y -= step; if (k === 'arrowdown') e.y += step;
      });
      remember(before); drawElements(); showPanel();
      return;
    }
    if (k === 'q' && sel.size) return action('rot-');
    if (k === 'e' && sel.size) return action('rot+');
    const shortcuts = { v: 'select', b: 'desk', r: 'block', s: 'room', k: 'booth', w: 'wall', z: 'zone', t: 'label' };
    if (shortcuts[k]) setTool(shortcuts[k]);
    if (k === 'g') toggleGrid();
  });

  function doUndo() {
    if (!undoStack.length) return;
    redoStack.push(snapshot());
    elements = JSON.parse(undoStack.pop());
    sel = new Set([...sel].filter(id => find(id)));
    markDirty(); drawElements(); showPanel();
  }
  function doRedo() {
    if (!redoStack.length) return;
    undoStack.push(snapshot());
    elements = JSON.parse(redoStack.pop());
    sel = new Set([...sel].filter(id => find(id)));
    markDirty(); drawElements(); showPanel();
  }
  function toggleGrid() {
    gridOn = !gridOn;
    document.getElementById('grid-on').classList.toggle('active', gridOn);
    drawAll();
  }

  // ── Saving ────────────────────────────────────────────────────────────────
  async function save(force = false) {
    statusEl.textContent = t('Saving…');
    const r = await api(`/admin/api/floors/${FLOOR_ID}/elements`, { method: 'POST', body: { elements, force } });
    if (r.status === 409 && r.confirm) {
      const list = r.confirm.map(k => `${k.code}: ${k.n}`).join(', ');
      if (await confirmDialog(t('The removed desks have upcoming bookings ({list}). The bookings will be cancelled and the people will get an e-mail. Continue?', { list }),
                              { button: t('Save and cancel the bookings'), danger: true })) return save(true);
      markDirty(); return;
    }
    if (!r.ok) { toast(r.error || t('Could not save'), true); markDirty(); return; }
    // new elements get their real ids from the database
    const map = id => (r.map && r.map[String(id)] !== undefined) ? r.map[String(id)] : id;
    elements.forEach(e => { e.id = map(e.id); });
    sel = new Set([...sel].map(map));
    undoStack = undoStack.map(s => JSON.stringify(JSON.parse(s).map(e => ({ ...e, id: map(e.id) }))));
    redoStack = redoStack.map(s => JSON.stringify(JSON.parse(s).map(e => ({ ...e, id: map(e.id) }))));
    markDirty(false);
    drawElements();
    toast(t('Plan saved.'));
  }

  window.addEventListener('beforeunload', ev => { if (dirty) { ev.preventDefault(); ev.returnValue = ''; } });

  document.querySelectorAll('#tools [data-tool]').forEach(b => b.addEventListener('click', () => setTool(b.dataset.tool)));
  document.getElementById('undo').onclick = doUndo;
  document.getElementById('redo').onclick = doRedo;
  document.getElementById('grid-on').onclick = toggleGrid;
  document.getElementById('save').onclick = () => save();
  document.getElementById('zoom-in').onclick = () => view.zoom(0.8);
  document.getElementById('zoom-out').onclick = () => view.zoom(1.25);
  document.getElementById('zoom-fit').onclick = () => view.fit(floor.width, floor.height);

  (async () => {
    const r = await api(`/admin/api/floors/${FLOOR_ID}/elements`);
    floor = r.floor; background = r.background;
    elements = r.elements.map(e => ({ ...e, teams: e.teams || [] }));
    drawAll();
    view.fit(floor.width, floor.height);
    markDirty(false);
    statusEl.textContent = elements.length ? t('Plan loaded') : t('Empty plan - choose a tool on the left and draw');
    showPanel();
  })();
})();
