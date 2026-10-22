"""MCP server (Model Context Protocol) - booking through an AI assistant.

An assistant (Claude, ChatGPT, Copilot... - any MCP client with the HTTP transport) connects to
{APP_URL}/mcp with the header `Authorization: Bearer <API key>`. The tools act with the
permissions of the key owner, exactly like in the browser: a user books for themselves (a manager
also for their team), an administrator sees all bookings. Messages use the owner's language.

Transport: Streamable HTTP - POST with a JSON-RPC 2.0 message, JSON response (no SSE stream:
tools answer immediately). Protocol version 2025-06-18 (older versions accepted).
"""
import json
from datetime import date, timedelta
from urllib.parse import urlparse

from flask import Blueprint, Response, g, request

import booking
import config
import i18n
import permissions as perm

mcp_bp = Blueprint('mcp', __name__)

VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
SERVER = {'name': 'desk-booking', 'title': 'Desk booking', 'version': '2.0'}

_DATE = {'type': 'string', 'description': 'Date YYYY-MM-DD'}
_TIME = {'type': 'string', 'description': 'Time HH:MM (empty = the whole day)'}
_PLACE = {'type': ['string', 'integer'],
          'description': 'Place id or its code / name, e.g. "A-12", "Room Wisła"'}


def _tool(name, description, properties=None, required=(), read_only=True, destructive=False):
    return {
        'name': name,
        'description': description,
        'inputSchema': {'type': 'object', 'properties': properties or {}, 'required': list(required),
                        'additionalProperties': False},
        'annotations': {'readOnlyHint': read_only, 'destructiveHint': destructive, 'openWorldHint': False},
    }


TOOLS = [
    _tool('whoami', 'Who the logged-in person is: role, teams, whom they can book for.'),
    _tool('list_floors', 'Floors / office locations with ids.'),
    _tool('find_free_places', 'Places (desks, meeting rooms, booths) I can book on a day and in given hours.', {
        'date': _DATE, 'floor': {'type': 'integer', 'description': 'Floor id (optional)'},
        'type': {'type': 'string', 'enum': ['desk', 'room', 'booth']},
        'time_from': _TIME, 'time_to': _TIME,
        'min_capacity': {'type': 'integer', 'description': 'Minimum number of seats in a room'},
        'for': {'type': 'string', 'description': 'E-mail of the person I book for (manager / administrator)'},
    }, ['date']),
    _tool('floor_plan', 'Status of every place on a floor on a day: free, taken (by whom), mine.', {
        'floor': {'type': 'integer'}, 'date': _DATE}, ['floor']),
    _tool('place_availability', 'Is a given place free, day by day in a date range.', {
        'place': _PLACE, 'floor': {'type': 'integer'}, 'date_from': _DATE, 'date_to': _DATE,
        'time_from': _TIME, 'time_to': _TIME}, ['place', 'date_from']),
    _tool('book', 'Books a place for a day or a date range (Mon-Fri by default), optionally for hours. '
          'Returns the result for every day.', {
              'place': _PLACE, 'floor': {'type': 'integer'},
              'date': _DATE, 'date_to': {'type': 'string', 'description': 'Last day of a range (optional)'},
              'weekdays': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0, 'maximum': 6},
                           'description': 'Weekdays in the range, 0 = Monday'},
              'time_from': _TIME, 'time_to': _TIME,
              'title': {'type': 'string', 'description': 'Meeting subject (rooms)'},
              'for': {'type': 'string', 'description': 'E-mail of the person I book for'},
          }, ['place', 'date'], read_only=False),
    _tool('my_bookings', 'My upcoming (and recent) bookings with ids for cancelling.', {
        'from': _DATE, 'to': _DATE}),
    _tool('cancel_bookings', 'Cancels the bookings with the given ids.', {
        'ids': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1}}, ['ids'],
        read_only=False, destructive=True),
    _tool('place_schedule', 'Bookings of one place (e.g. a meeting room) for the next days.', {
        'place': _PLACE, 'floor': {'type': 'integer'}, 'from': _DATE,
        'days': {'type': 'integer', 'minimum': 1, 'maximum': 31}}, ['place']),
    _tool('find_person', 'At which desk a person sits on a day (search by name or e-mail).', {
        'q': {'type': 'string'}, 'date': _DATE}, ['q']),
    _tool('team_week', 'Who from my team is in the office on which day of the week containing the date.', {
        'date': _DATE, 'group': {'type': 'integer', 'description': 'Number of another team from other_groups'}}),
]
ADMIN_TOOLS = [
    _tool('all_bookings', 'Administrator: all bookings in a date range with filters.', {
        'from': _DATE, 'to': _DATE, 'floor': {'type': 'integer'},
        'type': {'type': 'string', 'enum': ['desk', 'room', 'booth']},
        'q': {'type': 'string', 'description': 'Person, place or subject'}}),
]


