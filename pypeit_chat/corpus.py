"""
The bot's own PypeIt checkout (``cache/PypeIt``) at the latest release tag.

The bot never reads a developer's working copy.  It keeps a blobless clone of
the official repository with a sparse checkout of the files it indexes (the
docs and the code, without ``pypeit/data`` and the doc images), and checks out
the newest tag reachable from ``origin/release``.
"""
import subprocess
from datetime import datetime, timezone
from pathlib import Path

#: Sparse-checkout patterns (non-cone): docs and code, no data or images.
SPARSE_PATTERNS = ['/*.rst', '/*.cfg', '/*.toml',
                   '/doc/', '!/doc/**/*.png', '!/doc/**/*.jpg', '!/doc/**/*.jpeg',
                   '!/doc/**/*.gif', '!/doc/**/*.pdf', '!/doc/**/*.fits',
                   '/pypeit/', '!/pypeit/data/']


class CorpusError(Exception):
    """A problem with the corpus checkout, with a message for the operator."""


def git(repo, *args, timeout=600):
    """
    Run a git command in ``repo`` and return its standard output.

    Raises
    ------
    CorpusError
        If git fails.
    """
    try:
        out = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                             text=True, check=True, timeout=timeout)
    except subprocess.CalledProcessError as e:
        raise CorpusError(f"git {' '.join(args)} failed: {e.stderr.strip()}") from e
    except subprocess.TimeoutExpired as e:
        raise CorpusError(f"git {' '.join(args)} timed out") from e
    return out.stdout.strip()


def latest_tag(repo, ref='origin/release'):
    """
    Newest tag reachable from ``ref``.

    Uses ``git describe`` rather than sorting tag names, because old tags
    such as ``v1.3`` sort above ``2.0.1``.

    Parameters
    ----------
    repo : Path
        Git repository.
    ref : str, optional
        Branch to describe.

    Returns
    -------
    str
    """
    return git(repo, 'describe', '--tags', '--abbrev=0', ref)


def clone(url, corpus_dir, blobless=True):
    """
    Clone ``url`` into ``corpus_dir`` without checking anything out, and
    set up the sparse checkout.
    """
    corpus_dir = Path(corpus_dir)
    corpus_dir.parent.mkdir(parents=True, exist_ok=True)
    args = ['clone', '--no-checkout']
    if blobless:
        args.append('--filter=blob:none')
    git(corpus_dir.parent, *args, url, corpus_dir.name, timeout=1800)
    git(corpus_dir, 'sparse-checkout', 'set', '--no-cone', *SPARSE_PATTERNS)


def update(corpus_dir, url, tag=None, fetch=True, blobless=True):
    """
    Make sure ``corpus_dir`` is a checkout of ``tag`` (or the latest release
    tag) and return what it holds.

    Parameters
    ----------
    corpus_dir : Path
        The bot's checkout; cloned from ``url`` if it does not exist.
    url : str
        Repository to clone.
    tag : str, optional
        Tag to check out.  Default: the latest tag on ``origin/release``.
    fetch : bool, optional
        Fetch branches and tags from ``origin`` first.
    blobless : bool, optional
        Use a blobless (``--filter=blob:none``) clone.

    Returns
    -------
    dict
        ``tag``, ``sha``, ``commit_time`` and ``changed`` (whether the
        checkout moved).
    """
    corpus_dir = Path(corpus_dir)
    if not (corpus_dir / '.git').exists():
        clone(url, corpus_dir, blobless=blobless)
        fetch = False
    if fetch:
        git(corpus_dir, 'fetch', '--tags', '--prune', '--force', 'origin', timeout=1800)
    tag = tag or latest_tag(corpus_dir)
    sha = git(corpus_dir, 'rev-parse', f'{tag}^{{commit}}')
    try:
        current = git(corpus_dir, 'rev-parse', 'HEAD')
    except CorpusError:
        current = None
    if current != sha or not (corpus_dir / 'pypeit').is_dir():
        git(corpus_dir, 'checkout', '--quiet', '--detach', sha, timeout=1800)
    return {'tag': tag, 'sha': sha,
            'commit_time': git(corpus_dir, 'show', '-s', '--format=%cI', sha),
            'changed': current != sha}


def describe(corpus_dir):
    """
    What the checkout holds now, without fetching.

    Returns
    -------
    dict
        ``tag`` (exact tag at HEAD, or ``git describe`` output), ``sha`` and
        ``commit_time``.
    """
    sha = git(corpus_dir, 'rev-parse', 'HEAD')
    try:
        tag = git(corpus_dir, 'describe', '--tags', '--exact-match', sha)
    except CorpusError:
        tag = git(corpus_dir, 'describe', '--tags', sha)
    return {'tag': tag, 'sha': sha,
            'commit_time': git(corpus_dir, 'show', '-s', '--format=%cI', sha)}


def now():
    """Current UTC time as an ISO string (seconds)."""
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
