"""
Build the index: update the corpus checkout, make chunks, cross-reference
docs and code, embed, and write ``index/`` atomically (``meta.json`` last).

Run as ``pypeit_chat_ingest``.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from pypeit_chat import corpus
from pypeit_chat.config import Settings
from pypeit_chat.index import search_text
from pypeit_chat.ingest import code, devsuite, params, rst, xref
from pypeit_chat.ingest.tags import Tagger
from pypeit_chat.llm import LLMClient, LLMError

INDEX_FILES = ('chunks.jsonl', 'embeddings.npy', 'xref.json', 'consistency.json')


def make_chunks(root, tag, sha, docs_url, dev_suite_dir=''):
    """
    All chunks, cross-references and consistency findings for a checkout.

    Parameters
    ----------
    root : Path
        Checkout root (``doc/`` and ``pypeit/``).
    tag : str
        Release tag, used in docs URLs.
    sha : str
        Commit, used in code URLs.
    docs_url : str
        Docs base without the version.
    dev_suite_dir : str, optional
        Dev-suite checkout for example files.

    Returns
    -------
    chunks : list of dict
    xrefs : dict
    findings : list of dict
    tagger : Tagger
    """
    root = Path(root)
    url_base = f"{docs_url.rstrip('/')}/{tag}"
    tagger = Tagger.from_root(root)
    sections, ctx = rst.read_sections(root / 'doc')
    chunks = []
    for s in sections:
        if params.is_override_section(s) or (
                s['page'] == 'pypeit_par' and params.KEYWORDS_RE.match(s['title'])):
            continue
        chunks += rst.section_chunks(s, ctx, url_base, tagger)
    chunks += params.param_chunks(sections, ctx, url_base, tagger)
    chunks += params.override_chunks(sections, ctx, url_base, tagger)
    chunks += code.code_chunks(root, sha, tagger)
    if dev_suite_dir:
        chunks += devsuite.example_chunks(dev_suite_dir, tagger)
    paths = params.parset_paths(params.parset_tables(sections))
    xrefs, findings = xref.build(root, chunks, paths)
    return chunks, xrefs, findings, tagger


def embed_chunks(client, chunks, batch=32):
    """
    Embed every chunk, printing progress.

    Returns
    -------
    numpy.ndarray
        Float32, one normalized row per chunk.
    """
    texts = [search_text(c) for c in chunks]
    out, t0 = [], time.time()
    for i in range(0, len(texts), batch):
        out.append(client.embed(texts[i:i + batch]))
        done = min(i + batch, len(texts))
        rate = done / max(time.time() - t0, 1e-6)
        print(f'\r  embedding {done}/{len(texts)}  ({rate:.0f}/s, '
              f'~{(len(texts) - done) / max(rate, 1e-6):.0f}s left)   ', end='', flush=True)
    print()
    return np.vstack(out).astype(np.float32)


def write_index(out_dir, chunks, vectors, xrefs, findings, meta):
    """
    Write the index files, then ``meta.json`` last.

    The files are written to a temporary folder inside ``out_dir`` and moved
    into place; ``meta.json`` records their sizes so a reader can tell a
    finished build from one being moved.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir, prefix='.build-') as tmp:
        tmp = Path(tmp)
        with open(tmp / 'chunks.jsonl', 'w') as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + '\n')
        if vectors is not None:
            np.save(tmp / 'embeddings.npy', vectors)
        (tmp / 'xref.json').write_text(json.dumps(xrefs, ensure_ascii=False))
        (tmp / 'consistency.json').write_text(json.dumps(findings, indent=1, ensure_ascii=False))
        meta = dict(meta, files={n: (tmp / n).stat().st_size for n in INDEX_FILES
                                 if (tmp / n).exists()})
        for name in INDEX_FILES:
            if (tmp / name).exists():
                os.replace(tmp / name, out_dir / name)
            elif (out_dir / name).exists():
                (out_dir / name).unlink()
        (tmp / 'meta.json').write_text(json.dumps(meta, indent=2))
        os.replace(tmp / 'meta.json', out_dir / 'meta.json')
    return meta


def parser():
    p = argparse.ArgumentParser(description='Build or refresh the pypeit-chat corpus and index.')
    p.add_argument('--no-update', action='store_true',
                   help='Use the checkout as it is: no clone, fetch or tag change')
    p.add_argument('--tag', default=None, help='Release tag to index (default: PYPEIT_TAG, '
                                               'else the latest tag on origin/release)')
    p.add_argument('--out', default=None, help='Index folder (default: INDEX_DIR)')
    p.add_argument('--no-embed', action='store_true', help='Keyword-only index')
    p.add_argument('--if-changed', action='store_true',
                   help='Do nothing if the index is already at the checkout commit')
    p.add_argument('--batch', type=int, default=32, help='Embedding batch size')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    s = Settings.load()
    out = Path(args.out).expanduser() if args.out else s.index_dir
    t0 = time.time()
    try:
        if args.no_update:
            info = corpus.describe(s.corpus_dir)
        else:
            info = corpus.update(s.corpus_dir, s.corpus_url, tag=args.tag or s.pypeit_tag or None)
    except corpus.CorpusError as e:
        sys.exit(f'ERROR: {e}')
    if args.if_changed and (out / 'meta.json').exists():
        old = json.loads((out / 'meta.json').read_text())
        if old.get('sha') == info['sha']:
            print(f"Index already at PypeIt {info['tag']} ({info['sha'][:9]}); nothing to do.")
            return 0
    print(f"Indexing PypeIt {info['tag']} ({info['sha'][:9]}) from {s.corpus_dir}")
    chunks, xrefs, findings, tagger = make_chunks(s.corpus_dir, info['tag'], info['sha'],
                                                  s.docs_url, s.dev_suite_dir)
    counts = {}
    for c in chunks:
        counts[c['kind']] = counts.get(c['kind'], 0) + 1
    print('  ' + ', '.join(f'{v} {k}' for k, v in counts.items())
          + f'; {sum(len(v) for v in xrefs["links"].values())} links, '
            f'{len(findings)} consistency findings  ({time.time() - t0:.1f}s)')

    vectors = None
    if not args.no_embed:
        client = LLMClient(s)
        try:
            vectors = embed_chunks(client, chunks, args.batch)
        except LLMError as e:
            sys.exit(f'\nERROR: {e}\n  (Run with --no-embed for a keyword-only index.)')
    meta = {'tag': info['tag'], 'sha': info['sha'], 'commit_time': info['commit_time'],
            'docs_base': f"{s.docs_url.rstrip('/')}/{info['tag']}",
            'created': corpus.now(), 'counts': counts,
            'has_embeddings': vectors is not None,
            'embed_model': s.embed_model if vectors is not None else None,
            'embed_dim': int(vectors.shape[1]) if vectors is not None else None,
            'instruments': tagger.families, 'build_seconds': round(time.time() - t0, 1)}
    write_index(out, chunks, vectors, xrefs, findings, meta)
    print(f'Wrote {len(chunks)} chunks to {out}  ({time.time() - t0:.1f}s total)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
