"""Single sign-on with OpenID Connect.

Providers:
  * microsoft - Microsoft Entra ID (Azure AD / Microsoft 365), a specific tenant
                or `organizations` / `common`,
  * google    - Google accounts / Google Workspace,
  * oidc      - any OpenID Connect provider with a discovery address
                (Keycloak, Okta, Auth0, ADFS, Authentik, ...).

Flow: authorization code + PKCE + state + nonce. The ID token is verified with the provider's
signing keys (JWKS, PyJWT) plus issuer, audience, expiry and nonce. The configuration (client
id and secret) is entered by an administrator in the settings; the redirect URI to register
at the provider is {APP_URL}/sso/<provider>/callback.
"""
import base64
import hashlib
import logging
import secrets
import threading
import time
from urllib.parse import urlencode

import jwt
import requests

import app_settings
import config
from i18n import _

log = logging.getLogger(__name__)

PROVIDERS = {
    'microsoft': 'Microsoft (Entra ID / Azure AD)',
    'google': 'Google',
    'oidc': 'Other OpenID Connect provider',
}
ICONS = {'microsoft': 'ms', 'google': 'g', 'oidc': 'key'}
_FLOW_TIMEOUT = 600       # seconds to come back from the provider


class SSOError(Exception):
    pass


def provider_config(provider, with_secrets=False):
    if provider not in PROVIDERS:
        raise SSOError(_('Unknown login provider.'))
    return app_settings.get('sso_' + provider, with_secrets=with_secrets)


def redirect_uri(provider, cfg=None):
    cfg = cfg or provider_config(provider)
    return (cfg.get('redirect_uri') or '').strip() or f'{config.APP_URL}/sso/{provider}/callback'


def button_name(provider, cfg=None):
    if provider == 'oidc':
        cfg = cfg or provider_config(provider)
        return (cfg.get('name') or '').strip() or 'SSO'
    return {'microsoft': 'Microsoft', 'google': 'Google'}[provider]


def is_complete(provider, cfg):
    if not (cfg.get('client_id') and cfg.get('client_secret')):
        return False
    if provider == 'oidc' and not cfg.get('discovery_url'):
        return False
    return True


