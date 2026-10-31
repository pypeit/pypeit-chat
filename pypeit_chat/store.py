"""
SQLite log of interactions, feedback and rate-limit counters (DESIGN
Section 10).

Question and answer text are stored only when ``LOG_TEXT=1``.  User IDs are
salted hashes.  Rows older than ``LOG_RETENTION_DAYS`` are deleted by
:meth:`Store.purge`.  Search never uses the database.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time REAL NOT NULL,
    event_id TEXT,
    channel_type TEXT,
    channel TEXT,
    user_hash TEXT,
    question TEXT,
    query TEXT,
    sources TEXT,
    answer TEXT,
    model TEXT,
    tag TEXT,
    sha TEXT,
    seconds REAL,
    tokens INTEGER,
    outcome TEXT,
    answer_ts TEXT
);
CREATE INDEX IF NOT EXISTS interactions_msg ON interactions (channel, answer_ts);
CREATE TABLE IF NOT EXISTS feedback (
    interaction_id INTEGER NOT NULL,
    user_hash TEXT NOT NULL,
    rating INTEGER NOT NULL,
    time REAL NOT NULL,
    PRIMARY KEY (interaction_id, user_hash)
);
CREATE TABLE IF NOT EXISTS usage (
    user_hash TEXT NOT NULL,
    channel TEXT NOT NULL,
    hour INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (user_hash, channel, hour)
);
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,
    time REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""
#: Outcomes recorded in ``interactions.outcome``.
OUTCOMES = ('ok', 'refused', 'rate-limited', 'error')


def load_salt(settings):
    """
    The salt for user-ID hashes: ``HASH_SALT``, or a random value kept in
    ``hash_salt`` next to the database (created with mode 600).
    """
    if settings.hash_salt:
        return settings.hash_salt
    path = Path(settings.db_path).parent / 'hash_salt'
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(salt)
    return salt


class Store:
    """
    The SQLite database.  Thread-safe (one connection, one lock).

    Parameters
    ----------
    path : Path or str
        Database file (``:memory:`` for tests).
    settings : pypeit_chat.config.Settings
        For ``LOG_TEXT``, ``LOG_RETENTION_DAYS`` and the salt.
    clock : callable, optional
        Returns the time in seconds (for tests).
    """

    def __init__(self, path, settings, clock=time.time):
        self.settings = settings
        self.clock = clock
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.salt = load_salt(settings)

    def _exec(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    def user_hash(self, user_id):
        """Salted hash of a Slack user ID (16 hex characters)."""
        return hashlib.sha256(f'{self.salt}:{user_id}'.encode()).hexdigest()[:16]

    # --- De-duplication ----------------------------------------------------

    def first_time(self, *keys):
        """
        Record keys (event ids, ``channel:ts``) as processed.

        Returns
        -------
        bool
            True if none of the keys had been seen before.
        """
        now = self.clock()
        with self.lock:
            new = True
            for key in keys:
                if not key:
                    continue
                cur = self.db.execute('INSERT OR IGNORE INTO seen (key, time) VALUES (?, ?)',
                                      (key, now))
                new = new and cur.rowcount == 1
            return new

    # --- Rate limits ----------------------------------------------------------

    def usage(self, user_hash, channel):
        """
        Questions counted for rate limits.

        Returns
        -------
        tuple of int
            (user this hour, user in the last 24 hours, channel this hour).
        """
        hour = int(self.clock() // 3600)
        with self.lock:
            q = self.db.execute
            user_hour = q('SELECT COALESCE(SUM(count), 0) FROM usage WHERE user_hash=? AND hour=?',
                          (user_hash, hour)).fetchone()[0]
            user_day = q('SELECT COALESCE(SUM(count), 0) FROM usage WHERE user_hash=? AND hour>?',
                         (user_hash, hour - 24)).fetchone()[0]
            chan_hour = q('SELECT COALESCE(SUM(count), 0) FROM usage WHERE channel=? AND hour=?',
                          (channel, hour)).fetchone()[0]
        return user_hour, user_day, chan_hour

    def count(self, user_hash, channel):
        """Count one question for the rate limits."""
        hour = int(self.clock() // 3600)
        self._exec('INSERT INTO usage (user_hash, channel, hour, count) VALUES (?, ?, ?, 1) '
                   'ON CONFLICT (user_hash, channel, hour) DO UPDATE SET count = count + 1',
                   (user_hash, channel, hour))

    # --- Interactions and feedback ---------------------------------------------

    def log(self, *, event_id=None, channel_type=None, channel=None, user_id=None,
            question=None, query=None, sources=None, answer=None, model=None, tag=None,
            sha=None, seconds=None, tokens=None, outcome='ok', answer_ts=None):
        """
        Record an interaction.  Text fields are dropped unless ``LOG_TEXT``.

        Parameters
        ----------
        sources : list of str, optional
            Source chunk ids (always kept: they are not user text).

        Returns
        -------
        int
            Interaction id.
        """
        if outcome not in OUTCOMES:
            raise ValueError(f'unknown outcome {outcome!r}')
        keep = self.settings.log_text
        cur = self._exec(
            'INSERT INTO interactions (time, event_id, channel_type, channel, user_hash, '
            'question, query, sources, answer, model, tag, sha, seconds, tokens, outcome, '
            'answer_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (self.clock(), event_id, channel_type, channel,
             self.user_hash(user_id) if user_id else None,
             question if keep else None, query if keep else None,
             json.dumps(sources) if sources is not None else None,
             answer if keep else None, model, tag, sha, seconds, tokens, outcome, answer_ts))
        return cur.lastrowid

    def interaction_for(self, channel, ts):
        """Interaction id of the bot answer at ``channel``/``ts``, or None."""
        row = self._exec('SELECT id FROM interactions WHERE channel=? AND answer_ts=?',
                         (channel, ts)).fetchone()
        return row[0] if row else None

    def feedback(self, interaction_id, user_id, rating):
        """Record (or replace) a user's rating (+1/-1) of an answer."""
        self._exec('INSERT INTO feedback (interaction_id, user_hash, rating, time) '
                   'VALUES (?, ?, ?, ?) ON CONFLICT (interaction_id, user_hash) '
                   'DO UPDATE SET rating=excluded.rating, time=excluded.time',
                   (interaction_id, self.user_hash(user_id), int(rating), self.clock()))

    # --- Housekeeping ------------------------------------------------------------

    def purge(self):
        """
        Delete interactions and feedback older than ``LOG_RETENTION_DAYS``,
        rate-limit counters older than 2 days and de-duplication keys older
        than 1 day.
        """
        now = self.clock()
        cutoff = now - self.settings.log_retention_days * 86400
        with self.lock:
            self.db.execute('DELETE FROM feedback WHERE interaction_id IN '
                            '(SELECT id FROM interactions WHERE time < ?)', (cutoff,))
            self.db.execute('DELETE FROM interactions WHERE time < ?', (cutoff,))
            self.db.execute('DELETE FROM usage WHERE hour < ?', (int(now // 3600) - 48,))
            self.db.execute('DELETE FROM seen WHERE time < ?', (now - 86400,))

    def get(self, key, default=None):
        """Read a stored value."""
        row = self._exec('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def put(self, key, value):
        """Store a value."""
        self._exec('INSERT INTO kv (key, value) VALUES (?, ?) '
                   'ON CONFLICT (key) DO UPDATE SET value=excluded.value', (key, str(value)))

    def summary(self, since):
        """
        Usage counts since a time (for the weekly summary).

        Returns
        -------
        dict
            ``questions``, ``users``, outcome counts, ``thumbs_up``,
            ``thumbs_down`` and ``median_seconds``.
        """
        with self.lock:
            q = self.db.execute
            out = {'questions': q('SELECT COUNT(*) FROM interactions WHERE time >= ?',
                                  (since,)).fetchone()[0],
                   'users': q('SELECT COUNT(DISTINCT user_hash) FROM interactions '
                              'WHERE time >= ?', (since,)).fetchone()[0]}
            for outcome, n in q('SELECT outcome, COUNT(*) FROM interactions WHERE time >= ? '
                                'GROUP BY outcome', (since,)):
                out[outcome] = n
            for rating, n in q('SELECT rating, COUNT(*) FROM feedback WHERE time >= ? '
                               'GROUP BY rating', (since,)):
                out['thumbs_up' if rating > 0 else 'thumbs_down'] = n
            secs = [r[0] for r in q('SELECT seconds FROM interactions WHERE time >= ? AND '
                                    "outcome = 'ok' AND seconds IS NOT NULL ORDER BY seconds",
                                    (since,))]
        out['median_seconds'] = secs[len(secs) // 2] if secs else None
        return out

    def export_rows(self):
        """All interactions with their summed rating, for CSV export."""
        rows = self._exec(
            'SELECT i.*, (SELECT SUM(rating) FROM feedback f WHERE f.interaction_id = i.id) '
            'AS rating FROM interactions i ORDER BY i.id').fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['time'] = datetime.fromtimestamp(d['time'], timezone.utc).isoformat(
                timespec='seconds')
            out.append(d)
        return out
