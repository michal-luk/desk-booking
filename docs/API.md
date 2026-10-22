# REST API

Base URL: `{APP_URL}/api/v1` — `GET {APP_URL}/api/v1` returns the list of endpoints.

## Authentication

Every user — including the administrator — generates a personal key in **My account → Generate an API key**.
Showing, regenerating or deleting an existing key requires the account password.

```
X-API-Key: rzb_...
# or
Authorization: Bearer rzb_...
```

- The key has exactly the permissions of its owner: a user's key sees and books what the user can in the browser,
  a team manager's key can book for team members, an administrator's key sees all bookings.
- Keys are stored encrypted (AES-256-GCM) and looked up by SHA-256 hash; administrators can revoke a user's key
  (*Users → user → Revoke the key*) or all keys (*Settings → API and MCP*).
- Rate limit per key (default 120 requests / minute, configurable). API and MCP can be switched off separately.
- Requests with a key don't use cookies, so no CSRF token is needed.
- Messages (errors, place names, "all day") come in the **language of the key owner** (*My account → Language*).

## Conventions

- Dates `YYYY-MM-DD`, times `HH:MM`. No times = the whole day.
- Place types: `desk`, `room` (meeting room), `booth` (phone booth).
- Errors: JSON `{"error": "message"}` with `400` (invalid input), `401` (no / invalid key), `403` (not allowed),
  `404` (not found), `409` (place taken), `429` (rate limit).

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/me` | Key owner: e-mail, name, role, language, teams, people they can book for |
| GET | `/floors` | Active floors |
| GET | `/plan?floor=ID&date=…[&for=email]` | Floor plan with the status of every place for the day |
| GET | `/free?date=…[&floor=ID&type=…&time_from=…&time_to=…&capacity=N&for=email]` | Places free for me (or for `for`) |
| GET | `/places/ID/availability?date_from=…[&date_to=…&time_from=…&time_to=…]` | Availability of one place day by day |
| GET | `/places/ID/schedule[?from=…&days=7]` | Bookings of one place (e.g. a meeting room) |
| GET | `/bookings[?from=…&to=…]` | My bookings: `upcoming`, `past`, `fixed` (permanent desks) |
| POST | `/bookings` | Book (see below) — `201` when at least one day was booked, `409` when none |
| DELETE | `/bookings/ID` | Cancel a booking |
| GET | `/people?q=…[&date=…]` | Where does a person sit on a given day |
| GET | `/team[?date=…&group=N]` | My team's office week |
| GET | `/admin/bookings?from=…&to=…[&floor&type&team&q&limit]` | All bookings (administrator) |
| GET | `/admin/users[?q=…]` | Accounts (administrator) |

Plan statuses: `free`, `partial` (part of the day taken), `mine`, `taken`, `team` (someone from my team),
`assigned` / `assigned_mine` (permanent desk), `unavailable`.

### Booking

```jsonc
POST /api/v1/bookings
{
  "place": "A-01",              // or "element_id": 12; rooms by name: "Room Ocean"
  "floor": 1,                   // optional, needed when the code exists on several floors
  "date": "2026-11-03",         // or "date_from" + "date_to" for a range
  "weekdays": [0, 2, 4],        // optional weekdays in a range, 0 = Monday (default Mon–Fri)
  "time_from": "09:00",         // optional, hours
  "time_to": "10:00",
  "title": "Sprint planning",   // optional, meeting subject
  "for": "john.miller@example.com"   // optional, team manager / administrator
}
```

Response: the result for every day (`results: [{date, ok, error}]`), the number of booked days (`booked`) and the new
booking ids (`ids`).

## Examples

```bash
KEY=rzb_...
URL=https://desks.example.com/desks/api/v1

curl -H "X-API-Key: $KEY" "$URL/free?date=2026-11-03&type=desk"

curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"place": "A-01", "date": "2026-11-03"}' "$URL/bookings"

curl -H "X-API-Key: $KEY" "$URL/bookings"
curl -X DELETE -H "X-API-Key: $KEY" "$URL/bookings/123"
```

```python
import requests
api = requests.Session()
api.headers['X-API-Key'] = 'rzb_...'
URL = 'https://desks.example.com/desks/api/v1'

free = api.get(f'{URL}/free', params={'date': '2026-11-03', 'type': 'room', 'time_from': '10:00', 'time_to': '11:00'}).json()
r = api.post(f'{URL}/bookings', json={'element_id': free['places'][0]['id'], 'date': '2026-11-03',
                                       'time_from': '10:00', 'time_to': '11:00', 'title': 'Demo'})
print(r.status_code, r.json()['results'])
```

All API and MCP actions are written to the audit log with the channel (`channel: api` / `mcp`).
