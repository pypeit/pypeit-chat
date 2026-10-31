"""
The search index: loading (and reloading when ``meta.json`` changes), BM25,
embedding similarity, reciprocal rank fusion, identifier pinning,
spectrograph boosts, two source pools (docs and code) and docs-code
cross-reference expansion (DESIGN Section 7.1).
"""
import json
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pypeit_chat.ingest.tags import Tagger

DOC_KINDS = ('doc', 'param', 'override', 'example')
CODE_KINDS = ('code',)
RRF_K = 60
CANDIDATES = 100
MAX_PER_FILE = 4
MAX_PER_TITLE = 2
#: Spectrograph boosts: named, other spectrographs, any spectrograph when none named.
BOOST_NAMED, BOOST_OTHER, BOOST_UNNAMED = 1.5, 0.6, 0.8
#: A pool has relevant matches if its best chunk has cosine similarity
#: >= COS_RELEVANT[pool], or covers >= COVER_RELEVANT of the query's term
#: weight.  Calibrated with ``pypeit_chat_eval --calibrate``.
COS_RELEVANT = {'docs': 0.55, 'code': 0.55}
COVER_RELEVANT = 0.5
#: Off-topic (DESIGN 11.3.3): no PypeIt term, and the best docs cosine is
#: below COS_OFFTOPIC (hybrid) or the best term coverage is below
#: COVER_OFFTOPIC (keyword-only).
COS_OFFTOPIC = 0.66
COVER_OFFTOPIC = 0.34
#: Docs hits whose links are followed (besides every parameter hit), and the
#: most linked chunks added per pool.
EXPAND_FROM = 3
MAX_LINKED = 2
#: Link reasons followed from code hits to docs (literal mentions are too weak).
CODE_TO_DOC_REASONS = ('definition', 'role', 'script', 'use', 'override')

_TOKEN_RE = re.compile(r'[a-z0-9]+(?:[_.-][a-z0-9]+)*')
SNAKE_RE = re.compile(r'\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b')
DOTTED_RE = re.compile(r'\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b')
CAMEL_RE = re.compile(r'\b[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+\b')
BACKTICK_RE = re.compile(r'`([^`\s]+)`')
USAGE_RE = re.compile(r'^\s*\$?\s*usage:\s+(\S+)', re.M)
#: Names never pinned as identifiers (the package itself).
NOT_IDENTIFIERS = {'pypeit'}
#: Words that mark a question as being about spectroscopic data reduction,
#: for the off-topic test (with spectrograph names and identifiers).
DOMAIN_TERMS = frozenset("""
pypeit spectrum spectra spectral spectroscopy spectroscopic spectrograph spectrographs
wavelength wavelengths arc arcs lamp lamps flat flats flatfield flat-field flat-fielding
bias biases dark darks slit slits slitmask longslit multislit echelle ifu fiber fibers
datacube coadd coadds coadding coadded telluric tellurics sensfunc flux fluxing fluxed
calibration calibrations calibrate calibrated reduction reductions extraction extract
extracted boxcar trace traces tracing tilt tilts sky skysub detector detectors ccd binning
exposure exposures fits header headers dispersion grating grism resolution redshift
spec1d spec2d ginga nod nodding a-b dithering airmass seeing standards sensitivity
""".split())
STOPWORDS = frozenset("""
a an and are as at be but by can do does for from how i if in into is it its me my
of on or so that the their them then there these this to use used using was what
when where which who why will with you your we our should would could also any
""".split())


def tokenize(text):
    """
    Split text into search tokens.

    Identifiers such as ``pypeit_setup`` or ``pypeit.core.skysub`` and
    hyphenated words such as ``A-B`` are kept whole and their parts are
    added too, so both ``bspline_spacing`` and ``spacing`` match.

    Parameters
    ----------
    text : str

    Returns
    -------
    list of str
        Lower-case tokens without stopwords.
    """
    out = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if len(tok) > 1 and tok not in STOPWORDS:
            out.append(tok)
        if '_' in tok or '.' in tok or '-' in tok:
            out.extend(p for p in re.split(r'[_.-]', tok) if len(p) > 1 and p not in STOPWORDS)
    return out


