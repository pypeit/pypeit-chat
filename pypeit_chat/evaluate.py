"""
Run the evaluation set (DESIGN Section 13) and write dated results.

Run as ``pypeit_chat_eval``.  ``--retrieval-only`` skips the chat model and
reports retrieval metrics plus the score distribution used to calibrate the
coverage and off-topic thresholds in :mod:`pypeit_chat.index`.  ``--export``
writes the interaction log to CSV for review.
"""
import argparse
import csv
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from pypeit_chat import index as index_mod
from pypeit_chat.answer import Answerer
from pypeit_chat.config import REPO_DIR, Settings

EVAL_FILE = Path(__file__).resolve().parent / 'tests' / 'eval_questions.yaml'
RESULTS_DIR = REPO_DIR / 'eval'
DIFFERENCE_RE = re.compile(r'differ|out of date|outdated|does not exist|doesn.t exist|'
                           r'not (?:in|found in) the code|stale|but the code|'
                           r'is not (?:located|defined) in|no longer', re.I)


def load_questions(path=EVAL_FILE, ids=None):
    """
    Read the evaluation set.

    Parameters
    ----------
    path : Path, optional
    ids : list of str, optional
        Keep only these ids.

    Returns
    -------
    list of dict
    """
    qs = yaml.safe_load(Path(path).read_text())
    if ids:
        qs = [q for q in qs if q['id'] in ids]
    return qs


def matches(source, expected):
    """True if a chunk matches an expected path or chunk-id prefix."""
    c = source.chunk
    return any(c['id'].startswith(e) or c['path'].startswith(e) for e in expected)


def has_fact(text, fact):
    """True if any alternative of ``fact`` (``a|b``) appears in ``text``."""
    low = text.lower()
    return any(alt.strip().lower() in low for alt in str(fact).split('|'))


def score_retrieval(q, result):
    """
    Retrieval metrics for one question.

    Returns
    -------
    dict
    """
    expected = q.get('expected_sources') or []
    pools = {s.pool for s in result.sources}
    return {
        'expected_hit': any(matches(s, expected) for s in result.sources) if expected else None,
        'expected_rank': next((n for n, s in enumerate(result.sources, 1)
                               if matches(s, expected)), None) if expected else None,
        'both_pools': pools == {'docs', 'code'},
        'off_topic_pred': result.off_topic,
        'best_cos_docs': round(result.pool_best.get('docs', (0, 0))[0], 3),
        'best_cos_code': round(result.pool_best.get('code', (0, 0))[0], 3),
        'best_cover': round(result.best_cover, 3),
        'coverage': result.coverage,
        'sources': [f"{s.pool}:{s.reason}:{s.chunk['id']}" for s in result.sources],
    }


def score_answer(q, ans, first_text_s):
    """
    Answer metrics for one question.

    Returns
    -------
    dict
    """
    cited = [s for _, s in ans.sources]
    expected = q.get('expected_sources') or []
    facts = q.get('key_facts') or []
    out = {
        'outcome': ans.outcome,
        'seconds': ans.seconds,
        'first_text_seconds': first_text_s,
        'tokens': ans.tokens,
        'facts_found': [f for f in facts if has_fact(ans.text, f)],
        'facts_missing': [f for f in facts if not has_fact(ans.text, f)],
        'expected_cited': any(matches(s, expected) for s in cited) if expected else None,
        'cited_docs': any(s.pool == 'docs' for s in cited),
        'cited_code': any(s.pool == 'code' for s in cited),
        'n_cited': len(cited),
        'flagged': ans.flagged,
        'answer': ans.text,
    }
    if q.get('difference'):
        out['difference_reported'] = bool(ans.flagged or DIFFERENCE_RE.search(ans.text))
    return out


def summarize(rows, retrieval_only):
    """
    Aggregate metrics.

    Returns
    -------
    dict
    """
    def frac(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(bool(v) for v in vals) / len(vals), 3) if vals else None

    on = [r for r in rows if not r['q'].get('off_topic')]
    off = [r for r in rows if r['q'].get('off_topic')]
    s = {
        'questions': len(rows), 'on_topic': len(on), 'off_topic': len(off),
        'expected_source_top10': frac(r['retrieval']['expected_hit'] for r in on),
        'both_pools_retrieved': frac(r['retrieval']['both_pools'] for r in on),
        'off_topic_refused': frac(r['retrieval']['off_topic_pred'] for r in off),
        'on_topic_wrongly_refused': frac(r['retrieval']['off_topic_pred'] for r in on),
    }
    if retrieval_only:
        return s
    ans = [r for r in on if 'answer' in r]
    both = [r for r in ans if r['q'].get('both')]
    secs = [r['answer']['seconds'] for r in ans if r['answer']['seconds']]
    first = [r['answer']['first_text_seconds'] for r in ans if r['answer']['first_text_seconds']]
    n_facts = sum(len(r['q'].get('key_facts') or []) for r in ans)
    s.update({
        'answered_ok': frac(r['answer']['outcome'] == 'ok' for r in ans),
        'key_facts_found': round(sum(len(r['answer']['facts_found']) for r in ans)
                                 / n_facts, 3) if n_facts else None,
        'expected_source_cited': frac(r['answer']['expected_cited'] for r in ans),
        'docs_and_code_cited': frac(r['answer']['cited_docs'] and r['answer']['cited_code']
                                    for r in both),
        'differences_reported': frac(r['answer'].get('difference_reported') for r in ans
                                     if r['q'].get('difference')),
        'median_seconds': round(statistics.median(secs), 1) if secs else None,
        'median_first_text_seconds': round(statistics.median(first), 1) if first else None,
    })
    return s


