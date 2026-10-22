"""
Server configuration read from the .env file in the project directory (no python-dotenv needed).

Only what is needed to start the server lives in .env. The database, single sign-on,
mail and API settings are configured by an administrator in the application
(Administration → Settings).
"""
import os

_loaded = False
_config = {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')


def _load_env():
    global _loaded
    if _loaded:
        return
    _loaded = True
    env_path = os.path.join(BASE_DIR, '.env')
    if not os.path.exists(env_path):
        return
    with open(env_path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            _config[key.strip()] = value


def get(key, default=None):
    """Environment variable, then .env, then the default."""
    _load_env()
    return os.environ.get(key) or _config.get(key) or default


def get_bool(key, default=False):
    val = get(key)
    if val is None:
        return default
    return val.lower() in ('true', '1', 'yes', 'tak')


def get_int(key, default=0):
    try:
        return int(get(key))
    except (TypeError, ValueError):
        return default


_load_env()

ENV = get('ENV', 'test')
APP_HOST = get('APP_HOST', 'localhost')
APP_PORT = get_int('APP_PORT', 2230)
APP_DEBUG = get_bool('APP_DEBUG', False)
# URL prefix of every address - lets the app live next to others on the same host.
APP_PREFIX = '/' + get('APP_PREFIX', 'desks').strip('/')
APP_URL = get('APP_URL', f'http://localhost:{APP_PORT}{APP_PREFIX}').rstrip('/')
SSL_CERT = get('SSL_CERT', '')
SSL_KEY = get('SSL_KEY', '')

# Keys: empty = generated on the first start and stored in data/ (security.py).
SECRET_KEY = get('SECRET_KEY', '')
ENCRYPTION_KEY = get('ENCRYPTION_KEY', '')

# Default SQLite database (until an administrator chooses another one in the settings).
DB_PATH = get('DB_PATH', os.path.join(DATA_DIR, 'desks.sqlite'))
# File with the chosen database (the database password is encrypted).
DB_CONFIG_PATH = get('DB_CONFIG_PATH', os.path.join(DATA_DIR, 'database.json'))
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')

# --- Booking rules ---
MAX_DAYS_AHEAD = get_int('MAX_DAYS_AHEAD', 30)
SKIP_WEEKENDS = get_bool('SKIP_WEEKENDS', True)
# Hours in which rooms / booths / desks can be booked for part of the day, and the time step in minutes.
BOOKING_HOURS_FROM = get('BOOKING_HOURS_FROM', '07:00')
BOOKING_HOURS_TO = get('BOOKING_HOURS_TO', '20:00')
TIME_STEP_MINUTES = get_int('TIME_STEP_MINUTES', 30)

IDLE_TIMEOUT = get_int('IDLE_TIMEOUT_HOURS', 8) * 3600
