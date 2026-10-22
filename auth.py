"""Login: e-mail and password, single sign-on (OpenID Connect - sso.py) and API keys.

  * Password - scrypt hash; after 5 failed attempts the account is locked for 15 minutes,
    and one IP address can try at most 20 times per 10 minutes.
  * SSO - buttons of the providers enabled in the settings. A new account from SSO is created
    only when an administrator enabled "create accounts automatically".
  * First start (no administrator) - the /setup wizard protected by a one-time code printed
    to the server log.
  * API key (header X-API-Key or Authorization: Bearer) - every user, including the administrator,
    has their own key with their own permissions (routes/account.py).
"""
import functools
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, g, make_response, redirect, render_template, request, session, url_for)

import app_settings
import config
import db
import i18n
import notifications
import security
import sso
from i18n import _

log = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

MAX_ACCOUNT_ATTEMPTS = 5
LOCK_MINUTES = 15
MAX_IP_ATTEMPTS = 20
IP_WINDOW_SECONDS = 600


# ─────────────────────────────────────────────────────────────────────────────
# Decorators
# ─────────────────────────────────────────────────────────────────────────────

def _is_api():
    return g.get('by_key') or request.path.startswith((config.APP_PREFIX + '/api/', config.APP_PREFIX + '/mcp'))


