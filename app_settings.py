"""Settings changed by an administrator in the application (table `settings`).

Every group is one JSON row. Secret fields (SSO client secret, SMTP password) are stored
encrypted with AES-GCM and are never sent back to the browser - the form only shows
"saved", and an empty field on save means "unchanged".
"""
import json

from flask import g, has_app_context

import db
import security

DEFAULTS = {
    'general': {
        'default_language': 'auto',    # pl | en | auto (browser language)
    },
    'theme': {                         # main colours (theme.py)
        'header': '#0b2f5b', 'main': '#1d5fbf', 'accent': '#0d9488',
    },
    'login': {
        'password_login': True,        # e-mail and password login
        'password_admins_only': False,  # with SSO on: password login for administrators only (break-glass)
    },
    'sso_microsoft': {
        'enabled': False, 'client_id': '', 'client_secret': '', 'tenant': 'organizations',
        'redirect_uri': '', 'domains': '', 'auto_create': False,
    },
    'sso_google': {
        'enabled': False, 'client_id': '', 'client_secret': '',
        'redirect_uri': '', 'domains': '', 'auto_create': False,
    },
    'sso_oidc': {
        'enabled': False, 'name': 'Company SSO', 'discovery_url': '', 'client_id': '', 'client_secret': '',
        'scope': 'openid email profile', 'redirect_uri': '', 'domains': '', 'auto_create': False,
    },
    'mail': {
        'enabled': False, 'host': '', 'port': 587, 'mode': 'starttls', 'login': '', 'password': '',
        'sender': '', 'sender_name': 'Desk booking',
    },
    'api': {
        'api': True,                   # REST /api/v1 with a key
        'mcp': True,                   # MCP server /mcp for AI assistants
        'rate_limit': 120,             # requests per key per minute
    },
}
SECRET = {'client_secret', 'password'}


def _load(group):
    raw = db.setting(group)
    data = dict(DEFAULTS[group])
    if raw:
        try:
            data.update(json.loads(raw))
        except ValueError:
            pass
    return data


def get(group, with_secrets=False):
    """Settings of a group. Without `with_secrets` secrets are replaced by True/False (set or not)."""
    if has_app_context():
        cache = g.setdefault('_settings', {})
        if group not in cache:
            cache[group] = _load(group)
        data = dict(cache[group])
    else:
        data = _load(group)
    for k in SECRET & data.keys():
        data[k] = security.decrypt(data[k]) if with_secrets else bool(data[k])
    return data


def save(group, changes):
    """Save changes of a group. Empty secret = unchanged; secret None = delete."""
    raw = _load(group)
    for k, v in changes.items():
        if k not in DEFAULTS[group]:
            continue
        if k in SECRET:
            if v is None:
                raw[k] = ''
            elif v != '':
                raw[k] = security.encrypt(v)
        else:
            raw[k] = v
    db.save_setting(group, json.dumps(raw, ensure_ascii=False))
    if has_app_context():
        g.pop('_settings', None)
