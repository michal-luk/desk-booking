"""Database: SQLite, PostgreSQL or MariaDB / MySQL - one interface for the whole application.

Application code writes SQL with `?` placeholders and calls `query` / `execute` / `insert`;
here the query is translated to the driver's parameter style and rows come back as
dictionaries (access by column name and by column number).

The chosen database is saved by an administrator (Administration → Settings → Database) in
data/database.json - outside the database itself, because without it the app would not know
what to connect to. The database password is encrypted in that file (security.py).
No file = SQLite.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime

from flask import g, has_app_context

import config
import security

DB_TYPES = {
    'sqlite': 'SQLite',
    'postgresql': 'PostgreSQL',
    'mariadb': 'MariaDB',
    'mysql': 'MySQL',
}
DEFAULT_PORTS = {'postgresql': 5432, 'mariadb': 3306, 'mysql': 3306}
TLS_MODES = ('off', 'require', 'verify')

# Order matters: parent tables before child tables (foreign keys, copying data).
TABLES = ('users', 'tokens', 'teams', 'team_members', 'floors', 'elements',
          'element_teams', 'bookings', 'audit_log', 'settings')

# {ID} - auto-increment key, {S} - short text (indexable), {T} - long text,
# {R} - floating point number, {OPTS} - table options (MySQL: InnoDB + utf8mb4).
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email                {S} PRIMARY KEY,          -- always lower case
    first_name           {S},
    last_name            {S},
    display_name         {S},                      -- "First Last" for display and search
    role                 {S} NOT NULL DEFAULT 'user',   -- admin | editor | user
    active               INTEGER NOT NULL DEFAULT 1,
    language             {S},                      -- pl | en | NULL = default
    password_hash        {T},                      -- scrypt; NULL = SSO / invitation only
    must_change_password INTEGER NOT NULL DEFAULT 0,
    sso_provider         {S},
    sso_subject          {S},
    api_key              {T},                      -- encrypted (AES-GCM)
    api_key_hash         VARCHAR(64),              -- SHA-256 for the lookup by header
    api_key_created      {S},
    api_key_used         {S},
    notifications        INTEGER NOT NULL DEFAULT 1,
    failed_logins        INTEGER NOT NULL DEFAULT 0,
    locked_until         {S},
    last_login           {S},
    created              {S}
){OPTS};
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_api_key ON users(api_key_hash);
CREATE INDEX IF NOT EXISTS ix_users_name ON users(display_name);

-- Invitations and password resets: only the token hash is stored.
CREATE TABLE IF NOT EXISTS tokens (
    token_hash VARCHAR(64) PRIMARY KEY,
    email      {S} NOT NULL,
    purpose    {S} NOT NULL,                       -- password
    expires    {S} NOT NULL,
    created    {S} NOT NULL
){OPTS};

CREATE TABLE IF NOT EXISTS teams (
    id            {ID},
    name          {S} NOT NULL UNIQUE,
    description   {T},
    color         {S} NOT NULL DEFAULT '#2563eb',
    manager_email {S},
    created       {S}
){OPTS};

CREATE TABLE IF NOT EXISTS team_members (
    team_id INTEGER NOT NULL,
    email   {S} NOT NULL,
    PRIMARY KEY (team_id, email),
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
){OPTS};

CREATE TABLE IF NOT EXISTS floors (
    id                 {ID},
    name               {S} NOT NULL,
    location           {S},
    width              INTEGER NOT NULL DEFAULT 1600,
    height             INTEGER NOT NULL DEFAULT 1000,
    grid               INTEGER NOT NULL DEFAULT 20,
    background         {S},                        -- file name in static/uploads
    background_opacity {R} NOT NULL DEFAULT 0.5,
    sort_order         INTEGER NOT NULL DEFAULT 0,
    active             INTEGER NOT NULL DEFAULT 1
){OPTS};

-- Everything on the plan: desks, rooms, booths, walls, zones, labels.
CREATE TABLE IF NOT EXISTS elements (
    id             {ID},
    floor_id       INTEGER NOT NULL,
    type           {S} NOT NULL,                   -- desk | room | booth | wall | zone | label
    code           {S},                            -- e.g. "A-12"
    label          {S},
    x {R} NOT NULL, y {R} NOT NULL, w {R} NOT NULL, h {R} NOT NULL,
    rotation       {R} NOT NULL DEFAULT 0,
    color          {S},
    bookable       INTEGER NOT NULL DEFAULT 1,
    assigned_email {S},                            -- permanent desk of a person
    equipment      {T},
    capacity       INTEGER,                        -- seats in a room / booth
    FOREIGN KEY (floor_id) REFERENCES floors(id) ON DELETE CASCADE
){OPTS};
CREATE INDEX IF NOT EXISTS ix_elements_floor ON elements(floor_id);

-- No rows = the place is open to everyone.
CREATE TABLE IF NOT EXISTS element_teams (
    element_id INTEGER NOT NULL,
    team_id    INTEGER NOT NULL,
    PRIMARY KEY (element_id, team_id),
    FOREIGN KEY (element_id) REFERENCES elements(id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
){OPTS};

-- A booking for the whole day (time_from / time_to = NULL) or for hours 'HH:MM'.
-- Collisions are checked by the code in a transaction with a lock (Connection.lock).
CREATE TABLE IF NOT EXISTS bookings (
    id         {ID},
    element_id INTEGER NOT NULL,
    email      {S} NOT NULL,
    date       VARCHAR(10) NOT NULL,               -- YYYY-MM-DD
    time_from  VARCHAR(5),
    time_to    VARCHAR(5),
    title      {S},
    created    {S} NOT NULL,
    created_by {S} NOT NULL,
    FOREIGN KEY (element_id) REFERENCES elements(id) ON DELETE CASCADE
){OPTS};
CREATE INDEX IF NOT EXISTS ix_bookings_date ON bookings(date);
CREATE INDEX IF NOT EXISTS ix_bookings_element_date ON bookings(element_id, date);
CREATE INDEX IF NOT EXISTS ix_bookings_email_date ON bookings(email, date);

CREATE TABLE IF NOT EXISTS audit_log (
    id      {ID},
    at      {S} NOT NULL,
    actor   {S},
    action  {S} NOT NULL,
    details {T}
){OPTS};

CREATE TABLE IF NOT EXISTS settings (
    name  VARCHAR(100) PRIMARY KEY,
    value {T}
){OPTS}
"""

