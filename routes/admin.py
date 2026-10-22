"""Administration: accounts and roles, teams, floors and the plan editor, bookings, audit log
and settings (database, language, login and SSO, mail, API and MCP)."""
import csv
import io
import json
import os
import re
import secrets
from datetime import date, timedelta

from flask import (Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for)

import app_settings
import auth
import booking
import config
import db
import i18n
import notifications
import permissions as perm
import security
import sso
import theme
from auth import admin_required, editor_required
from i18n import _

admin_bp = Blueprint('admin', __name__)

TYPES = ('desk', 'room', 'booth', 'wall', 'zone', 'label')
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
EMAIL_RE = re.compile(r'^[^@\s<>"\']+@[^@\s<>"\']+\.[^@\s<>"\']+$')
BACKGROUND_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.svg', '.webp'}


def _email(txt):
    return (txt or '').strip().lower() or None


@admin_bp.route('/')
@editor_required
def index():
    return redirect(url_for('admin.floors'))


# ─────────────────────────────────────────────────────────────────────────────
# Accounts
# ─────────────────────────────────────────────────────────────────────────────

def ensure_account(email, first_name='', last_name='', role='user'):
    """The account with this address (existing or newly created). Returns (email, is_new) or (None, False)."""
    email = _email(email)
    if not email or not EMAIL_RE.match(email):
        return None, False
    u = db.query('SELECT email, first_name, last_name FROM users WHERE email = ?', (email,), one=True)
    first_name, last_name = (first_name or '').strip(), (last_name or '').strip()
    if u:
        if (first_name or last_name) and not (u['first_name'] or u['last_name']):
            db.execute('UPDATE users SET first_name = ?, last_name = ?, display_name = ? WHERE email = ?',
                       (first_name, last_name, f'{first_name} {last_name}'.strip(), email))
        return email, False
    db.execute("""INSERT INTO users(email, first_name, last_name, display_name, role, created)
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (email, first_name, last_name, f'{first_name} {last_name}'.strip() or email,
                role if role in perm.ROLES else 'user', db.now()))
    db.log(g.user['email'], 'account_created', email=email)
    return email, True


def _send_password_link(email, purpose='invite'):
    """Sends a link to set a password; without mail returns the link to pass on manually."""
    link = auth.password_link(email, hours=72 if purpose == 'invite' else 24)
    if notifications.configured():
        notifications.invitation(email, link, g.user['email'], purpose)
        return None
    return link


def _admins():
    return [r['email'] for r in db.query("SELECT email FROM users WHERE role = 'admin' AND active = 1")]


@admin_bp.route('/users')
@admin_required
def users():
    q = (request.args.get('q') or '').strip().lower()
    role = request.args.get('role') or ''
    state = request.args.get('state') or ''
    team_id = request.args.get('team', type=int)
    teams = perm.all_teams()
    membership = {}
    for t in teams:
        for e in perm.team_members(t):
            membership.setdefault(e, []).append(t)
    where, params = [], []
    if q:
        where.append('(LOWER(display_name) LIKE ? OR email LIKE ?)')
        params += [f'%{q}%'] * 2
    if role in perm.ROLES:
        where.append('role = ?')
        params.append(role)
    if state == 'blocked':
        where.append('active = 0')
    elif state == 'no_password':
        where.append('password_hash IS NULL')
    elif state == 'api_key':
        where.append('api_key_hash IS NOT NULL')
    if team_id:
        t = next((x for x in teams if x['id'] == team_id), None)
        emails = sorted(perm.team_members(t)) if t else []
        where.append(f"email IN ({','.join('?' * len(emails)) or 'NULL'})")
        params += emails
    sql = 'SELECT * FROM users' + (' WHERE ' + ' AND '.join(where) if where else '')
    people = []
    for r in db.query(sql + ' ORDER BY last_name, first_name, email LIMIT 1000', params):
        p = dict(r)
        p['teams'] = membership.get(p['email'], [])
        people.append(p)
    stats = db.query("""SELECT COUNT(*) AS total,
                               SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active,
                               SUM(CASE WHEN last_login IS NOT NULL THEN 1 ELSE 0 END) AS logged_in,
                               SUM(CASE WHEN api_key_hash IS NOT NULL THEN 1 ELSE 0 END) AS with_key
                        FROM users""", one=True)
    return render_template('admin_users.html', people=people, q=q, role=role, state=state, teams=teams,
                           team_id=team_id, stats=stats, mail=notifications.configured())


@admin_bp.route('/users/add', methods=['POST'])
@admin_required
def add_user():
    f = request.form
    first, last = (f.get('first_name') or '').strip(), (f.get('last_name') or '').strip()
    if not first or not last:
        flash(_('Enter the first and last name.'), 'error')
        return redirect(url_for('admin.users'))
    email, new = ensure_account(f.get('email'), first, last, f.get('role') or 'user')
    if not email:
        flash(_('Enter a valid e-mail address.'), 'error')
        return redirect(url_for('admin.users'))
    if not new:
        flash(_('The account {email} already exists.', email=email), 'error')
        return redirect(url_for('admin.user', email=email))
    if f.get('language') in i18n.LANGUAGES:
        db.execute('UPDATE users SET language = ? WHERE email = ?', (f['language'], email))
    password = f.get('password') or ''
    if password:
        problem = security.password_problem(password, email)
        if problem:
            flash(_('The account has been created, but the password was not set: {problem}', problem=problem), 'error')
        else:
            db.execute('UPDATE users SET password_hash = ?, must_change_password = ? WHERE email = ?',
                       (security.hash_password(password), 1 if f.get('force_change') else 0, email))
            flash(_('Account {email} created with a password.', email=email), 'ok')
    elif f.get('invite'):
        link = _send_password_link(email)
        flash(_('Account {email} created and an invitation e-mail sent.', email=email) if not link else
              _('Account {email} created. Mail is not configured - pass on the link to set a password: {link}',
                email=email, link=link), 'ok')
    else:
        flash(_('Account {email} created (login with SSO).', email=email) if sso.active() else
              _('Account {email} created - set a password or send an invitation.', email=email), 'ok')
    return redirect(url_for('admin.user', email=email))


@admin_bp.route('/users/<email>', methods=['GET', 'POST'])
@admin_required
def user(email):
    u = db.query('SELECT * FROM users WHERE email = ?', (email.lower(),), one=True)
    if not u:
        abort(404)
    email = u['email']
    if request.method == 'GET':
        teams = [t for t in perm.all_teams() if perm.in_team(email, t)]
        upcoming = db.query('SELECT COUNT(*) AS n FROM bookings WHERE email = ? AND date >= ?',
                            (email, date.today().isoformat()), one=True)['n']
        return render_template('admin_user.html', u=u, teams=teams, upcoming=upcoming,
                               mail=notifications.configured(), last_admin=_admins() == [email])
    f = request.form
    action = f.get('action') or 'details'
    me = g.user['email']
    only_admin = _admins() == [email]
    if action == 'details':
        first, last = (f.get('first_name') or '').strip(), (f.get('last_name') or '').strip()
        role = f.get('role') if f.get('role') in perm.ROLES else u['role']
        active = 1 if f.get('active') else 0
        language = f.get('language') if f.get('language') in i18n.LANGUAGES else None
        if only_admin and (role != 'admin' or not active):
            flash(_('This is the only administrator - give the role to someone else first.'), 'error')
        elif email == me and (role != 'admin' or not active):
            flash(_('You cannot take the administrator role away from yourself or block your own account.'), 'error')
        elif not first or not last:
            flash(_('Enter the first and last name.'), 'error')
        else:
            db.execute('UPDATE users SET first_name = ?, last_name = ?, display_name = ?, role = ?, active = ?, '
                       'language = ? WHERE email = ?', (first, last, f'{first} {last}', role, active, language, email))
            db.log(me, 'account_changed', email=email, role=role, active=active)
            flash(_('Saved.'), 'ok')
    elif action == 'password':
        password = f.get('password') or ''
        problem = security.password_problem(password, email)
        if problem:
            flash(problem, 'error')
        else:
            db.execute('UPDATE users SET password_hash = ?, must_change_password = ?, failed_logins = 0, '
                       'locked_until = NULL WHERE email = ?',
                       (security.hash_password(password), 1 if f.get('force_change') else 0, email))
            db.log(me, 'password_set_by_admin', email=email)
            flash(_('Password set.') + (' ' + _('The user will change it at the next login.') if f.get('force_change') else ''),
                  'ok')
    elif action == 'invite':
        link = _send_password_link(email, 'reset' if u['password_hash'] else 'invite')
        db.log(me, 'password_link', email=email)
        flash(_('An e-mail with a link to set the password has been sent.') if not link else
              _('Link to set the password (valid 72 h, pass it on to the person): {link}', link=link), 'ok')
    elif action == 'unlock':
        db.execute('UPDATE users SET failed_logins = 0, locked_until = NULL WHERE email = ?', (email,))
        flash(_('The lock after failed logins has been removed.'), 'ok')
    elif action == 'remove_password':
        db.execute('UPDATE users SET password_hash = NULL WHERE email = ?', (email,))
        db.log(me, 'password_removed', email=email)
        flash(_('Password removed - the account logs in only with SSO or after an invitation.'), 'ok')
    elif action == 'revoke_key':
        db.execute('UPDATE users SET api_key = NULL, api_key_hash = NULL, api_key_created = NULL WHERE email = ?', (email,))
        db.log(me, 'api_key_revoked', email=email)
        flash(_('API key revoked.'), 'ok')
    elif action == 'delete':
        if email == me or only_admin:
            flash(_('You cannot delete this account.'), 'error')
        else:
            today = date.today().isoformat()
            db.execute('DELETE FROM bookings WHERE email = ? AND date >= ?', (email, today))
            db.execute('DELETE FROM team_members WHERE email = ?', (email,))
            db.execute('UPDATE teams SET manager_email = NULL WHERE manager_email = ?', (email,))
            db.execute('UPDATE elements SET assigned_email = NULL WHERE assigned_email = ?', (email,))
            db.execute('DELETE FROM tokens WHERE email = ?', (email,))
            db.execute('DELETE FROM users WHERE email = ?', (email,))
            db.log(me, 'account_deleted', email=email)
            flash(_('Account {email} and its upcoming bookings deleted.', email=email), 'ok')
            return redirect(url_for('admin.users'))
    return redirect(url_for('admin.user', email=email))


@admin_bp.route('/api/account/<email>/password-link', methods=['POST'])
@editor_required
def api_password_link(email):
    """Quick reset from the user list / team members: an e-mail with a link to set the password.

    An editor only sends the link by mail - without mail the link would appear on the editor's
    screen and let them take over someone else's account. An administrator without mail gets
    the link to pass on.
    """
    u = db.query('SELECT email, password_hash, active FROM users WHERE email = ?', (email.lower(),), one=True)
    if not u:
        return {'error': _('No such account.')}, 404
    if not u['active']:
        return {'error': _('The account is blocked - unblock it first.')}, 400
    if not notifications.configured() and not perm.is_admin():
        return {'error': _('Mail is not configured - ask an administrator to set the password.')}, 403
    link = _send_password_link(u['email'], 'reset' if u['password_hash'] else 'invite')
    db.log(g.user['email'], 'password_link', email=u['email'])
    return {'ok': True, 'sent': link is None, 'link': link, 'email': u['email']}


@admin_bp.route('/api/account/<email>/password', methods=['POST'])
@admin_required
def api_set_password(email):
    """An administrator sets a temporary password (to be changed at the first login)."""
    u = db.query('SELECT email FROM users WHERE email = ?', (email.lower(),), one=True)
    if not u:
        return {'error': _('No such account.')}, 404
    password = (request.get_json(silent=True) or {}).get('password') or ''
    problem = security.password_problem(password, u['email'])
    if problem:
        return {'error': problem}, 400
    db.execute('UPDATE users SET password_hash = ?, must_change_password = 1, failed_logins = 0, '
               'locked_until = NULL WHERE email = ?', (security.hash_password(password), u['email']))
    db.log(g.user['email'], 'password_set_by_admin', email=u['email'])
    return {'ok': True}


# ─────────────────────────────────────────────────────────────────────────────
# Teams
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/teams')
@editor_required
def teams():
    items = perm.all_teams()
    places = {}
    for team_ids in perm.element_teams().values():
        for tid in team_ids:
            places[tid] = places.get(tid, 0) + 1
    names = {r['email']: r['display_name'] for r in db.query('SELECT email, display_name FROM users '
                                                             'WHERE email IN (SELECT manager_email FROM teams)')}
    for t in items:
        t['count'] = len(perm.team_members(t))
        t['places'] = places.get(t['id'], 0)
        t['manager_name'] = names.get(t.get('manager_email')) or t.get('manager_email')
    return render_template('admin_teams.html', teams=items)


def _save_team(team_id=None):
    f = request.form
    name = (f.get('name') or '').strip()
    color = f.get('color') if COLOR_RE.match(f.get('color') or '') else '#2563eb'
    if not name:
        flash(_('A team must have a name.'), 'error')
        return None
    manager = _email(f.get('manager_email'))
    if manager and not db.query('SELECT 1 FROM users WHERE email = ?', (manager,), one=True):
        flash(_('There is no account {email} - add the person before making them a manager.', email=manager), 'error')
        manager = None
    data = (name, f.get('description'), color, manager)
    try:
        if team_id:
            db.execute('UPDATE teams SET name=?, description=?, color=?, manager_email=? WHERE id=?', (*data, team_id))
        else:
            team_id = db.insert('INSERT INTO teams(name, description, color, manager_email, created) '
                                'VALUES (?,?,?,?,?)', (*data, db.now()))
    except db.integrity_errors():
        flash(_('A team with this name already exists.'), 'error')
        return None
    # the manager is a member straight away - visible on the list and removable like anyone else
    if manager:
        _add_members(team_id, [manager])
    db.log(g.user['email'], 'team_saved', id=team_id, name=name)
    return team_id


def _add_members(team_id, emails):
    already = {r['email'] for r in db.query('SELECT email FROM team_members WHERE team_id = ?', (team_id,))}
    added = [e for e in dict.fromkeys(emails) if e not in already]
    for e in added:
        db.execute('INSERT INTO team_members(team_id, email) VALUES (?, ?)', (team_id, e))
    return added


@admin_bp.route('/teams/new', methods=['POST'])
@editor_required
def new_team():
    tid = _save_team()
    return redirect(url_for('admin.team', team_id=tid) if tid else url_for('admin.teams'))


@admin_bp.route('/teams/<int:team_id>', methods=['GET', 'POST'])
@editor_required
def team(team_id):
    if request.method == 'POST':
        if _save_team(team_id):
            flash(_('Team saved.'), 'ok')
        return redirect(url_for('admin.team', team_id=team_id))
    t = next((x for x in perm.all_teams() if x['id'] == team_id), None)
    if not t:
        abort(404)
    emails = sorted(perm.team_members(t))
    members = []
    if emails:
        ph = ','.join('?' * len(emails))
        known = {r['email']: dict(r) for r in db.query(f'SELECT * FROM users WHERE email IN ({ph})', emails)}
        members = [known.get(e) or {'email': e, 'display_name': e, 'active': 0} for e in emails]
    members.sort(key=lambda o: ((o.get('last_name') or o.get('display_name') or '').lower(),
                                (o.get('first_name') or '').lower()))
    effective = perm.element_teams(with_source=True)
    ids = [i for i, (tids, _zone) in effective.items() if team_id in tids]
    places = []
    if ids:
        ph = ','.join('?' * len(ids))
        for r in db.query(f"""SELECT e.id, e.code, e.label, e.type, f.name AS floor, f.id AS floor_id
                             FROM elements e JOIN floors f ON f.id = e.floor_id WHERE e.id IN ({ph})
                             ORDER BY f.name, e.code""", ids):
            places.append({**dict(r), 'name': booking.place_name(r), 'zone': effective[r['id']][1]})
    manager = db.query('SELECT email, display_name FROM users WHERE email = ?', (t.get('manager_email'),),
                       one=True) if t.get('manager_email') else None
    return render_template('admin_team.html', t=t, members=members, places=places, manager=manager,
                           mail=notifications.configured())


def _people_from_text(text):
    """A pasted list → [(email, first, last)].

    A line: "email; First; Last", "First Last <email>" (from Outlook) or just an e-mail.
    Several addresses on one line (separated by commas / semicolons) work too.
    """
    out, invalid = [], []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        fields = [p.strip().strip('"\'') for p in re.split(r'[;\t]', line)]
        if len(fields) >= 3 and EMAIL_RE.match(fields[0].lower()):
            out.append((fields[0].lower(), fields[1], fields[2]))
            continue
        for chunk in re.split(r'[,;]', line):
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.match(r'^"?([^"<]*?)"?\s*<([^>]+)>$', chunk)
            if m:
                full_name, email = m.group(1).strip(), m.group(2).strip().lower()
                parts = full_name.split()
                first, last = (parts[0], ' '.join(parts[1:])) if parts else ('', '')
            else:
                email, first, last = chunk.strip('<>"\'').lower(), '', ''
            (out.append((email, first, last)) if EMAIL_RE.match(email) else invalid.append(chunk))
    return out, invalid


@admin_bp.route('/teams/<int:team_id>/members', methods=['POST'])
@editor_required
def team_members(team_id):
    """Adding members (a person: e-mail + first + last name, or a list) and removing them."""
    if not db.query('SELECT 1 FROM teams WHERE id = ?', (team_id,), one=True):
        abort(404)
    f = request.form
    action = f.get('action') or 'person'
    if action == 'remove':
        emails = [_email(e) for e in f.getlist('email') if _email(e)]
        for e in emails:
            db.execute('DELETE FROM team_members WHERE team_id = ? AND email = ?', (team_id, e))
        db.log(g.user['email'], 'members_removed', team=team_id, emails=emails)
        flash(_('Removed from the team: {n}.', n=len(emails)), 'ok')
        return redirect(url_for('admin.team', team_id=team_id))

    if action == 'list':
        people, invalid = _people_from_text(f.get('list'))
    else:
        people, invalid = [(_email(f.get('email')) or '', f.get('first_name') or '', f.get('last_name') or '')], []
    new_emails, new_accounts, without_name = [], [], []
    for email, first, last in people:
        exists = db.query('SELECT 1 FROM users WHERE email = ?', (email,), one=True)
        if not exists and not (first.strip() and last.strip()):
            without_name.append(email)       # a new account needs a first and last name
            continue
        e, new = ensure_account(email, first, last)
        if not e:
            invalid.append(email)
            continue
        new_emails.append(e)
        if new:
            new_accounts.append(e)
    added = _add_members(team_id, new_emails)
    links = []
    if new_accounts and f.get('invite'):
        for e in new_accounts:
            link = _send_password_link(e)
            if link:
                links.append(f'{e}: {link}')
    db.log(g.user['email'], 'members_added', team=team_id, count=len(added), new_accounts=new_accounts)
    message = _('Added to the team: {n}', n=len(added))
    if new_accounts:
        message += ' ' + _('(new accounts: {n})', n=len(new_accounts))
    if len(new_emails) > len(added):
        message += '; ' + _('{n} were already in the team', n=len(new_emails) - len(added))
    flash(message + '.', 'ok' if added else 'error')
    if without_name:
        flash(_('There are no accounts yet for: {emails} - enter the first and last name to create them.',
                emails=', '.join(without_name)), 'error')
    if invalid:
        flash(_('Invalid addresses (skipped): {items}.', items=', '.join(invalid)), 'error')
    if links:
        flash(_('Mail is not configured - pass on the links to set a password: {links}', links=' · '.join(links)), 'ok')
    return redirect(url_for('admin.team', team_id=team_id))


@admin_bp.route('/teams/<int:team_id>/delete', methods=['POST'])
@editor_required
def delete_team(team_id):
    db.execute('DELETE FROM element_teams WHERE team_id = ?', (team_id,))
    db.execute('DELETE FROM team_members WHERE team_id = ?', (team_id,))
    db.execute('DELETE FROM teams WHERE id = ?', (team_id,))
    db.log(g.user['email'], 'team_deleted', id=team_id)
    flash(_('Team deleted. Its places are now open to everyone (unless they had other teams).'), 'ok')
    return redirect(url_for('admin.teams'))


# ─────────────────────────────────────────────────────────────────────────────
# Floors
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/floors')
@editor_required
def floors():
    today = date.today().isoformat()
    items = db.query("""SELECT f.*,
            (SELECT COUNT(*) FROM elements e WHERE e.floor_id = f.id AND e.type = 'desk') AS desks,
            (SELECT COUNT(*) FROM elements e WHERE e.floor_id = f.id AND e.type = 'desk'
                AND e.assigned_email IS NOT NULL) AS fixed,
            (SELECT COUNT(*) FROM bookings b JOIN elements e ON e.id = b.element_id
                WHERE e.floor_id = f.id AND b.date = ?) AS today
        FROM floors f ORDER BY f.sort_order, f.name""", (today,))
    return render_template('admin_floors.html', floors=items)


@admin_bp.route('/floors/save', methods=['POST'])
@editor_required
def save_floor():
    f = request.form
    fid = f.get('id', type=int)
    data = ((f.get('name') or _('New floor')).strip(), f.get('location'),
            max(400, min(6000, f.get('width', 1600, type=int))),
            max(300, min(6000, f.get('height', 1000, type=int))),
            max(5, min(100, f.get('grid', 20, type=int))),
            f.get('sort_order', 0, type=int), 1 if f.get('active') else 0)
    if fid:
        db.execute("""UPDATE floors SET name=?, location=?, width=?, height=?, grid=?,
                      sort_order=?, active=? WHERE id=?""", (*data, fid))
    else:
        fid = db.insert("""INSERT INTO floors(name, location, width, height, grid, sort_order, active)
                           VALUES (?,?,?,?,?,?,?)""", data)
    db.log(g.user['email'], 'floor_saved', id=fid, name=data[0])
    if request.form.get('to_editor'):
        return redirect(url_for('admin.editor', floor_id=fid))
    flash(_('Floor saved.'), 'ok')
    return redirect(url_for('admin.floors'))


@admin_bp.route('/floors/<int:floor_id>/delete', methods=['POST'])
@admin_required
def delete_floor(floor_id):
    db.execute('DELETE FROM floors WHERE id = ?', (floor_id,))
    db.log(g.user['email'], 'floor_deleted', id=floor_id)
    flash(_('Floor deleted with its places and bookings.'), 'ok')
    return redirect(url_for('admin.floors'))


@admin_bp.route('/floors/<int:floor_id>/background', methods=['POST'])
@editor_required
def background(floor_id):
    f = db.query('SELECT * FROM floors WHERE id = ?', (floor_id,), one=True)
    if not f:
        abort(404)
    if request.form.get('remove'):
        db.execute('UPDATE floors SET background = NULL WHERE id = ?', (floor_id,))
    elif request.files.get('file'):
        upload = request.files['file']
        ext = os.path.splitext(upload.filename or '')[1].lower()
        if ext not in BACKGROUND_EXTENSIONS:
            return {'error': _('Allowed: PNG, JPG, SVG, WEBP.')}, 400
        os.makedirs(config.UPLOAD_DIR, exist_ok=True)
        name = f'floor{floor_id}_{secrets.token_hex(4)}{ext}'
        upload.save(os.path.join(config.UPLOAD_DIR, name))
        db.execute('UPDATE floors SET background = ? WHERE id = ?', (name, floor_id))
    opacity = request.form.get('opacity', type=float)
    if opacity is not None:
        db.execute('UPDATE floors SET background_opacity = ? WHERE id = ?', (max(0, min(1, opacity)), floor_id))
    f = db.query('SELECT background, background_opacity FROM floors WHERE id = ?', (floor_id,), one=True)
    return {'background': booking.background_url(f), 'opacity': f['background_opacity']}


@admin_bp.route('/floors/<int:floor_id>/editor')
@editor_required
def editor(floor_id):
    f = db.query('SELECT * FROM floors WHERE id = ?', (floor_id,), one=True)
    if not f:
        abort(404)
    teams = [{'id': t['id'], 'name': t['name'], 'color': t['color']} for t in perm.all_teams()]
    return render_template('editor.html', f=f, teams=teams,
                           floors=db.query('SELECT id, name FROM floors ORDER BY sort_order, name'))


@admin_bp.route('/api/floors/<int:floor_id>/elements')
@editor_required
def api_elements(floor_id):
    f = db.query('SELECT * FROM floors WHERE id = ?', (floor_id,), one=True)
    if not f:
        abort(404)
    teams = perm.element_teams(floor_id)
    names = {r['email']: r['display_name'] for r in db.query(
        """SELECT u.email, u.display_name FROM elements e JOIN users u ON u.email = e.assigned_email
           WHERE e.floor_id = ?""", (floor_id,))}
    out = []
    for e in db.query('SELECT * FROM elements WHERE floor_id = ? ORDER BY id', (floor_id,)):
        d = dict(e)
        d['teams'] = sorted(teams.get(e['id'], set()))
        d['assigned_name'] = names.get(e['assigned_email'])
        out.append(d)
    return {'floor': dict(f), 'elements': out, 'background': booking.background_url(f)}


def _number(v, default=0.0):
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return default


def _lost_bookings(where, params):
    return [{'email': r['email'], 'element': {'type': r['type'], 'code': r['code'], 'label': r['label']},
             'date': r['date'], 'time_from': r['time_from'], 'time_to': r['time_to']}
            for r in db.query(f"""SELECT b.email, b.date, b.time_from, b.time_to, e.type, e.code, e.label
                                  FROM bookings b JOIN elements e ON e.id = b.element_id WHERE {where}""", params)]


@admin_bp.route('/api/floors/<int:floor_id>/elements', methods=['POST'])
@editor_required
def api_save_elements(floor_id):
    """Save the whole plan: existing id -> UPDATE, new -> INSERT, missing -> DELETE.

    Deleting places with upcoming bookings needs a confirmation (force=true).
    """
    if not db.query('SELECT 1 FROM floors WHERE id = ?', (floor_id,), one=True):
        abort(404)
    data = request.get_json(force=True) or {}
    elements = data.get('elements') or []
    existing = {r['id'] for r in db.query('SELECT id FROM elements WHERE floor_id = ?', (floor_id,))}
    kept = {e.get('id') for e in elements if isinstance(e.get('id'), int)} & existing
    removed = existing - kept
    today = date.today().isoformat()

    if removed and not data.get('force'):
        ph = ','.join('?' * len(removed))
        conflicts = db.query(f"""SELECT e.code, COUNT(*) AS n FROM bookings b JOIN elements e ON e.id = b.element_id
                                 WHERE b.element_id IN ({ph}) AND b.date >= ? GROUP BY e.code""", (*removed, today))
        if conflicts:
            return {'confirm': [dict(c) for c in conflicts]}, 409

    valid_teams = {t['id'] for t in perm.all_teams()}
    codes = [((e.get('code') or '').strip()) for e in elements if e.get('type') == 'desk']
    duplicates = sorted({c for c in codes if c and codes.count(c) > 1})
    if duplicates:
        return {'error': _('Duplicate desk numbers: {codes}', codes=', '.join(duplicates))}, 400

    # whom the changes affect: bookings of removed places and of desks assigned permanently to someone
    lost = []
    if removed:
        ph = ','.join('?' * len(removed))
        lost += _lost_bookings(f'b.element_id IN ({ph}) AND b.date >= ?', (*removed, today))
    for e in elements:
        assigned = _email(e.get('assigned_email')) if e.get('type') == 'desk' else None
        if assigned and e.get('id') in kept:
            lost += _lost_bookings('b.element_id = ? AND b.email <> ? AND b.date >= ?', (e['id'], assigned, today))

    conn = db.get_db()
    id_map = {}
    try:
        for i in removed:
            conn.execute('DELETE FROM element_teams WHERE element_id = ?', (i,))
            conn.execute('DELETE FROM bookings WHERE element_id = ?', (i,))
            conn.execute('DELETE FROM elements WHERE id = ?', (i,))
        for e in elements:
            kind = e.get('type')
            if kind not in TYPES:
                continue
            color = e.get('color') if COLOR_RE.match(e.get('color') or '') else None
            assigned = _email(e.get('assigned_email')) if kind == 'desk' else None
            capacity = e.get('capacity') if kind in ('room', 'booth') else None
            try:
                capacity = int(capacity) if capacity not in (None, '') else None
            except (TypeError, ValueError):
                capacity = None
            values = (kind, (e.get('code') or '').strip() or None, (e.get('label') or '').strip() or None,
                      _number(e.get('x')), _number(e.get('y')),
                      max(2, _number(e.get('w'), 60)), max(2, _number(e.get('h'), 40)),
                      _number(e.get('rotation')) % 360, color,
                      1 if e.get('bookable', True) else 0, assigned,
                      (e.get('equipment') or '').strip() or None, capacity)
            if e.get('id') in kept:
                conn.execute("""UPDATE elements SET type=?, code=?, label=?, x=?, y=?, w=?, h=?, rotation=?,
                                color=?, bookable=?, assigned_email=?, equipment=?, capacity=? WHERE id=?""",
                             (*values, e['id']))
                eid = e['id']
            else:
                eid = conn.insert("""INSERT INTO elements(type, code, label, x, y, w, h, rotation, color,
                                     bookable, assigned_email, equipment, capacity, floor_id)
                                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*values, floor_id))
            id_map[str(e.get('id'))] = eid
            conn.execute('DELETE FROM element_teams WHERE element_id = ?', (eid,))
            if kind in ('desk', 'room', 'booth', 'zone'):
                for tid in set(e.get('teams') or []):
                    if tid in valid_teams:
                        conn.execute('INSERT INTO element_teams(element_id, team_id) VALUES (?, ?)', (eid, tid))
            # A permanently assigned desk cannot have bookings of other people.
            if assigned:
                conn.execute('DELETE FROM bookings WHERE element_id = ? AND email <> ? AND date >= ?',
                             (eid, assigned, today))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    db.log(g.user['email'], 'plan_saved', floor=floor_id, elements=len(elements), removed=len(removed))
    notifications.cancellation(g.user['email'], lost)
    return {'ok': True, 'map': id_map}


