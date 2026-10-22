"""My account: details, language, password, API key and connecting an AI assistant (MCP).

Every user - including the administrator - has their own API key with the permissions of their
account. The key is stored encrypted (AES-GCM) and as a SHA-256 hash for the lookup by header.
Showing, regenerating or deleting an existing key requires the password (an account without
a password: a login within the last 15 minutes).
"""
import time

from flask import Blueprint, flash, g, make_response, redirect, render_template, request, session, url_for

import app_settings
import config
import db
import i18n
import security
from auth import login_required
from i18n import _

account_bp = Blueprint('account', __name__)

FRESH_LOGIN = 15 * 60


def _confirmed(password):
    """Has the person confirmed who they are: the password, or a fresh login (account without a password)."""
    if g.user['password_hash']:
        return security.check_password(g.user['password_hash'], password or '')
    return time.time() - (session.get('logged_in_at') or 0) < FRESH_LOGIN


@account_bp.route('/')
@login_required
def index():
    return _page()


def _page(show_key=None, key_error=None, status=200):
    # The key only goes into the body of this one response - never into the session cookie.
    resp = make_response(render_template('account.html', api=app_settings.get('api'), show_key=show_key,
                                         key_error=key_error, mcp_url=config.APP_URL + '/mcp',
                                         api_url=config.APP_URL + '/api/v1'), status)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@account_bp.route('/details', methods=['POST'])
@login_required
def details():
    first = (request.form.get('first_name') or '').strip()
    last = (request.form.get('last_name') or '').strip()
    language = request.form.get('language')
    if not first or not last:
        flash(_('Enter the first and last name.'), 'error')
    else:
        db.execute('UPDATE users SET first_name = ?, last_name = ?, display_name = ?, language = ? WHERE email = ?',
                   (first, last, f'{first} {last}', language if language in i18n.LANGUAGES else None, g.user['email']))
        if language in i18n.LANGUAGES:
            g.lang = language
        flash(_('Saved.'), 'ok')
    return redirect(url_for('account.index'))


@account_bp.route('/password', methods=['GET', 'POST'])
@login_required
def password():
    u = g.user
    if request.method == 'GET':
        return render_template('change_password.html', forced=bool(u['must_change_password']))
    f = request.form
    if u['password_hash'] and not security.check_password(u['password_hash'], f.get('current')):
        problem = _('The current password is wrong.')
    elif f.get('password') != f.get('password2'):
        problem = _('The new passwords do not match.')
    elif u['password_hash'] and security.check_password(u['password_hash'], f.get('password')):
        problem = _('The new password must be different from the current one.')
    else:
        problem = security.password_problem(f.get('password'), u['email'])
    if problem:
        return render_template('change_password.html', forced=bool(u['must_change_password']), error=problem), 400
    db.execute('UPDATE users SET password_hash = ?, must_change_password = 0 WHERE email = ?',
               (security.hash_password(f['password']), u['email']))
    db.log(u['email'], 'password_changed')
    flash(_('Password changed.'), 'ok')
    return redirect(url_for('account.index') if not u['must_change_password'] else url_for('web.plan'))


@account_bp.route('/key', methods=['GET', 'POST'])
@login_required
def key():
    """action: new | show | delete. From the page via fetch (X-Requested-With: fetch) it answers with JSON,
    without JavaScript - with a page. When a key exists, every action requires the password."""
    if request.method == 'GET':                 # e.g. coming back from the browser history
        return redirect(url_for('account.index') + '#api')
    by_fetch = request.headers.get('X-Requested-With') == 'fetch'
    action = request.form.get('action')
    if action not in ('new', 'show', 'delete'):
        return ({'error': _('Unknown action.')}, 400) if by_fetch else redirect(url_for('account.index'))
    email = g.user['email']
    if g.user['api_key'] and not _confirmed(request.form.get('password')):
        if g.user['password_hash']:
            problem = _('Wrong password.') if request.form.get('password') else _('Enter the password of your account.')
        else:
            problem = _('Log out and log in again - a login within the last 15 minutes is the confirmation.')
        return ({'error': problem}, 403) if by_fetch else _page(key_error=problem, status=403)
    if action == 'new':
        new, created = security.new_api_key(), db.now()
        db.execute('UPDATE users SET api_key = ?, api_key_hash = ?, api_key_created = ?, api_key_used = NULL '
                   'WHERE email = ?', (security.encrypt(new), security.token_hash(new), created, email))
        db.log(email, 'api_key_created')
        if by_fetch:
            return {'ok': True, 'key': new, 'created': created}
        flash(_('A new API key has been generated. The previous one (if any) no longer works.'), 'ok')
        g.user = dict(db.query('SELECT * FROM users WHERE email = ?', (email,), one=True))
        return _page(new)
    if action == 'show':
        if not g.user['api_key']:
            return ({'error': _('You have no key yet.')}, 404) if by_fetch else redirect(url_for('account.index'))
        plain = security.decrypt(g.user['api_key'])
        return {'ok': True, 'key': plain} if by_fetch else _page(plain)
    db.execute('UPDATE users SET api_key = NULL, api_key_hash = NULL, api_key_created = NULL WHERE email = ?', (email,))
    db.log(email, 'api_key_deleted')
    if by_fetch:
        return {'ok': True}
    flash(_('API key deleted.'), 'ok')
    return redirect(url_for('account.index') + '#api')