_COLUMN_TYPES = {
    'sqlite': {'ID': 'INTEGER PRIMARY KEY AUTOINCREMENT', 'S': 'TEXT', 'T': 'TEXT', 'R': 'REAL', 'OPTS': ''},
    'postgresql': {'ID': 'SERIAL PRIMARY KEY', 'S': 'VARCHAR(255)', 'T': 'TEXT', 'R': 'DOUBLE PRECISION', 'OPTS': ''},
    'mysql': {'ID': 'INTEGER AUTO_INCREMENT PRIMARY KEY', 'S': 'VARCHAR(255)', 'T': 'TEXT', 'R': 'DOUBLE',
              'OPTS': ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci'},
}


class DatabaseError(Exception):
    pass


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def dialect_of(db_type):
    return 'mysql' if db_type in ('mariadb', 'mysql') else db_type


# ─────────────────────────────────────────────────────────────────────────────
# Connection settings (data/database.json)
# ─────────────────────────────────────────────────────────────────────────────

_configuration = None
_configuration_lock = threading.Lock()


def configuration():
    """Current database configuration (password encrypted, as in the file)."""
    global _configuration
    with _configuration_lock:
        if _configuration is None:
            cfg = {'type': 'sqlite', 'path': config.DB_PATH}
            if os.path.exists(config.DB_CONFIG_PATH):
                with open(config.DB_CONFIG_PATH, encoding='utf-8') as f:
                    cfg.update(json.load(f))
            _configuration = cfg
        return dict(_configuration)


def save_configuration(cfg):
    """Save the chosen database; from the next request the app connects to it."""
    global _configuration
    data = {k: v for k, v in cfg.items() if v not in (None, '')}
    if data.get('password'):
        data['password'] = security.encrypt(data['password'])
    os.makedirs(os.path.dirname(config.DB_CONFIG_PATH), exist_ok=True)
    tmp = config.DB_CONFIG_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config.DB_CONFIG_PATH)
    with _configuration_lock:
        _configuration = None


def describe(cfg=None):
    cfg = cfg or configuration()
    if cfg['type'] == 'sqlite':
        return f"SQLite: {cfg.get('path') or config.DB_PATH}"
    return f"{DB_TYPES.get(cfg['type'], cfg['type'])}: {cfg.get('user', '')}@{cfg.get('host', '')}:" \
           f"{cfg.get('port') or DEFAULT_PORTS.get(cfg['type'], '')}/{cfg.get('database', '')}"


# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────

class Row(dict):
    """A result row: a dictionary that also accepts a column number (r[0])."""
    __slots__ = ('_columns',)

    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._columns = columns

    def __getitem__(self, key):
        if isinstance(key, int):
            key = self._columns[key]
        return dict.__getitem__(self, key)


