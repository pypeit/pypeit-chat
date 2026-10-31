"""
Tests of retrieval (DESIGN 7.1): tokenizer, BM25, pinning, boosts, pools,
cross-reference expansion, coverage, off-topic and reloading.
"""
import json
import os
import shutil
import time

import numpy as np
import pytest

from pypeit_chat import index as index_mod
from pypeit_chat.index import (BM25, Index, IndexHolder, IndexNotReady, Retrieval, Source,
                               tokenize)
from pypeit_chat.llm import LLMClient
from pypeit_chat.tests import mock_llm
from pypeit_chat.tests.conftest import test_settings


def qvec(text, tmp_path):
    c = LLMClient(test_settings(tmp_path), transport=mock_llm.transport())
    return c.embed([text], query=True)[0]


def ids(result, pool=None):
    return [s.chunk['id'] for s in result.sources if pool is None or s.pool == pool]


def test_tokenizer_keeps_identifiers_and_parts():
    toks = tokenize('How is bspline_spacing used in pypeit.core.skysub for A-B data?')
    assert 'bspline_spacing' in toks and 'spacing' in toks
    assert 'pypeit.core.skysub' in toks and 'skysub' in toks
    assert 'a-b' in toks
    assert 'how' not in toks and 'is' not in toks


def test_bm25_ranks_and_coverage():
    bm = BM25(['sky subtraction with bspline', 'flux calibration', 'sky sky sky'])
    s, cover = bm.scores('bspline sky')
    assert s.argmax() == 0
    assert cover[0] == pytest.approx(1.0) and cover[1] == 0


def test_identifier_pins_every_parset(index):
    r = index.retrieve('what is snr_thresh?', None)
    docs = ids(r, 'docs')
    for par in ('FindObjPar', 'AlignPar', 'EdgeTracePar'):
        assert f'param:{par}.snr_thresh' in docs[:3]
    assert all(s.reason == 'pinned' for s in r.sources[:3])


def test_function_name_pins_code(index):
    r = index.retrieve('Where is pypeit.core.skysub.global_skysub?', None)
    assert ids(r, 'code')[0] == 'code:pypeit.core.skysub.global_skysub'
    r = index.retrieve('what does global_skysub do', None)
    assert 'code:pypeit.core.skysub.global_skysub' in ids(r, 'code')[:2]


def test_script_name_pins_help_section(index):
    r = index.retrieve('how do I run pypeit_setup', None)
    assert ids(r, 'docs')[0] == 'doc:setup#run-setup:0'


def test_package_name_is_not_an_identifier(index):
    assert index.identifiers('PypeIt pypeit') == []


def test_two_pools_always(index, tmp_path):
    q = 'how is the global sky model fit with a bspline'
    r = index.retrieve(q, qvec(q, tmp_path), doc_k=3, code_k=2)
    pools = [s.pool for s in r.sources]
    assert pools.count('docs') == 3 and pools.count('code') == 2
    assert pools == sorted(pools, key=lambda p: p != 'docs')        # docs first


def test_cross_reference_expansion_adds_definition(index):
    def src(cid, pool, reason='search'):
        return Source(index.chunks[index.pos[cid]], 1.0, pool, reason)
    r = Retrieval(sources=[src('param:SkySubPar.bspline_spacing', 'docs'),
                           src('code:pypeit.scripts.setup.Setup', 'code')],
                  coverage={'docs': True, 'code': False}, spectrographs=[], mode='keyword')
    index.expand(r, doc_k=1, code_k=1, relevance=np.zeros(len(index.chunks)))
    code = [s for s in r.sources if s.pool == 'code']
    # The searched code chunk was replaced by the parameter's definition.
    assert [(s.chunk['id'], s.reason) for s in code] == \
        [('code:pypeit.par.pypeitpar.SkySubPar.__init__', 'link:definition')]
    assert code[0].linked_from == 'param:SkySubPar.bspline_spacing'
    assert r.coverage['code'] is True
    assert len(r.sources) == 2


def test_code_to_docs_links_must_be_relevant(index):
    def src(cid, pool):
        return Source(index.chunks[index.pos[cid]], 1.0, pool)
    r = Retrieval(sources=[src('doc:index#:0', 'docs'),
                           src('code:pypeit.core.skysub.global_skysub', 'code')],
                  coverage={'docs': True, 'code': True}, spectrographs=[], mode='keyword')
    index.expand(r, doc_k=1, code_k=1, relevance=np.zeros(len(index.chunks)))
    assert [s.chunk['id'] for s in r.sources if s.pool == 'docs'] == ['doc:index#:0']
    rel = np.zeros(len(index.chunks))
    rel[index.pos['doc:skysub#global:0']] = 0.1
    index.expand(r, doc_k=1, code_k=1, relevance=rel)
    assert [s.chunk['id'] for s in r.sources if s.pool == 'docs'] == ['doc:skysub#global:0']


