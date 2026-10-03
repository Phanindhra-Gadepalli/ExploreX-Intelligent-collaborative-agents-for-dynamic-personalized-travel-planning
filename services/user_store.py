"""Persistent account store for ExploreX email authentication.

SQLite-backed so accounts survive backend restarts while server-side
session files are intentionally wiped on startup (a restarted backend must
never resurrect a previous user's session).
"""

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'users.db')

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')

_lock = threading.Lock()


class AuthError(Exception):
    """Validation or credential failure with a user-safe message."""

    def __init__(self, message, field=None, status=400):
        super().__init__(message)
        self.message = message
        self.field = field
        self.status = status


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_db():
    with _lock, _connect() as conn:
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT,
                provider TEXT NOT NULL DEFAULT 'email',
                created_at TEXT NOT NULL
            )'''
        )


def validate_password(password):
    if not password or len(password) < 8:
        raise AuthError('Password must be at least 8 characters.', field='password')
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        raise AuthError('Password must contain at least one letter and one number.', field='password')


def create_user(name, email, password, provider='email'):
    name = (name or '').strip()
    email = (email or '').strip().lower()
    if len(name) < 2:
        raise AuthError('Please enter your full name.', field='name')
    if not EMAIL_RE.match(email):
        raise AuthError('Please enter a valid email address.', field='email')
    if provider == 'email':
        validate_password(password)

    with _lock, _connect() as conn:
        existing = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        if existing:
            raise AuthError('An account with this email already exists. Try signing in instead.', field='email', status=409)
        conn.execute(
            'INSERT INTO users (name, email, password_hash, provider, created_at) VALUES (?, ?, ?, ?, ?)',
            (name, email, generate_password_hash(password) if password else None, provider,
             datetime.now(timezone.utc).isoformat()),
        )
    return {'name': name, 'email': email, 'provider': provider}


def verify_credentials(email, password):
    email = (email or '').strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError('Please enter a valid email address.', field='email')
    if not password:
        raise AuthError('Please enter your password.', field='password')

    with _lock, _connect() as conn:
        row = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    if not row or not row['password_hash']:
        raise AuthError('Invalid email or password.', status=401)
    if not check_password_hash(row['password_hash'], password):
        raise AuthError('Invalid email or password.', status=401)
    return {'name': row['name'], 'email': row['email'], 'provider': row['provider']}


def find_or_create_oauth_user(name, email, provider):
    """OAuth identities are verified by the provider before this is called."""
    email = (email or '').strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError('The provider did not return a valid email address.', status=502)
    name = (name or '').strip() or email.split('@')[0]
    with _lock, _connect() as conn:
        row = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        if row:
            return {'name': row['name'], 'email': row['email'], 'provider': provider}
        conn.execute(
            'INSERT INTO users (name, email, password_hash, provider, created_at) VALUES (?, ?, NULL, ?, ?)',
            (name, email, provider, datetime.now(timezone.utc).isoformat()),
        )
    return {'name': name, 'email': email, 'provider': provider}


init_db()
