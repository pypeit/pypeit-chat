"""
Tests of Slack formatting and output sanitizing (DESIGN 8.2, 11.4), thread
history (8.3) and the SQLite store (10).
"""
import pytest

from pypeit_chat.answer import Answer
from pypeit_chat.index import Source
from pypeit_chat.slack import format as fmt
from pypeit_chat.slack.history import ANSWER_EVENT, messages_to_turns
from pypeit_chat.store import Store
from pypeit_chat.tests.conftest import test_settings

DOC = {'id': 'doc:a#b:0', 'kind': 'doc', 'title': 'Sky <Subtraction>',
       'url': 'https://pypeit.readthedocs.io/en/2.0.1/skysub.html#global', 'path': 'doc/s.rst'}
CODE = {'id': 'code:pypeit.core.skysub.global_skysub', 'kind': 'code', 'codetype': 'def',
        'title': 'pypeit.core.skysub.global_skysub', 'start': 504, 'end': 629,
        'url': 'https://github.com/pypeit/PypeIt/blob/abc/pypeit/core/skysub.py#L504-L629',
        'path': 'pypeit/core/skysub.py', 'flags': ['Docs and code differ: x']}


# --- Markdown -> mrkdwn ---------------------------------------------------------


def test_basic_conversion():
    out = fmt.to_mrkdwn('## Steps\n**Bold** and *it* with `code_x`\n- one\n* two')
    assert out == '*Steps*\n*Bold* and _it_ with `code_x`\n• one\n• two'


def test_code_fences_kept_and_language_dropped():
    out = fmt.to_mrkdwn('Do:\n```ini\n[reduce]\n    **not bold** <x>\n```\nDone')
    assert out == 'Do:\n```\n[reduce]\n    **not bold** &lt;x&gt;\n```\nDone'


def test_citations_become_links():
    out = fmt.to_mrkdwn('Default is 0.6 [1][2], see [9].', {1: 'https://pypeit.readthedocs.io/a',
                                                            2: 'https://github.com/pypeit/b'})
    assert out == ('Default is 0.6 <https://pypeit.readthedocs.io/a|[1]>'
                   '<https://github.com/pypeit/b|[2]>, see [9].')


def test_escaping_blocks_control_sequences():
    out = fmt.to_mrkdwn('<!channel> <@U123> <#C1|x> <!subteam^S1> a & b')
    assert '<' not in out.replace('&lt;', '') and '&lt;!channel&gt;' in out
    assert '&lt;@U123&gt;' in out and 'a &amp; b' in out


def test_mass_pings_neutralized():
    out = fmt.to_mrkdwn('Hey @channel and @here and @everyone')
    for word in ('channel', 'here', 'everyone'):
        assert f'@{word}' not in out and f'@{fmt.ZWSP}{word}' in out


def test_link_allowlist():
    out = fmt.to_mrkdwn('[docs](https://pypeit.readthedocs.io/en/2.0.1/) '
                        '[login](https://evil.example.com/login) '
                        'https://github.com/pypeit/PypeIt/issues/1 '
                        'https://github.com/someone/else http://evil.com evil.com/x '
                        'file setup.py')
    assert '<https://pypeit.readthedocs.io/en/2.0.1/|docs>' in out
    assert '<https://github.com/pypeit/PypeIt/issues/1>' in out
    assert 'https://evil.example.com' not in out.replace('`https://evil.example.com/login`', '')
    assert '`https://github.com/someone/else`' in out
    assert '`http://evil.com`' in out and '`evil.com/x`' in out
    assert not any(seg.startswith(('<https://evil', '<http://evil', '<https://github.com/someone'))
                   for seg in out.split())