class Cursor:
    def __init__(self, cur):
        self._cur = cur

    def _columns(self):
        return [d[0] for d in self._cur.description] if self._cur.description else []

    def fetchall(self):
        cols = self._columns()
        return [Row(cols, r) for r in self._cur.fetchall()]

    def fetchone(self):
        r = self._cur.fetchone()
        return Row(self._columns(), r) if r is not None else None

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._cur.lastrowid


class Connection:
    def __init__(self, raw, dialect):
        self.raw = raw
        self.dialect = dialect

    def _sql(self, sql):
        if self.dialect == 'sqlite':
            return sql
        return sql.replace('%', '%%').replace('?', '%s')

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        if params:
            cur.execute(self._sql(sql), tuple(params))
        else:
            cur.execute(sql)
        return Cursor(cur)

    def executemany(self, sql, rows):
        cur = self.raw.cursor()
        cur.executemany(self._sql(sql), [tuple(p) for p in rows])
        return Cursor(cur)

    def insert(self, sql, params=()):
        """INSERT into a table with an `id` column - returns the new id."""
        if self.dialect == 'postgresql':
            return self.execute(sql + ' RETURNING id', params).fetchone()[0]
        return self.execute(sql, params).lastrowid

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def begin_write(self):
        """A new transaction; in SQLite with a write lock straight away (BEGIN IMMEDIATE)."""
        self.raw.commit()
        if self.dialect == 'sqlite':
            self.raw.execute('BEGIN IMMEDIATE')

    def lock(self, table, column, value):
        """Row lock until the end of the transaction (PostgreSQL / MySQL). In SQLite the write lock
        from begin_write covers the whole database, so nothing is needed here."""
        if self.dialect != 'sqlite':
            self.execute(f'SELECT {column} FROM {table} WHERE {column} = ? FOR UPDATE', (value,))


def _connect_sqlite(cfg):
    path = cfg.get('path') or config.DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    # SQLite's built-in LOWER() only knows ASCII - "Łukasz" would not match a search.
    conn.create_function('LOWER', 1, lambda s: s.lower() if isinstance(s, str) else s, deterministic=True)
    return Connection(conn, 'sqlite')


def _connect_postgresql(cfg):
    try:
        import psycopg2
    except ImportError:
        raise DatabaseError('PostgreSQL driver missing - install it: pip install psycopg2-binary')
    tls = cfg.get('tls') or 'off'
    options = {'sslmode': {'off': 'prefer', 'require': 'require', 'verify': 'verify-full'}[tls]}
    if tls == 'verify' and cfg.get('ca'):
        options['sslrootcert'] = cfg['ca']
    conn = psycopg2.connect(host=cfg.get('host') or 'localhost', port=int(cfg.get('port') or 5432),
                            dbname=cfg.get('database'), user=cfg.get('user'),
                            password=security.decrypt(cfg.get('password')) or None,
                            connect_timeout=10, client_encoding='UTF8', **options)
    return Connection(conn, 'postgresql')


def _connect_mysql(cfg):
    try:
        import pymysql
    except ImportError:
        raise DatabaseError('MariaDB/MySQL driver missing - install it: pip install PyMySQL')
    tls = cfg.get('tls') or 'off'
    options = {}
    if tls == 'require':
        options['ssl_verify_cert'] = False
    elif tls == 'verify':
        options.update(ssl_verify_cert=True, ssl_verify_identity=True)
        if cfg.get('ca'):
            options['ssl_ca'] = cfg['ca']
    conn = pymysql.connect(host=cfg.get('host') or 'localhost', port=int(cfg.get('port') or 3306),
                           database=cfg.get('database'), user=cfg.get('user'),
                           password=security.decrypt(cfg.get('password')) or '',
                           charset='utf8mb4', connect_timeout=10, autocommit=False, **options)
    with conn.cursor() as cur:
        # After a row lock, later reads must see what another transaction has committed.
        cur.execute('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')
    return Connection(conn, 'mysql')


def connect(cfg=None):
    cfg = cfg or configuration()
    d = dialect_of(cfg['type'])
    if d == 'sqlite':
        return _connect_sqlite(cfg)
    if d == 'postgresql':
        return _connect_postgresql(cfg)
    if d == 'mysql':
        return _connect_mysql(cfg)
    raise DatabaseError(f"Unknown database type: {cfg['type']}")


def get_db():
    """Connection bound to the application context (request, start-up); outside of it - a new one."""
    if has_app_context():
        if 'db' not in g:
            g.db = connect()
        return g.db
    return connect()


def close_db(_exc=None):
    conn = g.pop('db', None)
    if conn is not None:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - the connection may already be gone
            pass
        conn.close()


def dialect():
    return get_db().dialect


