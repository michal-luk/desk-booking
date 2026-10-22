# Desk booking (Rezerwacja biurek)

Self-hosted web application for booking **desks, meeting rooms and phone booths** in an office.
Draw your floor plans, group people into teams, let everyone book from a laptop or a phone — and
let an **AI assistant book for them** through the built-in MCP server.

The interface is available in **English and Polish** (choice on the login screen and per user);
more languages can be added with one JSON file. Polish version of this page: [README.pl.md](README.pl.md).

![Floor plan with a meeting room selected](docs/screenshots/plan-room.png)

## Features

**Booking**
- Interactive floor plan (zoom, pan, touch) and a list view; works on phones.
- Desks for a full day or for hours, meeting rooms and booths by the hour, date ranges with chosen weekdays.
- Collision-safe: one place cannot be double-booked, one person cannot hold two desks (or two rooms) at the same time.
- Permanent (assigned) desks, desks and zones reserved for teams, desks open to everyone.
- *My bookings* with bulk cancellation, *My team* — who from my team is in the office on which day.
- Team managers can book and cancel for their team members; administrators for anyone.
- E-mail notifications (booking confirmation, cancellation by someone else) in the recipient's language — users can switch them off.

**Administration**
- Floor plan editor (SVG): desks, desk blocks, rooms, booths, walls, team zones, labels, background image of the floor,
  undo/redo, keyboard shortcuts.
- Users: e-mail, first and last name, role (*Administrator*, *Editor*, *User*), language, active/blocked.
  Invitation and password-reset links by e-mail, temporary passwords, unlock after failed logins.
- Teams: add members one by one (e-mail + name, the account is created automatically) or paste a list
  (`email; First; Last` from Excel or `First Last <email>` from Outlook); send password links straight from the member list.
- All bookings with filters, bulk cancel and CSV export; audit log and occupancy chart.
- **Settings in the app** (no file editing): database, language, login & SSO, SMTP mail, API & MCP, colours.

**Login**
- E-mail and password (scrypt hashes, lockout after failed attempts, rate limiting per IP).
- **Single sign-on (OpenID Connect)**: Microsoft Entra ID (Azure AD / Microsoft 365), Google, or any OIDC provider
  (Keycloak, Okta, Auth0, ADFS, Authentik, …). Optional domain restriction and automatic account creation.
- Password login can be kept next to SSO, limited to administrators (break-glass access) or disabled.

**Databases** — chosen by the administrator in *Settings → Database*, with a one-click **copy of all data** to the new database:
SQLite (default, zero configuration), PostgreSQL, MariaDB, MySQL.

**Languages and look**
- English and Polish interface; the language is chosen on the login screen, in the top bar or in *My account* and remembered per user.
  The default language (or "the browser's language") is set by the administrator.
- Translations are plain JSON files in [`locales/`](locales/) — copy `en.json` to e.g. `de.json`, translate, restart.
- Main colours (header, main colour, accent) with ready-made sets and a live preview; too light colours are rejected.

**Integrations**
- **REST API** (`/api/v1`) with a personal API key — every user, including the administrator, has their own key with their own permissions.
- **MCP server** (`/mcp`, Streamable HTTP) — connect Claude, ChatGPT, Copilot or any MCP client and ask in plain language:
  *“book me a desk next Tuesday”*, *“who from my team is in the office tomorrow?”*, *“cancel my bookings for next week”*.

| | |
|---|---|
| ![Editor](docs/screenshots/editor.png) | ![SSO settings](docs/screenshots/settings-sso.png) |
| Floor plan editor | Language, login and SSO settings |
| ![Team week](docs/screenshots/team-week.png) | ![API and MCP](docs/screenshots/account-api-mcp.png) |
| Who is in the office this week | Personal API key and MCP connection |
| ![Appearance](docs/screenshots/settings-appearance.png) | ![Polish interface](docs/screenshots/plan-room-pl.png) |
| Main colours | The same plan in Polish |

## Quick start

### Docker

```bash
git clone <repository-url> desk-booking && cd desk-booking
docker compose up -d
docker compose logs app            # shows the one-time setup code
```

Open <http://localhost:2230/desks>, enter the setup code and create the first administrator.
The bundled PostgreSQL service can be selected later in *Settings → Database* (host `db`, database and user `desks`).

### Python

Requires Python 3.10+.

