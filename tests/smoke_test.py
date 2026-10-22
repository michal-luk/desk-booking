"""End-to-end smoke test on a separate database:

    python tests/smoke_test.py [--db postgresql://user:pass@host:port/db | mariadb://... | mysql://...]

Without --db: an empty SQLite database in a temporary directory. With --db: the given server
(the database must be empty). Goes through the whole path: setup -> login -> accounts and teams ->
floor and plan -> bookings and manager rights -> languages -> API key -> REST -> MCP -> SSO ->
switching the database.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # project directory
TMP = tempfile.mkdtemp()
os.environ['DB_PATH'] = os.path.join(TMP, 'test.sqlite')
os.environ['DB_CONFIG_PATH'] = os.path.join(TMP, 'database.json')
os.environ['ENCRYPTION_KEY'] = 'ab' * 32
os.environ['SECRET_KEY'] = 'test'

parser = argparse.ArgumentParser()
parser.add_argument('--db')
ARGS = parser.parse_args()

import db  # noqa: E402
if ARGS.db:
    u = urlparse(ARGS.db)
    db.save_configuration({'type': u.scheme, 'host': u.hostname, 'port': u.port, 'database': u.path.strip('/'),
                           'user': u.username, 'password': u.password})

import auth  # noqa: E402
import config  # noqa: E402
from app import create_app  # noqa: E402

FAILED = []


def check(condition, text):
    print(('  OK    ' if condition else '  FAIL  ') + text)
    if not condition:
        FAILED.append(text)


app = create_app()
app.config['TESTING'] = True
P = config.APP_PREFIX
print('Database:', db.describe())


def client():
    c = app.test_client()
    c.get(P + '/login')
    with c.session_transaction() as s:
        c.csrf = s['csrf']
    return c


def post(c, url, **data):
    return c.post(P + url, data={'csrf': c.csrf, **data})


def api(c, url, method='POST', **data):
    return c.open(P + url, method=method, json=data, headers={'X-CSRF-Token': c.csrf})


def log_in(email, password):
    c = client()
    r = post(c, '/login', email=email, password=password)
    with c.session_transaction() as s:
        c.csrf = s.get('csrf') or c.csrf
    return c, r


# ── Setup ─────────────────────────────────────────────────────────────────────
print('Setup')
c = client()
r = c.get(P + '/')
check(r.status_code == 302 and '/setup' in r.location, 'no administrator -> setup wizard')
ADMIN, PASSWORD = 'admin@example.test', 'SecretPass123'
r = post(c, '/setup', code='wrong', email=ADMIN, first_name='Anna', last_name='Admin', password=PASSWORD, password2=PASSWORD)
check(r.status_code == 400, 'wrong setup code rejected')
r = post(c, '/setup', code=auth.setup_code(), email=ADMIN, first_name='Anna', last_name='Admin',
         password=PASSWORD, password2=PASSWORD)
check(r.status_code == 302, 'administrator created')

# ── Login ─────────────────────────────────────────────────────────────────────
print('Login')
_, r = log_in(ADMIN, 'wrong-password-1')
check(r.status_code == 401, 'wrong password rejected')
a, r = log_in(ADMIN, PASSWORD)
check(r.status_code == 302 and r.location.endswith(P + '/'), 'password login')
check(a.get(P + '/').status_code == 200, 'plan after login')
with app.app_context():
    h = db.query('SELECT password_hash FROM users WHERE email = ?', (ADMIN,), one=True)['password_hash']
check(h.startswith('scrypt:') and PASSWORD not in h, 'password stored as a scrypt hash')

# ── Accounts and teams ────────────────────────────────────────────────────────
print('Accounts and teams')
r = post(a, '/admin/users/add', email='manager@example.test', first_name='Karl', last_name='Manager',
         role='user', password='ManagerPass1234', force_change='1')
check(r.status_code == 302, 'account added with a password')
r = post(a, '/admin/teams/new', name='Test team', color='#123456', manager_email='manager@example.test')
tid = int(re.search(r'/teams/(\d+)', r.location).group(1))
r = post(a, f'/admin/teams/{tid}/members', action='person', email='jan@example.test', first_name='Jan', last_name='Nowak')
check(r.status_code == 302, 'member added: e-mail + first + last name')
post(a, f'/admin/teams/{tid}/members', action='list',
     list='ola@example.test; Ola; Lis\nPiotr Zieliński <piotr@example.test>\nno-account@example.test\nbad-address')
with app.app_context():
    members = {r['email'] for r in db.query('SELECT email FROM team_members WHERE team_id = ?', (tid,))}
    piotr = db.query('SELECT first_name, last_name FROM users WHERE email = ?', ('piotr@example.test',), one=True)
check({'manager@example.test', 'jan@example.test', 'ola@example.test', 'piotr@example.test'} <= members, 'list: everyone added')
check('no-account@example.test' not in members, 'an e-mail alone does not create an account without a name')
check(piotr and piotr['last_name'] == 'Zieliński', 'Outlook format: first and last name from the entry')
r = a.get(P + f'/admin/teams/{tid}')
check('Jan Nowak' in r.get_data(as_text=True), 'team page with members')
r = post(a, '/admin/users/jan@example.test', action='invite')
link = re.search(r'(/set-password/[\w-]+)', a.get(P + '/admin/users/jan@example.test').get_data(as_text=True))
check(link is not None, 'link to set the password (without mail shown to the administrator)')
j = client()
r = post(j, link.group(1), password='JanPass12345', password2='JanPass12345')
check(r.status_code == 302, 'password set from the invitation')
r = post(j, link.group(1), password='JanPass12345', password2='JanPass12345')
check(r.status_code == 410, 'one-time link')
jan, r = log_in('jan@example.test', 'JanPass12345')
check(r.status_code == 302, 'login with the password from the invitation')
check(jan.get(P + '/admin/users').status_code == 403, 'a user cannot enter the administration')
mgr, _ = log_in('manager@example.test', 'ManagerPass1234')
check(mgr.get(P + '/').status_code == 302 and '/account/password' in mgr.get(P + '/').location,
      'initial password -> forced change')
r = post(mgr, '/account/password', current='ManagerPass1234', password='NewManager12345', password2='NewManager12345')
check(mgr.get(P + '/').status_code == 200, 'after the change - access to the plan')

# quick password reset from the lists (users / team members)
r = api(a, '/admin/api/account/ola@example.test/password-link')
check(r.status_code == 200 and '/set-password/' in (r.get_json().get('link') or ''),
      'password link from a list: administrator without mail gets the link')
post(a, '/admin/users/manager@example.test', action='details', first_name='Karl', last_name='Manager', role='editor', active='1')
editor, _ = log_in('manager@example.test', 'NewManager12345')
check(api(editor, '/admin/api/account/ola@example.test/password-link').status_code == 403,
      'an editor without mail does not get the link (cannot take over the account)')
check(api(editor, '/admin/api/account/ola@example.test/password', password='Temporary123').status_code == 403,
      'an editor cannot set somebody else\'s password')
check(api(a, '/admin/api/account/ola@example.test/password', password='short').status_code == 400, 'too weak password rejected')
r = api(a, '/admin/api/account/ola@example.test/password', password='Temporary123')
check(r.status_code == 200, 'administrator sets a temporary password from a list')
ola, r = log_in('ola@example.test', 'Temporary123')
check(r.status_code == 302 and '/account/password' in r.location, 'temporary password -> forced change at login')
post(a, '/admin/users/manager@example.test', action='details', first_name='Karl', last_name='Manager', role='user', active='1')

# lock after 5 attempts
for _ in range(5):
    log_in('ola@example.test', 'anything123')
with app.app_context():
    check(db.query('SELECT locked_until FROM users WHERE email = ?', ('ola@example.test',), one=True)['locked_until']
          is not None, 'account locked after 5 failed attempts')

# ── Floor and plan ────────────────────────────────────────────────────────────
print('Floor and plan')
r = post(a, '/admin/floors/save', name='Test floor', active='1', to_editor='1')
fid = int(re.search(r'/floors/(\d+)/editor', r.location).group(1))
elements = [{'id': f'n{i}', 'type': 'desk', 'code': f'T-{i}', 'x': i * 80, 'y': 10, 'w': 60, 'h': 40} for i in range(1, 4)]
elements.append({'id': 'n4', 'type': 'desk', 'code': 'T-Z', 'x': 400, 'y': 10, 'w': 60, 'h': 40, 'teams': [tid]})
elements.append({'id': 'n5', 'type': 'room', 'label': 'Room Test', 'x': 10, 'y': 200, 'w': 200, 'h': 150, 'capacity': 8})
r = api(a, f'/admin/api/floors/{fid}/elements', elements=elements)
id_map = r.get_json()['map']
check(r.status_code == 200 and len(id_map) == 5, 'plan saved')
d1 = date.today() + timedelta(days=1)
while d1.weekday() > 4:
    d1 += timedelta(days=1)
D = d1.isoformat()
plan = api(jan, f'/api/plan?floor={fid}&date={D}', 'GET').get_json()
check(any(e['status'] == 'free' for e in plan['elements']), 'plan with the status of places')

# ── Bookings ──────────────────────────────────────────────────────────────────
print('Bookings')
d_1, d_team, room = id_map['n1'], id_map['n4'], id_map['n5']
r = api(jan, '/api/book', element_id=d_1, date=D)
check(r.status_code == 200 and r.get_json()['booked'] == 1, 'desk booked')
r = api(mgr, '/api/book', element_id=d_1, date=D)
check(r.get_json()['booked'] == 0, 'collision: the same desk taken')
r = api(jan, '/api/book', element_id=id_map['n2'], date=D)
check(r.get_json()['booked'] == 0, 'collision: two desks of one person')
r = api(mgr, '/api/book', element_id=d_team, date=D, **{'for': 'ola@example.test'})
check(r.get_json().get('booked') == 1, 'manager books for a team member (team desk)')
r = api(jan, '/api/book', element_id=id_map['n3'], date=D, **{'for': 'manager@example.test'})
check(r.status_code == 403, 'a regular user cannot book for others')
r = api(jan, '/api/book', element_id=room, date=D, time_from='09:00', time_to='10:00', title='Meeting')
check(r.get_json()['booked'] == 1, 'room for hours')
r = api(mgr, '/api/book', element_id=room, date=D, time_from='09:30', time_to='11:00')
check(r.get_json()['booked'] == 0, 'room: overlapping hours')
r = api(mgr, '/api/book', element_id=room, date=D, time_from='10:00', time_to='11:00')
check(r.get_json()['booked'] == 1, 'room: the next hour is free')
mine = jan.get(P + '/my').get_data(as_text=True)
check('T-1' in mine and 'Room Test' in mine, 'my bookings')
week = mgr.get(P + '/team?date=' + D).get_data(as_text=True)
check('Jan Nowak' in week and 'T-1' in week, 'team week')
with app.app_context():
    bid = db.query('SELECT id FROM bookings WHERE email = ? AND element_id = ?', ('ola@example.test', d_team), one=True)['id']
r = api(jan, '/api/cancel', id=bid)
check(r.status_code == 403, 'cannot cancel somebody else\'s booking')
r = api(mgr, '/api/cancel', id=bid)
check(r.status_code == 200, 'manager cancels a member\'s booking')

# ── Languages ─────────────────────────────────────────────────────────────────
print('Languages')
check('My bookings' in jan.get(P + '/my').get_data(as_text=True), 'default language: English (no browser preference)')
r = jan.get(P + '/language/pl', headers={'Referer': P + '/my'})
check(r.status_code == 302 and 'lang=pl' in r.headers.get('Set-Cookie', ''), 'language switch sets the cookie')
check('Moje rezerwacje' in jan.get(P + '/my').get_data(as_text=True), 'interface in Polish after switching')
with app.app_context():
    check(db.query('SELECT language FROM users WHERE email = ?', ('jan@example.test',), one=True)['language'] == 'pl',
          'the language is remembered on the account')
j2, _ = log_in('jan@example.test', 'JanPass12345')
check('Moje rezerwacje' in j2.get(P + '/my').get_data(as_text=True), 'the account language applies in a new session')
r = api(jan, '/api/book', element_id=d_1, date=D)
check('Zajęte (cały dzień, Ty)' in (r.get_json()['results'][0]['error'] or ''), 'API messages in the user\'s language')
c_pl = app.test_client()
check('Rezerwacja biurek' in c_pl.get(P + '/login', headers={'Accept-Language': 'pl-PL,pl;q=0.9'}).get_data(as_text=True),
      'login screen follows the browser language')
jan.get(P + '/language/en')

# ── API key and REST ──────────────────────────────────────────────────────────
print('API key and REST')
r = post(jan, '/account/key', action='new')
KEY = re.search(r'(rzb_[\w-]+)', r.get_data(as_text=True)).group(1)
check(r.status_code == 200 and KEY, 'API key generated')
with jan.session_transaction() as s:
    check(KEY not in json.dumps(dict(s)), 'the key does not go into the session cookie')
with app.app_context():
    w = db.query('SELECT api_key, api_key_hash FROM users WHERE email = ?', ('jan@example.test',), one=True)
check(w['api_key'].startswith('v1:') and KEY not in w['api_key'], 'the key is encrypted in the database')
r = post(jan, '/account/key', action='show', password='wrong')
check(KEY not in r.get_data(as_text=True), 'showing the key requires the password')
r = post(jan, '/account/key', action='show', password='JanPass12345')
check(KEY in r.get_data(as_text=True), 'showing the key after confirming with the password')
r = jan.post(P + '/account/key', data={'action': 'show'}, headers={'X-CSRF-Token': jan.csrf, 'X-Requested-With': 'fetch'})
check(r.status_code == 403 and r.get_json().get('error'), 'from the page (fetch): JSON error without the password')
x = app.test_client()
H = {'X-API-Key': KEY}
check(x.get(P + '/api/v1/me').status_code == 401, 'REST without a key: 401')
check(x.get(P + '/api/v1/me', headers={'X-API-Key': 'rzb_bad'}).status_code == 401, 'REST with a wrong key: 401')
check(x.post(P + '/api/v1/bookings', json={'element_id': 1}).status_code == 401, 'REST write without a key or session: 401')
r = x.get(P + '/api/v1/me', headers=H)
check(r.status_code == 200 and r.get_json()['email'] == 'jan@example.test', 'REST /me with a key (no CSRF, no session)')
r = x.get(P + f'/api/v1/free?date={D}&type=desk', headers=H)
free = [m['code'] for m in r.get_json()['places']]
check('T-1' not in free and 'T-Z' not in free and 'T-2' not in free,
      'REST /free: no taken desks, no desks of another team, no second desk on the same day')
D2 = (d1 + timedelta(days=7)).isoformat()
r = x.post(P + '/api/v1/bookings', headers=H, json={'place': 'T-2', 'floor': fid, 'date': D2})
check(r.status_code == 201, 'REST: booking by the place code')
new = r.get_json()['ids'][0]
r = x.get(P + '/api/v1/bookings', headers=H)
check(any(z['id'] == new for z in r.get_json()['upcoming']), 'REST: my bookings')
r = x.delete(P + f'/api/v1/bookings/{new}', headers=H)
check(r.status_code == 200, 'REST: cancel (DELETE)')
check(x.get(P + '/api/v1/admin/bookings', headers=H).status_code == 403, 'REST: a user key has no admin access')
r = post(a, '/account/key', action='new')
ADMIN_KEY = re.search(r'(rzb_[\w-]+)', r.get_data(as_text=True)).group(1)
r = x.get(P + f'/api/v1/admin/bookings?from={D}&to={D}', headers={'Authorization': 'Bearer ' + ADMIN_KEY})
check(r.status_code == 200 and len(r.get_json()['bookings']) >= 3, 'REST: the administrator key sees everything')

# ── MCP ───────────────────────────────────────────────────────────────────────
print('MCP')


def mcp(method, params=None, key=KEY, id_=1):
    msg = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
    if id_ is not None:
        msg['id'] = id_
    return x.post(P + '/mcp', json=msg, headers={'Authorization': 'Bearer ' + key,
                                                 'Accept': 'application/json, text/event-stream'})


check(x.post(P + '/mcp', json={}).status_code == 401, 'MCP without a key: 401')
r = mcp('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '1'}})
check(r.get_json()['result']['serverInfo']['name'] == 'desk-booking', 'MCP initialize')
check(mcp('notifications/initialized', id_=None).status_code == 202, 'MCP notification -> 202')
tools = {t['name'] for t in mcp('tools/list').get_json()['result']['tools']}
check({'find_free_places', 'book', 'cancel_bookings', 'my_bookings'} <= tools and 'all_bookings' not in tools,
      'MCP tools/list (no admin tools for a user)')
admin_tools = {t['name'] for t in mcp('tools/list', key=ADMIN_KEY).get_json()['result']['tools']}
check('all_bookings' in admin_tools, 'MCP: the administrator has the admin tools')


def tool(name, **args):
    return mcp('tools/call', {'name': name, 'arguments': args}).get_json()['result']


w = tool('find_free_places', date=D2, type='desk')
check(not w['isError'] and any(m['code'] == 'T-3' for m in w['structuredContent']['places']), 'MCP find_free_places')
w = tool('book', place='T-3', floor=fid, date=D2)
check(not w['isError'] and w['structuredContent']['booked'] == 1, 'MCP book')
w = tool('book', place='T-Z', floor=fid, date=D2)
check(w['isError'] and 'already have a booking' in w['content'][0]['text'], 'MCP: second desk on the same day -> tool error')
w = tool('my_bookings')
ids = [z['id'] for z in w['structuredContent']['upcoming'] if z['date'] == D2]
check(len(ids) == 1, 'MCP my_bookings')
w = tool('cancel_bookings', ids=ids)
check(not w['isError'] and w['structuredContent']['cancelled'] == ids, 'MCP cancel_bookings')
w = tool('floor_plan', floor=fid, date=D)
check(any(m['status'] == 'mine' for m in w['structuredContent']['places']), 'MCP floor_plan')
w = tool('find_person', q='Nowak', date=D)
check(w['structuredContent']['results'][0]['code'] == 'T-1', 'MCP find_person')
check(tool('team_week', date=D)['structuredContent']['groups'], 'MCP team_week')
r = x.post(P + '/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
           headers={'Authorization': 'Bearer ' + KEY, 'Origin': 'https://bad.example'})
check(r.status_code == 403, 'MCP: a foreign Origin is rejected')
post(a, '/admin/settings/api', api='1', rate_limit='120')
check(mcp('ping').status_code == 403, 'MCP switched off by the administrator')
post(a, '/admin/settings/api', api='1', mcp='1', rate_limit='120')

# ── SSO (simulated OpenID Connect provider) ──────────────────────────────────
print('SSO')
import jwt as pyjwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

import sso  # noqa: E402

rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
META = {'issuer': 'https://idp.test', 'authorization_endpoint': 'https://idp.test/auth',
        'token_endpoint': 'https://idp.test/token', 'jwks_uri': 'https://idp.test/jwks'}
sso.metadata = lambda p, c: META
sso._jwks_client = lambda p, c: type('K', (), {'get_signing_key_from_jwt': lambda s, t: type(
    'S', (), {'key': rsa_key.public_key()})()})()
post(a, '/admin/settings/sso/oidc', enabled='1', name='Test IdP', discovery_url='https://idp.test',
     client_id='client', client_secret='sso-secret', domains='example.test', auto_create='1')
with app.app_context():
    raw = db.setting('sso_oidc')
check('sso-secret' not in raw and '"v1:' in raw, 'the SSO client secret is encrypted in the database')
check('Log in with Test IdP' in client().get(P + '/login').get_data(as_text=True), 'SSO button on the login screen')


def sso_login(email, **claims):
    s = client()
    r = s.get(P + '/sso/oidc')
    q = parse_qs(urlparse(r.location).query)
    check(q['code_challenge_method'] == ['S256'] and q['redirect_uri'][0].endswith('/sso/oidc/callback'),
          'SSO: redirect with PKCE and the redirect URI')
    token = pyjwt.encode({'iss': 'https://idp.test', 'aud': 'client', 'sub': 'id-' + email, 'email': email,
                          'email_verified': True, 'given_name': 'Eva', 'family_name': 'Sso', 'nonce': q['nonce'][0],
                          'iat': int(time.time()), 'exp': int(time.time()) + 300, **claims}, rsa_key, algorithm='RS256')
    sso.requests.post = lambda *a, **k: type('O', (), {'status_code': 200, 'json': lambda s: {'id_token': token}})()
    return s, s.get(P + '/sso/oidc/callback', query_string={'code': 'abc', 'state': q['state'][0]})


s, r = sso_login('eva@example.test')
check(r.status_code == 302 and s.get(P + '/').status_code == 200, 'SSO: login and an automatic account')
_, r = sso_login('someone@other.test')
check(r.status_code == 400, 'SSO: a domain outside the list is rejected')
_, r = sso_login('eva@example.test', nonce='forged')
check(r.status_code == 400, 'SSO: a wrong nonce is rejected')
_, r = sso_login('eva@example.test', aud='another-client')
check(r.status_code == 400, 'SSO: a token for another client is rejected')

# ── Settings: mail, switching the database ────────────────────────────────────
print('Settings')
post(a, '/admin/settings/mail', host='smtp.test', port='587', mode='starttls', login='u', password='smtp-password',
     sender='desks@example.test')
with app.app_context():
    check('smtp-password' not in db.setting('mail'), 'the SMTP password is encrypted in the database')
check(a.get(P + '/admin/settings?tab=database').status_code == 200, 'settings page')
post(a, '/admin/settings/general', default_language='pl')
check('Rezerwacja biurek' in app.test_client().get(P + '/login').get_data(as_text=True), 'default language set by the administrator')
post(a, '/admin/settings/general', default_language='auto')
post(a, '/admin/settings/theme', preset='green')
check('--brand:#15803d' in a.get(P + '/my').get_data(as_text=True), 'colour preset applied to every page')
post(a, '/admin/settings/theme', header='#0b2f5b', main='#ffff99', accent='#0d9488')
check('--brand:#15803d' in a.get(P + '/my').get_data(as_text=True), 'too light main colour rejected (white text unreadable)')
post(a, '/admin/settings/theme', header='#101820', main='#7c3aed', accent='#db2777')
page = a.get(P + '/my').get_data(as_text=True)
check('--navy:#101820' in page and '--accent2:#db2777' in page, 'own colours saved')
post(a, '/admin/settings/theme', preset='blue')
if not ARGS.db:
    second = os.path.join(TMP, 'second.sqlite')
    r = post(a, '/admin/settings/database', type='sqlite', path=second, data='copy', action='save')
    check(db.configuration()['path'] == second, 'switched to another database with a copy of the data')
    check(a.get(P + '/my').status_code == 200, 'after switching the session and data work')
    with app.app_context():
        n = db.query('SELECT COUNT(*) AS n FROM bookings WHERE date = ?', (D,), one=True)['n']
    check(n >= 3, 'bookings copied to the new database')

print()
if FAILED:
    print(f'FAILED: {len(FAILED)}')
    sys.exit(1)
print('All OK')