def login_required(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        if not g.get('user'):
            if _is_api():
                return {'error': _('Not authorized - log in or send an API key.')}, 401
            return redirect(url_for('auth.login', next=request.full_path))
        return f(*a, **kw)
    return wrapper


def _requires(check):
    def decorator(f):
        @functools.wraps(f)
        @login_required
        def wrapper(*a, **kw):
            if not check():
                if _is_api():
                    return {'error': _('Not allowed.')}, 403
                abort(403)
            return f(*a, **kw)
        return wrapper
    return decorator


admin_required = _requires(lambda: g.get('role') == 'admin')
editor_required = _requires(lambda: g.get('role') in ('admin', 'editor'))


# ─────────────────────────────────────────────────────────────────────────────
# First start
# ─────────────────────────────────────────────────────────────────────────────

_setup_code = None
_has_admin = False


def has_admin():
    global _has_admin
    if not _has_admin:
        _has_admin = bool(db.query("SELECT 1 FROM users WHERE role = 'admin' AND active = 1", one=True))
    return _has_admin


def setup_code():
    global _setup_code
    if _setup_code is None:
        _setup_code = '-'.join(secrets.token_hex(2).upper() for _i in range(3))
        log.warning('=' * 70)
        log.warning('No administrator yet. Open %s/setup and enter the code: %s', config.APP_URL, _setup_code)
        log.warning('=' * 70)
    return _setup_code


@auth_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    global _has_admin, _setup_code
    if has_admin():
        return redirect(url_for('auth.login'))
    setup_code()
    if request.method == 'GET':
        return render_template('setup.html')
    f = request.form
    data = {k: (f.get(k) or '').strip() for k in ('email', 'first_name', 'last_name')}
    data['email'] = data['email'].lower()
    problem = None
    if not secrets.compare_digest((f.get('code') or '').strip().upper(), _setup_code):
        problem = _('Invalid setup code - you will find it in the server log.')
    elif '@' not in data['email'] or not data['first_name'] or not data['last_name']:
        problem = _('Enter the e-mail, first name and last name.')
    elif f.get('password') != f.get('password2'):
        problem = _('The passwords do not match.')
    else:
        problem = security.password_problem(f.get('password'), data['email'])
    if problem:
        return render_template('setup.html', error=problem, **data), 400
    name = f"{data['first_name']} {data['last_name']}"
    if db.query('SELECT 1 FROM users WHERE email = ?', (data['email'],), one=True):
        db.execute("""UPDATE users SET first_name = ?, last_name = ?, display_name = ?, role = 'admin', active = 1,
                      password_hash = ?, language = ? WHERE email = ?""",
                   (data['first_name'], data['last_name'], name, security.hash_password(f['password']),
                    i18n.current(), data['email']))
    else:
        db.execute("""INSERT INTO users(email, first_name, last_name, display_name, role, password_hash, language, created)
                      VALUES (?, ?, ?, ?, 'admin', ?, ?, ?)""",
                   (data['email'], data['first_name'], data['last_name'], name,
                    security.hash_password(f['password']), i18n.current(), db.now()))
    db.log(data['email'], 'setup')
    _has_admin, _setup_code = True, None
    flash(_('The administrator account is ready - log in.'), 'ok')
    return redirect(url_for('auth.login', email=data['email']))


# ─────────────────────────────────────────────────────────────────────────────
# Session
# ─────────────────────────────────────────────────────────────────────────────

def _log_in(u, source):
    """Common end of a login (password and SSO). Returns an HTTP response."""
    nxt = session.get('next')
    session.clear()
    session.permanent = True
    session['email'] = u['email']
    session['last_activity'] = time.time()
    session['logged_in_at'] = time.time()
    session['source'] = source
    session['csrf'] = secrets.token_urlsafe(32)        # a new token after logging in
    # The language chosen on the login screen becomes the account's language if it has none yet.
    language = u['language'] or (request.cookies.get('lang') if request.cookies.get('lang') in i18n.LANGUAGES else None)
    db.execute('UPDATE users SET last_login = ?, failed_logins = 0, locked_until = NULL, language = ? '
               'WHERE email = ?', (db.now(), language, u['email']))
    db.log(u['email'], 'login', source=source, ip=request.remote_addr)
    if u['must_change_password'] and source == 'password':
        return redirect(url_for('account.password'))
    if nxt and nxt.startswith(config.APP_PREFIX + '/') and not nxt.startswith('//'):
        return redirect(nxt)
    return redirect(url_for('web.plan'))


def password_login_allowed(role=None):
    """Is the password field shown / can this account log in with a password."""
    cfg = app_settings.get('login')
    if not sso.active():
        return True          # without SSO the password must work, otherwise nobody can log in
    if not cfg.get('password_login'):
        return role == 'admin' if role else False
    if cfg.get('password_admins_only'):
        return role == 'admin' if role else True
    return True


_ip_attempts = {}
_ip_lock = threading.Lock()
_dummy = []


def _dummy_hash():
    if not _dummy:
        _dummy.append(security.hash_password(secrets.token_hex(8)))
    return _dummy[0]


def _too_many_from_ip():
    ip = request.remote_addr or '?'
    now = time.time()
    with _ip_lock:
        items = [t for t in _ip_attempts.get(ip, []) if now - t < IP_WINDOW_SECONDS]
        items.append(now)
        _ip_attempts[ip] = items
        return len(items) > MAX_IP_ATTEMPTS


def _login_page(**kw):
    return render_template('login.html', providers=sso.active(),
                           password_field=password_login_allowed() or bool(request.args.get('password')),
                           password_reset=notifications.configured(), **kw)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if g.get('user'):
        return redirect(url_for('web.plan'))
    nxt = request.args.get('next')
    if nxt:
        session['next'] = nxt
    if request.method == 'GET':
        return _login_page(email=request.args.get('email', ''))

    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    failed = _('Invalid e-mail or password.')
    if _too_many_from_ip():
        return _login_page(email=email, error=_('Too many login attempts - try again in a few minutes.')), 429
    u = db.query('SELECT * FROM users WHERE email = ?', (email,), one=True)
    if not u:
        security.check_password(_dummy_hash(), password)   # the same response time as for an existing account
        return _login_page(email=email, error=failed), 401
    if u['locked_until'] and u['locked_until'] > db.now():
        return _login_page(email=email, error=_('The account is temporarily locked after failed attempts - '
                                                'try again after {time}.', time=u['locked_until'][11:16])), 429
    if not security.check_password(u['password_hash'], password):
        attempts = (u['failed_logins'] or 0) + 1
        locked = None
        if attempts >= MAX_ACCOUNT_ATTEMPTS:
            locked, attempts = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime('%Y-%m-%d %H:%M:%S'), 0
            db.log(email, 'account_locked', ip=request.remote_addr)
        db.execute('UPDATE users SET failed_logins = ?, locked_until = ? WHERE email = ?', (attempts, locked, email))
        return _login_page(email=email, error=failed), 401
    if not u['active']:
        return _login_page(email=email, error=_('The account is blocked. Contact the administrator.')), 403
    if not password_login_allowed(u['role']):
        return _login_page(email=email, error=_('Log in with SSO (the buttons above).')), 403
    return _log_in(u, 'password')


@auth_bp.route('/sso/<provider>')
def sso_start(provider):
    try:
        return redirect(sso.login_url(provider, session, request.args.get('email')))
    except sso.SSOError as e:
        return _login_page(error=str(e)), 400


@auth_bp.route('/sso/<provider>/callback')
def sso_callback(provider):
    try:
        data = sso.finish(provider, request.args, session)
    except sso.SSOError as e:
        log.warning('SSO %s: %s', provider, e)
        return _login_page(error=_('Login with {provider} failed: {error}', provider=sso.PROVIDERS.get(provider, ''),
                                   error=e)), 400
    email = data['email']
    u = db.query('SELECT * FROM users WHERE email = ?', (email,), one=True)
    if not u and data['auto_create']:
        name = f"{data['first_name']} {data['last_name']}".strip() or email
        db.execute("""INSERT INTO users(email, first_name, last_name, display_name, role, sso_provider, sso_subject, created)
                      VALUES (?, ?, ?, ?, 'user', ?, ?, ?)""",
                   (email, data['first_name'], data['last_name'], name, provider, data['subject'], db.now()))
        db.log(email, 'account_from_sso', provider=provider)
        u = db.query('SELECT * FROM users WHERE email = ?', (email,), one=True)
    if not u or not u['active']:
        db.log(email, 'sso_refused', provider=provider)
        session.clear()
        return render_template('logged_out.html', login_again=True, message=_(
            'The account {email} has no access to the desk booking system. Ask the administrator to add it.',
            email=email)), 403
    changes, params = ['sso_provider = ?', 'sso_subject = ?'], [provider, data['subject']]
    if not u['first_name'] and data['first_name']:
        changes += ['first_name = ?', 'last_name = ?', 'display_name = ?']
        params += [data['first_name'], data['last_name'], f"{data['first_name']} {data['last_name']}".strip()]
    db.execute(f"UPDATE users SET {', '.join(changes)} WHERE email = ?", (*params, email))
    return _log_in(u, 'sso_' + provider)


@auth_bp.route('/logout')
def logout():
    if session.get('email'):
        db.log(session['email'], 'logout')
    session.clear()
    return render_template('logged_out.html', message=_('You have been logged out.'), login_again=True)


@auth_bp.route('/language/<code>')
def set_language(code):
    """Language switch: a cookie for the login screen, and the account's language when logged in."""
    if code not in i18n.LANGUAGES:
        abort(404)
    target = request.args.get('next') or request.referrer or url_for('web.plan')
    parsed = urlparse(target)
    if parsed.netloc and parsed.netloc != request.host:
        target = url_for('web.plan')
    if g.get('user'):
        db.execute('UPDATE users SET language = ? WHERE email = ?', (code, g.user['email']))
    resp = make_response(redirect(target))
    resp.set_cookie('lang', code, max_age=365 * 24 * 3600, samesite='Lax', httponly=True,
                    secure=config.APP_URL.startswith('https'))
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Invitations and password reset
# ─────────────────────────────────────────────────────────────────────────────

def password_link(email, hours=72):
    """One-time link to set a password; only the token hash is stored."""
    token = security.new_token()
    db.execute('DELETE FROM tokens WHERE email = ? OR expires < ?', (email, db.now()))
    db.execute('INSERT INTO tokens(token_hash, email, purpose, expires, created) VALUES (?, ?, ?, ?, ?)',
               (security.token_hash(token), email, 'password',
                (datetime.now() + timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S'), db.now()))
    return f'{config.APP_URL}/set-password/{token}'


@auth_bp.route('/set-password/<token>', methods=['GET', 'POST'])
def set_password(token):
    row = db.query('SELECT * FROM tokens WHERE token_hash = ? AND expires > ?', (security.token_hash(token), db.now()),
                   one=True)
    u = db.query('SELECT * FROM users WHERE email = ? AND active = 1', (row['email'],), one=True) if row else None
    if not u:
        return render_template('logged_out.html', login_again=True,
                               message=_('The link has expired or has already been used. Ask for a new one.')), 410
    if request.method == 'GET':
        return render_template('set_password.html', u=u)
    password = request.form.get('password') or ''
    problem = _('The passwords do not match.') if password != request.form.get('password2') \
        else security.password_problem(password, u['email'])
    if problem:
        return render_template('set_password.html', u=u, error=problem), 400
    db.execute('UPDATE users SET password_hash = ?, must_change_password = 0, failed_logins = 0, '
               'locked_until = NULL WHERE email = ?', (security.hash_password(password), u['email']))
    db.execute('DELETE FROM tokens WHERE email = ?', (u['email'],))
    db.log(u['email'], 'password_set')
    flash(_('Password set - you can log in.'), 'ok')
    return redirect(url_for('auth.login', email=u['email']))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if not notifications.configured():
        abort(404)
    if request.method == 'GET':
        return render_template('forgot_password.html')
    email = (request.form.get('email') or '').strip().lower()
    if not _too_many_from_ip():
        u = db.query('SELECT * FROM users WHERE email = ? AND active = 1', (email,), one=True)
        if u:
            notifications.invitation(email, password_link(email, hours=24), email, purpose='reset')
            db.log(email, 'password_reset', ip=request.remote_addr)
    # The same answer whether the account exists or not.
    return render_template('logged_out.html', login_again=True, message=_(
        'If an account with this address exists, we have sent a link to set a password to it.'))


# ─────────────────────────────────────────────────────────────────────────────
# API keys
# ─────────────────────────────────────────────────────────────────────────────

def key_from_request():
    k = request.headers.get('X-API-Key')
    if not k:
        header = request.headers.get('Authorization') or ''
        if header.lower().startswith('bearer '):
            k = header[7:].strip()
    return k or None


def user_by_key(key):
    if not key or not key.startswith('rzb_'):
        return None
    u = db.query('SELECT * FROM users WHERE api_key_hash = ?', (security.token_hash(key),), one=True)
    if not u or not u['active']:
        return None
    now = db.now()
    if (u['api_key_used'] or '')[:16] != now[:16]:      # write at most once a minute
        db.execute('UPDATE users SET api_key_used = ? WHERE email = ?', (now, u['email']))
    return u


_api_calls = {}
_api_lock = threading.Lock()


def api_rate_exceeded(email):
    limit = int(app_settings.get('api').get('rate_limit') or 0)
    if limit <= 0:
        return False
    now = time.time()
    with _api_lock:
        items = [t for t in _api_calls.get(email, []) if now - t < 60]
        items.append(now)
        _api_calls[email] = items
        return len(items) > limit
