"""
Parameters from ``doc/pypeit_par.rst``: one chunk per ParSet parameter,
titled with its full ``.pypeit`` path and ParSet, and one chunk per
spectrograph for the "Instrument-Specific Default Configuration" sections.
"""
import ast
import re

from pypeit_chat.ingest.rst import heading_text, inline, make_id, section_chunks

BORDER_RE = re.compile(r'^=+(?: +=+)+\s*$')
PARCLASS_RE = re.compile(r'pypeitpar\.(\w+Par)\b')
KEYWORDS_RE = re.compile(r'^(\w+Par) Keywords$')
INSTR_HEADING = 'Instrument-Specific Default Configuration'
SPEC_NAME_RE = re.compile(r'\(([a-z0-9_]+)\)\s*$')
INI_SECTION_RE = re.compile(r'^\s*(\[+)\s*([^\]]+?)\s*(\]+)\s*$')
INI_KEY_RE = re.compile(r'^\s*([A-Za-z_][\w]*)\s*=\s*(.*?)\s*$')
#: Maximum nesting when following ParSet references from PypeItPar.
MAX_DEPTH = 6


def parse_simple_table(lines):
    """
    Parse an RST simple table.

    Parameters
    ----------
    lines : list of str
        Lines containing the table.

    Returns
    -------
    header : list of str
    rows : list of list of str
        Continuation lines (empty first column) are merged into the row above.
    """
    borders = [i for i, l in enumerate(lines) if BORDER_RE.match(l)]
    if len(borders) < 3:
        return [], []
    starts = [m.start() for m in re.finditer(r'=+', lines[borders[0]])]

    def cols(line):
        return [line[s:(starts[k + 1] if k + 1 < len(starts) else None)].strip()
                for k, s in enumerate(starts)]

    header = cols(lines[borders[0] + 1])
    rows = []
    for line in lines[borders[1] + 1:borders[2]]:
        if not line.strip():
            continue
        c = cols(line)
        if not c[0] and rows:
            rows[-1] = [f'{a} {b}'.strip() for a, b in zip(rows[-1], c)]
        else:
            rows.append(c)
    return header, rows


def bracket(path):
    """``['reduce', 'skysub']`` -> ``[reduce][skysub]``."""
    return ''.join(f'[{p}]' for p in path)


def configobj_block(path, key, value):
    """Render ``[a][b] key = value`` as a nested configobj (``.pypeit``) block."""
    out = ['    ' * d + '[' * (d + 1) + sec + ']' * (d + 1) for d, sec in enumerate(path)]
    out.append('    ' * len(path) + f'{key} = {value}')
    return '\n'.join(out)


def parset_tables(sections):
    """
    The ParSet tables in ``pypeit_par.rst``.

    Parameters
    ----------
    sections : list of dict
        From :func:`pypeit_chat.ingest.rst.read_sections`.

    Returns
    -------
    dict
        ParSet class name -> (anchor, rows).
    """
    tables = {}
    for s in sections:
        m = KEYWORDS_RE.match(s['title'])
        if s['page'] == 'pypeit_par' and m:
            anchor = make_id(s['labels'][-1]) if s['labels'] else make_id(s['title'])
            tables[m.group(1)] = (anchor, parse_simple_table(s['body'])[1])
    return tables


def parset_paths(tables):
    """
    Every ``.pypeit`` path to each ParSet, following which table's Type
    column references which ParSet, starting from ``PypeItPar``.

    Parameters
    ----------
    tables : dict
        From :func:`parset_tables`.

    Returns
    -------
    dict
        ParSet name -> list of paths (each a list of keys).
    """
    edges = {}
    for par, (_, rows) in tables.items():
        for row in rows:
            m = PARCLASS_RE.search(row[1]) if len(row) > 1 else None
            if m:
                edges.setdefault(par, []).append((row[0].strip('`'), m.group(1)))
    paths, queue = {'PypeItPar': [[]]}, ['PypeItPar']
    while queue:
        parent = queue.pop(0)
        for key, child in edges.get(parent, []):
            for p in paths[parent]:
                new = p + [key]
                if len(new) <= MAX_DEPTH and new not in paths.setdefault(child, []):
                    paths[child].append(new)
                    queue.append(child)
    return paths


def param_title(key, par_paths):
    """Chunk title: every path if there are few, else the common suffix."""
    if len(par_paths) <= 3:
        return ' or '.join(bracket(p) for p in par_paths) + f' {key}'
    suffix = []
    for parts in zip(*[reversed(p) for p in par_paths]):
        if len(set(parts)) != 1:
            break
        suffix.insert(0, parts[0])
    return '[…]' + bracket(suffix) + f' {key}'