def search_text(chunk):
    """Text used for keyword search and embedding: title, location and body."""
    return f"{chunk['title']}\n{chunk.get('location', '')}\n\n{chunk['text']}"


class BM25:
    """
    Okapi BM25 with numpy postings.

    Parameters
    ----------
    docs : list of str
    k1, b : float, optional
    """

    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.n = len(docs)
        postings = defaultdict(lambda: ([], []))
        lengths = np.zeros(self.n, dtype=np.float32)
        for i, d in enumerate(docs):
            counts = Counter(tokenize(d))
            lengths[i] = sum(counts.values())
            for term, tf in counts.items():
                ids, tfs = postings[term]
                ids.append(i)
                tfs.append(tf)
        avgdl = float(lengths.mean()) if self.n else 1.0
        self.norm = k1 * (1 - b + b * lengths / max(avgdl, 1e-9))
        self.postings = {t: (np.asarray(ids, dtype=np.int32), np.asarray(tfs, dtype=np.float32))
                         for t, (ids, tfs) in postings.items()}

    def idf(self, term):
        """Inverse document frequency of a term (0 if unknown)."""
        if term not in self.postings:
            return 0.0
        df = len(self.postings[term][0])
        return float(np.log(1 + (self.n - df + 0.5) / (df + 0.5)))

    def scores(self, query):
        """
        BM25 score of every document, and the idf-weighted fraction of the
        query terms each document contains.

        Returns
        -------
        scores, coverage : numpy.ndarray
        """
        s = np.zeros(self.n, dtype=np.float32)
        cover = np.zeros(self.n, dtype=np.float32)
        terms = set(tokenize(query))
        total = sum(self.idf(t) for t in terms) or 1.0
        for term in terms:
            if term not in self.postings:
                continue
            ids, tfs = self.postings[term]
            idf = self.idf(term)
            s[ids] += idf * tfs * (self.k1 + 1) / (tfs + self.norm[ids])
            cover[ids] += idf / total
        return s, cover


@dataclass
class Source:
    """One retrieved chunk."""
    chunk: dict
    score: float
    pool: str                      # 'docs' or 'code'
    reason: str = 'search'         # 'pinned', 'search' or 'link:<reason>'
    linked_from: str = None        # chunk id this one was linked from


@dataclass
class Retrieval:
    """The result of :meth:`Index.retrieve`."""
    sources: list
    coverage: dict                 # pool -> bool (has relevant matches)
    spectrographs: list
    mode: str                      # 'hybrid' or 'keyword'
    best_cos: float = 0.0
    best_cover: float = 0.0
    pypeit_terms: list = field(default_factory=list)
    pool_best: dict = field(default_factory=dict)  # pool -> (best cos, best cover)

    @property
    def off_topic(self):
        """True if nothing in the question looks like PypeIt (DESIGN 11.3.3)."""
        if self.pypeit_terms:
            return False
        if self.mode == 'keyword':
            return self.best_cover < COVER_OFFTOPIC
        return self.pool_best.get('docs', (self.best_cos, 0))[0] < COS_OFFTOPIC


class IndexNotReady(Exception):
    """No complete index on disk."""


