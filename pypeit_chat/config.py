"""
Settings for pypeit-chat, read from environment variables or a ``.env`` file.

Environment variables take precedence over ``.env``.  Relative paths are
resolved against the repository root (the folder holding ``setup.py``).
See ``DESIGN.md`` Section 9 for the meaning of each setting.
"""
import os
from dataclasses import MISSING, dataclass, field, fields, replace
from pathlib import Path

#: Root of the pypeit-chat repository.
REPO_DIR = Path(__file__).resolve().parents[1]

#: Prefixes some embedding models expect: (document prefix, query prefix).
EMBED_PREFIXES = {
    'nomic-embed-text': ('search_document: ', 'search_query: '),
    'mxbai-embed-large': ('', 'Represent this sentence for searching relevant passages: '),
    'snowflake-arctic-embed': ('', 'Represent this sentence for searching relevant passages: '),
}

#: How each runtime switches reasoning ("thinking") on and off; see
#: :func:`pypeit_chat.llm.think_options`.
THINK_SWITCHES = ('reasoning_effort', 'chat_template_kwargs', 'none')


def read_dotenv(path):
    """
    Read ``KEY=value`` lines from a ``.env`` file.

    Parameters
    ----------
    path : Path
        File to read.  A missing file gives an empty dict.

    Returns
    -------
    dict
        Keys and values; surrounding quotes are removed from the values.
    """
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip().removeprefix('export ').strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
            value = value[1:-1]
        out[key] = value
    return out


def _ids(value):
    return frozenset(v.strip() for v in value.split(',') if v.strip())


def _bool(value):
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _path(value):
    p = Path(value).expanduser()
    return p if p.is_absolute() else REPO_DIR / p


@dataclass(frozen=True)
class Settings:
    """
    All settings.  Each field maps to the environment variable of the same
    name in upper case (e.g. ``chat_model`` <- ``CHAT_MODEL``).
    """
    llm_base_url: str = 'http://127.0.0.1:11434/v1'
    llm_api_key: str = 'ollama'
    chat_model: str = 'qwen3.8:27b-mlx'
    embed_model: str = 'nomic-embed-text'
    temperature: float = 0.2
    think: bool = False
    think_switch: str = 'reasoning_effort'
    llm_timeout: float = 300.0
    doc_sources: int = 6
    code_sources: int = 4
    corpus_dir: Path = REPO_DIR / 'cache' / 'PypeIt'
    corpus_url: str = 'https://github.com/pypeit/PypeIt.git'
    pypeit_tag: str = ''
    dev_suite_dir: str = ''
    index_dir: Path = REPO_DIR / 'index'
    docs_url: str = 'https://pypeit.readthedocs.io/en'
    slack_bot_token: str = ''
    slack_app_token: str = ''
    home_team_id: str = ''
    allowed_channel_ids: frozenset = field(default_factory=frozenset)
    allowed_user_ids: frozenset = field(default_factory=frozenset)
    blocked_user_ids: frozenset = field(default_factory=frozenset)
    admin_channel_id: str = ''
    admin_user_ids: frozenset = field(default_factory=frozenset)
    rate_per_user_hour: int = 20
    rate_per_user_day: int = 60
    rate_per_channel_hour: int = 60
    max_concurrent: int = 1
    max_queue: int = 10
    max_question_chars: int = 2000
    db_path: Path = REPO_DIR / 'cache' / 'pypeit_chat.sqlite3'
    log_text: bool = False
    log_retention_days: int = 180
    hash_salt: str = ''
    paused_file: Path = REPO_DIR / 'cache' / 'PAUSED'

    @classmethod
    def load(cls, env=None, dotenv=None, **overrides):
        """
        Build the settings from the environment and ``.env``.

        Parameters
        ----------
        env : dict, optional
            Variables to use instead of ``os.environ``.
        dotenv : Path, optional
            ``.env`` file; defaults to ``.env`` in the repository root.  Pass
            a non-existent path to ignore it.
        **overrides
            Field values that take precedence over both.

        Returns
        -------
        Settings
        """
        values = read_dotenv(REPO_DIR / '.env' if dotenv is None else Path(dotenv))
        values.update(os.environ if env is None else env)
        kwargs = {}
        for f in fields(cls):
            raw = values.get(f.name.upper())
            if raw is None:
                continue
            default = f.default if f.default_factory is MISSING else f.default_factory()
            if isinstance(default, bool):
                kwargs[f.name] = _bool(raw)
            elif isinstance(default, int):
                kwargs[f.name] = int(raw)
            elif isinstance(default, float):
                kwargs[f.name] = float(raw)
            elif isinstance(default, frozenset):
                kwargs[f.name] = _ids(raw)
            elif isinstance(default, Path):
                kwargs[f.name] = _path(raw)
            else:
                kwargs[f.name] = raw.strip()
        kwargs.update(overrides)
        settings = cls(**kwargs)
        if settings.think_switch not in THINK_SWITCHES:
            raise ValueError(f'THINK_SWITCH must be one of {THINK_SWITCHES}, '
                             f'not {settings.think_switch!r}')
        return settings

    def with_(self, **changes):
        """Return a copy with some fields changed."""
        return replace(self, **changes)


def embed_prefixes(model):
    """
    Return the (document, query) prefixes for an embedding model.

    Parameters
    ----------
    model : str
        Model name, with or without a ``:tag``.

    Returns
    -------
    tuple
        Two strings; empty for models that don't use prefixes.
    """
    base = model.split(':')[0].split('/')[-1]
    return EMBED_PREFIXES.get(base, ('', ''))
