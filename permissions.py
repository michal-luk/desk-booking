"""Roles, teams and access to places.

The role is stored on the account (users.role): admin | editor | user.
A blocked account = active = 0.

A manager is not a role but a fact: the person is set as the manager of a team.
A manager can book and cancel for the members of their teams.
"""
import math

from flask import g

import db

# Element types that can be booked: desks for a day (or hours), rooms and booths usually for hours.
BOOKABLE = ('desk', 'room', 'booth')

ROLES = {
    'admin': 'Administrator',
    'editor': 'Floor plan and team editor',
    'user': 'User',
}


def all_teams():
    if 'teams' not in g:
        g.teams = [dict(r) for r in db.query('SELECT * FROM teams ORDER BY name')]
        members = {}
        for r in db.query('SELECT team_id, email FROM team_members'):
            members.setdefault(r['team_id'], set()).add(r['email'])
        for t in g.teams:
            t['members'] = members.get(t['id'], set())
    return g.teams


def in_team(email, team):
    return email in team['members'] or team.get('manager_email') == email


def teams_of(email):
    """Ids of the teams the person belongs to."""
    return {t['id'] for t in all_teams() if in_team(email, t)}


def team_members(team):
    """E-mails of the members including the manager."""
    out = set(team['members'])
    if team.get('manager_email'):
        out.add(team['manager_email'])
    return out


def is_admin():
    return g.get('role') == 'admin'


def is_editor():
    return g.get('role') in ('admin', 'editor')


def managed_people(email):
    """Whom the person can book for: members of the teams they manage."""
    out = set()
    for t in all_teams():
        if t.get('manager_email') == email:
            out.update(team_members(t))
    out.discard(email)
    return out


def can_book_for(who, for_whom):
    if who == for_whom:
        return True
    if is_admin():
        return True
    return for_whom in managed_people(who)


def _in_zone(place, zone):
    """Is the centre of the place inside the (possibly rotated) zone."""
    px, py = place['x'] + place['w'] / 2, place['y'] + place['h'] / 2
    cx, cy = zone['x'] + zone['w'] / 2, zone['y'] + zone['h'] / 2
    r = -math.radians(zone['rotation'] or 0)
    lx = cx + (px - cx) * math.cos(r) - (py - cy) * math.sin(r)
    ly = cy + (px - cx) * math.sin(r) + (py - cy) * math.cos(r)
    return zone['x'] <= lx <= zone['x'] + zone['w'] and zone['y'] <= ly <= zone['y'] + zone['h']


def element_teams(floor_id=None, with_source=False):
    """{element_id: set(team_id)} - effective teams of places; missing = open to everyone.

    A place with its own team list has only that list. A place without its own list inherits
    the teams of the zones it stands in - so one space can be shared by several teams without
    marking every desk separately.
    With `with_source=True` returns {id: (set, 'zone name' | None)}.
    """
    where, params = '', ()
    if floor_id is not None:
        where, params = ' AND e.floor_id = ?', (floor_id,)
    own = {}
    for r in db.query('SELECT et.element_id, et.team_id FROM element_teams et '
                      'JOIN elements e ON e.id = et.element_id WHERE 1 = 1' + where, params):
        own.setdefault(r['element_id'], set()).add(r['team_id'])
    elements = db.query("SELECT id, floor_id, type, x, y, w, h, rotation, label FROM elements e "
                        "WHERE type IN ('desk', 'room', 'booth', 'zone')" + where, params)
    zones = [z for z in elements if z['type'] == 'zone' and own.get(z['id'])]
    out = {}
    for p in elements:
        if p['type'] == 'zone':
            continue
        if own.get(p['id']):
            out[p['id']] = (own[p['id']], None)
            continue
        teams, names = set(), []
        for z in zones:
            if z['floor_id'] == p['floor_id'] and _in_zone(p, z):
                teams |= own[z['id']]
                names.append(z['label'] or 'zone')
        if teams:
            out[p['id']] = (teams, ', '.join(names))
    return out if with_source else {k: v[0] for k, v in out.items()}


def place_access(element, teams_of_element, email, teams_of_person, admin=False):
    """(allowed, reason) for person `email` and place `element` (desk, room, booth).

    `admin=True` (an administrator is booking) skips the team restriction - permanent
    assignment and non-bookable places still apply. The reason is already translated.
    """
    from i18n import _
    if element['type'] not in BOOKABLE or not element['bookable']:
        return False, _('This place cannot be booked.')
    if element['type'] == 'desk' and element['assigned_email'] and element['assigned_email'] != email:
        return False, _('This desk is permanently assigned to someone else.')
    if teams_of_element and not (teams_of_element & teams_of_person) and not admin:
        return False, _('This place is reserved for another team.')
    return True, None