def test_link_allowed():
    assert fmt.link_allowed('https://pypeit.readthedocs.io/en/2.0.1/x.html')
    assert fmt.link_allowed('https://github.com/pypeit/PypeIt/blob/x')
    assert not fmt.link_allowed('https://github.com/evil/PypeIt')
    assert not fmt.link_allowed('https://pypeit.readthedocs.io.evil.com/')
    assert not fmt.link_allowed('https://user:pw@pypeit.readthedocs.io/')
    assert not fmt.link_allowed('javascript:alert(1)')


def test_split_blocks_limit_and_fences():
    text = 'para\n\n' + '```\n' + '\n'.join(f'line {i} ' * 10 for i in range(200)) + '\n```'
    pieces = fmt.split_blocks(text)
    assert len(pieces) > 1
    for p in pieces:
        assert len(p) <= fmt.BLOCK_MAX
        assert p.count('```') % 2 == 0


def test_truncate_closes_fence():
    text = 'x\n```\n' + 'y\n' * 3000
    out = fmt.truncate(text, 100)
    assert out.endswith('```\n(truncated)') and len(out) < 130


def answer(**kw):
    a = Answer(question='q', text='Use it [1], code [2].', tag='2.0.1', model='m', seconds=9.8)
    a.sources = [(1, Source(DOC, 1, 'docs')), (2, Source(CODE, 1, 'code'))]
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_answer_message():
    msg = fmt.answer_message(answer(flagged=True))
    texts = [b.get('text', {}).get('text') or b['elements'][0]['text'] for b in msg['blocks']]
    assert texts[0] == ('Use it <https://pypeit.readthedocs.io/en/2.0.1/skysub.html#global|[1]>, '
                        'code <https://github.com/pypeit/PypeIt/blob/abc/pypeit/core/skysub.py'
                        '#L504-L629|[2]>.')
    assert texts[1].startswith('⚠ Docs and code differ here') and '2.0.1' in texts[1]
    assert texts[2].startswith('*Sources — Docs:* [1] <https://pypeit') and \
        'Sky &lt;Subtraction&gt;' in texts[2]
    assert texts[3] == ('*Sources — Code:* [2] <https://github.com/pypeit/PypeIt/blob/abc/pypeit/'
                        'core/skysub.py#L504-L629|pypeit.core.skysub.global_skysub> · L504–629')
    assert texts[4] == ('PypeIt 2.0.1 · m · 9.8 s · AI answers can be wrong — check the sources')
    assert msg['text'] == 'Use it [1], code [2].'


def test_related_when_uncited():
    a = answer(text='No citations.')
    a.related, a.sources = a.sources, []
    texts = [b.get('text', {}).get('text') or b['elements'][0]['text']
             for b in fmt.answer_message(a)['blocks']]
    assert any(t.startswith('*Related — Docs:*') for t in texts)


def test_long_answer_split_and_capped():
    a = answer(text='word ' * 2000)
    msg = fmt.answer_message(a)
    sections = [b for b in msg['blocks'] if b['type'] == 'section']
    assert all(len(b['text']['text']) <= fmt.BLOCK_MAX for b in sections)
    assert sections[-1]['text']['text'].endswith('(truncated)')


# --- Thread history ----------------------------------------------------------------


def test_thread_to_turns():
    msgs = [
        {'ts': '1.0', 'user': 'U1', 'text': '<@UBOT> how do I flux?'},
        {'ts': '1.1', 'user': 'UBOT', 'text': 'Searching PypeIt 2.0.1…'},
        {'ts': '1.2', 'user': 'UBOT', 'bot_id': 'B1', 'text': 'Use pypeit_sensfunc [1] &amp; more',
         'metadata': {'event_type': ANSWER_EVENT}},
        {'ts': '1.3', 'bot_id': 'B9', 'text': 'another bot'},
        {'ts': '1.4', 'user': 'U2', 'subtype': 'channel_join', 'text': 'joined'},
        {'ts': '1.5', 'user': 'U1', 'text': '<@UBOT> and for LRIS?'},
    ]
    turns = messages_to_turns(msgs, 'UBOT', 'B1', before_ts='1.5')
    assert turns == [{'role': 'user', 'content': 'how do I flux?'},
                     {'role': 'assistant', 'content': 'Use pypeit_sensfunc & more'}]
    many = [{'ts': f'{i}.0', 'user': 'U1', 'text': 'x' * 4000} for i in range(10)]
    turns = messages_to_turns(many, 'UBOT')
    assert len(turns) == 6 and all(len(t['content']) == 3000 for t in turns)