def integrity_errors():
    """"UNIQUE / foreign key violated" exceptions of all drivers."""
    out = [sqlite3.IntegrityError]
    try:
        import psycopg2
        out.append(psycopg2.IntegrityError)
    except ImportError:
        pass
    try:
        import pymysql
        out.append(pymysql.err.IntegrityError)
    except ImportError:
        pass
    return tuple(out)


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

def _schema_statements(d):
    sql = SCHEMA.format(**_COLUMN_TYPES[d])
    sql = '\n'.join(ln.split('--')[0].rstrip() for ln in sql.splitlines())   # comments may contain semicolons
    for stmt in sql.split(';'):
        stmt = stmt.strip()
        if stmt:
            yield stmt


def create_schema(conn):
    for stmt in _schema_statements(conn.dialect):
        if conn.dialect == 'mysql' and stmt.startswith('CREATE') and 'INDEX IF NOT EXISTS' in stmt:
            # MySQL has no CREATE INDEX IF NOT EXISTS - check it ourselves.
            name = stmt.split('INDEX IF NOT EXISTS')[1].split()[0]
            table = stmt.split(' ON ')[1].split('(')[0].strip()
            exists = conn.execute("""SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE()
                                     AND table_name = ? AND index_name = ?""", (table, name)).fetchone()
            if exists:
                continue
            stmt = stmt.replace('INDEX IF NOT EXISTS', 'INDEX')
        conn.execute(stmt)
    conn.commit()


def init_db(cfg=None):
    conn = connect(cfg)
    try:
        create_schema(conn)
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Connection test and copying data between databases
# ─────────────────────────────────────────────────────────────────────────────

def _row_counts(conn):
    """{table: number of rows} for the existing application tables."""
    out = {}
    for t in TABLES:
        try:
            out[t] = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        except Exception:  # noqa: BLE001 - the table does not exist yet
            conn.rollback()
    return out


def check_connection(cfg):
    """(ok, version or error message, {table: rows})."""
    try:
        conn = connect(cfg)
    except Exception as e:  # noqa: BLE001 - the driver message goes to the administrator
        return False, str(e), {}
    try:
        version_sql = {'sqlite': 'SELECT sqlite_version()', 'postgresql': 'SELECT version()',
                       'mysql': 'SELECT VERSION()'}[conn.dialect]
        version = str(conn.execute(version_sql).fetchone()[0]).split(',')[0]
        return True, version, _row_counts(conn)
    except Exception as e:  # noqa: BLE001
        return False, str(e), {}
    finally:
        conn.close()


def copy_data(cfg_source, cfg_target, overwrite=False):
    """Copies all application tables from the source database to the target one (one transaction).

    The target database must be empty unless `overwrite=True` - then its data is deleted.
    Returns {table: copied rows}.
    """
    source, target = connect(cfg_source), connect(cfg_target)
    try:
        create_schema(target)
        busy = {t: n for t, n in _row_counts(target).items() if n}
        if busy and not overwrite:
            raise DatabaseError(', '.join(f'{t}: {n}' for t, n in busy.items()))
        for t in reversed(TABLES):
            target.execute(f'DELETE FROM {t}')
        result = {}
        for t in TABLES:
            rows = source.execute(f'SELECT * FROM {t}').fetchall()
            result[t] = len(rows)
            if not rows:
                continue
            columns = list(rows[0].keys())
            sql = f"INSERT INTO {t}({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
            for i in range(0, len(rows), 500):
                target.executemany(sql, [[r[c] for c in columns] for r in rows[i:i + 500]])
        if target.dialect == 'postgresql':
            # Ids were inserted explicitly - sequences must continue after the largest one.
            for t in ('teams', 'floors', 'elements', 'bookings', 'audit_log'):
                target.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                               f"COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)")
        target.commit()
        return result
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def query(sql, params=(), one=False):
    rows = get_db().execute(sql, params).fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    conn = get_db()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return cur


def insert(sql, params=()):
    conn = get_db()
    try:
        new_id = conn.insert(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new_id


def setting(name, default=None):
    row = query('SELECT value FROM settings WHERE name = ?', (name,), one=True)
    return row['value'] if row else default


def save_setting(name, value):
    if query('SELECT 1 FROM settings WHERE name = ?', (name,), one=True):
        execute('UPDATE settings SET value = ? WHERE name = ?', (value, name))
    else:
        execute('INSERT INTO settings(name, value) VALUES (?, ?)', (name, value))


def log(actor, action, /, **details):
    execute('INSERT INTO audit_log(at, actor, action, details) VALUES (?, ?, ?, ?)',
            (now(), actor, action, json.dumps(details, ensure_ascii=False, default=str) if details else None))
