"""
Tests of settings, the corpus checkout and the model client.
"""
import subprocess

import httpx
import numpy as np
import pytest

from pypeit_chat import corpus
from pypeit_chat.config import Settings, embed_prefixes, read_dotenv
from pypeit_chat.llm import LLMClient, LLMError, LLMUnavailable, think_options
from pypeit_chat.tests import mock_llm
from pypeit_chat.tests.conftest import test_settings


# --- Settings ------------------------------------------------------------------


def test_settings_from_env_and_dotenv(tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_text('# comment\nCHAT_MODEL="from-dotenv"\nMAX_QUEUE=3\n'
                        'export ALLOWED_USER_IDS=U1, U2\n')
    s = Settings.load(env={'MAX_QUEUE': '7', 'LOG_TEXT': '1', 'INDEX_DIR': 'elsewhere'},
                      dotenv=env_file)
    assert s.chat_model == 'from-dotenv'
    assert s.max_queue == 7                      # environment wins
    assert s.log_text is True
    assert s.allowed_user_ids == frozenset({'U1', 'U2'})
    assert s.index_dir.is_absolute() and s.index_dir.name == 'elsewhere'
    assert read_dotenv(tmp_path / 'missing') == {}


def test_settings_defaults_match_design(tmp_path):
    s = Settings.load(env={}, dotenv=tmp_path / 'none')
    assert s.llm_base_url == 'http://127.0.0.1:11434/v1'
    assert (s.doc_sources, s.code_sources) == (6, 4)
    assert (s.rate_per_user_hour, s.rate_per_user_day, s.rate_per_channel_hour) == (20, 60, 60)
    assert (s.max_concurrent, s.max_queue, s.max_question_chars) == (1, 10, 2000)
    assert s.log_text is False and s.log_retention_days == 180
    assert s.think is False


def test_bad_think_switch(tmp_path):
    with pytest.raises(ValueError):
        Settings.load(env={'THINK_SWITCH': 'magic'}, dotenv=tmp_path / 'none')


def test_embed_prefixes():
    assert embed_prefixes('nomic-embed-text:latest') == ('search_document: ', 'search_query: ')
    assert embed_prefixes('unknown') == ('', '')


# --- Corpus --------------------------------------------------------------------


def git(repo, *args):
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def upstream(tmp_path):
    """A repository with an old v1.3 tag and a newer 2.0.1 on 'release'."""
    repo = tmp_path / 'upstream'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'develop')
    git(repo, 'config', 'user.email', 't@example.com')
    git(repo, 'config', 'user.name', 'T')
    (repo / 'doc').mkdir()
    (repo / 'pypeit').mkdir()
    (repo / 'pypeit' / 'data').mkdir()
    (repo / 'doc' / 'index.rst').write_text('Old\n===\n')
    (repo / 'pypeit' / '__init__.py').write_text('')
    (repo / 'pypeit' / 'data' / 'big.dat').write_text('x' * 100)
    git(repo, 'add', '.')
    git(repo, 'commit', '-q', '-m', 'one')
    git(repo, 'tag', 'v1.3')
    git(repo, 'branch', 'release')
    git(repo, 'checkout', '-q', 'release')
    (repo / 'doc' / 'index.rst').write_text('New\n===\n')
    git(repo, 'commit', '-q', '-am', 'two')
    git(repo, 'tag', '2.0.1')
    git(repo, 'checkout', '-q', 'develop')
    (repo / 'doc' / 'index.rst').write_text('Dev\n===\n')
    git(repo, 'commit', '-q', '-am', 'three')
    git(repo, 'tag', '2.1.0.dev')
    return repo


def test_latest_tag_not_fooled_by_name_sorting(upstream, tmp_path):
    info = corpus.update(tmp_path / 'cache' / 'PypeIt', upstream.as_uri(), blobless=False)
    assert info['tag'] == '2.0.1'
    checkout = tmp_path / 'cache' / 'PypeIt'
    assert (checkout / 'doc' / 'index.rst').read_text().startswith('New')
    assert not (checkout / 'pypeit' / 'data').exists()          # sparse checkout
    assert corpus.describe(checkout)['tag'] == '2.0.1'
    # A second update is a no-op.
    assert corpus.update(checkout, upstream.as_uri(), blobless=False)['changed'] is False


def test_explicit_tag(upstream, tmp_path):
    info = corpus.update(tmp_path / 'c', upstream.as_uri(), tag='v1.3', blobless=False)
    assert info['tag'] == 'v1.3'
    assert (tmp_path / 'c' / 'doc' / 'index.rst').read_text().startswith('Old')


def test_corpus_error(tmp_path):
    with pytest.raises(corpus.CorpusError):
        corpus.update(tmp_path / 'c', (tmp_path / 'nothing').as_uri(), blobless=False)


# --- Model client ------------------------------------------------------------------


def client(tmp_path, recorder=None, down=False, **changes):
    s = test_settings(tmp_path, **changes)
    return LLMClient(s, transport=mock_llm.transport(recorder, down=down))


def test_think_options():
    assert think_options('reasoning_effort', False) == {'reasoning_effort': 'none'}
    assert think_options('chat_template_kwargs', True) == \
        {'chat_template_kwargs': {'enable_thinking': True}}
    assert think_options('none', True) == {}


def test_chat_stream_content_reasoning_and_usage(tmp_path):
    rec = mock_llm.Recorder()
    events = list(client(tmp_path, rec, chat_model='thinker').chat_stream(
        [{'role': 'user', 'content': 'hi'}]))
    kinds = {k for k, _ in events}
    assert {'reasoning', 'content', 'done'} <= kinds
    text = ''.join(t for k, t in events if k == 'content')
    assert text == mock_llm.ANSWER
    done = events[-1][1]
    assert done['tokens'] and done['finish_reason'] == 'stop'
    path, body = rec.requests[-1]
    assert path.endswith('/chat/completions')
    assert body['stream'] is True and body['reasoning_effort'] == 'none'
    assert body['temperature'] == 0.2


def test_chat_errors(tmp_path):
    with pytest.raises(LLMError, match='not available'):
        list(client(tmp_path, chat_model='nosuchmodel').chat_stream([]))
    with pytest.raises(LLMError, match='500'):
        list(client(tmp_path, chat_model='error').chat_stream([]))
    with pytest.raises(LLMUnavailable):
        list(client(tmp_path, down=True).chat_stream([]))


def test_embed_adds_prefix_and_normalizes(tmp_path):
    rec = mock_llm.Recorder()
    vecs = client(tmp_path, rec).embed(['sky subtraction', 'flux'], query=True)
    assert vecs.dtype == np.float32 and vecs.shape == (2, mock_llm.DIM)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1)
    assert rec.requests[-1][1]['input'][0] == 'search_query: sky subtraction'


def test_check(tmp_path):
    assert client(tmp_path).check() == []
    assert 'not served' in client(tmp_path, chat_model='absent').check()[0]
    assert 'not responding' in client(tmp_path, down=True).check()[0]


def test_connect_timeout_is_short(tmp_path):
    c = client(tmp_path)
    with c._client(300) as h:
        assert h.timeout.connect == 5.0
    assert isinstance(h.timeout, httpx.Timeout)