```bash
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env  - set APP_URL
python run.py                      # http://localhost:2230/desks
```

On the first start the app prints a **setup code** to the console; open `/setup`, enter the code and create the
administrator account. Everything else is configured in the app.

## Configuration

`.env` holds only what is needed to start the server (see [.env.example](.env.example)):

| Variable | Meaning |
|---|---|
| `APP_URL` | Public address, e.g. `https://desks.example.com/desks` — used for SSO redirect URIs, e-mail links and the MCP address |
| `APP_PREFIX`, `APP_HOST`, `APP_PORT` | URL prefix (default `desks`) and listening address |
| `SECRET_KEY` | Session signing key (generated into `data/` when empty) |
| `ENCRYPTION_KEY` | AES-256 key (64 hex chars) for secrets stored in the database (generated into `data/` when empty) — **keep a backup** |
| `SSL_CERT`, `SSL_KEY` | Serve HTTPS directly (otherwise use a reverse proxy) |
| `MAX_DAYS_AHEAD`, `SKIP_WEEKENDS`, `BOOKING_HOURS_FROM`, `BOOKING_HOURS_TO`, `TIME_STEP_MINUTES`, `IDLE_TIMEOUT_HOURS` | Booking rules: days ahead, skip weekends in ranges, bookable hours, time step, idle logout |

Database, language, SSO, mail, API and colour settings live in the application (*Administration → Settings*).

## Documentation

- [Installation and deployment](docs/INSTALL.md) — reverse proxy, HTTPS, databases, backups, upgrades
- [Single sign-on](docs/SSO.md) — Microsoft Entra ID, Google, generic OpenID Connect
- [REST API](docs/API.md) — endpoints, authentication, examples
- [MCP server for AI assistants](docs/MCP.md) — connecting Claude / ChatGPT / VS Code, available tools
- [Translations](docs/TRANSLATIONS.md) — adding or improving a language

## Roles

| Role | Can |
|---|---|
| **Administrator** | everything: users and roles, settings, floors, teams, booking for anyone without the days-ahead limit |
| **Editor** | floor plans, teams and desk assignment |
| **User** | book places open to everyone and places of their teams |
| *Team manager* (not a role) | book and cancel for members of the teams they manage |

## Security

- Passwords: scrypt; 5 failed attempts lock the account for 15 minutes; per-IP attempt limit.
- Secrets in the database (SSO client secrets, SMTP password, API keys) and the database password in `data/database.json`
  are encrypted with AES-256-GCM. API keys and one-time tokens are looked up by SHA-256 hash.
- SSO: authorization code flow with PKCE, `state` and `nonce`; ID token signature verified against the provider's JWKS.
- Sessions: HttpOnly / SameSite cookies (Secure over HTTPS), CSRF tokens, idle timeout, security headers (HSTS over HTTPS).
- Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## Project layout

| Path | Purpose |
|---|---|
| `app.py`, `run.py`, `config.py` | Flask application factory, server start (waitress), configuration from `.env` |
| `db.py` | database layer for SQLite / PostgreSQL / MariaDB / MySQL, schema, copying data between databases |
| `auth.py`, `sso.py` | password login, setup wizard, invitations, API keys, language switch; OpenID Connect |
| `security.py`, `app_settings.py` | encryption and hashing; settings stored in the database |
| `booking.py`, `permissions.py` | booking logic shared by the web UI, REST and MCP; roles and access rules |
| `notifications.py`, `i18n.py`, `theme.py` | e-mail over SMTP; languages; main colours |
| `routes/` | web pages (`web.py`), account, administration, REST API (`api_v1.py`), MCP server (`mcp.py`) |
| `templates/`, `static/` | Jinja templates, CSS, JavaScript (plan, editor) |
| `locales/` | translations (`en.json` lists every text, `pl.json`, …) |
| `tests/smoke_test.py`, `tools/i18n_check.py` | end-to-end smoke test (also run by GitHub Actions); translation checker |

## Development

```bash
python tests/smoke_test.py                                             # SQLite in a temp directory
python tests/smoke_test.py --db postgresql://user:pass@localhost:5432/empty_db
python tests/smoke_test.py --db mariadb://user:pass@localhost:3306/empty_db
python tools/i18n_check.py                                             # every text translated?
```

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 Michał Łukawski — michal.lukawski@neodesk.pl
