"""
Who may ask, how much, and what they may send (DESIGN Sections 11.1-11.3,
11.6).  Every check runs before any model call, so refused requests cost
nothing.  :class:`WorkQueue` enforces the global concurrency and queue
limits and one active question per user.
"""
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

#: Message subtypes that carry a person's question; all others (edits,
#: deletions, joins, bot messages...) are ignored.
QUESTION_SUBTYPES = (None, 'file_share', 'thread_broadcast')

REFUSE_TEAM = 'Sorry, I only answer members of this workspace.'
REFUSE_BLOCKED = 'Sorry, I can\'t answer your questions.'
REFUSE_CHANNEL = 'Sorry, I\'m not available in this channel yet.'
REFUSE_USER = 'Sorry, I\'m in a limited beta and can\'t answer your questions yet.'
REFUSE_SIZE = 'Your question is too long ({n} characters; the limit is {limit}). Please shorten it.'
REFUSE_USER_HOUR = 'You\'ve reached the limit of {limit} questions per hour. Please try again later.'
REFUSE_USER_DAY = 'You\'ve reached the limit of {limit} questions per day. Please try again tomorrow.'
REFUSE_CHANNEL_HOUR = 'This channel has reached its limit of {limit} questions per hour. ' \
                      'Please try again later.'
PAUSED = 'I\'m temporarily paused. Please try again later.'
BUSY = 'Busy — please try again in a few minutes.'


@dataclass
class Decision:
    """
    The outcome of :meth:`Guard.check`.

    ``action`` is ``'answer'``, ``'ignore'`` (say nothing), ``'refuse'``
    (ephemeral ``message``), ``'reply'`` (``message`` in the thread) or
    ``'rate-limited'`` (ephemeral ``message``).
    """
    action: str
    reason: str = ''
    message: str = ''


class Guard:
    """
    Apply the abuse-protection rules to an incoming message.

    Parameters
    ----------
    settings : pypeit_chat.config.Settings
    store : pypeit_chat.store.Store
        For de-duplication and rate-limit counters.
    bot_user_id : str
        The bot's own user id (its own messages are ignored).
    home_team_id : str
        Our workspace; events from others are refused.
    """

    def __init__(self, settings, store, bot_user_id, home_team_id):
        self.s = settings
        self.store = store
        self.bot_user_id = bot_user_id
        self.home_team_id = home_team_id

    @property
    def paused(self):
        """True if the kill switch is on (the ``PAUSED`` file exists)."""
        return Path(self.s.paused_file).exists()

    def set_paused(self, paused):
        """Turn the kill switch on or off."""
        path = Path(self.s.paused_file)
        if paused:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        elif path.exists():
            path.unlink()

    def is_admin(self, user):
        """True if ``user`` may pause and resume the bot."""
        return user in self.s.admin_user_ids

    def ignorable(self, event):
        """
        True for events that must never be answered: bots (including this
        one), edits, deletions and other non-question subtypes (11.1.2).
        """
        if event.get('bot_id') or event.get('bot_profile'):
            return True
        if event.get('subtype') not in QUESTION_SUBTYPES:
            return True
        user = event.get('user')
        return not user or user == self.bot_user_id

    def check(self, event, body, text):
        """
        Decide what to do with a message.

        Parameters
        ----------
        event : dict
            The Slack event.
        body : dict
            The event envelope (``event_id``, ``team_id``).
        text : str
            The question with mentions removed.

        Returns
        -------
        Decision
        """
        if self.ignorable(event):
            return Decision('ignore', 'bot-or-subtype')
        team = event.get('user_team') or event.get('team') or body.get('team_id')
        if self.home_team_id and team != self.home_team_id:
            return Decision('refuse', 'other-team', REFUSE_TEAM)
        keys = [body.get('event_id'), f"{event.get('channel')}:{event.get('ts')}"]
        if not self.store.first_time(*keys):
            return Decision('ignore', 'duplicate')
        user, channel = event['user'], event.get('channel', '')
        if user in self.s.blocked_user_ids:
            return Decision('refuse', 'blocked', REFUSE_BLOCKED)
        is_dm = event.get('channel_type') == 'im'
        if self.s.allowed_channel_ids and not is_dm and channel not in self.s.allowed_channel_ids:
            return Decision('refuse', 'channel-not-allowed', REFUSE_CHANNEL)
        if self.s.allowed_user_ids and user not in self.s.allowed_user_ids:
            return Decision('refuse', 'user-not-allowed', REFUSE_USER)
        if self.paused and not self.is_admin(user):
            return Decision('reply', 'paused', PAUSED)
        if len(text) > self.s.max_question_chars:
            return Decision('refuse', 'too-long',
                            REFUSE_SIZE.format(n=len(text), limit=self.s.max_question_chars))
        return Decision('answer')

    def check_rate(self, user, channel):
        """
        Check and count the rate limits for one question (11.2.1-2).

        Returns
        -------
        Decision
            ``answer`` (and the question is counted) or ``rate-limited``.
        """
        h = self.store.user_hash(user)
        user_hour, user_day, chan_hour = self.store.usage(h, channel)
        if user_hour >= self.s.rate_per_user_hour:
            return Decision('rate-limited', 'user-hour',
                            REFUSE_USER_HOUR.format(limit=self.s.rate_per_user_hour))
        if user_day >= self.s.rate_per_user_day:
            return Decision('rate-limited', 'user-day',
                            REFUSE_USER_DAY.format(limit=self.s.rate_per_user_day))
        if chan_hour >= self.s.rate_per_channel_hour:
            return Decision('rate-limited', 'channel-hour',
                            REFUSE_CHANNEL_HOUR.format(limit=self.s.rate_per_channel_hour))
        self.store.count(h, channel)
        return Decision('answer')


