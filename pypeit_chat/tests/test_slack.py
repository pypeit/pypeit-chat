"""
Tests of the Slack bot with a fake Slack client: every abuse-protection rule
of DESIGN Section 11, the queue, progress, feedback and admin commands.
"""
import itertools
import threading

import pytest

from pypeit_chat import answer as answer_mod
from pypeit_chat.answer import Answerer
from pypeit_chat.index import IndexHolder
from pypeit_chat.llm import LLMClient
from pypeit_chat.slack import guard as guard_mod
from pypeit_chat.slack.app import HELP, Bot
from pypeit_chat.slack.guard import Busy, WorkQueue
from pypeit_chat.slack.history import ANSWER_EVENT
from pypeit_chat.store import Store
from pypeit_chat.tests import mock_llm
from pypeit_chat.tests.conftest import test_settings

TEAM, BOT_USER, BOT_ID = 'T1', 'UBOT', 'BBOT'


class FakeSlack:
    """Records Web API calls and returns plausible responses."""

    def __init__(self, replies=None):
        self.calls = []
        self.ts = (f'100.{i}' for i in itertools.count(1))
        self.replies = replies or []
        self.lock = threading.Lock()

    def _record(self, name, kw):
        with self.lock:
            self.calls.append((name, kw))

    def chat_postMessage(self, **kw):
        self._record('post', kw)
        return {'ok': True, 'ts': next(self.ts)}

    def chat_update(self, **kw):
        self._record('update', kw)
        return {'ok': True}

    def chat_postEphemeral(self, **kw):
        self._record('ephemeral', kw)
        return {'ok': True}

    def reactions_add(self, **kw):
        self._record('react', kw)

    def reactions_remove(self, **kw):
        self._record('unreact', kw)

    def conversations_replies(self, **kw):
        self._record('replies', kw)
        return {'messages': self.replies}

    def of(self, name):
        return [kw for n, kw in self.calls if n == name]


@pytest.fixture
def make_bot(tmp_path, index_dir):
    bots = []

    def make(model='mock', down=False, queue=None, **changes):
        s = test_settings(tmp_path, chat_model=model, index_dir=index_dir,
                          admin_channel_id='CADMIN', **changes)
        rec = mock_llm.Recorder()
        answerer = Answerer(s, IndexHolder(index_dir),
                            LLMClient(s, transport=mock_llm.transport(rec, down=down)))
        slack = FakeSlack()
        bot = Bot(s, slack, answerer, Store(':memory:', s), BOT_USER, BOT_ID, TEAM,
                  queue=queue or WorkQueue(s.max_concurrent, s.max_queue))
        bot.rec, bot.slack = rec, slack
        bots.append(bot)
        return bot
    yield make
    for b in bots:
        b.queue.close()


counter = itertools.count(1)


def mention(text='How do I change bspline_spacing?', user='U1', channel='C1', **kw):
    n = next(counter)
    event = {'type': 'app_mention', 'user': user, 'text': f'<@{BOT_USER}> {text}',
             'ts': f'{n}.0', 'channel': channel, 'team': TEAM, 'channel_type': 'channel'}
    event.update(kw)
    return event, {'event_id': f'Ev{n}', 'team_id': TEAM}


def running(queue, n=1):
    """Wait until ``n`` jobs are running."""
    with queue.cond:
        assert queue.cond.wait_for(lambda: queue.running == n, 5)


def ask(bot, *args, **kw):
    event, body = mention(*args, **kw)
    bot.handle_message(event, body)
    bot.queue.join()
    return event


def model_calls(bot):
    return [b for p, b in bot.rec.requests if p.endswith('/chat/completions')]


def final_text(bot):
    upd = bot.slack.of('update')[-1]
    return ' '.join(b['text']['text'] for b in upd.get('blocks', []) if b['type'] == 'section') \
        or upd['text']


# --- Normal flow --------------------------------------------------------------------------


def test_answer_in_thread_with_progress_and_reactions(make_bot):
    bot = make_bot()
    event = ask(bot)
    posts = bot.slack.of('post')
    assert posts[0]['text'] == 'Searching PypeIt 2.0.1…'
    assert posts[0]['thread_ts'] == event['ts']
    final = bot.slack.of('update')[-1]
    assert final['metadata']['event_type'] == ANSWER_EVENT
    assert any(b['type'] == 'context' and 'PypeIt 2.0.1 · mock' in b['elements'][0]['text']
               for b in final['blocks'])
    reactions = [(n, kw['name']) for n, kw in bot.slack.calls if n in ('react', 'unreact')]
    assert reactions == [('react', 'eyes'), ('unreact', 'eyes'), ('react', 'white_check_mark')]
    row = bot.store.export_rows()[-1]
    assert row['outcome'] == 'ok' and row['answer_ts'] == '100.1'


def test_dm_answered_without_mention(make_bot):
    bot = make_bot()
    event, body = mention(channel='D1', channel_type='im', type='message')
    event['text'] = 'How do I change bspline_spacing?'
    bot.handle_message(event, body)
    bot.queue.join()
    assert len(model_calls(bot)) == 1