# ─────────────────────────────────────────────────────────────────────────────
# All bookings
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/bookings')
@admin_required
def bookings():
    """All bookings with filters, bulk cancellation and CSV export."""
    f = request.args
    today = date.today()
    start = f.get('from') or today.isoformat()
    end = f.get('to') or (today + timedelta(days=30)).isoformat()
    LIMIT = 2000
    rows, truncated = booking.all_bookings(start, end, f.get('floor', type=int), f.get('type'),
                                           f.get('team', type=int), f.get('q'), LIMIT)
    if f.get('csv'):
        out = io.StringIO()
        w = csv.writer(out, delimiter=';')
        w.writerow([_('Date'), _('Hours'), _('Place'), _('Floor'), _('Person'), _('E-mail'), _('Subject'),
                    _('Booked by'), _('Created')])
        for x in rows:
            w.writerow([x['date'], x['hours'], x['place'], x['floor'], x['person'] or '', x['email'],
                        x['title'] or '', x['created_by_name'] or x['created_by'], x['created']])
        # BOM, so that Excel shows national characters correctly
        return Response('﻿' + out.getvalue(), mimetype='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename=bookings_{start}_{end}.csv'})
    summary = {
        'total': len(rows),
        'people': len({x['email'] for x in rows}),
        'desks': sum(1 for x in rows if x['type'] == 'desk'),
        'rooms': sum(1 for x in rows if x['type'] != 'desk'),
    }
    return render_template('admin_bookings.html', rows=rows, truncated=truncated, limit=LIMIT, f=f, start=start,
                           end=end, floors=db.query('SELECT id, name FROM floors ORDER BY sort_order, name'),
                           teams=perm.all_teams(), summary=summary, today=today.isoformat(),
                           month_ago=(today - timedelta(days=30)).isoformat())


