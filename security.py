"""Encryption of secrets, token hashes and passwords.

  * AES-256-GCM (format `v1:<nonce hex>:<ciphertext hex>`) - where the app needs the plain value:
    SSO client secrets, the SMTP password, the database password, API keys (shown to their owner
    in "My account").
  * SHA-256 - where a comparison is enough: looking up an API key from a request header,
    invitation and password-reset tokens (the plain value is never stored).
  * User passwords - scrypt hashes (werkzeug); they cannot be read back.

Encryption key: ENCRYPTION_KEY from .env (64 hex characters). Without it the app generates
a key on the first start and stores it in data/encryption_key - move it to .env afterwards
and keep a copy outside the server (secrets cannot be decrypted without it).
"""
import hashlib
import logging
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from werkzeug.security import check_password_hash, generate_password_hash

import config

log = logging.getLogger(__name__)

PREFIX = 'v1:'
_key = None


def _key_file(name, length):
    """Key from a file in data/; no file = a new random key stored permanently."""
    path = os.path.join(config.DATA_DIR, name)
    if os.path.exists(path):
        with open(path, encoding='ascii') as f:
            return f.read().strip()
    os.makedirs(config.DATA_DIR, exist_ok=True)
    value = secrets.token_hex(length)
    with open(path, 'w', encoding='ascii') as f:
        f.write(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    log.warning('Generated a new key: %s - move it to .env and keep a copy.', path)
    return value


def key():
    global _key
    if _key is None:
        hexa = config.ENCRYPTION_KEY or _key_file('encryption_key', 32)
        try:
            raw = bytes.fromhex(hexa)
        except ValueError:
            raise RuntimeError('ENCRYPTION_KEY must be hex encoded (64 characters). '
                               'Generate one: python -c "import secrets; print(secrets.token_hex(32))"')
        if len(raw) != 32:
            raise RuntimeError(f'ENCRYPTION_KEY must be 32 bytes (64 hex characters), got {len(raw)}.')
        _key = raw
    return _key


def session_key():
    """Flask SECRET_KEY: from .env or generated and stored in data/."""
    s = config.SECRET_KEY
    if s and s.lower() not in ('change_me', 'change-me-in-env-file'):
        return s
    return _key_file('session_key', 32)


def encrypt(text):
    """Text → `v1:...`. Empty or already encrypted - unchanged."""
    if not text or is_encrypted(text):
        return text
    nonce = os.urandom(12)
    ciphertext = AESGCM(key()).encrypt(nonce, str(text).encode('utf-8'), None)
    return f'{PREFIX}{nonce.hex()}:{ciphertext.hex()}'


def decrypt(value):
    """`v1:...` → text. Damaged ciphertext or another key = None (the secret must be entered again)."""
    if not value:
        return value
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    try:
        nonce_hex, ciphertext_hex = value[len(PREFIX):].split(':', 1)
        return AESGCM(key()).decrypt(bytes.fromhex(nonce_hex), bytes.fromhex(ciphertext_hex), None).decode('utf-8')
    except Exception:  # noqa: BLE001 - wrong key / damaged data
        log.error('Could not decrypt a secret - a different ENCRYPTION_KEY than when it was saved?')
        return None


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX) and value.count(':') == 2


def token_hash(value):
    """SHA-256 (hex) of a high-entropy token - API keys, invitation tokens."""
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def new_api_key():
    return 'rzb_' + secrets.token_urlsafe(32)


def new_token():
    return secrets.token_urlsafe(32)


# ─────────────────────────────────────────────────────────────────────────────
# User passwords
# ─────────────────────────────────────────────────────────────────────────────

MIN_PASSWORD_LENGTH = 10


def hash_password(password):
    return generate_password_hash(password, method='scrypt')


def check_password(stored_hash, password):
    if not stored_hash or not password:
        return False
    try:
        return check_password_hash(stored_hash, password)
    except ValueError:
        return False


def password_problem(password, email=None):
    """Message when the password is too weak, otherwise None (already translated)."""
    from i18n import _
    if len(password or '') < MIN_PASSWORD_LENGTH:
        return _('The password must be at least {n} characters long.', n=MIN_PASSWORD_LENGTH)
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return _('The password must contain letters and digits.')
    if email and password.lower() in email.lower():
        return _('The password cannot be a part of the e-mail address.')
    return None
