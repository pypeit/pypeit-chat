"""
The Slack bot (DESIGN Section 8): Bolt's synchronous ``App`` with a
``SocketModeHandler`` (no inbound ports).  Bolt acknowledges each event at
once; the work runs on a bounded :class:`~pypeit_chat.slack.guard.WorkQueue`.

:class:`Bot` holds the front-end logic and only needs a Slack Web API
client, so it is tested with a fake client.  Run as ``pypeit_chat_slack``.
"""
import argparse
import logging
import os
import stat
import sys
import threading
import time
from collections import Counter
from datetime import date

from pypeit_chat.answer import MODEL_DOWN, Answerer, strip_mentions
from pypeit_chat.config import REPO_DIR, Settings
from pypeit_chat.index import IndexNotReady
from pypeit_chat.slack import format as fmt
from pypeit_chat.slack.guard import BUSY, Busy, Guard, WorkQueue
from pypeit_chat.slack.history import ANSWER_EVENT, thread_history
from pypeit_chat.store import Store

log = logging.getLogger('pypeit_chat.slack')

#: Seconds between edits of a streaming answer (DESIGN 8.1).
EDIT_INTERVAL = 1.5
#: Alerts to admins when one user hits a rate limit this many times in a day,
#: or gets this many off-topic refusals in a day (DESIGN 11.6).
RATE_ALERT_AFTER = 3
OFF_TOPIC_ALERT_AFTER = 5
#: Seconds between repeated alerts of the same kind.
ALERT_EVERY = 600
WEEK = 7 * 86400
THUMBS = {'+1': 1, 'thumbsup': 1, '-1': -1, 'thumbsdown': -1}

HELP = """\
I'm *pypeit-chat*. I answer questions about *PypeIt {tag}*: how to set up and run \
reductions, what parameters do, and why a step failed. Answers come from the PypeIt \
documentation and source code, with links to the sources.

• In a channel, @mention me; in the thread, @mention me again for follow-ups.
• In a direct message, just ask.

Examples:
• How do I make a PypeIt file for my Keck/LRIS red data?
• What is the default S/N threshold for object finding and how do I lower it?
• PypeIt ran but no 1D spectra were extracted. Why?

React 👍 or 👎 to my answers to tell us how I did. Answers are written by an AI model \
and can be wrong: always check the linked sources."""


class Alerter:
    """
    Post alerts to ``ADMIN_CHANNEL_ID``, at most once per ``ALERT_EVERY``
    seconds per kind (DESIGN 11.6).
    """

    def __init__(self, client, channel, clock=time.time):
        self.client = client
        self.channel = channel
        self.clock = clock
        self.last = {}
        self.counts = Counter()
        self.day = date.today()

    def alert(self, kind, text, every=ALERT_EVERY):
        """Post ``text`` unless an alert of this kind was posted recently."""
        now = self.clock()
        if not self.channel or now - self.last.get(kind, -1e18) < every:
            return False
        self.last[kind] = now
        try:
            self.client.chat_postMessage(channel=self.channel, text=f':rotating_light: {text}')
        except Exception as e:
            log.error('could not post alert: %s', e)
        return True

    def note(self, kind, user_hash, threshold, text):
        """Count an event for a user today; alert once when it reaches ``threshold``."""
        if date.today() != self.day:
            self.counts.clear()
            self.day = date.today()
        self.counts[(kind, user_hash)] += 1
        if self.counts[(kind, user_hash)] == threshold:
            self.alert(f'{kind}:{user_hash}', text, every=0)