# --- Store -------------------------------------------------------------------------------


def store(tmp_path, clock, **changes):
    return Store(':memory:', test_settings(tmp_path, **changes), clock=clock)


def test_text_only_with_log_text(tmp_path):
    now = [1e9]
    for log_text in (False, True):
        s = store(tmp_path, lambda: now[0], log_text=log_text)
        s.log(user_id='U1', question='secret q', answer='a', query='q', sources=['x'])
        row = s.export_rows()[0]
        assert (row['question'] == 'secret q') is log_text
        assert (row['answer'] == 'a') is log_text
        assert row['sources'] == '["x"]'
        assert row['user_hash'] != 'U1' and len(row['user_hash']) == 16


def test_salted_hashes(tmp_path):
    a = store(tmp_path, lambda: 0, hash_salt='one').user_hash('U1')
    b = store(tmp_path, lambda: 0, hash_salt='two').user_hash('U1')
    assert a != b


def test_salt_file_created_private(tmp_path):
    s = Store(tmp_path / 'db.sqlite3', test_settings(tmp_path, hash_salt='',
                                                     db_path=tmp_path / 'db.sqlite3'))
    path = tmp_path / 'hash_salt'
    assert path.exists() and (path.stat().st_mode & 0o777) == 0o600
    assert Store(tmp_path / 'db.sqlite3', test_settings(
        tmp_path, hash_salt='', db_path=tmp_path / 'db.sqlite3')).salt == s.salt


def test_retention_and_feedback(tmp_path):
    now = [1e9]
    s = store(tmp_path, lambda: now[0], log_retention_days=180)
    old = s.log(user_id='U1', channel='C', answer_ts='1.0')
    s.feedback(old, 'U2', 1)
    s.feedback(old, 'U2', -1)                    # replaces the earlier rating
    assert s.export_rows()[0]['rating'] == -1
    assert s.interaction_for('C', '1.0') == old
    now[0] += 181 * 86400
    new = s.log(user_id='U1')
    s.purge()
    assert [r['id'] for r in s.export_rows()] == [new]


def test_usage_windows_and_dedupe(tmp_path):
    now = [3600 * 1000 + 10]
    s = store(tmp_path, lambda: now[0])
    h = s.user_hash('U1')
    for _ in range(3):
        s.count(h, 'C1')
    s.count(h, 'C2')
    s.count(s.user_hash('U2'), 'C1')
    assert s.usage(h, 'C1') == (4, 4, 4)
    now[0] += 3600
    assert s.usage(h, 'C1') == (0, 4, 0)
    now[0] += 24 * 3600
    assert s.usage(h, 'C1') == (0, 0, 0)
    assert s.first_time('Ev1', 'C1:1.0') is True
    assert s.first_time('Ev2', 'C1:1.0') is False       # same message, new event id
    assert s.first_time('Ev1') is False


def test_summary(tmp_path):
    s = store(tmp_path, lambda: 100.0)
    i = s.log(user_id='U1', seconds=5.0)
    s.log(user_id='U2', outcome='refused')
    s.feedback(i, 'U1', 1)
    summary = s.summary(0)
    assert summary['questions'] == 2 and summary['users'] == 2
    assert summary['ok'] == 1 and summary['refused'] == 1 and summary['thumbs_up'] == 1
    assert summary['median_seconds'] == 5.0


def test_unknown_outcome(tmp_path):
    with pytest.raises(ValueError):
        store(tmp_path, lambda: 0).log(outcome='whatever')