def test_spectrograph_boost(index):
    r = index.retrieve('setup keck_lris_red', None, doc_k=6, code_k=4)
    assert r.spectrographs == ['keck_lris']
    tagged = [s for s in r.sources if 'keck_lris' in s.chunk.get('instruments', [])]
    assert tagged and r.sources.index(tagged[0]) < 3


def test_unnamed_spectrograph_damped(index):
    def score(spectrographs):
        r = index.retrieve('setup data', None, spectrographs=spectrographs)
        return next(s.score for s in r.sources
                    if s.chunk['id'] == 'doc:tutorials/lris_howto#setup:0')
    assert score([]) == pytest.approx(score(['keck_lris']) * index_mod.BOOST_UNNAMED
                                      / index_mod.BOOST_NAMED)
    assert score(['shane_kast']) == pytest.approx(score(['keck_lris']) * index_mod.BOOST_OTHER
                                                  / index_mod.BOOST_NAMED)


def test_diversity_caps(index):
    r = index.retrieve('snr_thresh bspline_spacing sky_sigrej trace_npoly parameters',
                       None, doc_k=6, code_k=4)
    per_file = {}
    for s in r.sources:
        key = (s.chunk['path'], s.chunk['kind'])
        per_file[key] = per_file.get(key, 0) + 1
    pinned_extra = sum(1 for s in r.sources if s.reason == 'pinned')
    assert max(per_file.values()) <= index_mod.MAX_PER_FILE + pinned_extra


def test_coverage_note_when_pool_has_nothing(index):
    r = index.retrieve('release notes version', None)
    assert r.coverage['docs'] is True
    assert r.coverage['code'] is False


def test_off_topic(index, tmp_path):
    q = 'what is the capital of france'
    r = index.retrieve(q, qvec(q, tmp_path))
    assert r.off_topic
    q = 'how do I flux calibrate my spectra'
    r = index.retrieve(q, qvec(q, tmp_path))
    assert not r.off_topic and 'flux' in r.pypeit_terms


def test_keyword_only_mode(index):
    r = index.retrieve('global sky bspline', None)
    assert r.mode == 'keyword' and r.sources


def test_param_facts(index):
    c = index.chunks[index.pos['param:FindObjPar.snr_thresh']]
    f = index.param_facts(c)
    assert f['code_default'] == '10.0' and f['parset'] == 'FindObjPar'
    assert index.param_facts(index.chunks[index.pos['doc:skysub#global:0']]) is None


def test_holder_reloads_and_survives_partial_build(index_dir, tmp_path):
    d = tmp_path / 'idx'
    shutil.copytree(index_dir, d)
    holder = IndexHolder(d)
    first = holder.get()
    assert first.tag == '2.0.1'
    # A new release: meta.json changes and the holder reloads.
    meta = json.loads((d / 'meta.json').read_text())
    meta['tag'] = '2.0.2'
    (d / 'meta.json').write_text(json.dumps(meta))
    os.utime(d / 'meta.json', ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    assert holder.get().tag == '2.0.2'
    # A half-moved build (sizes differ): keep the old index.
    with open(d / 'chunks.jsonl', 'a') as f:
        f.write('\n')
    meta['tag'] = '2.0.3'
    (d / 'meta.json').write_text(json.dumps(meta))
    os.utime(d / 'meta.json', ns=(time.time_ns() + 2 * 10**9, time.time_ns() + 2 * 10**9))
    assert holder.get().tag == '2.0.2'
    assert 'rebuilt' in holder.error


def test_no_index(tmp_path):
    with pytest.raises(IndexNotReady):
        IndexHolder(tmp_path / 'none').get()
    with pytest.raises(IndexNotReady):
        Index(tmp_path / 'none')


def test_vectors_ignored_if_count_differs(index_dir, tmp_path):
    d = tmp_path / 'idx'
    shutil.copytree(index_dir, d)
    np.save(d / 'embeddings.npy', np.zeros((2, 4), dtype=np.float32))
    meta = json.loads((d / 'meta.json').read_text())
    meta['files']['embeddings.npy'] = (d / 'embeddings.npy').stat().st_size
    (d / 'meta.json').write_text(json.dumps(meta))
    assert Index(d).has_vectors is False


def test_spectrograph_name_pins_its_defaults(index):
    r = index.retrieve('default bspline_spacing for keck_lris_red', None)
    pinned = [s.chunk['id'] for s in r.sources if s.reason == 'pinned']
    assert 'override:keck_lris_red:0' in pinned
    assert 'code:pypeit.spectrographs.keck_lris.KeckLRISRSpectrograph.config_specific_par' \
        in pinned


def test_query_vector_of_wrong_size_falls_back_to_keywords(index):
    r = index.retrieve('global sky bspline', np.ones(3, dtype=np.float32))
    assert r.mode == 'keyword' and r.sources


def test_stale_reference_pins_flagged_docs(index):
    r = index.retrieve('what does pypeit.fluxcalibrate.apply_flux_calib do', None)
    pinned = [s.chunk['id'] for s in r.sources if s.reason == 'pinned']
    assert 'doc:setup#run-setup:0' in pinned
