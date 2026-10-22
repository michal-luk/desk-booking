"""User interface languages - translations in locales/<code>.json.

Code and templates use English texts: `_('Book')`, in JavaScript `t('Book')`. Every language is
one JSON file in locales/ (e.g. pl.json): `{"_language": "Polski", "Book": "Zarezerwuj", ...}`.
en.json lists every text of the application and is the template for new translations - copy it
as de.json, translate the values and restart the app; the new language appears in the switcher
automatically. Placeholders use names in braces: `_('Booked: {place}', place=name)`.

The language of a request:
  1. the language saved on the account (chosen in the top bar or in "My account"),
  2. the `lang` cookie (choice on the login screen),
  3. the default language from Settings (one of the languages or the browser language).
E-mails use the recipient's language, REST API and MCP messages - the key owner's language.
"""
import contextlib
import glob
import json
import os

from flask import g, has_request_context, request

LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locales')
FALLBACK = 'en'
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')

CATALOGS = {}      # {code: {english text: translation}}
LANGUAGES = {}     # {code: language name in that language}


def load():
    """Read every locales/*.json file (at start-up)."""
    CATALOGS.clear()
    LANGUAGES.clear()
    for path in sorted(glob.glob(os.path.join(LOCALES_DIR, '*.json'))):
        code = os.path.splitext(os.path.basename(path))[0].lower()
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        LANGUAGES[code] = data.pop('_language', code)
        CATALOGS[code] = {k: v for k, v in data.items() if not k.startswith('_') and v}
    LANGUAGES.setdefault(FALLBACK, 'English')
    CATALOGS.setdefault(FALLBACK, {})


load()


def current():
    return g.get('lang') or FALLBACK


def _(text, **params):
    out = CATALOGS.get(current(), {}).get(text, text)
    return out.format(**params) if params else out


def detect(user=None):
    """Language for this request (see the module description)."""
    if user and user.get('language') in LANGUAGES:
        return user['language']
    if has_request_context():
        cookie = request.cookies.get('lang')
        if cookie in LANGUAGES:
            return cookie
    try:
        import app_settings
        default = app_settings.get('general').get('default_language')
    except Exception:  # noqa: BLE001 - e.g. before the schema exists
        default = 'auto'
    if default in LANGUAGES:
        return default
    if has_request_context():
        return request.accept_languages.best_match(list(LANGUAGES)) or FALLBACK
    return FALLBACK


def language_of(email):
    """Language of a person (e-mails): their own choice, the default one, or - when the default is
    "browser language" - the language of whoever triggers the message (e.g. the person inviting)."""
    import app_settings
    import db
    row = db.query('SELECT language FROM users WHERE email = ?', (email,), one=True)
    if row and row['language'] in LANGUAGES:
        return row['language']
    default = app_settings.get('general').get('default_language')
    return default if default in LANGUAGES else current()


@contextlib.contextmanager
def language(lang):
    """Temporarily switch the language (e.g. an e-mail in the recipient's language)."""
    previous = g.get('lang')
    g.lang = lang if lang in LANGUAGES else FALLBACK
    try:
        yield
    finally:
        g.lang = previous


def weekday(d):
    """Short weekday name of a date in the current language."""
    return _(WEEKDAYS[d.weekday()])


def js_catalog():
    """Translations for JavaScript in the current language."""
    return CATALOGS.get(current(), {}) if current() != FALLBACK else {}
