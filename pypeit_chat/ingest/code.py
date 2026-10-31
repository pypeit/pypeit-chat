"""
Code chunks from ``pypeit/**/*.py`` via ``ast``: one per module (docstring
and outline), class (signature, docstring and method list), function and
method.
"""
import ast
import sys
from pathlib import Path

GITHUB = 'https://github.com/pypeit/PypeIt/blob'
#: Maximum characters of source kept in a code chunk.
CODE_MAX = 3500
#: Folders under ``pypeit/`` that are not indexed.
CODE_SKIP = ('pypeit/tests/', 'pypeit/data/')


def module_name(rel):
    """``pypeit/core/skysub.py`` -> ``pypeit.core.skysub``."""
    return rel[:-3].replace('/', '.').removesuffix('.__init__')


def code_files(root):
    """
    The Python files to index.

    Parameters
    ----------
    root : Path
        Checkout root (holding ``pypeit/``).

    Returns
    -------
    list of (Path, str)
        Absolute path and repository-relative POSIX path.
    """
    root = Path(root)
    out = []
    for path in sorted((root / 'pypeit').rglob('*.py')):
        rel = path.relative_to(root).as_posix()
        if not rel.startswith(CODE_SKIP):
            out.append((path, rel))
    return out


def parse(path, rel):
    """
    Read and parse a Python file.

    Returns
    -------
    tuple
        (source lines, ast tree), or (None, None) if it does not parse.
    """
    src = path.read_text(errors='replace')
    try:
        return src.splitlines(), ast.parse(src)
    except SyntaxError as e:
        print(f'WARNING: skipping {rel}: {e}', file=sys.stderr)
        return None, None


def node_start(node):
    """First line of a definition, including its decorators."""
    return min([d.lineno for d in getattr(node, 'decorator_list', [])] + [node.lineno])


def _docstring_end(node):
    first = node.body[0] if node.body else None
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return first.end_lineno
    return None


def code_chunks(root, sha, tagger):
    """
    One chunk per module, class, function and method under ``pypeit/``.

    Parameters
    ----------
    root : Path
        Checkout root.
    sha : str
        Commit used in GitHub links.
    tagger : Tagger
        Instrument tagger.

    Returns
    -------
    list of dict
        Chunks with ``qualname``, ``module``, ``codetype`` (``module``,
        ``class`` or ``def``), ``start`` and ``end`` (1-based, inclusive;
        ``start`` includes decorators).  ``text`` is the source of lines
        ``start``... (truncated to :data:`CODE_MAX`), except for modules
        and classes, which hold a header and outline.
    """
    chunks = []
    for path, rel in code_files(root):
        lines, tree = parse(path, rel)
        if tree is None:
            continue
        module = module_name(rel)
        tags = tagger.tags(rel)

        def add(qualname, codetype, text, start, end):
            if len(text) > CODE_MAX:
                text = text[:CODE_MAX].rsplit('\n', 1)[0] + '\n# … (truncated)'
            title = f'{module}.{qualname}' if qualname else module
            chunks.append({
                'id': f'code:{title}', 'kind': 'code', 'path': rel, 'title': title,
                'location': f'{rel} L{start}–{end}',
                'url': f'{GITHUB}/{sha}/{rel}#L{start}-L{end}',
                'module': module, 'qualname': qualname, 'codetype': codetype,
                'start': start, 'end': end, 'instruments': tags, 'weight': 1.0,
                'text': text})

        # Module: docstring plus outline.
        doc_end = _docstring_end(tree) or 0
        outline = []
        classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
        funcs = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if classes:
            outline.append('# Classes: ' + ', '.join(classes))
        if funcs:
            outline.append('# Functions: ' + ', '.join(funcs))
        head = '\n'.join(lines[:doc_end]) if doc_end else ''
        if head or outline:
            add('', 'module', '\n'.join([head] + outline).strip(), 1, max(doc_end, 1))

        def visit(body, prefix):
            for node in body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                start, end = node_start(node), node.end_lineno
                qual = prefix + node.name
                if isinstance(node, ast.ClassDef):
                    doc_end = _docstring_end(node)
                    head_end = doc_end if doc_end else node.body[0].lineno - 1
                    head = '\n'.join(lines[start - 1:max(head_end, node.lineno)])
                    methods = [n.name for n in node.body
                               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    if methods:
                        head += '\n\n# Methods: ' + ', '.join(methods)
                    add(qual, 'class', head, start, end)
                    visit(node.body, qual + '.')
                else:
                    add(qual, 'def', '\n'.join(lines[start - 1:end]), start, end)

        visit(tree.body, '')
    return chunks