def test_followup_reads_thread(make_bot):
    bot = make_bot()
    bot.slack.replies = [{'ts': '1.0', 'user': 'U1', 'text': f'<@{BOT_USER}> sky question'}]
    ask(bot, 'and for LRIS?', thread_ts='1.0')
    assert bot.slack.of('replies')[0]['ts'] == '1.0'
    msgs = model_calls(bot)[-1]['messages']
    assert msgs[1] == {'role': 'user', 'content': 'sky question'}


def test_help(make_bot):
    bot = make_bot()
    ask(bot, 'help')
    assert bot.slack.of('post')[0]['text'] == HELP.format(tag='2.0.1')
    assert model_calls(bot) == []


# --- 11.1 Who can ask ------------------------------------------------------------------------


def test_other_team_refused(make_bot):
    bot = make_bot()
    ask(bot, user_team='TOTHER')
    assert bot.slack.of('ephemeral')[0]['text'] == guard_mod.REFUSE_TEAM
    assert model_calls(bot) == [] and bot.slack.of('post') == []


def test_bots_edits_and_self_ignored(make_bot):
    bot = make_bot()
    ask(bot, bot_id='B2')
    ask(bot, user=BOT_USER)
    ask(bot, subtype='message_changed')
    ask(bot, subtype='bot_message')
    assert bot.slack.calls == [] and model_calls(bot) == []


def test_allowlists_and_blocklist(make_bot):
    bot = make_bot(allowed_channel_ids=frozenset({'CBETA'}), allowed_user_ids=frozenset({'U1'}),
                   blocked_user_ids=frozenset({'UBAD'}))
    ask(bot, channel='COTHER')
    ask(bot, user='U2', channel='CBETA')
    ask(bot, user='UBAD', channel='CBETA')
    texts = [kw['text'] for kw in bot.slack.of('ephemeral')]
    assert texts == [guard_mod.REFUSE_CHANNEL, guard_mod.REFUSE_USER, guard_mod.REFUSE_BLOCKED]
    assert model_calls(bot) == []
    ask(bot, user='U1', channel='CBETA')
    ask(bot, user='U1', channel='D9', channel_type='im')       # DMs: no channel allowlist
    assert len(model_calls(bot)) == 2
    assert [r['outcome'] for r in bot.store.export_rows()].count('refused') == 3


# --- 11.2 How much they can ask ------------------------------------------------------------


def test_per_user_hourly_limit(make_bot):
    bot = make_bot(rate_per_user_hour=2)
    for _ in range(3):
        ask(bot)
    assert len(model_calls(bot)) == 2
    assert bot.slack.of('ephemeral')[-1]['text'] == \
        guard_mod.REFUSE_USER_HOUR.format(limit=2)
    assert bot.store.export_rows()[-1]['outcome'] == 'rate-limited'
    ask(bot, user='U2')                                    # other users unaffected
    assert len(model_calls(bot)) == 3


def test_per_channel_limit(make_bot):
    bot = make_bot(rate_per_channel_hour=1)
    ask(bot, user='U1')
    ask(bot, user='U2')
    assert len(model_calls(bot)) == 1
    assert 'This channel' in bot.slack.of('ephemeral')[-1]['text']


def test_rate_limit_alert(make_bot, monkeypatch):
    monkeypatch.setattr('pypeit_chat.slack.app.RATE_ALERT_AFTER', 2)
    bot = make_bot(rate_per_user_hour=0)
    ask(bot)
    ask(bot)
    alerts = [kw for kw in bot.slack.of('post') if kw['channel'] == 'CADMIN']
    assert len(alerts) == 1 and 'rate limit' in alerts[0]['text']


def test_duplicate_events_processed_once(make_bot):
    bot = make_bot()
    event, body = mention()
    bot.handle_message(event, body)
    bot.handle_message(event, body)                           # Slack retry
    bot.handle_message(event, dict(body, event_id='EvOther'))   # same message, new id
    bot.queue.join()
    assert len(model_calls(bot)) == 1


def test_queue_full_is_never_silent(make_bot):
    gate = threading.Event()
    queue = WorkQueue(1, 1)
    queue.submit('x', gate.wait)                # running
    running(queue)
    queue.submit('y', gate.wait)                # waiting: the queue is full
    bot = make_bot(queue=queue)
    event, body = mention()
    bot.handle_message(event, body)
    assert bot.slack.of('post')[0]['text'] == guard_mod.BUSY
    assert ('react', 'warning') in [(n, kw.get('name')) for n, kw in bot.slack.calls]
    gate.set()
    queue.join()


def test_place_in_line_shown(make_bot):
    gate = threading.Event()
    queue = WorkQueue(1, 5)
    queue.submit('x', gate.wait)
    running(queue)
    bot = make_bot(queue=queue)
    event, body = mention()
    bot.handle_message(event, body)
    assert bot.slack.of('post')[0]['text'] == "You're #1 in line."
    gate.set()
    queue.join()
    # The place-in-line message becomes the answer.
    assert bot.slack.of('update')[0]['text'] == 'Searching PypeIt 2.0.1…'
    assert bot.slack.of('update')[-1]['ts'] == '100.1'