class Bot:
    """
    The bot's behaviour, independent of how events arrive.

    Parameters
    ----------
    settings : pypeit_chat.config.Settings
    client : slack_sdk.WebClient
        Or a fake with the same methods.
    answerer : pypeit_chat.answer.Answerer
    store : pypeit_chat.store.Store
    bot_user_id, bot_id : str
        The bot's identity (from ``auth.test``).
    home_team_id : str
        Our workspace.
    queue : WorkQueue, optional
    """

    def __init__(self, settings, client, answerer, store, bot_user_id, bot_id, home_team_id,
                 queue=None):
        self.s = settings
        self.client = client
        self.answerer = answerer
        self.store = store
        self.bot_user_id = bot_user_id
        self.bot_id = bot_id
        self.guard = Guard(settings, store, bot_user_id, home_team_id)
        self.queue = queue or WorkQueue(settings.max_concurrent, settings.max_queue)
        self.alerts = Alerter(client, settings.admin_channel_id)

    # --- Small helpers -------------------------------------------------------

    def tag(self):
        """The PypeIt release the index covers."""
        try:
            return self.answerer.holder.get().tag
        except IndexNotReady:
            return '?'

    def _call(self, method, **kwargs):
        try:
            return getattr(self.client, method)(**kwargs)
        except Exception as e:
            log.warning('%s failed: %s', method, e)
            return None

    def react(self, channel, ts, name):
        """Add a reaction to a message (errors are logged, not raised)."""
        self._call('reactions_add', channel=channel, timestamp=ts, name=name)

    def unreact(self, channel, ts, name):
        """Remove a reaction."""
        self._call('reactions_remove', channel=channel, timestamp=ts, name=name)

    def say(self, channel, thread_ts, text):
        """Post a trusted bot message in a thread (never in the channel)."""
        return self._call('chat_postMessage', channel=channel, thread_ts=thread_ts,
                          **fmt.plain_message(text))

    def whisper(self, channel, user, thread_ts, text):
        """Post an ephemeral message only ``user`` sees."""
        return self._call('chat_postEphemeral', channel=channel, user=user,
                          thread_ts=thread_ts, text=text)

    # --- Events ----------------------------------------------------------------

    def handle_message(self, event, body):
        """
        Handle an ``app_mention`` or a direct message.

        Parameters
        ----------
        event : dict
        body : dict
            The envelope (``event_id``, ``team_id``).
        """
        text = strip_mentions(event.get('text', ''))
        d = self.guard.check(event, body, text)
        if d.action == 'ignore':
            return
        user, channel = event['user'], event['channel']
        thread_ts = event.get('thread_ts') or event['ts']
        base = {'event_id': body.get('event_id'), 'channel_type': event.get('channel_type'),
                'channel': channel, 'user_id': user, 'question': text, 'tag': self.tag()}
        if d.action in ('refuse', 'reply'):
            log.info('refused (%s)', d.reason)
            if d.action == 'refuse':
                self.whisper(channel, user, thread_ts, d.message)
            else:
                self.say(channel, thread_ts, d.message)
            self.store.log(outcome='refused', **base)
            return

        command = text.strip().lower().rstrip('.!?')
        if command in ('help', ''):
            self.say(channel, thread_ts, HELP.format(tag=self.tag()))
            return
        if command in ('pause', 'resume', 'status'):
            if self.guard.is_admin(user):
                self.admin_command(command, channel, thread_ts, user)
                return
            if command != 'status':
                self.whisper(channel, user, thread_ts, 'Only admins can pause or resume me.')
                return

        d = self.guard.check_rate(user, channel)
        if d.action == 'rate-limited':
            self.whisper(channel, user, thread_ts, d.message)
            self.store.log(outcome='rate-limited', **base)
            self.alerts.note('rate', self.store.user_hash(user), RATE_ALERT_AFTER,
                             f'A user hit the {d.reason} rate limit {RATE_ALERT_AFTER} '
                             f'times today (user {self.store.user_hash(user)}).')
            return

        self.react(channel, event['ts'], 'eyes')
        status = {'ts': None, 'pos': None}

        def on_position(pos):
            if pos <= 0 or pos == status['pos']:
                return
            status['pos'] = pos
            msg = f"You're #{pos} in line."
            if status['ts'] is None:
                resp = self.say(channel, thread_ts, msg)
                status['ts'] = resp['ts'] if resp else None
            else:
                self._call('chat_update', channel=channel, ts=status['ts'], text=msg)

        def job():
            self.answer_job(event, text, thread_ts, status, base)

        try:
            pos = self.queue.submit(user, job, on_position)
        except Busy:
            self.say(channel, thread_ts, BUSY)
            self.react(channel, event['ts'], 'warning')
            self.unreact(channel, event['ts'], 'eyes')
            self.store.log(outcome='refused', **base)
            self.alerts.alert('busy', 'The question queue is full.')
            return
        on_position(pos)

    def answer_job(self, event, text, thread_ts, status, base):
        """Answer one question (runs on a worker thread)."""
        channel = event['channel']
        try:
            searching = f'Searching PypeIt {self.tag()}…'
            if status['ts'] is None:
                resp = self.say(channel, thread_ts, searching)
                status['ts'] = resp['ts'] if resp else None
            else:
                self._call('chat_update', channel=channel, ts=status['ts'], text=searching)
            history = []
            if event.get('thread_ts'):
                history = thread_history(self.client, channel, event['thread_ts'],
                                         self.bot_user_id, self.bot_id, before_ts=event['ts'])
            last = [0.0]

            def on_text(partial):
                now = time.monotonic()
                if status['ts'] and now - last[0] >= EDIT_INTERVAL:
                    last[0] = now
                    self._call('chat_update', channel=channel, ts=status['ts'],
                               **fmt.progress_message(partial))

            ans = self.answerer.answer(text, history, on_text=on_text)
            if ans.outcome == 'ok':
                msg = fmt.answer_message(ans)
                metadata = {'event_type': ANSWER_EVENT, 'event_payload': {'tag': ans.tag}}
            else:
                msg, metadata = fmt.plain_message(ans.text), None
            if status['ts']:
                self._call('chat_update', channel=channel, ts=status['ts'], metadata=metadata,
                           **msg)
            else:
                resp = self._call('chat_postMessage', channel=channel, thread_ts=thread_ts,
                                  metadata=metadata, **msg)
                status['ts'] = resp['ts'] if resp else None
            self.unreact(channel, event['ts'], 'eyes')
            if ans.outcome == 'ok':
                self.react(channel, event['ts'], 'white_check_mark')
            elif ans.outcome == 'error':
                self.react(channel, event['ts'], 'warning')
            outcome = {'ok': 'ok', 'off_topic': 'refused'}.get(ans.outcome, 'error')
            self.store.log(query=ans.query, answer=ans.text, model=ans.model, sha=ans.sha,
                           seconds=ans.seconds, tokens=ans.tokens, outcome=outcome,
                           answer_ts=status['ts'],
                           sources=[s.chunk['id'] for _, s in ans.sources],
                           **dict(base, tag=ans.tag or base['tag']))
            if ans.outcome == 'off_topic':
                self.alerts.note('off-topic', self.store.user_hash(event['user']),
                                 OFF_TOPIC_ALERT_AFTER,
                                 f'{OFF_TOPIC_ALERT_AFTER} off-topic questions today from user '
                                 f'{self.store.user_hash(event["user"])}.')
            if ans.text == MODEL_DOWN:
                self.alerts.alert('model-down', f'The model server at {self.s.llm_base_url} '
                                                'is not responding.')
            elif ans.outcome == 'error':
                self.alerts.alert('error', f'Answering failed: {ans.text}')
        except Exception as e:
            log.exception('answering failed')
            sorry = 'Sorry, something went wrong while answering. Please try again later.'
            if status['ts']:
                self._call('chat_update', channel=channel, ts=status['ts'], text=sorry)
            else:
                self.say(channel, thread_ts, sorry)
            self.unreact(channel, event['ts'], 'eyes')
            self.react(channel, event['ts'], 'warning')
            self.store.log(outcome='error', **base)
            self.alerts.alert('error', f'Answering failed: {type(e).__name__}: {e}')

    def admin_command(self, command, channel, thread_ts, user):
        """``pause``, ``resume`` or ``status`` from an admin."""
        if command == 'pause':
            self.guard.set_paused(True)
            self.say(channel, thread_ts, 'Paused. I will refuse questions until `resume`.')
            self.alerts.alert('pause', 'pypeit-chat was paused by an admin.', every=0)
        elif command == 'resume':
            self.guard.set_paused(False)
            self.say(channel, thread_ts, 'Resumed.')
            self.alerts.alert('pause', 'pypeit-chat was resumed by an admin.', every=0)
        else:
            q = self.queue
            self.say(channel, thread_ts,
                     f"PypeIt {self.tag()} · {self.s.chat_model} · "
                     f"{'paused' if self.guard.paused else 'running'} · "
                     f"{q.running} answering, {len(q.waiting)} waiting")

    def handle_reaction(self, event, body):
        """Record 👍/👎 on one of the bot's answers (DESIGN F9)."""
        item = event.get('item') or {}
        name = (event.get('reaction') or '').split('::')[0]
        user = event.get('user')
        if item.get('type') != 'message' or name not in THUMBS or not user \
                or user == self.bot_user_id:
            return
        if event.get('item_user') and event['item_user'] != self.bot_user_id:
            return
        iid = self.store.interaction_for(item.get('channel'), item.get('ts'))
        if iid is not None:
            self.store.feedback(iid, user, THUMBS[name])

    # --- Housekeeping -------------------------------------------------------------

    def housekeeping(self):
        """Purge old rows and post the weekly summary when due."""
        self.store.purge()
        now = time.time()
        last = float(self.store.get('last_summary', 0))
        if last == 0:
            self.store.put('last_summary', now)
        elif now - last >= WEEK:
            s = self.store.summary(last)
            self.alerts.alert('summary', 'Weekly usage: ' + ', '.join(
                f'{k} {v}' for k, v in s.items() if v is not None), every=0)
            self.store.put('last_summary', now)


