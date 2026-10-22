"""Desk booking - Flask application factory."""
import logging
import secrets
import time
from datetime import timedelta

from flask import Flask, abort, g, redirect, render_template, request, session, url_for

import app_settings
import auth
import config
import db
import i18n
import permissions as perm
import security
import theme
from i18n import _

log = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__, static_url_path=config.APP_PREFIX + '/static')
    app.secret_key = security.session_key()
    https = config.APP_URL.startswith('https')
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(hours=24),
        SESSION_COOKIE_NAME='desks_session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=https,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    )
    app.json.ensure_ascii = False
    app.jinja_env.globals.update(_=_, weekday=i18n.weekday)
    security.key()        # a key configuration error must show up at start, not at the first save

    db.init_db()
    app.teardown_appcontext(db.close_db)

    from routes.account import account_bp
    from routes.admin import admin_bp
    from routes.api_v1 import api_bp
    from routes.mcp import mcp_bp
    from routes.web import web_bp
    app.register_blueprint(auth.auth_bp, url_prefix=config.APP_PREFIX)
    app.register_blueprint(web_bp, url_prefix=config.APP_PREFIX)
    app.register_blueprint(account_bp, url_prefix=config.APP_PREFIX + '/account')
    app.register_blueprint(admin_bp, url_prefix=config.APP_PREFIX + '/admin')
    app.register_blueprint(api_bp, url_prefix=config.APP_PREFIX + '/api/v1')
    app.register_blueprint(mcp_bp, url_prefix=config.APP_PREFIX + '/mcp')

    api_path = config.APP_PREFIX + '/api/v1'
    mcp_path = config.APP_PREFIX + '/mcp'
    auth_header = {'WWW-Authenticate': 'Bearer realm="desks"'}

    @app.route('/favicon.ico')
    def favicon():
        return app.send_static_file('favicon.ico')

    @app.route('/')
    def root():
        return redirect(url_for('web.plan'))

    def _by_key(key):
        """A request with an API key: no session and no CSRF token (cookies are not involved)."""
        mcp = request.path.startswith(mcp_path)
        cfg = app_settings.get('api')
        if not cfg.get('mcp' if mcp else 'api'):
            return {'error': _('The MCP server is switched off by the administrator.') if mcp
                    else _('The API is switched off by the administrator.')}, 403
        u = auth.user_by_key(key)
        if not u:
            return {'error': _('Invalid or revoked API key.')}, 401, auth_header
        g.lang = i18n.detect(u)
        if auth.api_rate_exceeded(u['email']):
            return {'error': _('Too many requests - try again in a moment.')}, 429, {'Retry-After': '60'}
        g.user, g.role = dict(u), u['role']
        g.by_key = True
        g.channel = 'mcp' if mcp else 'api'
        return None

    @app.before_request
    def load_user():
        g.user = None
        g.role = None
        if request.path.startswith(config.APP_PREFIX + '/static'):
            return None
        g.lang = i18n.detect()
        api = request.path.startswith((api_path, mcp_path))
        key = auth.key_from_request() if api else None
        if key:
            return _by_key(key)
        if request.path.startswith(mcp_path):
            return {'error': _('The MCP server requires an API key (header Authorization: Bearer <key>).')}, 401, auth_header
        if api and not session.get('email') and request.method != 'GET':
            # REST without a key and without a logged-in session: 401, not a form token error
            return {'error': _('Not authorized - send an API key.')}, 401, auth_header

        # CSRF token in the session; every POST from the browser must send it back (form or header).
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf')
            if not token or not secrets.compare_digest(token, session['csrf']):
                abort(400, _('Invalid form token - refresh the page.'))

        if not auth.has_admin() and request.endpoint not in ('auth.setup', 'auth.set_language', 'favicon'):
            if api:
                return {'error': _('The application is not configured yet.')}, 503
            return redirect(url_for('auth.setup'))

        email = session.get('email')
        if not email:
            return None
        if time.time() - (session.get('last_activity') or 0) > config.IDLE_TIMEOUT:
            session.clear()
            return render_template('logged_out.html', login_again=True,
                                   message=_('The session expired after a period of inactivity.')), 401
        session['last_activity'] = time.time()
        u = db.query('SELECT * FROM users WHERE email = ?', (email,), one=True)
        if not u or not u['active']:
            session.clear()
            return render_template('logged_out.html', message=_('Access to the system has been revoked.')), 403
        g.user = dict(u)
        g.role = u['role']
        g.lang = i18n.detect(g.user)
        if u['must_change_password'] and session.get('source') == 'password' \
                and request.endpoint not in ('account.password', 'auth.logout', 'auth.set_language'):
            return redirect(url_for('account.password'))
        return None

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        if https:
            resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
        return resp

    @app.context_processor
    def context():
        manager = bool(g.get('user') and perm.managed_people(g.user['email']))
        return {
            'csrf_token': session.get('csrf', ''),
            'me': g.get('user'),
            'my_role': g.get('role'),
            'is_admin': g.get('role') == 'admin',
            'is_editor': g.get('role') in ('admin', 'editor'),
            'is_manager': manager,
            'ROLES': perm.ROLES,
            'PREFIX': config.APP_PREFIX,
            'APP_URL': config.APP_URL,
            'environment': config.ENV,
            'lang': i18n.current(),
            'LANGUAGES': i18n.LANGUAGES,
            'js_catalog': i18n.js_catalog(),
            'theme_css': theme.css(),
        }

    def _json():
        return request.path.startswith((config.APP_PREFIX + '/api/', mcp_path)) or request.is_json

    @app.errorhandler(403)
    def forbidden(e):
        if _json():
            return {'error': _('Not allowed.')}, 403
        return render_template('error.html', code=403, text=_('You are not allowed to see this page.')), 403

    @app.errorhandler(404)
    def not_found(e):
        if _json():
            return {'error': _('Not found.')}, 404
        return render_template('error.html', code=404, text=_('Page not found.')), 404

    @app.errorhandler(400)
    def bad_request(e):
        if _json():
            return {'error': e.description}, 400
        return render_template('error.html', code=400, text=e.description), 400

    with app.app_context():
        if not auth.has_admin():
            auth.setup_code()
    return app