# ─────────────────────────────────────────────────────────────────────────────
# Audit log and occupancy
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/log')
@admin_required
def audit_log():
    q = (request.args.get('q') or '').strip().lower()
    params = []
    sql = 'SELECT a.*, u.display_name FROM audit_log a LEFT JOIN users u ON u.email = a.actor'
    if q:
        sql += ' WHERE LOWER(a.actor) LIKE ? OR a.action LIKE ? OR LOWER(a.details) LIKE ?'
        params = [f'%{q}%'] * 3
    sql += ' ORDER BY a.id DESC LIMIT 500'
    entries = []
    for r in db.query(sql, params):
        d = dict(r)
        try:
            d['details'] = json.loads(d['details']) if d['details'] else {}
        except ValueError:
            d['details'] = {'text': d['details']}
        entries.append(d)
    today = date.today()
    occupancy = db.query("""SELECT b.date, COUNT(*) AS n FROM bookings b WHERE b.date BETWEEN ? AND ?
                            GROUP BY b.date ORDER BY b.date""",
                         ((today - timedelta(days=30)).isoformat(), (today + timedelta(days=14)).isoformat()))
    desks = db.query("SELECT COUNT(*) AS n FROM elements WHERE type = 'desk' AND bookable = 1 "
                     "AND assigned_email IS NULL", one=True)['n']
    return render_template('admin_log.html', entries=entries, q=q, occupancy=occupancy, desks=desks,
                           today=today.isoformat())


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/settings')
@admin_required
def settings():
    cfg_db = db.configuration()
    return render_template(
        'admin_settings.html', tab=request.args.get('tab') or 'database',
        database=cfg_db, database_text=db.describe(cfg_db), DB_TYPES=db.DB_TYPES, TLS_MODES=db.TLS_MODES,
        DEFAULT_PORTS=db.DEFAULT_PORTS,
        general=app_settings.get('general'), login=app_settings.get('login'),
        sso_cfg={p: app_settings.get('sso_' + p) for p in sso.PROVIDERS},
        sso_redirect={p: sso.redirect_uri(p) for p in sso.PROVIDERS}, PROVIDERS=sso.PROVIDERS,
        mail=app_settings.get('mail'), api=app_settings.get('api'),
        colors=theme.colors(), PRESETS=theme.PRESETS,
        key_from_env=bool(config.ENCRYPTION_KEY))