def active():
    """[(provider, button label, icon)] of enabled and configured providers."""
    out = []
    for p in PROVIDERS:
        cfg = provider_config(p)
        if cfg.get('enabled') and is_complete(p, cfg):
            out.append((p, button_name(p, cfg), ICONS[p]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Provider metadata (discovery) and signing keys
# ─────────────────────────────────────────────────────────────────────────────

_cache = {}
_cache_lock = threading.Lock()


def _discovery_url(provider, cfg):
    if provider == 'microsoft':
        tenant = (cfg.get('tenant') or 'organizations').strip()
        return f'https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration'
    if provider == 'google':
        return 'https://accounts.google.com/.well-known/openid-configuration'
    url = cfg['discovery_url'].strip()
    if not url.endswith('/.well-known/openid-configuration'):
        url = url.rstrip('/') + '/.well-known/openid-configuration'
    return url


def metadata(provider, cfg):
    url = _discovery_url(provider, cfg)
    with _cache_lock:
        entry = _cache.get(url)
        if entry and time.time() - entry[0] < 3600:
            return entry[1]
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        meta = resp.json()
    except Exception as e:  # noqa: BLE001
        raise SSOError(_('Could not download the provider configuration ({url}): {error}', url=url, error=e))
    for k in ('authorization_endpoint', 'token_endpoint', 'jwks_uri', 'issuer'):
        if not meta.get(k):
            raise SSOError(_('The provider configuration has no {field} field.', field=k))
    with _cache_lock:
        _cache[url] = (time.time(), meta, jwt.PyJWKClient(meta['jwks_uri'], cache_keys=True, lifespan=3600))
    return meta


def _jwks_client(provider, cfg):
    metadata(provider, cfg)
    return _cache[_discovery_url(provider, cfg)][2]


def check_provider(provider):
    """Test from the settings page: can the metadata be downloaded. Returns a description or raises SSOError."""
    cfg = provider_config(provider, with_secrets=True)
    meta = metadata(provider, cfg)
    missing = [k for k in ('client_id', 'client_secret') if not cfg.get(k)]
    text = _('The provider responds: {issuer}', issuer=meta['issuer'])
    if missing:
        text += ' · ' + _('missing: {fields}', fields=', '.join(missing))
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Login flow
# ─────────────────────────────────────────────────────────────────────────────

def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _domains(cfg):
    return [d.strip().lower().lstrip('@') for d in (cfg.get('domains') or '').replace(';', ',').split(',') if d.strip()]


def login_url(provider, session, login_hint=None):
    """URL to redirect the browser to; the flow data is stored in the session."""
    cfg = provider_config(provider, with_secrets=True)
    if not (cfg.get('enabled') and is_complete(provider, cfg)):
        raise SSOError(_('This login method is not enabled.'))
    meta = metadata(provider, cfg)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    session['sso'] = {'provider': provider, 'state': state, 'nonce': nonce, 'verifier': verifier, 'time': time.time()}
    params = {
        'client_id': cfg['client_id'],
        'response_type': 'code',
        'redirect_uri': redirect_uri(provider, cfg),
        'scope': (cfg.get('scope') or 'openid email profile') if provider == 'oidc' else 'openid email profile',
        'state': state,
        'nonce': nonce,
        'code_challenge': _b64(hashlib.sha256(verifier.encode('ascii')).digest()),
        'code_challenge_method': 'S256',
        'prompt': 'select_account',
    }
    if login_hint:
        params['login_hint'] = login_hint
    domains = _domains(cfg)
    if provider == 'google' and len(domains) == 1:
        params['hd'] = domains[0]
    endpoint = meta['authorization_endpoint']
    return endpoint + ('&' if '?' in endpoint else '?') + urlencode(params)


def _check_issuer(provider, meta, claims):
    iss = claims.get('iss') or ''
    expected = meta['issuer']
    if provider == 'microsoft' and '{tenantid}' in expected:
        # "organizations" / "common" tenants: the issuer contains the user's tenant id.
        expected = expected.replace('{tenantid}', claims.get('tid') or '')
    if iss != expected:
        raise SSOError(_('The token was issued by a different provider than expected.'))


def _email(provider, claims):
    if provider == 'microsoft':
        email = claims.get('email') or claims.get('preferred_username') or claims.get('upn')
    else:
        email = claims.get('email')
        if claims.get('email_verified') is False or (provider == 'google' and not claims.get('email_verified')):
            raise SSOError(_('The provider has not confirmed the e-mail address.'))
    if not email or '@' not in email:
        raise SSOError(_('The provider did not return an e-mail address. Add the "email" scope to the client configuration.'))
    return email.strip().lower()


def finish(provider, args, session):
    """Exchange the code for tokens and verify the ID token.

    Returns {'email', 'first_name', 'last_name', 'subject', 'auto_create'}; raises SSOError.
    """
    flow = session.pop('sso', None)
    if not flow or flow.get('provider') != provider:
        raise SSOError(_('The login session has expired - please start again.'))
    if time.time() - flow.get('time', 0) > _FLOW_TIMEOUT:
        raise SSOError(_('Logging in took too long - please try again.'))
    if args.get('error'):
        raise SSOError(args.get('error_description') or args['error'])
    if not secrets.compare_digest(args.get('state') or '', flow['state']):
        raise SSOError(_('Invalid state parameter - please try again.'))
    code = args.get('code')
    if not code:
        raise SSOError(_('The provider did not return an authorization code.'))

    cfg = provider_config(provider, with_secrets=True)
    meta = metadata(provider, cfg)
    try:
        resp = requests.post(meta['token_endpoint'], timeout=15, data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri(provider, cfg),
            'client_id': cfg['client_id'],
            'client_secret': cfg['client_secret'],
            'code_verifier': flow['verifier'],
        })
        tokens = resp.json()
    except Exception as e:  # noqa: BLE001
        raise SSOError(_('Could not get the token: {error}', error=e))
    if resp.status_code >= 300 or 'id_token' not in tokens:
        log.warning('SSO %s: %s', provider, tokens)
        raise SSOError(tokens.get('error_description') or tokens.get('error') or _('The provider did not return an ID token.'))

    try:
        signing_key = _jwks_client(provider, cfg).get_signing_key_from_jwt(tokens['id_token'])
        claims = jwt.decode(tokens['id_token'], signing_key.key,
                            algorithms=['RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'PS256'],
                            audience=cfg['client_id'], leeway=60,
                            options={'verify_iss': False, 'require': ['exp', 'iat', 'aud', 'iss', 'sub']})
    except jwt.PyJWTError as e:
        raise SSOError(_('The ID token failed verification: {error}', error=e))
    _check_issuer(provider, meta, claims)
    if not secrets.compare_digest(claims.get('nonce') or '', flow['nonce']):
        raise SSOError(_('Invalid nonce in the token.'))

    email = _email(provider, claims)
    domains = _domains(cfg)
    if domains and email.split('@')[1] not in domains:
        raise SSOError(_('Login is allowed only for the domains: {domains}.', domains=', '.join(domains)))
    first, last = claims.get('given_name') or '', claims.get('family_name') or ''
    if not (first or last) and claims.get('name'):
        parts = claims['name'].split()
        first, last = parts[0], ' '.join(parts[1:])
    return {'email': email, 'first_name': first.strip(), 'last_name': last.strip(), 'subject': claims['sub'],
            'auto_create': bool(cfg.get('auto_create'))}