def _instructions():
    today = date.today()
    return (f'Desk, meeting room and booth booking system. Today is {today.strftime("%A")} {today.isoformat()}. '
            f'Logged in as {g.user["display_name"] or g.user["email"]} ({perm.ROLES.get(g.role, g.role)}); '
            f'preferred language: {i18n.LANGUAGES.get(i18n.current())}. '
            'Give dates as YYYY-MM-DD and times as HH:MM. To book, first check find_free_places '
            '(or list_floors + floor_plan), then call book with the place id. '
            'Before cancelling, get the ids from my_bookings and confirm with the user.')


def _place(a):
    return booking.find_place(a.get('place'), a.get('floor'))


def _plan(a):
    p = booking.plan(a['floor'], a.get('date'))
    places = []
    for e in p['elements']:
        if e['type'] not in perm.BOOKABLE:
            continue
        places.append({k: v for k, v in {
            'id': e['id'], 'name': e['name'], 'status': e['status'], 'who': e.get('who'),
            'reason': e.get('reason'), 'capacity': e.get('capacity'),
            'teams': [t['name'] for t in e['teams']] or None,
            'bookings': [{'id': r['id'], 'who': r['who'], 'hours': booking.hours_text(r['from'], r['to']),
                          'title': r['title']} for r in e['bookings']] or None,
        }.items() if v not in (None, [])})
    return {'floor': p['floor']['name'], 'date': p['date'], 'places': places,
            'legend': 'free, partial (part of the day taken), mine, taken, team (someone from my team), '
                      'assigned (permanent desk), unavailable'}


def _mine(a):
    m = booking.my_bookings(start=a.get('from'), end=a.get('to'), limit=50)
    fields = ('id', 'date', 'hours', 'place', 'floor', 'title')
    return {'upcoming': [{k: r[k] for k in fields} for r in m['upcoming']],
            'recent': [{k: r[k] for k in fields} for r in m['past'][:10]],
            'permanent_desks': [f"{s['code']} ({s['floor']})" for s in m['fixed']]}


def _all(a):
    if not perm.is_admin():
        raise booking.BookingError('Administrators only.', 403)
    today = date.today()
    rows, truncated = booking.all_bookings(a.get('from') or today.isoformat(),
                                           a.get('to') or (today + timedelta(days=7)).isoformat(),
                                           a.get('floor'), a.get('type'), None, a.get('q'), limit=300)
    fields = ('id', 'date', 'hours', 'place', 'floor', 'person', 'email', 'title')
    return {'bookings': [{k: r[k] for k in fields} for r in rows], 'truncated': truncated}


