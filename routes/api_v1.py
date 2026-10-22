"""REST API v1 - integrations and scripts.

Authentication: header `X-API-Key: rzb_...` or `Authorization: Bearer rzb_...` (key from
"My account"). The key acts with the permissions of its owner: an administrator's key sees and
cancels everything, a user's key - only what that user can in the browser. It also works from a
browser session (with the CSRF token).

JSON responses; an error = {"error": "..."} with a 4xx status. Description: GET /api/v1.
"""
import functools
from datetime import date, timedelta

from flask import Blueprint, g, request

import booking
import config
import db
import permissions as perm
from auth import admin_required, login_required

api_bp = Blueprint('api_v1', __name__)


def _json(f):
    @functools.wraps(f)
    def wrapper(*a, **kw):
        try:
            return f(*a, **kw)
        except booking.BookingError as e:
            return {'error': e.message}, e.status
    return wrapper


def _data():
    return request.get_json(silent=True) or request.form.to_dict() or {}


ENDPOINTS = [
    ('GET', '/me', 'The account the key belongs to'),
    ('GET', '/floors', 'Active floors'),
    ('GET', '/plan?floor=ID&date=YYYY-MM-DD[&for=email]', 'Floor plan with the status of every place'),
    ('GET', '/free?date=YYYY-MM-DD[&floor=ID&type=desk|room|booth&time_from=HH:MM&time_to=HH:MM&capacity=N&for=email]',
     'Places free for me'),
    ('GET', '/places/ID/availability?date_from=..[&date_to=..&time_from&time_to]', 'Availability of a place day by day'),
    ('GET', '/places/ID/schedule[?from=YYYY-MM-DD&days=7]', 'Bookings of a place over a range of days'),
    ('GET', '/bookings[?from=..&to=..]', 'My bookings (upcoming, past, permanent desks)'),
    ('POST', '/bookings', 'Book: {"element_id"|"place", "date"|"date_from"+"date_to", "weekdays", '
                          '"time_from", "time_to", "title", "for"}'),
    ('DELETE', '/bookings/ID', 'Cancel a booking'),
    ('GET', '/people?q=text[&date=..]', 'Where does a person sit on a day'),
    ('GET', '/team[?date=..&group=N]', 'Who from my team is in the office this week'),
    ('GET', '/admin/bookings?from=..&to=..[&floor&type&team&q&limit]', 'All bookings (administrator)'),
    ('GET', '/admin/users[?q=..]', 'Accounts (administrator)'),
]


@api_bp.route('')
@api_bp.route('/')
def describe():
    return {'name': 'Desk booking API', 'version': 1, 'url': config.APP_URL + '/api/v1',
            'authentication': 'X-API-Key: <key> or Authorization: Bearer <key>',
            'endpoints': [{'method': m, 'path': p, 'description': d} for m, p, d in ENDPOINTS]}


@api_bp.route('/me')
@login_required
def me():
    u = g.user
    return {'email': u['email'], 'first_name': u['first_name'], 'last_name': u['last_name'], 'role': u['role'],
            'language': u['language'],
            'teams': [t['name'] for t in perm.all_teams() if perm.in_team(u['email'], t)],
            'can_book_for': sorted(perm.managed_people(u['email']))}


@api_bp.route('/floors')
@login_required
def floors():
    return {'floors': [{k: f[k] for k in ('id', 'name', 'location')} for f in booking.floors()]}


@api_bp.route('/plan')
@login_required
@_json
def plan():
    return booking.plan(request.args.get('floor', type=int), request.args.get('date'), request.args.get('for'))


@api_bp.route('/free')
@login_required
@_json
def free():
    a = request.args
    return booking.free_places(a.get('date') or date.today().isoformat(), a.get('floor', type=int), a.get('type'),
                               a.get('time_from'), a.get('time_to'), a.get('capacity', type=int), a.get('for'))


@api_bp.route('/places/<int:eid>/availability')
@login_required
@_json
def availability(eid):
    return booking.availability(eid, request.args)


@api_bp.route('/places/<int:eid>/schedule')
@login_required
@_json
def schedule(eid):
    return booking.schedule(eid, request.args.get('from'), request.args.get('days', 7, type=int))


@api_bp.route('/bookings', methods=['GET'])
@login_required
@_json
def my_bookings():
    return booking.my_bookings(start=request.args.get('from'), end=request.args.get('to'))


@api_bp.route('/bookings', methods=['POST'])
@login_required
@_json
def book():
    result = booking.book(_data())
    return result, 201 if result['booked'] else 409


@api_bp.route('/bookings/<int:bid>', methods=['DELETE'])
@login_required
@_json
def cancel(bid):
    return booking.cancel([bid])


@api_bp.route('/people')
@login_required
def people():
    return {'results': booking.find_person(request.args.get('q'), request.args.get('date'))}


@api_bp.route('/team')
@login_required
def team():
    return week_json(booking.team_week(request.args.get('date'), request.args.get('group', 0, type=int)))


def week_json(w):
    days = [d.isoformat() for d in w['days']]
    groups = []
    for gr in w['groups']:
        people = []
        for p in gr['people']:
            per_day = {}
            for d in days:
                r = w['desks'].get((p['email'], d))
                per_day[d] = {
                    'desk': (r['code'] + (f" {r['time_from']}–{r['time_to']}" if r['time_from'] else '')) if r
                    else (p['fixed_code'] and f"{p['fixed_code']} (fixed)") or None,
                    'rooms': [f"{x['place']} {booking.hours_text(x['time_from'], x['time_to'])}"
                              for x in w['rooms'].get((p['email'], d), [])],
                }
            people.append({'name': p['name'], 'email': p['email'], 'days': per_day})
        groups.append({'team': gr['name'], 'in_office': dict(zip(days, gr['in_office'])), 'people': people})
    return {'days': days, 'groups': groups,
            'other_groups': [{'number': i, 'name': n, 'people': k} for i, n, k in w['candidates']]}


@api_bp.route('/admin/bookings')
@admin_required
def admin_bookings():
    a = request.args
    today = date.today()
    rows, truncated = booking.all_bookings(a.get('from') or today.isoformat(),
                                           a.get('to') or (today + timedelta(days=30)).isoformat(),
                                           a.get('floor', type=int), a.get('type'), a.get('team', type=int),
                                           a.get('q'), limit=min(a.get('limit', 500, type=int), 5000))
    return {'bookings': rows, 'truncated': truncated}


@api_bp.route('/admin/users')
@admin_required
def admin_users():
    q = (request.args.get('q') or '').strip().lower()
    sql = 'SELECT email, first_name, last_name, role, active, language, last_login FROM users'
    params = ()
    if q:
        sql += ' WHERE LOWER(display_name) LIKE ? OR email LIKE ?'
        params = (f'%{q}%', f'%{q}%')
    return {'users': [dict(r) for r in db.query(sql + ' ORDER BY display_name LIMIT 1000', params)]}
