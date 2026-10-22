"""Booking logic shared by the web UI, the REST API (/api/v1) and the MCP server.

Functions act on behalf of the logged-in person (g.user, g.role) - whether the request came
from the browser (session), with an API key or from an assistant over MCP.
A business error = BookingError with a message for people (translated) and an HTTP status.
"""
import re
from datetime import date, datetime, timedelta

from flask import g

import config
import db
import i18n
import notifications
import permissions as perm
from i18n import _

FULL_DAY = ('00:00', '24:00')
TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
TYPE_NAMES = {'desk': 'Desk', 'room': 'Room', 'booth': 'Booth'}


class BookingError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def parse_date(txt, default=None):
    if isinstance(txt, date):
        return txt
    try:
        return datetime.strptime(txt, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return default


def background_url(floor):
    return (config.APP_PREFIX + '/static/uploads/' + floor['background']) if floor['background'] else None


def place_name(e):
    """"Desk A-01", "Room Wisła", "Booth 2" - for messages and e-mails (current language)."""
    kind = _(TYPE_NAMES.get(e['type'], ''))
    if e['type'] == 'desk':
        return f"{kind} {e['code'] or ''}".strip()
    name = (e['label'] or e['code'] or '').strip()
    # "Sala Wisła" stays "Sala Wisła", not "Sala Sala Wisła"
    return name if name.lower().startswith(kind.lower()) else f'{kind} {name}'.strip()


def hours_text(time_from, time_to):
    return _('all day') if not time_from else f'{time_from}–{time_to}'


def _interval(r):
    return (r['time_from'] or FULL_DAY[0], r['time_to'] or FULL_DAY[1])


def _overlap(a, b):
    """'HH:MM' intervals (end exclusive) - 9:00–10:00 and 10:00–11:00 do not collide."""
    return a[0] < b[1] and b[0] < a[1]


def date_problem(d, time_to=None):
    """Error message or None."""
    today = date.today()
    if d < today:
        return _('Bookings cannot be made in the past.')
    if d == today and time_to and time_to <= datetime.now().strftime('%H:%M'):
        return _('These hours have already passed.')
    if not perm.is_admin() and d > today + timedelta(days=config.MAX_DAYS_AHEAD):
        return _('Bookings at most {n} days ahead.', n=config.MAX_DAYS_AHEAD)
    return None


def hours(data):
    """(from, to) from a request; (None, None) = the whole day. Raises BookingError."""
    t_from, t_to = (data.get('time_from') or '').strip(), (data.get('time_to') or '').strip()
    if not t_from and not t_to:
        return None, None
    if not (TIME_RE.match(t_from) and TIME_RE.match(t_to)):
        raise BookingError(_('Enter the hours as HH:MM.'))
    if t_from >= t_to:
        raise BookingError(_('The end time must be later than the start time.'))
    if t_from < config.BOOKING_HOURS_FROM or t_to > config.BOOKING_HOURS_TO:
        raise BookingError(_('Hourly bookings between {start} and {end}.', start=config.BOOKING_HOURS_FROM,
                             end=config.BOOKING_HOURS_TO))
    return t_from, t_to


def _collision(conn, e, for_email, d, interval, me):
    """Collision description or None. Called inside the transaction before saving.

    1. The same place in overlapping hours.
    2. The same person: two desks at a time, or two rooms/booths at a time (a desk + a room is fine).
    """
    for r in conn.execute("""SELECT b.time_from, b.time_to, b.email, u.display_name FROM bookings b
                             LEFT JOIN users u ON u.email = b.email
                             WHERE b.element_id = ? AND b.date = ?""", (e['id'], d)):
        if _overlap(interval, _interval(r)):
            who = _('you') if r['email'] == me else (r['display_name'] or r['email'])
            return _('Taken ({hours}, {who}).', hours=hours_text(r['time_from'], r['time_to']), who=who)
    group = ('desk',) if e['type'] == 'desk' else ('room', 'booth')
    ph = ','.join('?' * len(group))
    for r in conn.execute(f"""SELECT b.time_from, b.time_to, el.type, el.code, el.label FROM bookings b
                              JOIN elements el ON el.id = b.element_id
                              WHERE b.email = ? AND b.date = ? AND b.element_id <> ? AND el.type IN ({ph})""",
                          (for_email, d, e['id'], *group)):
        if _overlap(interval, _interval(r)):
            text = 'You already have a booking: {place} ({hours}).' if for_email == me \
                else 'This person already has a booking: {place} ({hours}).'
            return _(text, place=place_name(r), hours=hours_text(r['time_from'], r['time_to']))
    return None


def my_people(email):
    """E-mails highlighted on the plan: members of my teams."""
    out = set(perm.managed_people(email))
    mine = perm.teams_of(email)
    for t in perm.all_teams():
        if t['id'] in mine:
            out.update(perm.team_members(t))
    out.discard(email)
    return out


def booking_for(email):
    """E-mail of the person we book for, or BookingError 403."""
    me = g.user['email']
    email = (email or me).strip().lower()
    if not perm.can_book_for(me, email):
        raise BookingError(_('You cannot book for this person.'), 403)
    return email


# ─────────────────────────────────────────────────────────────────────────────
# Reading
# ─────────────────────────────────────────────────────────────────────────────

def floors():
    return [dict(r) for r in db.query('SELECT * FROM floors WHERE active = 1 ORDER BY sort_order, name')]


def floor(floor_id):
    f = db.query('SELECT * FROM floors WHERE id = ?', (floor_id,), one=True)
    if not f or (not f['active'] and not perm.is_editor()):
        raise BookingError(_('No such floor.'), 404)
    return f


def find_place(ref, floor_id=None):
    """A place by id or by code / name ("A-01", "Room Wisła")."""
    if ref is None or ref == '':
        raise BookingError(_('Give the place (id or code).'))
    if isinstance(ref, int) or str(ref).isdigit():
        e = db.query('SELECT * FROM elements WHERE id = ?', (int(ref),), one=True)
        if e:
            return e
    text = str(ref).strip().lower()
    bare = text
    for kind in ('desk', 'room', 'booth'):
        for word in {kind, _(TYPE_NAMES[kind]).lower()}:
            if text.startswith(word + ' ') and len(text) > len(word) + 1:
                bare = text[len(word) + 1:]
    where, params = '', []
    if floor_id:
        where, params = ' AND e.floor_id = ?', [floor_id]
    candidates = db.query(f"""SELECT e.* FROM elements e JOIN floors f ON f.id = e.floor_id
                              WHERE f.active = 1 AND e.type IN ('desk', 'room', 'booth')
                                AND (LOWER(e.code) IN (?, ?) OR LOWER(e.label) IN (?, ?)){where}""",
                          [text, bare, text, bare] + params)
    if not candidates:
        raise BookingError(_('Place "{ref}" not found.', ref=ref), 404)
    if len(candidates) > 1:
        raise BookingError(_('"{ref}" exists on several floors - give the floor or the place id.', ref=ref), 409)
    return candidates[0]


def plan(floor_id, d, for_email=None):
    """Floor plan on a day with the status of every place from the point of view of `for_email`."""
    f = floor(floor_id)
    d = parse_date(d, date.today())
    me = g.user['email']
    try:
        for_email = booking_for(for_email)
    except BookingError:
        for_email = me
    admin = perm.is_admin()
    teams_of_person = perm.teams_of(for_email)
    el_teams = perm.element_teams(floor_id, with_source=True)
    zone_teams = {}
    for r in db.query("""SELECT et.element_id, et.team_id FROM element_teams et
                         JOIN elements e ON e.id = et.element_id
                         WHERE e.floor_id = ? AND e.type = 'zone'""", (floor_id,)):
        zone_teams.setdefault(r['element_id'], set()).add(r['team_id'])
    teams = {t['id']: t for t in perm.all_teams()}
    mine_people = my_people(me)
    can_cancel_for = perm.managed_people(me)
    bookings = {}
    for r in db.query("""SELECT b.id, b.element_id, b.email, b.time_from, b.time_to, b.title, u.display_name
                         FROM bookings b JOIN elements e ON e.id = b.element_id
                         LEFT JOIN users u ON u.email = b.email
                         WHERE e.floor_id = ? AND b.date = ?
                         ORDER BY COALESCE(b.time_from, '')""", (floor_id, d.isoformat())):
        bookings.setdefault(r['element_id'], []).append(r)
    assigned = {r['email']: r['display_name'] for r in db.query(
        """SELECT u.email, u.display_name FROM elements e JOIN users u ON u.email = e.assigned_email
           WHERE e.floor_id = ?""", (floor_id,))}
    day_problem = date_problem(d)

    out = []
    for e in db.query('SELECT * FROM elements WHERE floor_id = ? ORDER BY id', (floor_id,)):
        el = {k: e[k] for k in ('id', 'type', 'code', 'label', 'x', 'y', 'w', 'h', 'rotation',
                                'color', 'equipment', 'capacity')}
        if e['type'] == 'zone':
            el['team_names'] = [teams[i]['name'] for i in sorted(zone_teams.get(e['id'], ())) if i in teams]
            if not el['color'] and el['team_names']:
                el['color'] = teams[sorted(zone_teams[e['id']])[0]]['color']
        if e['type'] in perm.BOOKABLE:
            team_ids, from_zone = el_teams.get(e['id'], (set(), None))
            el['name'] = place_name(e)
            el['from_zone'] = from_zone
            el['teams'] = [{'name': teams[i]['name'], 'color': teams[i]['color']} for i in team_ids if i in teams]
            ok, reason = perm.place_access(e, team_ids, for_email, teams_of_person, admin=admin)
            el['can_book'] = ok          # right to the place regardless of the day and hours
            items = bookings.get(e['id'], [])
            el['bookings'] = [{
                'id': r['id'], 'who': r['display_name'] or r['email'], 'from': r['time_from'], 'to': r['time_to'],
                'title': r['title'], 'mine': r['email'] == for_email,
                'can_cancel': r['email'] == me or admin or r['email'] in can_cancel_for,
            } for r in items]
            whole = next((r for r in items if not r['time_from']), None)
            if e['type'] == 'desk' and e['assigned_email']:
                el['who'] = assigned.get(e['assigned_email'], e['assigned_email'])
                el['status'] = 'assigned_mine' if e['assigned_email'] == for_email else 'assigned'
            elif whole:
                el['who'] = whole['display_name'] or whole['email']
                el['status'] = ('mine' if whole['email'] == for_email else
                                'team' if whole['email'] in mine_people else 'taken')
            elif any(r['email'] == for_email for r in items):
                el['status'] = 'mine'
            elif not ok or day_problem:
                el['status'] = 'unavailable'
                el['reason'] = reason or day_problem
            elif items:
                el['status'] = 'partial'      # hourly bookings, the rest of the day is free
            else:
                el['status'] = 'free'
        out.append(el)
    return {'floor': dict(f), 'date': d.isoformat(), 'for': for_email, 'elements': out, 'background': background_url(f)}


def free_places(d, floor_id=None, kind=None, time_from=None, time_to=None, min_capacity=None, for_email=None):
    """Places the person can book on a day (and hours)."""
    d = parse_date(d)
    if not d:
        raise BookingError(_('Give the date as YYYY-MM-DD.'))
    if kind and kind not in perm.BOOKABLE:
        raise BookingError(_('Place type: desk, room or booth.'))
    t_from, t_to = hours({'time_from': time_from, 'time_to': time_to})
    interval = (t_from, t_to) if t_from else FULL_DAY
    for_email = booking_for(for_email)
    problem = date_problem(d, t_to)
    if problem:
        raise BookingError(problem)
    conn = db.get_db()
    me = g.user['email']
    admin = perm.is_admin()
    teams_of_person = perm.teams_of(for_email)
    out = []
    for f in [f for f in floors() if not floor_id or f['id'] == int(floor_id)]:
        el_teams = perm.element_teams(f['id'])
        for e in db.query("""SELECT * FROM elements WHERE floor_id = ? AND type IN ('desk', 'room', 'booth')
                             ORDER BY type, code, label""", (f['id'],)):
            if kind and e['type'] != kind:
                continue
            if min_capacity and (e['capacity'] or 0) < int(min_capacity):
                continue
            ok, _reason = perm.place_access(e, el_teams.get(e['id'], set()), for_email, teams_of_person, admin=admin)
            if not ok or _collision(conn, e, for_email, d.isoformat(), interval, me):
                continue
            out.append({'id': e['id'], 'name': place_name(e), 'type': e['type'], 'code': e['code'],
                        'floor': f['name'], 'floor_id': f['id'], 'capacity': e['capacity'],
                        'equipment': e['equipment']})
    return {'date': d.isoformat(), 'hours': hours_text(t_from, t_to), 'for': for_email, 'places': out}


def date_range(data):
    """Dates from a from–to range, filtered by weekdays. Raises BookingError.

    `weekdays` - list 0..6 (0 = Monday); none = Mon–Fri (or every day when SKIP_WEEKENDS=false).
    A single day is always booked, also at the weekend.
    """
    start = parse_date(data.get('date_from') or data.get('date'))
    end = parse_date(data.get('date_to')) or start
    if not start or end < start:
        raise BookingError(_('Invalid date range.'))
    if (end - start).days > 62:
        raise BookingError(_('At most two months at a time.'))
    days = data.get('weekdays')
    if isinstance(days, str):
        days = [int(x) for x in days.split(',') if x.strip().isdigit()]
    if days:
        days = {int(x) for x in days if 0 <= int(x) <= 6}
    elif config.SKIP_WEEKENDS:
        days = {0, 1, 2, 3, 4}
    else:
        days = set(range(7))
    dates, d = [], start
    while d <= end:
        if start == end or d.weekday() in days:
            dates.append(d)
        d += timedelta(days=1)
    if not dates:
        raise BookingError(_('None of the chosen weekdays is in the selected range.'))
    return dates


def availability(element_id, data):
    """Status of a place day by day in a range (for the chosen hours) - preview before booking."""
    me = g.user['email']
    try:
        for_email = booking_for(data.get('for'))
    except BookingError:
        for_email = me
    e = db.query('SELECT * FROM elements WHERE id = ?', (element_id,), one=True)
    if not e:
        raise BookingError(_('No such place.'), 404)
    dates = date_range(data)
    t_from, t_to = hours(data)
    interval = (t_from, t_to) if t_from else FULL_DAY
    conn = db.get_db()
    days = []
    for d in dates:
        k = d.isoformat()
        entry = {'date': k, 'weekday': i18n.weekday(d)}
        problem = date_problem(d, t_to)
        own = conn.execute('SELECT time_from, time_to FROM bookings WHERE element_id = ? AND date = ? AND email = ?',
                           (e['id'], k, for_email)).fetchall()
        if any(_overlap(interval, _interval(r)) for r in own):
            entry['state'] = 'mine'
        elif problem:
            entry.update(state='outside', reason=problem)
        else:
            col = _collision(conn, e, for_email, k, interval, me)
            if col:
                # taken = this place is busy; other = the person already has another place then
                entry.update(state='taken' if _place_taken(conn, e, k, interval) else 'other', reason=col)
            else:
                entry['state'] = 'free'
        days.append(entry)
    return {'days': days, 'free': sum(1 for x in days if x['state'] == 'free')}


def _place_taken(conn, e, d, interval):
    return any(_overlap(interval, _interval(r)) for r in conn.execute(
        'SELECT time_from, time_to FROM bookings WHERE element_id = ? AND date = ?', (e['id'], d)).fetchall())


def schedule(element_id, start=None, days=7):
    """All bookings of one place in a range of days."""
    e = db.query('SELECT * FROM elements WHERE id = ?', (element_id,), one=True)
    if not e or e['type'] not in perm.BOOKABLE:
        raise BookingError(_('No such place.'), 404)
    start = parse_date(start, date.today())
    days = max(1, min(31, int(days or 7)))
    end = start + timedelta(days=days - 1)
    me = g.user['email']
    admin = perm.is_admin()
    managed = perm.managed_people(me)
    today = date.today().isoformat()
    items = [{
        'id': r['id'], 'date': r['date'], 'from': r['time_from'], 'to': r['time_to'], 'title': r['title'],
        'who': r['display_name'] or r['email'], 'mine': r['email'] == me,
        'can_cancel': (r['email'] == me or admin or r['email'] in managed) and r['date'] >= today,
    } for r in db.query("""SELECT b.*, u.display_name FROM bookings b LEFT JOIN users u ON u.email = b.email
                           WHERE b.element_id = ? AND b.date BETWEEN ? AND ?
                           ORDER BY b.date, COALESCE(b.time_from, '')""", (e['id'], start.isoformat(), end.isoformat()))]
    return {'place': place_name(e), 'type': e['type'], 'from': start.isoformat(), 'to': end.isoformat(),
            'days': [{'date': (start + timedelta(days=i)).isoformat(),
                      'weekday': i18n.weekday(start + timedelta(days=i)),
                      'weekend': (start + timedelta(days=i)).weekday() >= 5} for i in range(days)],
            'bookings': items, 'today': today}


def my_bookings(email=None, start=None, end=None, limit=80):
    """Bookings of a person: upcoming, past and permanent desks."""
    email = email or g.user['email']
    today = date.today().isoformat()
    sql = """SELECT b.*, e.code, e.label, e.type, f.name AS floor, f.id AS floor_id, u.display_name AS created_by_name
             FROM bookings b JOIN elements e ON e.id = b.element_id
             JOIN floors f ON f.id = e.floor_id
             LEFT JOIN users u ON u.email = b.created_by
             WHERE b.email = ? AND b.date {} ? {} ORDER BY b.date {}, COALESCE(b.time_from, '') LIMIT {}"""
    extra, params = '', []
    if start:
        extra += ' AND b.date >= ?'
        params.append(parse_date(start, date.today()).isoformat())
    if end:
        extra += ' AND b.date <= ?'
        params.append(parse_date(end, date.today()).isoformat())

    def row(r):
        return dict(r, place=place_name(r), hours=hours_text(r['time_from'], r['time_to']))
    upcoming = [row(r) for r in db.query(sql.format('>=', extra, 'ASC', int(limit)), (email, today, *params))]
    past = [row(r) for r in db.query(sql.format('<', extra, 'DESC', int(limit)), (email, today, *params))]
    fixed = [dict(r) for r in db.query("""SELECT e.id, e.code, e.label, f.name AS floor, f.id AS floor_id
                                          FROM elements e JOIN floors f ON f.id = e.floor_id
                                          WHERE e.assigned_email = ?""", (email,))]
    return {'upcoming': upcoming, 'past': past, 'fixed': fixed}


def all_bookings(start, end, floor_id=None, kind=None, team_id=None, q=None, limit=2000):
    """All bookings (administrator) with filters. Returns (rows, truncated)."""
    where, params = ['b.date BETWEEN ? AND ?'], [start, end]
    if floor_id:
        where.append('e.floor_id = ?')
        params.append(floor_id)
    if kind in perm.BOOKABLE:
        where.append('e.type = ?')
        params.append(kind)
    if team_id:
        t = next((x for x in perm.all_teams() if x['id'] == team_id), None)
        emails = sorted(perm.team_members(t)) if t else []
        where.append(f"b.email IN ({','.join('?' * len(emails)) or 'NULL'})")
        params += emails
    q = (q or '').strip().lower()
    if q:
        where.append('(LOWER(u.display_name) LIKE ? OR b.email LIKE ? OR LOWER(e.code) LIKE ? '
                     'OR LOWER(e.label) LIKE ? OR LOWER(b.title) LIKE ?)')
        params += [f'%{q}%'] * 5
    rows = []
    for r in db.query(f"""SELECT b.*, e.type, e.code, e.label, f.name AS floor, f.id AS floor_id,
                                 u.display_name AS person, uc.display_name AS created_by_name
                          FROM bookings b JOIN elements e ON e.id = b.element_id
                          JOIN floors f ON f.id = e.floor_id
                          LEFT JOIN users u ON u.email = b.email
                          LEFT JOIN users uc ON uc.email = b.created_by
                          WHERE {' AND '.join(where)}
                          ORDER BY b.date, COALESCE(b.time_from, ''), f.name, e.code LIMIT {int(limit) + 1}""", params):
        d = dict(r)
        d['place'] = place_name(r)
        d['hours'] = hours_text(r['time_from'], r['time_to'])
        rows.append(d)
    return rows[:limit], len(rows) > limit


def find_person(q, d=None):
    """At which desk does a person sit on a day (the "Find a person" field on the plan)."""
    q = (q or '').strip().lower()
    d = parse_date(d, date.today()).isoformat()
    if len(q) < 2:
        return []
    pattern = f'%{q}%'
    rows = db.query("""
        SELECT u.email, u.display_name AS name,
               COALESCE(eb.code, ef.code) AS code,
               COALESCE(eb.floor_id, ef.floor_id) AS floor_id,
               COALESCE(eb.id, ef.id) AS element_id,
               CASE WHEN eb.id IS NOT NULL THEN 'booking'
                    WHEN ef.id IS NOT NULL THEN 'fixed' END AS kind
        FROM users u
        LEFT JOIN elements eb ON eb.id = (
            SELECT b.element_id FROM bookings b JOIN elements x ON x.id = b.element_id AND x.type = 'desk'
            WHERE b.email = u.email AND b.date = ? ORDER BY COALESCE(b.time_from, '') LIMIT 1)
        LEFT JOIN elements ef ON ef.assigned_email = u.email
        WHERE u.active = 1 AND (LOWER(u.display_name) LIKE ? OR u.email LIKE ?)
        ORDER BY CASE WHEN eb.id IS NULL AND ef.id IS NULL THEN 1 ELSE 0 END, u.display_name LIMIT 15""",
                    (d, pattern, pattern))
    return [dict(r) for r in rows]


def find_accounts(q, limit=20):
    """Person suggestions (pickers)."""
    q = (q or '').strip().lower()
    if len(q) < 2:
        return []
    pattern = f'%{q}%'
    return [dict(r) for r in db.query(f"""SELECT email, first_name, last_name, display_name AS name FROM users
                                         WHERE active = 1 AND (LOWER(display_name) LIKE ? OR email LIKE ?)
                                         ORDER BY display_name LIMIT {int(limit)}""", (pattern, pattern))]


def team_week(d=None, chosen=0, only_booked=False):
    """A week: who from my teams sits at which desk.

    Groups to choose from: first the teams I manage, then the ones I belong to - smallest first
    (large umbrella teams at the end).
    """
    d = parse_date(d, date.today())
    monday = d - timedelta(days=d.weekday())
    days = [monday + timedelta(days=i) for i in range(5)]
    me = g.user['email']
    candidates = []  # [(name, color, set(emails))]
    for t in perm.all_teams():
        if t.get('manager_email') == me:
            candidates.append((t['name'], t['color'], perm.team_members(t)))
    member_of = [t for t in perm.all_teams() if perm.in_team(me, t) and t.get('manager_email') != me]
    for t in sorted(member_of, key=lambda t: len(perm.team_members(t))):
        candidates.append((t['name'], t['color'], perm.team_members(t)))
    if not 0 <= chosen < len(candidates):
        chosen = 0
    groups = candidates[chosen:chosen + 1]
    everyone = set().union(*[gr[2] for gr in groups]) if groups else set()
    desks, rooms, people = {}, {}, {}
    if everyone:
        ph = ','.join('?' * len(everyone))
        for r in db.query(f"""SELECT b.email, b.date, b.time_from, b.time_to, e.code, f.name AS floor, f.id AS floor_id
                              FROM bookings b JOIN elements e ON e.id = b.element_id AND e.type = 'desk'
                              JOIN floors f ON f.id = e.floor_id
                              WHERE b.email IN ({ph}) AND b.date BETWEEN ? AND ?
                              ORDER BY COALESCE(b.time_from, '')""",
                          (*everyone, days[0].isoformat(), days[-1].isoformat())):
            desks.setdefault((r['email'], r['date']), dict(r))
        for r in db.query(f"""SELECT b.email, b.date, b.time_from, b.time_to, b.title, e.type, e.code, e.label,
                                     f.id AS floor_id
                              FROM bookings b JOIN elements e ON e.id = b.element_id AND e.type IN ('room', 'booth')
                              JOIN floors f ON f.id = e.floor_id
                              WHERE b.email IN ({ph}) AND b.date BETWEEN ? AND ?
                              ORDER BY COALESCE(b.time_from, '')""",
                          (*everyone, days[0].isoformat(), days[-1].isoformat())):
            rooms.setdefault((r['email'], r['date']), []).append(dict(r, place=place_name(r)))
        for r in db.query(f"""SELECT u.email, u.display_name AS name, e.code AS fixed_code, f.name AS fixed_floor
                              FROM users u
                              LEFT JOIN elements e ON e.assigned_email = u.email
                              LEFT JOIN floors f ON f.id = e.floor_id
                              WHERE u.email IN ({ph})""", tuple(everyone)):
            people[r['email']] = dict(r)
    view = []
    for name, color, emails in groups:
        rows = sorted([people[e] for e in emails if e in people], key=lambda o: (o['name'] or '').lower())
        # "in the office" on a day = has a desk booking or a permanent desk
        in_office = [sum(1 for o in rows if (o['email'], x.isoformat()) in desks or o['fixed_code']) for x in days]
        if only_booked:
            rows = [o for o in rows if any((o['email'], x.isoformat()) in desks or (o['email'], x.isoformat()) in rooms
                                           for x in days)]
        view.append({'name': name, 'color': color, 'people': rows, 'in_office': in_office,
                     'total': len([e for e in emails if e in people])})
    return {'groups': view, 'days': days, 'desks': desks, 'rooms': rooms, 'chosen': chosen,
            'candidates': [(i, c[0], len(c[2])) for i, c in enumerate(candidates)]}


# ─────────────────────────────────────────────────────────────────────────────
# Writing
# ─────────────────────────────────────────────────────────────────────────────

def book(data):
    """Book a place for a day or a range of days (optionally hours).

    data: element_id | place (+ floor), date | date_from + date_to, weekdays,
          time_from, time_to, title, for.
    """
    me = g.user['email']
    for_email = booking_for(data.get('for'))
    if not db.query('SELECT 1 FROM users WHERE email = ? AND active = 1', (for_email,), one=True):
        raise BookingError(_('Unknown person.'))
    if data.get('element_id'):
        e = db.query('SELECT * FROM elements WHERE id = ?', (data.get('element_id'),), one=True)
        if not e:
            raise BookingError(_('No such place.'), 404)
    else:
        e = find_place(data.get('place'), data.get('floor'))
    ok, reason = perm.place_access(e, perm.element_teams(e['floor_id']).get(e['id'], set()), for_email,
                                   perm.teams_of(for_email), admin=perm.is_admin())
    if not ok:
        raise BookingError(reason, 403)
    dates = date_range(data)
    t_from, t_to = hours(data)
    interval = (t_from, t_to) if t_from else FULL_DAY
    title = (data.get('title') or '').strip()[:120] or None

    results = []
    conn = db.get_db()
    for d in dates:
        k = d.isoformat()
        problem = date_problem(d, t_to)
        if not problem:
            # Check and save in one transaction with a lock on the place and the person -
            # two people clicking at the same moment will not get the same place.
            conn.begin_write()
            try:
                conn.lock('elements', 'id', e['id'])
                conn.lock('users', 'email', for_email)
                problem = _collision(conn, e, for_email, k, interval, me)
                if not problem:
                    conn.execute("""INSERT INTO bookings(element_id, email, date, time_from, time_to, title,
                                    created, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                 (e['id'], for_email, k, t_from, t_to, title, db.now(), me))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        results.append({'date': k, 'ok': not problem, 'error': problem})
    booked = [r['date'] for r in results if r['ok']]
    name = place_name(e)
    if booked:
        db.log(me, 'booking', place=name, for_email=for_email, dates=booked, hours=hours_text(t_from, t_to),
               channel=g.get('channel', 'web'))
    f = db.query('SELECT name FROM floors WHERE id = ?', (e['floor_id'],), one=True)
    notifications.booking(me, for_email, dict(e), f['name'] if f else '', results, t_from, t_to, title)
    ids = []
    if booked:
        ph = ','.join('?' * len(booked))
        ids = [r['id'] for r in db.query(f'SELECT id FROM bookings WHERE element_id = ? AND email = ? '
                                         f'AND date IN ({ph}) ORDER BY date', (e['id'], for_email, *booked))]
    return {'results': results, 'booked': len(booked), 'place': name, 'element_id': e['id'],
            'hours': hours_text(t_from, t_to), 'for': for_email, 'ids': ids}


def cancel(ids):
    """Cancel one or many bookings. Returns {'cancelled', 'errors'}."""
    ids = [i for i in (ids or []) if i is not None]
    if not ids:
        raise BookingError(_('Give the booking to cancel.'))
    me = g.user['email']
    managed = perm.managed_people(me)
    today = date.today().isoformat()
    cancelled, errors, entries = [], [], []
    statuses = []
    for bid in ids:
        r = db.query('SELECT b.*, e.code, e.label, e.type FROM bookings b JOIN elements e ON e.id = b.element_id '
                     'WHERE b.id = ?', (bid,), one=True)
        if not r:
            errors.append(_('Booking {id} does not exist.', id=bid))
            statuses.append(404)
        elif not (r['email'] == me or perm.is_admin() or r['email'] in managed):
            errors.append(_('{date}: you cannot cancel this booking.', date=r['date']))
            statuses.append(403)
        elif r['date'] < today and not perm.is_admin():
            errors.append(_('{date}: past bookings cannot be cancelled.', date=r['date']))
            statuses.append(400)
        else:
            db.execute('DELETE FROM bookings WHERE id = ?', (r['id'],))
            db.log(me, 'cancellation', place=place_name(r), for_email=r['email'], date=r['date'],
                   hours=hours_text(r['time_from'], r['time_to']), channel=g.get('channel', 'web'))
            cancelled.append(r['id'])
            entries.append({'email': r['email'], 'element': {'type': r['type'], 'code': r['code'], 'label': r['label']},
                            'date': r['date'], 'time_from': r['time_from'], 'time_to': r['time_to']})
    notifications.cancellation(me, entries)
    if not cancelled:
        raise BookingError('; '.join(errors), statuses[0] if len(ids) == 1 else 400)
    return {'cancelled': cancelled, 'errors': errors}