def test_work_queue_one_active_question_per_user():
    queue = WorkQueue(2, 5)
    order, gate = [], threading.Event()

    def job(name, wait=False):
        def run():
            order.append(f'start {name}')
            if wait:
                gate.wait(5)
            order.append(f'end {name}')
        return run
    queue.submit('A', job('a1', wait=True))
    queue.submit('A', job('a2'))
    queue.submit('B', job('b1'))
    queue.join(timeout=0.5)
    assert 'start a2' not in order and 'end b1' in order        # B overtook A's 2nd
    gate.set()
    assert queue.join()
    assert order.index('start a2') > order.index('end a1')
    queue.close()


def test_work_queue_busy():
    gate = threading.Event()
    queue = WorkQueue(1, 1)
    queue.submit('a', gate.wait)
    running(queue)
    queue.submit('b', gate.wait)
    with pytest.raises(Busy):
        queue.submit('c', gate.wait)
    gate.set()
    queue.join()
    queue.close()


# --- 11.3 What they can send ----------------------------------------------------------------


def test_oversize_question_refused(make_bot):
    bot = make_bot(max_question_chars=50)
    ask(bot, 'x' * 51)
    assert 'too long' in bot.slack.of('ephemeral')[0]['text']
    assert model_calls(bot) == []


def test_off_topic_refused_without_model(make_bot):
    bot = make_bot()
    ask(bot, 'What is the capital of France?')
    assert model_calls(bot) == []
    assert final_text(bot) == answer_mod.OFF_TOPIC
    assert bot.store.export_rows()[-1]['outcome'] == 'refused'


def test_files_never_fetched(make_bot):
    bot = make_bot()
    ask(bot, subtype='file_share', files=[{'url_private': 'https://files.slack.com/x'}])
    assert len(model_calls(bot)) == 1
    assert not any('files' in str(kw) for _, kw in bot.slack.calls)


# --- 11.4 What the bot can post --------------------------------------------------------------


def test_model_output_sanitized(make_bot, monkeypatch):
    monkeypatch.setattr(mock_llm, 'ANSWER',
                        '<!channel> @here <@U999> see [login](https://evil.example.com/x) and '
                        'https://pypeit.readthedocs.io/en/2.0.1/setup.html [1]')
    bot = make_bot()
    ask(bot)
    text = final_text(bot)
    assert '<!channel>' not in text and '&lt;!channel&gt;' in text
    assert '<@U999>' not in text and '@here' not in text
    assert '<https://evil' not in text
    assert '<https://pypeit.readthedocs.io/en/2.0.1/setup.html>' in text
    assert all(kw.get('thread_ts') for kw in bot.slack.of('post'))   # threads only


# --- 11.6 Watching and stopping it -------------------------------------------------------------


def test_pause_and_resume(make_bot):
    bot = make_bot(admin_user_ids=frozenset({'UADMIN'}))
    ask(bot, 'pause', user='U1')
    assert not bot.guard.paused
    assert 'Only admins' in bot.slack.of('ephemeral')[-1]['text']
    ask(bot, 'pause', user='UADMIN')
    assert bot.guard.paused
    ask(bot, user='U1')
    assert bot.slack.of('post')[-1]['text'] == guard_mod.PAUSED
    assert model_calls(bot) == []
    ask(bot, 'resume', user='UADMIN')
    assert not bot.guard.paused
    ask(bot, user='U1')
    assert len(model_calls(bot)) == 1


def test_paused_file_is_a_kill_switch(make_bot, tmp_path):
    bot = make_bot()
    bot.s.paused_file.touch()
    ask(bot)
    assert bot.slack.of('post')[-1]['text'] == guard_mod.PAUSED


def test_model_down_reply_and_alert(make_bot):
    bot = make_bot(down=True)
    ask(bot)
    assert final_text(bot) == answer_mod.MODEL_DOWN
    assert ('react', 'warning') in [(n, kw.get('name')) for n, kw in bot.slack.calls]
    alerts = [kw['text'] for kw in bot.slack.of('post') if kw['channel'] == 'CADMIN']
    assert any('not responding' in a for a in alerts)
    assert bot.store.export_rows()[-1]['outcome'] == 'error'


# --- Feedback ----------------------------------------------------------------------------------


def test_reactions_recorded_on_answers_only(make_bot):
    bot = make_bot()
    ask(bot)
    answer_ts = bot.store.export_rows()[-1]['answer_ts']
    item = {'type': 'message', 'channel': 'C1', 'ts': answer_ts}
    bot.handle_reaction({'reaction': '+1::skin-tone-3', 'user': 'U2', 'item': item,
                         'item_user': BOT_USER}, {})
    bot.handle_reaction({'reaction': 'tada', 'user': 'U3', 'item': item}, {})
    bot.handle_reaction({'reaction': '-1', 'user': 'U4', 'item': dict(item, ts='9.9')}, {})
    bot.handle_reaction({'reaction': '-1', 'user': BOT_USER, 'item': item}, {})
    assert bot.store.export_rows()[-1]['rating'] == 1
