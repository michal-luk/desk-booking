"""Pages for everyone: floor plan with booking, my bookings, my team's week.

JSON under /api/... serves the browser UI (session + CSRF token).
Integrations use /api/v1 with an API key (routes/api_v1.py).
"""
import functools
from datetime import date, timedelta

from flask import Blueprint, g, render_template, request

import booking
import config
import db
import permissions as perm
from auth import login_required

web_bp = Blueprint('web', __name__)


def json_errors(f):
    """BookingError → {'error': ...} with the right HTTP status."""
    @functools.wraps(f)
    def wrapper(*a, **kw):
        try:
            return f(*a, **kw)
        except booking.BookingError as e:
            return {'error': e.message}, e.status
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────────────────────────────────────

@web_bp.route('/')
@login_required
def plan():
    floors = booking.floors()
    floor_id = request.args.get('floor', type=int)
    if floors and floor_id not in {f['id'] for f in floors}:
        floor_id = floors[0]['id']
    d = booking.parse_date(request.args.get('date'), date.today())
    people = []
    emails = perm.managed_people(g.user['email'])
    if emails:
        ph = ','.join('?' * len(emails))
        people = db.query(f'SELECT email, display_name FROM users WHERE email IN ({ph}) AND active = 1 '
                          'ORDER BY display_name', tuple(emails))
    return render_template('plan.html', floors=floors, floor_id=floor_id, date=d.isoformat(), people=people,
                           max_days=config.MAX_DAYS_AHEAD,
                           hours={'from': config.BOOKING_HOURS_FROM, 'to': config.BOOKING_HOURS_TO,
                                  'step': config.TIME_STEP_MINUTES})


@web_bp.route('/my')
@login_required
def my_bookings():
    return render_template('my_bookings.html', **booking.my_bookings(), parse_date=booking.parse_date)


@web_bp.route('/team')
@login_required
def team_week():
    """A week: who from my teams sits at which desk."""
    d = booking.parse_date(request.args.get('date'), date.today())
    only = request.args.get('only') == '1'
    w = booking.team_week(d, request.args.get('group', 0, type=int), only)
    return render_template('team_week.html', **w, date=d, timedelta=timedelta, today=date.today(), only=only)


# ─────────────────────────────────────────────────────────────────────────────
# JSON for the browser UI
# ─────────────────────────────────────────────────────────────────────────────

@web_bp.route('/api/plan')
@login_required
@json_errors
def api_plan():
    return booking.plan(request.args.get('floor', type=int), request.args.get('date'), request.args.get('for'))


@web_bp.route('/api/availability')
@login_required
@json_errors
def api_availability():
    return booking.availability(request.args.get('element_id', type=int), request.args)


@web_bp.route('/api/book', methods=['POST'])
@login_required
@json_errors
def api_book():
    return booking.book(request.get_json(force=True) or {})


@web_bp.route('/api/cancel', methods=['POST'])
@login_required
@json_errors
def api_cancel():
    """Cancel one (`id`) or many (`ids`) bookings."""
    data = request.get_json(force=True) or {}
    return {'ok': True, **booking.cancel(data.get('ids') or [data.get('id')])}


@web_bp.route('/api/schedule')
@login_required
@json_errors
def api_schedule():
    """Bookings of one place over a range of days - for the room / desk schedule drawer."""
    return booking.schedule(request.args.get('element_id', type=int), request.args.get('from'),
                            request.args.get('days', 7, type=int))


@web_bp.route('/api/notifications', methods=['POST'])
@login_required
def api_notifications():
    on = 1 if (request.get_json(force=True) or {}).get('enabled') else 0
    db.execute('UPDATE users SET notifications = ? WHERE email = ?', (on, g.user['email']))
    return {'ok': True, 'enabled': bool(on)}


@web_bp.route('/api/search')
@login_required
def api_search():
    """At which desk does a person sit on a day (the "Find a person" field on the plan)."""
    return {'results': booking.find_person(request.args.get('q'), request.args.get('date'))}


@web_bp.route('/api/people')
@login_required
def api_people():
    """Person suggestions (pickers in the administration)."""
    return {'results': booking.find_accounts(request.args.get('q'))}
