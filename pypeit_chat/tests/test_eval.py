"""
Tests of the evaluation set and its scoring (DESIGN 13).
"""
from pypeit_chat import evaluate
from pypeit_chat.answer import Answer
from pypeit_chat.index import Retrieval, Source

FIELDS = {'id', 'origin', 'question', 'expected_sources', 'key_facts', 'both', 'difference',
          'off_topic'}


def test_eval_set_is_well_formed():
    qs = evaluate.load_questions()
    ids = [q['id'] for q in qs]
    assert len(ids) == len(set(ids))
    assert len(qs) >= 30
    for q in qs:
        assert set(q) <= FIELDS and q['question'].strip()
        assert q['origin'] == 'report' or q['origin'] in ('handwritten', 'consistency') \
            or q['origin'].startswith('github#')
        if q.get('off_topic'):
            assert not q.get('expected_sources')
        else:
            assert q.get('expected_sources'), q['id']


def src(cid, path, pool):
    return Source({'id': cid, 'path': path, 'kind': 'code' if pool == 'code' else 'doc'}, 1,
                  pool)


def test_scoring():
    q = {'expected_sources': ['param:FindObjPar.snr_thresh', 'doc/skysub.rst'],
         'key_facts': ['10.0|10', 'FindObjPar', 'nope'], 'both': True, 'difference': True}
    r = Retrieval(sources=[src('doc:x', 'doc/x.rst', 'docs'),
                           src('param:FindObjPar.snr_thresh', 'doc/pypeit_par.rst', 'docs'),
                           src('code:a', 'pypeit/a.py', 'code')],
                  coverage={'docs': True, 'code': True}, spectrographs=[], mode='hybrid',
                  pool_best={'docs': (0.8, 1.0), 'code': (0.6, 0.5)})
    m = evaluate.score_retrieval(q, r)
    assert m['expected_hit'] and m['expected_rank'] == 2 and m['both_pools']
    ans = Answer(question='q', text='The default is 10 in FindObjPar; the docs differ.')
    ans.sources = [(2, r.sources[1]), (3, r.sources[2])]
    a = evaluate.score_answer(q, ans, 3.0)
    assert a['facts_found'] == ['10.0|10', 'FindObjPar'] and a['facts_missing'] == ['nope']
    assert a['expected_cited'] and a['cited_docs'] and a['cited_code']
    assert a['difference_reported']
    s = evaluate.summarize([{'q': q, 'retrieval': m, 'answer': a}], retrieval_only=False)
    assert s['expected_source_top10'] == 1.0 and s['key_facts_found'] == round(2 / 3, 3)
    assert s['docs_and_code_cited'] == 1.0 and s['median_first_text_seconds'] == 3.0