def calibration(rows):
    """Best-cosine distributions of on- and off-topic questions."""
    on = sorted(r['retrieval']['best_cos_docs'] for r in rows if not r['q'].get('off_topic'))
    off = sorted(r['retrieval']['best_cos_docs'] for r in rows if r['q'].get('off_topic'))
    lines = ['Best docs-pool cosine (on-topic): ' + ', '.join(f'{v:.3f}' for v in on),
             'Best docs-pool cosine (off-topic): ' + ', '.join(f'{v:.3f}' for v in off),
             f'Current COS_OFFTOPIC = {index_mod.COS_OFFTOPIC}']
    if on and off:
        lines.append(f'Gap: highest off-topic {max(off):.3f}, lowest on-topic {min(on):.3f}; '
                     f'midpoint {(max(off) + min(on)) / 2:.3f}')
    return '\n'.join(lines)


def export_csv(settings, out):
    """Write the interaction log (and feedback) to CSV for review."""
    from pypeit_chat.store import Store
    store = Store(settings.db_path, settings)
    rows = store.export_rows()
    with open(out, 'w', newline='') as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(f'Wrote {len(rows)} interactions to {out}')


def parser():
    p = argparse.ArgumentParser(description='Run the pypeit-chat evaluation set.')
    p.add_argument('--retrieval-only', action='store_true', help='Skip the chat model')
    p.add_argument('--calibrate', action='store_true',
                   help='Print the score distributions used for the thresholds')
    p.add_argument('--ids', nargs='*', help='Only these question ids')
    p.add_argument('--questions', default=str(EVAL_FILE), help='Evaluation YAML file')
    p.add_argument('--out', default=str(RESULTS_DIR), help='Folder for dated results')
    p.add_argument('--export', metavar='CSV', help='Export the interaction log to CSV and exit')
    p.add_argument('-v', '--verbose', action='store_true')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    settings = Settings.load()
    if args.export:
        export_csv(settings, args.export)
        return 0
    qs = load_questions(args.questions, args.ids)
    answerer = Answerer(settings)
    rows = []
    for q in qs:
        idx, query, result, warnings = answerer.retrieve(q['question'])
        row = {'q': q, 'retrieval': score_retrieval(q, result)}
        if not args.retrieval_only and not q.get('off_topic'):
            t0, first = time.monotonic(), []

            def on_text(text, t0=t0, first=first):
                if not first:
                    first.append(round(time.monotonic() - t0, 1))
            ans = answerer.answer(q['question'], on_text=on_text)
            row['answer'] = score_answer(q, ans, first[0] if first else None)
        rows.append(row)
        r = row['retrieval']
        status = ('refused' if r['off_topic_pred'] else
                  f"rank {r['expected_rank']}" if r['expected_rank'] else
                  'MISS' if r['expected_hit'] is False else '-')
        extra = ''
        if 'answer' in row:
            a = row['answer']
            extra = (f"  {a['seconds']}s facts {len(a['facts_found'])}/"
                     f"{len(a['facts_found']) + len(a['facts_missing'])}"
                     f" docs+code {'y' if a['cited_docs'] and a['cited_code'] else 'n'}")
        print(f"{q['id']:6s} {status:9s} cos {r['best_cos_docs']:.3f}{extra}  {q['question'][:60]}")
        if args.verbose:
            for s in r['sources']:
                print('         ', s)
    summary = summarize(rows, args.retrieval_only)
    print('\n' + json.dumps(summary, indent=1))
    if args.calibrate:
        print('\n' + calibration(rows))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d_%H%M')
    name = f"{stamp}_{'retrieval' if args.retrieval_only else settings.chat_model.replace(':', '-')}"
    meta = {'tag': idx.tag, 'sha': idx.meta.get('sha'), 'model': settings.chat_model,
            'embed_model': idx.meta.get('embed_model'), 'retrieval_only': args.retrieval_only,
            'doc_sources': settings.doc_sources, 'code_sources': settings.code_sources,
            'created': stamp}
    (out / f'{name}.json').write_text(json.dumps(
        {'meta': meta, 'summary': summary,
         'rows': [{'id': r['q']['id'], **{k: v for k, v in r.items() if k != 'q'}}
                  for r in rows]}, indent=1, ensure_ascii=False))
    print(f'Results written to {out / name}.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