def _database_from_form():
    f = request.form
    kind = f.get('type')
    if kind not in db.DB_TYPES:
        abort(400, _('Unknown database type.'))
    if kind == 'sqlite':
        return {'type': 'sqlite', 'path': (f.get('path') or '').strip() or config.DB_PATH}
    cfg = {'type': kind, 'host': (f.get('host') or '').strip() or 'localhost',
           'port': f.get('port', type=int) or db.DEFAULT_PORTS[kind],
           'database': (f.get('database') or '').strip(), 'user': (f.get('user') or '').strip(),
           'password': f.get('password') or '', 'tls': f.get('tls') if f.get('tls') in db.TLS_MODES else 'off',
           'ca': (f.get('ca') or '').strip()}
    if not cfg['password']:
        # empty field = the same password as in the current configuration (if it is the same server)
        current = db.configuration()
        if all(current.get(k) == cfg[k] for k in ('type', 'host', 'user')):
            cfg['password'] = current.get('password') or ''
    if not cfg['database'] or not cfg['user']:
        abort(400, _('Enter the database name and the user.'))
    return cfg


@admin_bp.route('/settings/database', methods=['POST'])
@admin_required
def settings_database():
    cfg = _database_from_form()
    action = request.form.get('action')
    ok, version, tables = db.check_connection(cfg)
    back = redirect(url_for('admin.settings', tab='database'))
    if not ok:
        flash(_('Could not connect: {error}', error=version), 'error')
        return back
    if action == 'test':
        text = _('The connection works ({version}).', version=version)
        if tables:
            text += ' ' + _('Application tables: {tables}.', tables=', '.join(f'{t}: {n}' for t, n in tables.items()))
        else:
            text += ' ' + _('The database is empty - the schema will be created when switching.')
        flash(text, 'ok')
        return back

    current = db.configuration()
    if db.describe(current) == db.describe(cfg) and current.get('type') == cfg['type']:
        db.save_configuration(cfg)
        flash(_('Connection settings saved.'), 'ok')
        return back
    try:
        if request.form.get('data') == 'copy':
            result = db.copy_data(current, cfg, overwrite=bool(request.form.get('overwrite')))
            text = _('Data copied: {tables}.', tables=', '.join(f'{t}: {n}' for t, n in result.items() if n))
        else:
            db.init_db(cfg)
            conn = db.connect(cfg)
            try:
                me = conn.execute("SELECT 1 FROM users WHERE email = ? AND role = 'admin' AND active = 1",
                                  (g.user['email'],)).fetchone()
            finally:
                conn.close()
            if not me:
                flash(_('Your administrator account does not exist in the chosen database - after switching nobody '
                        'could log in. Choose "copy data" or add the account in that database.'), 'error')
                return back
            text = _('The data already in the chosen database is used.')
    except db.DatabaseError as e:
        flash(_('The target database is not empty ({tables}). Tick "delete its data" to overwrite it.', tables=e), 'error')
        return back
    except Exception as e:  # noqa: BLE001 - the driver message goes to the administrator
        flash(_('Could not switch the database: {error}', error=e), 'error')
        return back
    db.log(g.user['email'], 'database_changed', old=db.describe(current), new=db.describe(cfg))
    db.save_configuration(cfg)
    conn = db.connect()
    try:
        conn.execute('INSERT INTO audit_log(at, actor, action, details) VALUES (?, ?, ?, ?)',
                     (db.now(), g.user['email'], 'database_changed',
                      json.dumps({'old': db.describe(current)}, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    flash(_('The application now uses the database {database}.', database=db.describe(cfg)) + ' ' + text, 'ok')
    return back


@admin_bp.route('/settings/general', methods=['POST'])
@admin_required
def settings_general():
    language = request.form.get('default_language')
    if language not in (*i18n.LANGUAGES, 'auto'):
        abort(400)
    app_settings.save('general', {'default_language': language})
    db.log(g.user['email'], 'settings_general', default_language=language)
    flash(_('Saved.'), 'ok')
    return redirect(url_for('admin.settings', tab='login'))


@admin_bp.route('/settings/theme', methods=['POST'])
@admin_required
def settings_theme():
    f = request.form
    if f.get('preset') in theme.PRESETS:
        chosen = {k: theme.PRESETS[f['preset']][k] for k in ('header', 'main', 'accent')}
    else:
        chosen = {k: (f.get(k) or '').strip().lower() for k in ('header', 'main', 'accent')}
    problem = theme.problem(**chosen)
    if problem:
        flash(problem, 'error')
    else:
        app_settings.save('theme', chosen)
        db.log(g.user['email'], 'settings_theme', **chosen)
        flash(_('Colours saved.'), 'ok')
    return redirect(url_for('admin.settings', tab='appearance'))


@admin_bp.route('/settings/login', methods=['POST'])
@admin_required
def settings_login():
    f = request.form
    changes = {'password_login': bool(f.get('password_login')), 'password_admins_only': bool(f.get('password_admins_only'))}
    app_settings.save('login', changes)
    db.log(g.user['email'], 'settings_login', **changes)
    flash(_('Login settings saved.'), 'ok')
    return redirect(url_for('admin.settings', tab='login'))


@admin_bp.route('/settings/sso/<provider>', methods=['POST'])
@admin_required
def settings_sso(provider):
    if provider not in sso.PROVIDERS:
        abort(404)
    f = request.form
    if f.get('action') == 'test':
        try:
            flash(sso.check_provider(provider), 'ok')
        except sso.SSOError as e:
            flash(str(e), 'error')
        return redirect(url_for('admin.settings', tab='login'))
    changes = {k: (f.get(k) or '').strip() for k in ('client_id', 'tenant', 'redirect_uri', 'domains',
                                                     'name', 'discovery_url', 'scope')}
    changes['client_secret'] = None if f.get('remove_secret') else (f.get('client_secret') or '').strip()
    changes['enabled'] = bool(f.get('enabled'))
    changes['auto_create'] = bool(f.get('auto_create'))
    app_settings.save('sso_' + provider, changes)
    db.log(g.user['email'], 'settings_sso', provider=provider, enabled=changes['enabled'])
    cfg = app_settings.get('sso_' + provider)
    if changes['enabled'] and not sso.is_complete(provider, cfg):
        flash(_('{provider}: saved, but the button appears only after the client id and secret are filled in.',
                provider=_(sso.PROVIDERS[provider])), 'error')
    else:
        flash(_('Saved: {provider}.', provider=_(sso.PROVIDERS[provider])), 'ok')
    return redirect(url_for('admin.settings', tab='login'))


@admin_bp.route('/settings/mail', methods=['POST'])
@admin_required
def settings_mail():
    f = request.form
    changes = {k: (f.get(k) or '').strip() for k in ('host', 'login', 'sender', 'sender_name')}
    changes['port'] = f.get('port', type=int) or 587
    changes['mode'] = f.get('mode') if f.get('mode') in ('starttls', 'ssl', 'none') else 'starttls'
    changes['password'] = None if f.get('remove_password') else (f.get('password') or '')
    changes['enabled'] = bool(f.get('enabled'))
    app_settings.save('mail', changes)
    db.log(g.user['email'], 'settings_mail', host=changes['host'], enabled=changes['enabled'])
    if f.get('action') == 'test':
        error = notifications.send([g.user['email']], _('Mail test - desk booking'),
                                   '<p>' + _('Mail works. This message confirms the SMTP settings.') + '</p>')
        flash(_('A test message has been sent to {email}.', email=g.user['email']) if not error
              else _('Sending does not work: {error}', error=error), 'ok' if not error else 'error')
    else:
        flash(_('Mail settings saved.'), 'ok')
    return redirect(url_for('admin.settings', tab='mail'))


@admin_bp.route('/settings/api', methods=['POST'])
@admin_required
def settings_api():
    f = request.form
    changes = {'api': bool(f.get('api')), 'mcp': bool(f.get('mcp')),
               'rate_limit': max(0, min(10000, f.get('rate_limit', 120, type=int)))}
    app_settings.save('api', changes)
    if f.get('revoke_all'):
        db.execute('UPDATE users SET api_key = NULL, api_key_hash = NULL, api_key_created = NULL')
        flash(_('All API keys have been revoked.'), 'ok')
    db.log(g.user['email'], 'settings_api', **changes, revoked=bool(f.get('revoke_all')))
    flash(_('API and MCP settings saved.'), 'ok')
    return redirect(url_for('admin.settings', tab='api'))