def param_chunks(sections, ctx, url_base, tagger):
    """
    One chunk per ParSet parameter.

    Parameters
    ----------
    sections : list of dict
        From :func:`pypeit_chat.ingest.rst.read_sections`.
    ctx : RstContext
        For cleaning descriptions.
    url_base : str
        Docs URL including the version.
    tagger : Tagger
        Instrument tagger.

    Returns
    -------
    list of dict
        Chunks with ``param``, ``parclass``, ``paths``, ``doc_default``,
        ``doc_options`` and ``doc_type``.
    """
    tables = parset_tables(sections)
    paths = parset_paths(tables)
    chunks = []
    for par, (anchor, rows) in tables.items():
        par_paths = paths.get(par) or [[]]
        for row in rows:
            key, typ, opts, default, desc = (row + [''] * 5)[:5]
            if PARCLASS_RE.search(typ):
                continue
            key = key.strip('`')
            typ = heading_text(typ, ctx)
            opts = '' if opts.strip() == '..' else heading_text(opts, ctx)
            default = None if default.strip() == '..' else heading_text(default, ctx)
            desc = inline(desc, ctx)
            example = next((p for p in par_paths if p[:1] == ['scienceframe']), par_paths[0])
            lines = [f'Parameter `{key}` (ParSet {par}).',
                     'Set in the PypeIt file as: '
                     + ', '.join(f'{bracket(p)} {key}' for p in par_paths),
                     f'Type: {typ}',
                     f'Default: {default if default is not None else "None"}']
            if opts:
                lines.append(f'Options: {opts}')
            lines += [f'Description: {desc}', '', 'Example (.pypeit file):', '```ini',
                      configobj_block(example, key,
                                      default if default is not None else '<value>'),
                      '```']
            chunks.append({
                'id': f'param:{par}.{key}', 'kind': 'param',
                'path': 'doc/pypeit_par.rst', 'page': 'pypeit_par',
                'title': param_title(key, par_paths) + f' ({par})',
                'location': f'User-level Parameters › {par} Keywords',
                'url': f'{url_base}/pypeit_par.html#{anchor}',
                'param': key, 'parclass': par, 'paths': par_paths,
                'doc_type': typ, 'doc_default': default, 'doc_options': opts,
                'instruments': tagger.tags(key), 'weight': 1.0,
                'text': '\n'.join(lines)})
    return chunks


def parse_ini(lines):
    """
    Parse a configobj block into settings.

    Parameters
    ----------
    lines : list of str
        Lines of the block.

    Returns
    -------
    dict
        Path tuple (sections + key) -> value string.
    """
    out, stack = {}, []
    for line in lines:
        m = INI_SECTION_RE.match(line)
        if m:
            depth = len(m.group(1))
            stack = stack[:depth - 1] + [m.group(2)]
            continue
        m = INI_KEY_RE.match(line)
        if m:
            out[tuple(stack) + (m.group(1),)] = m.group(2)
    return out


INI_FENCE_RE = re.compile(r'```ini[^\n]*\n(.*?)```', re.S)


def ini_settings(text):
    """
    Settings shown in the ```ini blocks of a cleaned docs chunk.

    Returns
    -------
    dict
        Path tuple (sections + key) -> value string.
    """
    out = {}
    for m in INI_FENCE_RE.finditer(text):
        out.update(parse_ini(m.group(1).split('\n')))
    return out


def is_override_section(section):
    """True for a spectrograph's section under the instrument-specific defaults."""
    return (section['page'] == 'pypeit_par' and INSTR_HEADING in section['headings']
            and section['headings'][-1] != INSTR_HEADING)


def override_chunks(sections, ctx, url_base, tagger):
    """
    One chunk per spectrograph from "Instrument-Specific Default
    Configuration".

    Returns
    -------
    list of dict
        Chunks with ``spectrograph`` and ``settings`` (``.pypeit`` path
        string -> value string, as the docs list them).
    """
    chunks = []
    for s in sections:
        if not is_override_section(s):
            continue
        m = SPEC_NAME_RE.search(s['title'])
        if not m:
            continue
        name = m.group(1)
        settings = parse_ini(s['body'])
        for part, c in enumerate(section_chunks(s, ctx, url_base, tagger, kind='override',
                                                weight=0.6)):
            c.update(id=f'override:{name}:{part}', spectrograph=name,
                     title=f'Default parameters for {name}',
                     instruments=sorted(set(c['instruments']) | set(tagger.tags(name))),
                     settings={bracket(k[:-1]) + ' ' + k[-1]: v for k, v in settings.items()})
            chunks.append(c)
    return chunks


def parse_value(text):
    """
    Canonical form of a value written in the docs (configobj style).

    ``None, 60.0,`` -> ``(None, 60.0)``; ``0.6`` -> ``0.6``;
    ``mean`` -> ``'mean'``.  Numbers become floats so 5 == 5.0.

    Parameters
    ----------
    text : str or None

    Returns
    -------
    object
        Hashable canonical value.
    """
    if text is None:
        return None
    text = text.strip()
    if ',' in text:
        return tuple(parse_value(p) for p in text.split(',') if p.strip())
    if text in ('None', ''):
        return None
    if text in ('True', 'False'):
        return text == 'True'
    try:
        return float(text)
    except ValueError:
        return text.strip('\'"')


def canonical(value):
    """
    Canonical form of a Python literal value, comparable with
    :func:`parse_value`.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)):
        return tuple(canonical(v) for v in value)
    return str(value)


def doc_value(text):
    """
    Canonical form of a value as written in the docs: a Python literal if it
    parses as one (``(1, 2, 3),``), else :func:`parse_value`.
    """
    if text is None:
        return None
    try:
        return canonical(ast.literal_eval(text.strip()))
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return parse_value(text)


#: Values the docs tables cannot tell apart (all rendered as ``..`` or empty).
EMPTY = (None, '', ())


def same_value(text, value):
    """
    True if a value written in the docs equals a canonical code value.

    Parameters
    ----------
    text : str or None
        Value from the docs (``None`` for ``..``).
    value : object
        :func:`canonical` value from the code.

    Returns
    -------
    bool
    """
    if isinstance(value, str) and text is not None:
        return text.strip().strip('\'"') == value
    doc = doc_value(text)
    if doc in EMPTY and value in EMPTY:
        return True
    if isinstance(value, tuple) and not isinstance(doc, tuple):
        doc = (doc,)
    return doc == value


def literal(node):
    """``ast.literal_eval`` of a node, or ``None`` and False if not a literal."""
    try:
        return ast.literal_eval(node), True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None, False
