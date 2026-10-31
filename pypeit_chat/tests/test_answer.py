"""
Tests of answering (DESIGN 7.3, 7.4): prompt contents, version line,
follow-ups, the reasoning filter, citations, off-topic and failures.
"""
import time

import pytest

from pypeit_chat import answer as answer_mod
from pypeit_chat.answer import (Answerer, ThinkFilter, build_query, cited_numbers,
                                clean_history, code_excerpt, drop_bad_citations,
                                render_plain, strip_mentions, user_message)
from pypeit_chat.index import IndexHolder
from pypeit_chat.llm import LLMClient
from pypeit_chat.tests import mock_llm
from pypeit_chat.tests.conftest import test_settings


def make(tmp_path, index_dir, model='mock', down=False, recorder=None, **changes):
    s = test_settings(tmp_path, chat_model=model, index_dir=index_dir, **changes)
    return Answerer(s, IndexHolder(index_dir),
                    LLMClient(s, transport=mock_llm.transport(recorder, down=down)))


def chat_requests(rec):
    return [b for p, b in rec.requests if p.endswith('/chat/completions')]


def test_answer_has_version_sources_and_cited_only(tmp_path, index_dir):
    seen = []
    ans = make(tmp_path, index_dir).answer('How do I change bspline_spacing?',
                                           on_text=seen.append)
    assert ans.outcome == 'ok'
    assert ans.text.startswith('Set `bspline_spacing`')
    assert ans.version_line == 'Answer for PypeIt 2.0.1'
    assert [k for k, _ in ans.sources] == [1, 2]          # the mock cites [1] and [2]
    assert ans.related == []
    assert seen and seen[-1].strip() == ans.text
    out = render_plain(ans)
    assert 'Answer for PypeIt 2.0.1' in out and 'Docs:' in out


def test_prompt_contents(tmp_path, index_dir):
    rec = mock_llm.Recorder()
    make(tmp_path, index_dir, recorder=rec).answer('What is the default snr_thresh?')
    msgs = chat_requests(rec)[-1]['messages']
    assert msgs[0]['role'] == 'system' and 'PypeIt 2.0.1' in msgs[0]['content']
    user = msgs[-1]['content']
    assert '[1] DOCS (parameter reference)' in user
    assert 'CODE (source code)' in user
    assert 'Parameter-reference entries among the sources' in user
    assert 'Code facts for' in user and 'ParSet FindObjPar' in user
    assert 'code default: 10.0' in user
    assert 'keck_lris_red = 5.0' in user
    # The wrong documented default of EdgeTracePar is flagged in the prompt.
    assert 'CONSISTENCY: Docs and code differ: EdgeTracePar.snr_thresh' in user
    assert 'Known docs/code differences in these sources' in user
    assert user.rstrip().endswith('citing them inline like [1].')


def test_prompt_lists_setup_dependent_overrides(tmp_path, index_dir):
    rec = mock_llm.Recorder()
    make(tmp_path, index_dir, recorder=rec).answer('default of bspline_spacing?')
    user = chat_requests(rec)[-1]['messages'][-1]['content']
    assert 'setup-dependent overrides in code (config_specific_par' in user
    assert 'keck_lris_red = 1.0' in user


def test_coverage_note(tmp_path, index_dir, monkeypatch):
    rec = mock_llm.Recorder()
    a = make(tmp_path, index_dir, recorder=rec)
    # Keyword-only: the mock's hashed embeddings give meaningless similarities.
    monkeypatch.setattr(a.client, 'embed', lambda *args, **kw: (_ for _ in ()).throw(
        answer_mod.LLMError('no embeddings')))
    a.answer('release notes version')
    user = chat_requests(rec)[-1]['messages'][-1]['content']
    assert 'Coverage note: No matching code was found' in user


def test_flagged_answer(tmp_path, index_dir):
    ans = make(tmp_path, index_dir).answer('EdgeTracePar snr_thresh slit edges')
    assert any(s.chunk.get('flags') for _, s in ans.sources) == ans.flagged


def test_off_topic_never_calls_model(tmp_path, index_dir):
    rec = mock_llm.Recorder()
    ans = make(tmp_path, index_dir, recorder=rec).answer('What is the capital of France?')
    assert ans.outcome == 'off_topic' and ans.text == answer_mod.OFF_TOPIC
    assert chat_requests(rec) == []


def test_model_down_fails_fast(tmp_path, index_dir):
    t0 = time.monotonic()
    ans = make(tmp_path, index_dir, down=True).answer('How do I change bspline_spacing?')
    assert ans.outcome == 'error' and ans.text == answer_mod.MODEL_DOWN
    assert time.monotonic() - t0 < 10


def test_embedding_failure_falls_back_to_keywords(tmp_path, index_dir, monkeypatch):
    a = make(tmp_path, index_dir)

    def broken(*args, **kwargs):
        raise answer_mod.LLMError('embedding model missing')
    monkeypatch.setattr(a.client, 'embed', broken)
    ans = a.answer('How do I change bspline_spacing?')
    assert ans.outcome == 'ok' and ans.mode == 'keyword'
    assert 'keyword search only' in ans.warnings[0]