class Busy(Exception):
    """The queue is full."""


class WorkQueue:
    """
    Run jobs on ``max_concurrent`` worker threads with at most ``max_queue``
    waiting, and at most one running job per user (11.2.3-4).

    Parameters
    ----------
    max_concurrent : int
    max_queue : int
    """

    def __init__(self, max_concurrent, max_queue):
        self.max_concurrent = max(1, max_concurrent)
        self.max_queue = max_queue
        self.waiting = deque()          # (user, job, on_position)
        self.active_users = set()
        self.running = 0
        self.cond = threading.Condition()
        self.closed = False
        self.threads = [threading.Thread(target=self._worker, daemon=True, name=f'worker-{i}')
                        for i in range(self.max_concurrent)]
        for t in self.threads:
            t.start()

    def submit(self, user, job, on_position=None):
        """
        Queue a job.

        Parameters
        ----------
        user : str
        job : callable
            Called with no arguments on a worker thread.
        on_position : callable, optional
            Called with the job's place in line (1 = next) whenever it is
            waiting and its place changes.

        Returns
        -------
        int
            Place in line (0 if it can start at once).

        Raises
        ------
        Busy
            If ``max_queue`` jobs are already waiting.
        """
        with self.cond:
            if len(self.waiting) >= self.max_queue:
                raise Busy()
            self.waiting.append((user, job, on_position))
            pos = self._position(job)
            self.cond.notify_all()
        return pos

    def _position(self, job):
        """Place in line: waiting jobs that will run first, plus one."""
        free = self.running < self.max_concurrent
        ahead = 0
        for u, j, _ in self.waiting:
            if j is job:
                break
            ahead += 1
        blocked_by_user = any(u in self.active_users for u, j, _ in self.waiting if j is job)
        if free and ahead == 0 and not blocked_by_user:
            return 0
        return ahead + 1

    def _next(self):
        for i, (user, job, cb) in enumerate(self.waiting):
            if user not in self.active_users:
                del self.waiting[i]
                return user, job
        return None

    def _worker(self):
        while True:
            with self.cond:
                item = None
                while not self.closed and (item := self._next()) is None:
                    self.cond.wait()
                if self.closed and item is None:
                    return
                user, job = item
                self.active_users.add(user)
                self.running += 1
                self.cond.notify_all()
                updates = [(cb, self._position(j)) for u, j, cb in self.waiting if cb]
            for cb, pos in updates:
                try:
                    cb(pos)
                except Exception:    # a failed position update must not stop the worker
                    pass
            try:
                job()
            finally:
                with self.cond:
                    self.active_users.discard(user)
                    self.running -= 1
                    self.cond.notify_all()

    def join(self, timeout=30.0):
        """Wait until nothing is waiting or running (for tests and shutdown)."""
        with self.cond:
            return self.cond.wait_for(lambda: not self.waiting and self.running == 0, timeout)

    def close(self):
        """Stop the workers after the current jobs."""
        with self.cond:
            self.closed = True
            self.cond.notify_all()
