# Installation and deployment

## Requirements

- Python 3.10+ (or Docker)
- Optional: PostgreSQL, MariaDB or MySQL server — SQLite works out of the box
- Optional: an SMTP account for e-mails (notifications, invitations, password reset)

## 1. Install

### With Docker

```bash
docker compose up -d
docker compose logs app              # one-time setup code
```

Data (SQLite, generated keys) is stored in the `data` volume, uploaded floor plans in `uploads`, PostgreSQL in `postgres`.
Set at least `APP_URL`, `ENCRYPTION_KEY`, `SECRET_KEY` and `POSTGRES_PASSWORD` as environment variables or in a
`.env` file next to `docker-compose.yml` before going to production.

### Without Docker

```bash
pip install -r requirements.txt
cp .env.example .env
python run.py
```

`run.py` serves the app with **waitress** (production WSGI server). With `APP_DEBUG=true` it uses the Flask
development server; with `SSL_CERT` and `SSL_KEY` it serves HTTPS itself.

On Windows the app can be run as a service, e.g. with [NSSM](https://nssm.cc/) (`nssm install desk-booking python run.py`).
On Linux use a systemd unit:

```ini
[Unit]
Description=Desk booking
After=network.target

[Service]
WorkingDirectory=/opt/desk-booking
ExecStart=/opt/desk-booking/.venv/bin/python run.py
User=desks
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## 2. First start

When the database has no administrator, every page redirects to `/setup` and the server log prints a one-time
**setup code** (`No administrator yet. Open …/setup and enter the code: XXXX-XXXX-XXXX`). Enter the code,
your name, e-mail and password — this becomes the first administrator account. The code is regenerated on every start
until an administrator exists.

## 3. Generate and keep the keys

```bash
python -c "import secrets; print(secrets.token_hex(32))"   # ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_hex(32))"   # SECRET_KEY
```

- `ENCRYPTION_KEY` encrypts secrets stored in the database (SSO client secrets, SMTP password, API keys) and the database
  password in `data/database.json`. **Losing it means re-entering those secrets** and regenerating API keys. Keep a copy
  outside the server.
- When the variables are empty, the app generates keys into `data/encryption_key` and `data/session_key` and logs
  a warning — fine for testing, but move them to `.env` for production.

## 4. HTTPS and reverse proxy

Set `APP_URL` to the public HTTPS address — session cookies then get the `Secure` flag and HSTS is sent.
The path in the proxy must match `APP_PREFIX` (default `/desks`).

**nginx**

```nginx
location /desks/ {
    proxy_pass http://127.0.0.1:2230;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 20m;          # floor plan images
}
```

**Apache**

```apache
ProxyPreserveHost On
ProxyPass        /desks http://127.0.0.1:2230/desks
ProxyPassReverse /desks http://127.0.0.1:2230/desks
RequestHeader set X-Forwarded-Proto "https"
```

**IIS** — URL Rewrite + Application Request Routing, reverse proxy rule for `desks/(.*)` → `http://127.0.0.1:2230/desks/{R:1}`.

## 5. Choosing the database

*Administration → Settings → Database*:

1. Create an empty database and a user on the server, e.g.
   - PostgreSQL: `CREATE DATABASE desks; CREATE USER desks WITH PASSWORD '…'; GRANT ALL ON DATABASE desks TO desks;`
     (PostgreSQL 15+: also `ALTER DATABASE desks OWNER TO desks;`)
   - MariaDB / MySQL: `CREATE DATABASE desks CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER 'desks'@'%' IDENTIFIED BY '…'; GRANT ALL ON desks.* TO 'desks'@'%';`
2. Enter host, port, database, user, password and the TLS mode, click **Check connection**.
3. Choose **Copy all data from the current database** (default) or **Use the data that is already in the chosen database**, then **Save and switch**.

The app creates the tables itself and switches on the next request. The choice is saved in `data/database.json`.
Drivers: `psycopg2-binary` (PostgreSQL), `PyMySQL` (MariaDB / MySQL) — both in `requirements.txt`.

## 6. E-mail

*Settings → Mail*: SMTP host, port, STARTTLS / SSL, login, password, sender. Examples:

| Provider | Host | Port | Mode |
|---|---|---|---|
| Microsoft 365 | `smtp.office365.com` | 587 | STARTTLS (SMTP AUTH must be enabled for the mailbox) |
| Gmail / Google Workspace | `smtp.gmail.com` | 587 | STARTTLS (app password) |
| Own server | your host | 587 / 465 | STARTTLS / SSL |

Use **Save and send a test** to verify. Messages are sent in the language of each recipient. Without mail, administrators get password links on screen to pass on manually.

## 7. Backups

- **SQLite**: copy `data/desks.sqlite` (stop the app or use `sqlite3 desks.sqlite ".backup backup.sqlite"`).
- **PostgreSQL / MariaDB / MySQL**: regular `pg_dump` / `mariadb-dump`.
- Always back up `.env` (keys), `data/database.json` and `static/uploads/` (floor plan images).

## 8. Upgrading

```bash
git pull
pip install -r requirements.txt
# restart the service
```

New tables are created automatically on start. Run `python tests/smoke_test.py` after larger changes.