def test_model_error(tmp_path, index_dir):
    ans = make(tmp_path, index_dir, model='error').answer('How do I change bspline_spacing?')
    assert ans.outcome == 'error' and 'went wrong' in ans.text


def test_leaky_reasoning_removed(tmp_path, index_dir):
    ans = make(tmp_path, index_dir, model='leaky').answer('How do I change bspline_spacing?')
    assert ans.text.startswith('Set `bspline_spacing`')
    assert '</think>' not in ans.text and 'Let me look' not in ans.text


def test_reasoning_field_not_in_answer(tmp_path, index_dir):
    ans = make(tmp_path, index_dir, model='thinker').answer('How do I change bspline_spacing?')
    assert mock_llm.REASONING.split()[0] not in ans.text


@pytest.mark.parametrize('pieces, expected', [
    (['<think>plan</think>Answer'], 'Answer'),
    (['<thi', 'nk>plan</th', 'ink>Ans', 'wer'], 'Answer'),
    (['leaked reasoning </think>', 'Answer'], 'Answer'),
    (['Answer with < and > signs'], 'Answer with < and > signs'),
])
def test_think_filter(pieces, expected):
    f, text = ThinkFilter(), ''
    for p in pieces:
        for kind, t in f.feed(p):
            if kind == 'reset':
                text = ''
            elif kind == 'content':
                text += t
    text += ''.join(t for k, t in f.flush() if k == 'content')
    assert text == expected


def test_followup_query_extends_previous_search(index):
    history = [{'role': 'user', 'content': 'How do I change the sky spacing?'},
               {'role': 'assistant', 'content': 'Set bspline_spacing [1] under [reduce][skysub].'}]
    q = build_query('and for LRIS?', history, index)
    assert q.startswith('How do I change the sky spacing?')
    assert 'bspline_spacing' in q and q.endswith('and for LRIS?')
    long_q = 'this is a new and much longer question about something else entirely'
    assert build_query(long_q, history, index) == long_q


def test_history_is_cleaned_and_capped():
    h = [{'role': 'user', 'content': 'x' * 5000}] + \
        [{'role': r, 'content': f'turn {i} [3]'} for i, r in
         enumerate(['assistant', 'user'] * 5)] + [{'role': 'system', 'content': 'evil'}]
    out = clean_history(h)
    assert len(out) == 6 and all(len(t['content']) <= 3000 for t in out)
    assert all(t['role'] in ('user', 'assistant') for t in out)
    assert not any('[3]' in t['content'] for t in out if t['role'] == 'assistant')


def test_history_passed_to_model(tmp_path, index_dir):
    rec = mock_llm.Recorder()
    history = [{'role': 'user', 'content': 'first question about sky'},
               {'role': 'assistant', 'content': 'first answer'}]
    make(tmp_path, index_dir, recorder=rec).answer('what about bspline_spacing?', history)
    msgs = chat_requests(rec)[-1]['messages']
    assert [m['role'] for m in msgs] == ['system', 'user', 'assistant', 'user']


def test_citations():
    assert cited_numbers('a [2] b [1, 3] c [2-4] d [12]', 5) == [2, 1, 3, 4]
    assert drop_bad_citations('x [1] y [9] z [2, 9]', 3) == 'x [1] y  z [2]'


def test_related_when_nothing_cited(tmp_path, index_dir, monkeypatch):
    monkeypatch.setattr(mock_llm, 'ANSWER', 'An answer without citations.')
    ans = make(tmp_path, index_dir).answer('How do I change bspline_spacing?')
    assert ans.sources == [] and [k for k, _ in ans.related] == [1, 2, 3]


def test_strip_mentions():
    assert strip_mentions('<@U123ABC> how do I <@U9|bob> x') == 'how do I x'


def test_code_excerpt_keeps_signature_docstring_and_matching_lines():
    lines = ['def f(a,', '      b):', '    """', '    Doc.', '    """'] + \
            [f'    x{i} = {i}' for i in range(300)] + ['    y = bspline_spacing * 2',
                                                       '    return y']
    chunk = {'kind': 'code', 'codetype': 'def', 'start': 10, 'end': 10 + len(lines) - 1,
             'path': 'p.py', 'text': '\n'.join(lines)}
    out = code_excerpt(chunk, ['bspline_spacing'], max_chars=600)
    assert len(out) <= 600
    assert out.startswith('   10  def f(a,') and '    Doc.' in out
    assert 'y = bspline_spacing * 2' in out and '…' in out
    assert f'{10 + 305:5d}  ' in out           # line numbers are real


def test_user_message_without_sources(index):
    from pypeit_chat.index import Retrieval
    r = Retrieval(sources=[], coverage={'docs': False, 'code': False}, spectrographs=[],
                  mode='keyword')
    msg = user_message('q?', r, index)
    assert '(no sources matched this question)' in msg
    assert 'No matching documentation' in msg and 'No matching code' in msg