def _call(name, a):
    from routes.api_v1 import floors, me, week_json
    if name == 'whoami':
        return me()
    if name == 'list_floors':
        return floors()
    if name == 'find_free_places':
        return booking.free_places(a.get('date'), a.get('floor'), a.get('type'), a.get('time_from'),
                                   a.get('time_to'), a.get('min_capacity'), a.get('for'))
    if name == 'floor_plan':
        return _plan(a)
    if name == 'place_availability':
        e = _place(a)
        return {'place': booking.place_name(e), **booking.availability(e['id'], a)}
    if name == 'book':
        e = _place(a)
        result = booking.book({**a, 'element_id': e['id']})
        if not result['booked']:
            # No day succeeded - the assistant must see an error, not a "success" with refusals inside.
            raise booking.BookingError(f"{result['place']}: " + '; '.join(
                f"{r['date']}: {r['error']}" for r in result['results']))
        return result
    if name == 'my_bookings':
        return _mine(a)
    if name == 'cancel_bookings':
        return booking.cancel(a.get('ids'))
    if name == 'place_schedule':
        s = booking.schedule(_place(a)['id'], a.get('from'), a.get('days') or 7)
        return {k: s[k] for k in ('place', 'from', 'to', 'bookings')}
    if name == 'find_person':
        return {'results': booking.find_person(a.get('q'), a.get('date'))}
    if name == 'team_week':
        return week_json(booking.team_week(a.get('date'), int(a.get('group') or 0)))
    if name == 'all_bookings':
        return _all(a)
    raise KeyError(name)


def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def _available_tools():
    return TOOLS + (ADMIN_TOOLS if perm.is_admin() else [])


def _handle(msg):
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or 'method' not in msg:
        return _error(msg.get('id') if isinstance(msg, dict) else None, -32600, 'Invalid JSON-RPC request.')
    method, id_, params = msg['method'], msg.get('id'), msg.get('params') or {}
    if 'id' not in msg:
        return None                                   # a notification (e.g. notifications/initialized)
    if method == 'initialize':
        version = params.get('protocolVersion')
        return _result(id_, {'protocolVersion': version if version in VERSIONS else VERSIONS[0],
                             'capabilities': {'tools': {'listChanged': False}},
                             'serverInfo': SERVER, 'instructions': _instructions()})
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': _available_tools()})
    if method == 'tools/call':
        name, args = params.get('name'), params.get('arguments') or {}
        if name not in {t['name'] for t in _available_tools()}:
            return _error(id_, -32602, f'Unknown tool: {name}')
        try:
            out = _call(name, args)
            if isinstance(out, tuple):
                out = out[0]
            return _result(id_, {'content': [{'type': 'text', 'text': json.dumps(out, ensure_ascii=False, default=str)}],
                                 'structuredContent': out, 'isError': False})
        except booking.BookingError as e:
            return _result(id_, {'content': [{'type': 'text', 'text': e.message}], 'isError': True})
        except (TypeError, ValueError, KeyError) as e:
            return _result(id_, {'content': [{'type': 'text', 'text': f'Invalid arguments: {e}'}], 'isError': True})
    if method in ('resources/list', 'prompts/list'):
        return _result(id_, {method.split('/')[0]: []})
    return _error(id_, -32601, f'Unsupported method: {method}')


def _origin_allowed():
    """DNS rebinding protection: a request from a browser must come from the application's address."""
    origin = request.headers.get('Origin')
    if not origin:
        return True
    return urlparse(origin).netloc == urlparse(config.APP_URL).netloc


@mcp_bp.route('', methods=['POST', 'GET', 'DELETE'], strict_slashes=False)
def mcp():
    if not _origin_allowed():
        return {'error': 'Origin not allowed.'}, 403
    if request.method != 'POST':
        return Response(status=405, headers={'Allow': 'POST'})
    try:
        msg = json.loads(request.get_data(as_text=True) or 'null')
    except ValueError:
        return _error(None, -32700, 'Invalid JSON.'), 400
    if isinstance(msg, list):                         # batches from older protocol versions
        answers = [a for a in (_handle(m) for m in msg) if a]
        return (json.dumps(answers, ensure_ascii=False, default=str), 200,
                {'Content-Type': 'application/json'}) if answers else Response(status=202)
    answer = _handle(msg)
    if answer is None:
        return Response(status=202)
    return Response(json.dumps(answer, ensure_ascii=False, default=str), mimetype='application/json',
                    headers={'MCP-Protocol-Version': VERSIONS[0]})