def check_env_file(path=REPO_DIR / '.env'):
    """Warn if ``.env`` is readable by others (DESIGN N5)."""
    if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
        log.warning('%s is readable by other users; run: chmod 600 %s', path, path)


def health(settings):
    """
    Check the model endpoint, both models, the index and the Slack tokens.

    Returns
    -------
    list of str
        Problems (empty if healthy).
    """
    from slack_sdk import WebClient
    from pypeit_chat.index import Index
    from pypeit_chat.llm import LLMClient
    problems = LLMClient(settings).check()
    try:
        idx = Index(settings.index_dir)
        print(f"index: PypeIt {idx.tag} ({idx.meta.get('sha', '')[:9]}), {len(idx.chunks)} "
              f"chunks, {'hybrid' if idx.has_vectors else 'keyword-only'}")
        if idx.has_vectors and idx.meta.get('embed_model') != settings.embed_model:
            problems.append(f"index was embedded with {idx.meta.get('embed_model')}, "
                            f'EMBED_MODEL is {settings.embed_model}')
    except Exception as e:
        problems.append(f'index: {e}')
    for name, token, prefix in (('SLACK_BOT_TOKEN', settings.slack_bot_token, 'xoxb-'),
                                ('SLACK_APP_TOKEN', settings.slack_app_token, 'xapp-')):
        if not token.startswith(prefix):
            problems.append(f'{name} is missing or does not start with {prefix}')
    if not problems:
        try:
            auth = WebClient(token=settings.slack_bot_token).auth_test()
            print(f"slack: bot {auth['user']} in team {auth['team']} ({auth['team_id']})")
            if settings.home_team_id and settings.home_team_id != auth['team_id']:
                problems.append(f"HOME_TEAM_ID {settings.home_team_id} is not the bot's "
                                f"team {auth['team_id']}")
            WebClient(token=settings.slack_app_token).apps_connections_open()
        except Exception as e:
            problems.append(f'slack: {e}')
    return problems