class Index:
    """
    The on-disk index loaded into memory.

    Parameters
    ----------
    directory : str or Path
        Folder written by ``pypeit_chat_ingest``.
    """

    def __init__(self, directory):
        directory = Path(directory)
        meta_path = directory / 'meta.json'
        if not meta_path.exists():
            raise IndexNotReady(f'No index in {directory}. Run pypeit_chat_ingest.')
        self.meta = json.loads(meta_path.read_text())
        for name, size in self.meta.get('files', {}).items():
            p = directory / name
            if not p.exists() or p.stat().st_size != size:
                raise IndexNotReady(f'The index in {directory} is being rebuilt.')
        with open(directory / 'chunks.jsonl') as f:
            self.chunks = [json.loads(line) for line in f if line.strip()]
        self.vectors = None
        emb = directory / 'embeddings.npy'
        if self.meta.get('has_embeddings') and emb.exists():
            self.vectors = np.load(emb)
            if len(self.vectors) != len(self.chunks):
                self.vectors = None
        xref_path = directory / 'xref.json'
        xref = json.loads(xref_path.read_text()) if xref_path.exists() else {}
        self.links = xref.get('links', {})
        self.params = xref.get('params', {})
        self.spectrographs = xref.get('spectrographs', {})
        cons = directory / 'consistency.json'
        self.findings = json.loads(cons.read_text()) if cons.exists() else []
        # Names the docs reference but the code lacks -> the stale docs chunks.
        self.stale_refs = defaultdict(list)
        for f in self.findings:
            if f.get('type') == 'stale_reference' and f.get('target'):
                for name in (f['target'], f['target'].split('.')[-1]):
                    self.stale_refs[name.lower()] += f['chunks']

        self.tag = self.meta.get('tag', 'unknown')
        self.tagger = Tagger(self.meta.get('instruments', []))
        self.vocabulary = set(DOMAIN_TERMS)
        for fam in self.tagger.families:
            self.vocabulary.update(p for p in fam.split('_') if len(p) >= 3)
        self.pos = {c['id']: i for i, c in enumerate(self.chunks)}
        self.bm25 = BM25([search_text(c) for c in self.chunks])
        kinds = np.array([c['kind'] for c in self.chunks])
        self.pool_mask = {'docs': np.isin(kinds, DOC_KINDS), 'code': np.isin(kinds, CODE_KINDS)}
        self.weight = np.array([c.get('weight', 1.0) for c in self.chunks], dtype=np.float32)
        self.tagged = [set(c.get('instruments') or []) for c in self.chunks]

        self.param_ids = defaultdict(list)
        self.code_names = defaultdict(list)
        self.script_ids = defaultdict(list)
        self.override_ids = defaultdict(list)
        for i, c in enumerate(self.chunks):
            if c['kind'] == 'override':
                self.override_ids[c['spectrograph'].lower()].append(i)
            if c['kind'] == 'param':
                self.param_ids[c['param'].lower()].append(i)
            elif c['kind'] == 'code':
                self.code_names[c['title'].lower()].append(i)
                if c.get('qualname'):
                    self.code_names[c['qualname'].lower()].append(i)
                    last = c['qualname'].split('.')[-1].lower()
                    if last != c['qualname'].lower():
                        self.code_names[last].append(i)
            elif c['kind'] == 'doc':
                for m in USAGE_RE.finditer(c['text']):
                    self.script_ids[m.group(1).lower()].append(i)

    @property
    def has_vectors(self):
        """True if the index has embeddings."""
        return self.vectors is not None

    def identifiers(self, query):
        """
        Exact PypeIt identifiers named in a query.

        Returns
        -------
        list of (str, list of int)
            Each name with the chunk positions it pins: parameter chunks
            (every ParSet), the code chunk of a dotted or function/class
            name, a script's help section, a spectrograph's documented
            defaults and its ``default_pypeit_par`` /
            ``config_specific_par``, or a docs section that references the
            name although the code lacks it (a stale reference).
        """
        names = []
        for rx in (BACKTICK_RE, DOTTED_RE, SNAKE_RE, CAMEL_RE):
            for m in rx.findall(query):
                m = m.strip('`()').rstrip('.,:;?')
                if m and m not in names:
                    names.append(m)
        out = []
        for name in names:
            low = name.lower()
            if low in NOT_IDENTIFIERS:
                continue
            ids = list(self.param_ids.get(low, []))
            ids += self.script_ids.get(low, [])[:1]
            code = self.code_names.get(low, [])
            if 0 < len(code) <= 3:
                ids += code
            if low in self.spectrographs:
                ids += self._spectrograph_pins(low, names)
            ids += [self.pos[c] for c in self.stale_refs.get(low, []) if c in self.pos][:1]
            if ids:
                out.append((name, list(dict.fromkeys(ids))))
        return out

    def _spectrograph_pins(self, name, names):
        """
        Chunks pinned for an exact spectrograph name: the part of its
        documented defaults that mentions another named identifier (else
        the first part), and its parameter methods in code.
        """
        parts = self.override_ids.get(name, [])
        others = [n.lower() for n in names if n.lower() != name]
        hit = [i for i in parts if any(o in self.chunks[i]['text'].lower() for o in others)]
        ids = (hit or parts)[:1]
        info = self.spectrographs[name]
        for key in ('default_pypeit_par', 'config_specific_par'):
            if info.get(key) in self.pos:
                ids.append(self.pos[info[key]])
        return ids

    def retrieve(self, query, qvec=None, doc_k=6, code_k=4, spectrographs=None):
        """
        Hybrid search over the docs pool and the code pool.

        Parameters
        ----------
        query : str
            Search query (question, plus context for follow-ups).
        qvec : numpy.ndarray, optional
            Normalized query embedding; keyword-only search if None.
        doc_k, code_k : int, optional
            Sources kept from each pool.
        spectrographs : list of str, optional
            Families to boost; default: those named in the query.

        Returns
        -------
        Retrieval
        """
        n = len(self.chunks)
        bm25, cover = self.bm25.scores(query)
        cos = None
        if qvec is not None and self.has_vectors:
            qvec = np.asarray(qvec, dtype=np.float32)
            if qvec.shape == (self.vectors.shape[1],):
                cos = self.vectors @ qvec
        specs = self.tagger.tags(query) if spectrographs is None else list(spectrographs)

        boost = np.ones(n, dtype=np.float32)
        for i, tags in enumerate(self.tagged):
            if not tags:
                continue
            if specs:
                boost[i] = BOOST_NAMED if tags & set(specs) else BOOST_OTHER
            else:
                boost[i] = BOOST_UNNAMED

        idents = self.identifiers(query)
        pinned = [i for _, ids in idents for i in ids]
        sources, coverage, pool_best = [], {}, {}
        relevance = np.zeros(n, dtype=np.float64)
        for pool, k in (('docs', doc_k), ('code', code_k)):
            mask = self.pool_mask[pool]
            fused = np.zeros(n, dtype=np.float64)
            rankings = [np.where(mask & (bm25 > 0), bm25, -np.inf)]
            if cos is not None:
                rankings.append(np.where(mask, cos, -np.inf))
            for s in rankings:
                top = np.argsort(-s)[:CANDIDATES]
                top = top[np.isfinite(s[top])]
                fused[top] += 1.0 / (RRF_K + 1 + np.arange(len(top)))
            fused *= self.weight * boost
            relevance += fused
            best_cos = float(cos[mask].max()) if cos is not None and mask.any() else 0.0
            best_cover = float(cover[mask].max()) if mask.any() else 0.0
            pool_pins = [i for i in pinned if mask[i]]
            pool_best[pool] = (best_cos, best_cover)
            coverage[pool] = bool(pool_pins) or best_cover >= COVER_RELEVANT or (
                cos is not None and best_cos >= COS_RELEVANT[pool])
            order = pool_pins + [i for i in np.argsort(-fused)
                                 if fused[i] > 0 and i not in pool_pins]
            sources += self._diverse(order, fused, pool, k, set(pool_pins))
        out = Retrieval(sources=sources, coverage=coverage, spectrographs=specs,
                        mode='hybrid' if cos is not None else 'keyword',
                        best_cos=float(cos.max()) if cos is not None else 0.0,
                        best_cover=float(cover.max()) if n else 0.0,
                        pypeit_terms=[name for name, _ in idents] + specs
                        + sorted(self.vocabulary.intersection(tokenize(query))),
                        pool_best=pool_best)
        self.expand(out, doc_k, code_k, relevance)
        return out

    def _diverse(self, order, fused, pool, k, pins):
        per_file, per_title, out = Counter(), Counter(), []
        for i in order:
            if len(out) >= k:
                break
            c = self.chunks[i]
            key = (c['path'], c['kind'])
            if per_file[key] >= MAX_PER_FILE or per_title[c['title']] >= MAX_PER_TITLE:
                continue
            per_file[key] += 1
            per_title[c['title']] += 1
            out.append(Source(c, float(fused[i]), pool, 'pinned' if i in pins else 'search'))
        return out

    def expand(self, result, doc_k, code_k, relevance):
        """
        Cross-reference expansion (DESIGN 7.1.5).  For every parameter hit
        and the top :data:`EXPAND_FROM` other docs hits, add one linked code
        chunk; for the top code hits, add one linked docs chunk.  A
        parameter's definition comes first; otherwise the linked chunk most
        relevant to the query is chosen (code-to-docs links must be relevant
        at all).  Linked chunks replace the lowest-ranked searched chunks of
        their pool, so the total stays the same.  Modifies ``result``.

        Parameters
        ----------
        result : Retrieval
        doc_k, code_k : int
            Pool sizes.
        relevance : numpy.ndarray
            Fused query score of every chunk.
        """
        def pool_of(name):
            return [s for s in result.sources if s.pool == name]

        for from_pool, to_pool, cap in (('docs', 'code', code_k), ('code', 'docs', doc_k)):
            present = {s.chunk['id'] for s in result.sources}
            hits = pool_of(from_pool)
            if from_pool == 'docs':
                hits = ([s for s in hits if s.chunk['kind'] == 'param']
                        + [s for s in hits if s.chunk['kind'] != 'param'][:EXPAND_FROM])
            else:
                hits = hits[:EXPAND_FROM]
            added = []
            for s in hits:
                cands = [(t, r) for t, r in self.links.get(s.chunk['id'], [])
                         if t not in present and t in self.pos]
                if from_pool == 'code':
                    cands = [(t, r) for t, r in cands if r in CODE_TO_DOC_REASONS
                             and relevance[self.pos[t]] > 0]
                if not cands:
                    continue
                first = next(((t, r) for t, r in cands if r == 'definition'), None)
                target, reason = first or max(cands, key=lambda c: relevance[self.pos[c[0]]])
                added.append(Source(self.chunks[self.pos[target]],
                                    float(relevance[self.pos[target]]), to_pool,
                                    f'link:{reason}', s.chunk['id']))
                present.add(target)
                if len(added) >= MAX_LINKED:
                    break
            if not added:
                continue
            current = pool_of(to_pool)
            overflow = len(current) + len(added) - cap
            if overflow > 0:
                # Drop the lowest-ranked searched (not pinned or linked) chunks.
                drop = [s for s in reversed(current) if s.reason == 'search'][:overflow]
                added = added[:len(added) - (overflow - len(drop))]
                dropped = {id(s) for s in drop}
                result.sources = [s for s in result.sources if id(s) not in dropped]
            result.sources += added
            if added:
                result.coverage[to_pool] = True
        result.sources.sort(key=lambda s: 0 if s.pool == 'docs' else 1)

    def param_facts(self, chunk):
        """Code facts (DESIGN 6.4) for a parameter chunk, or None."""
        if chunk.get('kind') != 'param':
            return None
        return self.params.get(f"{chunk['parclass']}.{chunk['param']}")


class IndexHolder:
    """
    Keep the current index loaded and reload it when ``meta.json`` changes,
    so a new release is picked up without restarting (DESIGN F10).  If the
    new files are incomplete, the old index stays in use.

    Parameters
    ----------
    directory : Path
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        self._index = None
        self._mtime = None
        self._lock = threading.Lock()
        self.error = None

    def get(self):
        """
        The current index.

        Raises
        ------
        IndexNotReady
            If no index has ever loaded.
        """
        meta = self.directory / 'meta.json'
        try:
            mtime = meta.stat().st_mtime_ns
        except FileNotFoundError:
            mtime = None
        if mtime != self._mtime:
            with self._lock:
                if mtime != self._mtime:
                    try:
                        self._index = Index(self.directory)
                        self._mtime = mtime
                        self.error = None
                    except IndexNotReady as e:
                        self.error = str(e)
                    except (OSError, ValueError, KeyError) as e:
                        self.error = f'Could not load the index: {e}'
        if self._index is None:
            raise IndexNotReady(self.error or f'No index in {self.directory}.')
        return self._index
