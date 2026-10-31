"""
Optional: example ``.pypeit`` files from the PypeIt development suite
(``DEV_SUITE_DIR``), one chunk per file.  The dev suite is read-only.
"""
import subprocess
from pathlib import Path

GITHUB = 'https://github.com/pypeit/PypeIt-development-suite/blob'
#: Maximum characters kept from an example file (the data table is long).
EXAMPLE_MAX = 3000


def example_chunks(dev_suite_dir, tagger):
    """
    One chunk per ``pypeit_files/*.pypeit`` file.

    Parameters
    ----------
    dev_suite_dir : str or Path
        Dev-suite checkout.
    tagger : Tagger
        Instrument tagger.

    Returns
    -------
    list of dict
    """
    root = Path(dev_suite_dir).expanduser()
    files = sorted((root / 'pypeit_files').glob('*.pypeit'))
    if not files:
        return []
    try:
        sha = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True,
                             text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = 'develop'
    chunks = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(errors='replace')
        if len(text) > EXAMPLE_MAX:
            text = text[:EXAMPLE_MAX].rsplit('\n', 1)[0] + '\n# … (truncated)'
        chunks.append({
            'id': f'example:{path.stem}', 'kind': 'example', 'path': rel,
            'title': f'Example PypeIt file {path.name}',
            'location': f'PypeIt development suite › {rel}',
            'url': f'{GITHUB}/{sha}/{rel}', 'instruments': tagger.tags(path.stem),
            'weight': 0.8, 'text': text})
    return chunks