def create_app(settings, answerer=None, store=None):
    """
    Build the Bolt app and the :class:`Bot`.

    Returns
    -------
    tuple
        (slack_bolt.App, Bot)
    """
    from slack_bolt import App
    app = App(token=settings.slack_bot_token)
    auth = app.client.auth_test()
    home = settings.home_team_id or auth['team_id']
    if not settings.home_team_id:
        log.warning('HOME_TEAM_ID is not set; using the bot\'s team %s', home)
    bot = Bot(settings, app.client, answerer or Answerer(settings),
              store or Store(settings.db_path, settings), auth['user_id'], auth.get('bot_id'),
              home)

    @app.event('app_mention')
    def on_mention(event, body):
        bot.handle_message(event, body)

    @app.event('message')
    def on_message(event, body):
        if event.get('channel_type') == 'im':
            bot.handle_message(event, body)

    @app.event('reaction_added')
    def on_reaction(event, body):
        bot.handle_reaction(event, body)

    return app, bot


def parser():
    p = argparse.ArgumentParser(description='Run the pypeit-chat Slack bot (Socket Mode).')
    p.add_argument('--check', action='store_true',
                   help='Check the model endpoint, models, index and Slack tokens, then exit')
    p.add_argument('-v', '--verbose', action='store_true', help='Debug logging')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if not args.verbose:
        logging.getLogger('httpx').setLevel(logging.WARNING)
    settings = Settings.load()
    check_env_file()
    if args.check:
        problems = health(settings)
        for p in problems:
            print(f'PROBLEM: {p}')
        print('OK' if not problems else f'{len(problems)} problem(s)')
        return 1 if problems else 0
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    app, bot = create_app(settings)

    def housekeeping_loop():
        while True:
            try:
                bot.housekeeping()
            except Exception:
                log.exception('housekeeping failed')
            time.sleep(3600)
    threading.Thread(target=housekeeping_loop, daemon=True, name='housekeeping').start()
    bot.alerts.alert('startup', f'pypeit-chat started (PypeIt {bot.tag()}, '
                                f'{settings.chat_model}, pid {os.getpid()}).', every=0)
    log.info('starting Socket Mode (PypeIt %s, %s)', bot.tag(), settings.chat_model)
    SocketModeHandler(app, settings.slack_app_token).start()
    return 0


if __name__ == '__main__':
    sys.exit(main())
