"""
Read the Sphinx docs: expand includes, find headings and anchors, clean RST
to text, and split sections into chunks.

Python roles (``:func:`` etc.) and inline literals are captured *before* the
RST is cleaned, so :mod:`pypeit_chat.ingest.xref` can link each section to
the code it is about.
"""
import os
import re
import unicodedata
from pathlib import Path

#: Folders under ``doc/`` that are not indexed at all.
DOC_SKIP_DIRS = {'api', '_static', '_templates', 'figures', '_build'}
#: Pages not indexed (``whatsnew`` only includes the release notes).
DOC_SKIP_FILES = {'whatsnew.rst'}
#: Folders Sphinx excludes as pages (``conf.py`` ``exclude_patterns``); their
#: files are only indexed where another page includes them.
DOC_INCLUDE_ONLY = {'include', 'help', 'scripts'}
#: Maximum characters in a docs chunk.
DOC_MAX = 2200

INCLUDE_RE = re.compile(r'^(\s*)\.\. include::\s*(\S+)\s*$')
DIRECTIVE_RE = re.compile(r'^(\s*)\.\.\s+([\w:-]+)::(?:\s+(.*))?$')
LABEL_RE = re.compile(r'^\s*\.\.\s+_([^:`]+):\s*$')
TARGET_RE = re.compile(r'^\s*\.\.\s+(?:_[^:]+|__):\s+\S')
COMMENT_RE = re.compile(r'^(\s*)\.\.(\s|$)')
ADORN_CHARS = set('=-~^"\'`#*+:._<>!$%&,;?@\\|/')
ROLE_RE = re.compile(r':(?:[a-z]+:)?([a-z]+):`([^`]+)`')
LITERAL_RE = re.compile(r'``([^`]+?)``')
IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$')
PY_ROLES = {'class', 'func', 'meth', 'attr', 'mod', 'obj', 'data', 'exc', 'const', 'any'}
ADMONITIONS = {'note', 'warning', 'tip', 'important', 'caution', 'danger', 'attention',
               'hint', 'error', 'todo', 'seealso', 'versionadded', 'versionchanged',
               'deprecated', 'admonition'}
SKIP_DIRECTIVES = {'toctree', 'raw', 'contents', 'highlight', 'index', 'tabularcolumns',
                   'include', 'meta', 'automodule', 'autoclass', 'autofunction',
                   'currentmodule', 'module', 'autosummary', 'image', 'only-latex'}
TITLED_CONTAINERS = {'rubric', 'topic', 'sidebar', 'table', 'tab-item', 'grid-item-card',
                     'card', 'dropdown', 'list-table', 'csv-table'}
CODE = '\x00'   # marks lines that are code and must not be cleaned


def make_id(text):
    """
    Docutils' ``make_id``: the HTML id Sphinx gives a label or heading.

    Parameters
    ----------
    text : str
        Label or heading text.

    Returns
    -------
    str
    """
    s = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'[^a-z0-9]+', '-', ' '.join(s.split()))
    return re.sub(r'^[-0-9]+|-+$', '', s)


def indent_of(line):
    """Number of leading spaces."""
    return len(line) - len(line.lstrip(' '))


def is_adorn(line):
    """True for a heading under/overline such as ``=====``."""
    s = line.rstrip()
    return len(s) >= 2 and s[0] in ADORN_CHARS and s == s[0] * len(s) and s != '::'


def take_block(lines, i, base):
    """Lines from ``i`` that are blank or indented deeper than ``base``."""
    j = i
    while j < len(lines) and (not lines[j].strip() or indent_of(lines[j]) > base):
        j += 1
    while j > i and not lines[j - 1].strip():
        j -= 1
    return lines[i:j], j


def dedent(block):
    """Remove the common indentation of a block of lines."""
    ind = min((indent_of(l) for l in block if l.strip()), default=0)
    return [l[ind:] if l.strip() else '' for l in block]


def split_options(block):
    """Drop a directive's ``:option:`` lines."""
    i = 0
    while i < len(block) and re.match(r'^:[\w-]+:', block[i]):
        i += 1
    while i < len(block) and not block[i].strip():
        i += 1
    return block[i:]


def expand_includes(path, docroot, seen, included, depth=0):
    """
    Read an RST file with its ``.. include::`` directives expanded inline.

    Parameters
    ----------
    path : Path
        File to read.
    docroot : Path
        The ``doc`` directory (for includes starting with ``/``).
    seen : tuple
        Files on the current include stack (cycle guard).
    included : set
        Collects every file that was included.
    depth : int, optional
        Current include depth.

    Returns
    -------
    list of str
        The lines of the file.
    """
    out = []
    for line in path.read_text(errors='replace').splitlines():
        m = INCLUDE_RE.match(line)
        if not m:
            out.append(line.rstrip())
            continue
        pad, target = m.groups()
        tpath = Path(os.path.normpath(docroot / target.lstrip('/') if target.startswith('/')
                                      else path.parent / target))
        if not tpath.is_file() or tpath in seen or depth > 8:
            continue
        included.add(tpath)
        sub = expand_includes(tpath, docroot, seen + (tpath,), included, depth + 1)
        out += [pad + l if l.strip() else '' for l in sub]
    return out


class RstContext:
    """Label and page titles used to resolve ``:ref:`` and ``:doc:`` roles."""

    def __init__(self):
        self.labels = {}       # label (lower case) -> title
        self.pages = {}        # page name -> title
        self.page = ''

    def ref(self, target):
        """Title for a ``:ref:`` label."""
        return self.labels.get(target.lower())

    def doc(self, target):
        """Title for a ``:doc:`` target relative to the current page."""
        if target.startswith('/'):
            name = target.lstrip('/')
        else:
            name = os.path.normpath(os.path.join(os.path.dirname(self.page), target))
        return self.pages.get(name.removesuffix('.rst'))


def split_role(content):
    """Split role content ``text <target>`` into (text, target)."""
    c = ' '.join(content.split())
    mt = re.match(r'^(.*?)\s*<([^<>]+)>$', c)
    return (mt.group(1).strip(), mt.group(2).strip()) if mt else ('', c)


def role_display(text, target):
    """The text Sphinx shows for a Python role (as :func:`inline` writes it)."""
    if text:
        return text
    if target.startswith('~'):
        return target[1:].split('.')[-1]
    return target.lstrip('!')


def inline(s, ctx):
    """
    Turn inline RST markup into plain text / light Markdown.

    Parameters
    ----------
    s : str
        RST text.
    ctx : RstContext
        Resolves ``:ref:`` and ``:doc:``.

    Returns
    -------
    str
    """
    def role(m):
        r = m.group(1)
        text, target = split_role(m.group(2))
        if r == 'math':
            return f'${m.group(2)}$'
        if r in ('ref', 'numref'):
            return text or ctx.ref(target) or target
        if r == 'doc':
            return text or ctx.doc(target) or target.split('/')[-1]
        if r in PY_ROLES:
            return f'`{role_display(text, target)}`'
        return text or target

    s = ROLE_RE.sub(role, s)
    s = re.sub(r'``(.+?)``', r'`\1`', s, flags=re.S)

    def link(m):
        text, target = ' '.join(m.group(1).split()), m.group(2)
        return f'{text} ({target})' if re.match(r'https?://', target) else text
    s = re.sub(r'(?<!`)`([^`<]+?)\s*<([^<>`]+)>`__?', link, s)
    s = re.sub(r'(?<!`)`([^`]+)`__?(?![\w])', lambda m: ' '.join(m.group(1).split()), s)
    s = re.sub(r'\s?\[(?:#\w*|\d+|\*)\]_', '', s)
    s = re.sub(r'\|([A-Za-z][\w -]*)\|', r'\1', s)
    return s


def heading_text(raw, ctx):
    """Plain text of a heading (no markup)."""
    return ' '.join(re.sub(r'[`*]', '', inline(raw, ctx)).split())


def rst_lines(lines, ctx):
    """
    Convert RST body lines to plain-text lines.  Code lines are prefixed with
    :data:`CODE` so they are not inline-cleaned later.
    """
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        m = DIRECTIVE_RE.match(line)
        if m:
            ind, name, arg = len(m.group(1)), m.group(2).lower(), (m.group(3) or '').strip()
            block, i = take_block(lines, i + 1, ind)
            content = split_options(dedent(block)) if block else []
            pad = ' ' * ind
            if name in ('code-block', 'code', 'sourcecode', 'parsed-literal'):
                lang = arg.split()[0] if arg else ''
                out += [CODE + pad + '```' + lang] + [CODE + pad + l for l in content] \
                       + [CODE + pad + '```']
            elif name == 'math':
                for eq in '\n'.join(content).split('\n\n'):
                    if eq.strip():
                        out.append(CODE + pad + '$$ ' + ' '.join(eq.split()) + ' $$')
            elif name == 'figure':
                out += [pad + l for l in rst_lines(content, ctx)]
            elif name in SKIP_DIRECTIVES or name.startswith('auto'):
                pass
            elif name in ADMONITIONS:
                title = arg if name == 'admonition' else name.capitalize()
                body = rst_lines(content, ctx)
                out.append(f'{pad}**{title}:** {"" if name == "admonition" else arg}'.rstrip())
                out += [(CODE + pad + l[1:]) if l.startswith(CODE) else pad + l for l in body]
            else:
                if arg and name in TITLED_CONTAINERS:
                    out.append(pad + arg)
                body = rst_lines(content, ctx)
                out += [(CODE + pad + l[1:]) if l.startswith(CODE) else pad + l for l in body]
            continue
        if LABEL_RE.match(line) or TARGET_RE.match(line):
            i += 1
            continue
        if COMMENT_RE.match(line):
            _, i = take_block(lines, i + 1, indent_of(line))
            continue
        stripped = line.rstrip()
        if stripped.endswith('::'):
            lead = stripped[:-2].rstrip()
            if lead.strip():
                out.append(lead if stripped.endswith(' ::') else lead + ':')
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n and indent_of(lines[j]) > indent_of(line):
                block, i = take_block(lines, j, indent_of(line))
                pad = ' ' * indent_of(line)
                out += [CODE + pad + '```'] + [CODE + pad + l for l in dedent(block)] \
                       + [CODE + pad + '```']
            else:
                i += 1
            continue
        if re.match(r'^\s*\|(\s|$)', line):
            line = re.sub(r'^(\s*)\|\s?', r'\1', line)
        out.append(line)
        i += 1
    return out


def dedent_text(text):
    """Remove the common indentation of a text."""
    lines = text.split('\n')
    ind = min((indent_of(l) for l in lines if l.strip()), default=0)
    return '\n'.join(l[ind:] if l.strip() else '' for l in lines)


def rst_to_text(lines, ctx):
    """
    Convert RST body lines to cleaned text, with code in Markdown fences.

    Parameters
    ----------
    lines : list of str
        RST lines.
    ctx : RstContext
        Resolves ``:ref:`` and ``:doc:``.

    Returns
    -------
    str
    """
    out, para = [], []

    def flush():
        if para:
            out.extend(inline('\n'.join(para), ctx).split('\n'))
            para.clear()

    for line in rst_lines(lines, ctx):
        if line.startswith(CODE):
            flush()
            out.append(line[1:])
        else:
            para.append(line)
    flush()
    text = '\n'.join(out)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return dedent_text(text).strip()


def find_headings(lines):
    """
    Locate section headings.

    Parameters
    ----------
    lines : list of str
        RST lines.

    Returns
    -------
    list of dict
        ``start`` (first line, including any overline), ``end`` (first body
        line), ``raw`` (title text), ``style`` and ``labels``.
    """
    heads, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        prev_blank = i == 0 or not lines[i - 1].strip()
        # Overline + title + underline
        if (is_adorn(line) and i + 2 < n and lines[i + 1].strip() and is_adorn(lines[i + 2])
                and lines[i + 2].rstrip() == line.rstrip() and prev_blank
                and len(line.rstrip()) >= len(lines[i + 1].strip())):
            heads.append({'start': i, 'end': i + 3, 'raw': lines[i + 1].strip(),
                          'style': (line.strip()[0], True)})
            i += 3
            continue
        # Title + underline
        if (line.strip() and not line[0].isspace() and not is_adorn(line) and prev_blank
                and i + 1 < n and is_adorn(lines[i + 1])
                and len(lines[i + 1].rstrip()) >= len(line.rstrip())
                and not line.lstrip().startswith('.. ')):
            heads.append({'start': i, 'end': i + 2, 'raw': line.strip(),
                          'style': (lines[i + 1].strip()[0], False)})
            i += 2
            continue
        i += 1
    for h in heads:
        labels, j = [], h['start'] - 1
        while j >= 0 and (not lines[j].strip() or LABEL_RE.match(lines[j])):
            m = LABEL_RE.match(lines[j])
            if m:
                labels.insert(0, m.group(1).strip())
            j -= 1
        h['labels'] = labels
    return heads


def split_text(text, maxlen=DOC_MAX):
    """
    Split text on blank lines, never inside code fences, into pieces of at
    most ``maxlen`` characters.

    Parameters
    ----------
    text : str
        Text to split.
    maxlen : int, optional
        Maximum piece length.

    Returns
    -------
    list of str
    """
    paras, cur, fence = [], [], False
    for line in text.split('\n'):
        if line.strip().startswith('```'):
            fence = not fence
        if not line.strip() and not fence:
            if cur:
                paras.append('\n'.join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        paras.append('\n'.join(cur))

    pieces, buf = [], ''
    for p in paras:
        if len(p) > maxlen:
            if buf:
                pieces.append(buf)
                buf = ''
            pieces += _hard_split(p, maxlen)
        elif buf and len(buf) + 2 + len(p) > maxlen:
            pieces.append(buf)
            buf = p
        else:
            buf = f'{buf}\n\n{p}' if buf else p
    if buf:
        pieces.append(buf)
    return pieces


def _hard_split(text, maxlen):
    """Split one long paragraph by lines, re-opening code fences as needed."""
    pieces, cur, fence, opener = [], [], False, '```'
    size = 0
    for line in text.split('\n'):
        line = line[:maxlen]
        if cur and size + len(line) + 1 > maxlen - 8:
            pieces.append('\n'.join(cur + (['```'] if fence else [])))
            cur, size = ([opener] if fence else []), 0
        if line.strip().startswith('```'):
            fence = not fence
            if fence:
                opener = line.strip()
        cur.append(line)
        size += len(line) + 1
    if cur:
        pieces.append('\n'.join(cur))
    return pieces


def fenced_blocks(text):
    """
    The fenced code blocks of a cleaned chunk, pairing fences in order.

    Returns
    -------
    list of (str, str)
        (language, code) for each block; the language may be empty.
    """
    blocks, lang, cur = [], None, []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('```'):
            if lang is None:
                lang, cur = stripped[3:].strip().lower(), []
            else:
                blocks.append((lang, '\n'.join(cur)))
                lang = None
            continue
        if lang is not None:
            cur.append(line)
    return blocks


def code_refs(lines):
    """
    Python roles and identifier-like inline literals in raw RST lines.

    Parameters
    ----------
    lines : list of str
        Raw (uncleaned) RST lines.

    Returns
    -------
    roles : list of dict
        ``role``, ``target`` (without ``~``/``!``) and ``display`` (the text
        that appears in the cleaned chunk).
    literals : list of str
        Inline literals that look like Python names, e.g. ``global_skysub``.
    """
    raw = '\n'.join(lines)
    roles, seen = [], set()
    for m in ROLE_RE.finditer(raw):
        if m.group(1) not in PY_ROLES:
            continue
        text, target = split_role(m.group(2))
        clean = target.lstrip('~!')
        if (m.group(1), clean) in seen or not IDENT_RE.match(clean):
            continue
        seen.add((m.group(1), clean))
        roles.append({'role': m.group(1), 'target': clean,
                      'display': role_display(text, target)})
    literals = []
    for m in LITERAL_RE.finditer(raw):
        lit = m.group(1).strip().removesuffix('()')
        if len(lit) >= 4 and IDENT_RE.match(lit) and lit not in literals:
            literals.append(lit)
    return roles, literals


def read_sections(docroot):
    """
    Split every docs page into sections.

    Parameters
    ----------
    docroot : Path
        The ``doc`` directory.

    Returns
    -------
    sections : list of dict
        ``page`` (e.g. ``tutorials/lris_howto``), ``path`` (``doc/...``),
        ``url_page``, ``title``, ``headings`` (the heading stack),
        ``location``, ``anchor`` (empty for the page top), ``labels`` and
        ``body`` (raw RST lines).
    ctx : RstContext
        Page and label titles, for cleaning.
    """
    docroot = Path(docroot)
    pages = {}
    for path in sorted(docroot.rglob('*.rst')):
        rel = path.relative_to(docroot)
        if rel.parts[0] in DOC_SKIP_DIRS or rel.as_posix() in DOC_SKIP_FILES:
            continue
        pages[Path(os.path.normpath(path))] = rel
    included, expanded = set(), {}
    for path in pages:
        expanded[path] = expand_includes(path, docroot, (path,), included)
    pages = {p: r for p, r in pages.items()
             if p not in included and r.parts[0] not in DOC_INCLUDE_ONLY}

    ctx = RstContext()
    heads = {}
    for path, rel in pages.items():
        page = rel.with_suffix('').as_posix()
        heads[path] = find_headings(expanded[path])
        for h in heads[path]:
            title = heading_text(h['raw'], ctx)
            if page not in ctx.pages:
                ctx.pages[page] = title
            for lab in h['labels']:
                ctx.labels.setdefault(lab.lower(), title)

    sections = []
    for path, rel in pages.items():
        lines, hs = expanded[path], heads[path]
        page = rel.with_suffix('').as_posix()
        page_title = ctx.pages.get(page, rel.stem)
        if not hs:
            hs = [{'start': 0, 'end': 0, 'raw': page_title, 'style': None, 'labels': []}]
            top_has_heading = False
        else:
            top_has_heading = True
        styles, stack = [], []
        for k, h in enumerate(hs):
            if h['style'] is not None and h['style'] not in styles:
                styles.append(h['style'])
            level = styles.index(h['style']) + 1 if h['style'] is not None else 1
            title = heading_text(h['raw'], ctx)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            headings = [t for _, t in stack]
            body_end = hs[k + 1]['start'] if k + 1 < len(hs) else len(lines)
            if k == 0:
                body = (lines[:h['start']] + lines[h['end']:body_end]) if top_has_heading \
                    else lines[:body_end]
            else:
                body = lines[h['end']:body_end]
            anchor = '' if k == 0 else (make_id(h['labels'][-1]) if h['labels']
                                        else make_id(title))
            location = ' › '.join(headings if headings[0] == page_title
                                  else [page_title] + headings)
            sections.append({
                'page': page, 'path': f'doc/{rel.as_posix()}',
                'url_page': 'whatsnew' if rel.parts[0] == 'releases' else page,
                'title': title, 'headings': headings, 'location': location,
                'anchor': anchor, 'labels': h['labels'], 'level': level, 'body': body})
    return sections, ctx


def doc_weight(section):
    """Retrieval weight of a docs section (user audience; DESIGN 6.2)."""
    top = section['path'].split('/')[1]
    if top == 'releases':
        return 0.5
    if top == 'dev':
        return 0.7
    return 1.0


def section_chunks(section, ctx, url_base, tagger, kind='doc', weight=None):
    """
    Turn one section into chunks of at most :data:`DOC_MAX` characters.

    Parameters
    ----------
    section : dict
        From :func:`read_sections`.
    ctx : RstContext
        For cleaning.
    url_base : str
        Docs URL including the version, e.g.
        ``https://pypeit.readthedocs.io/en/2.0.1``.
    tagger : Tagger
        Instrument tagger.
    kind : str, optional
        Chunk kind.
    weight : float, optional
        Retrieval weight; default from :func:`doc_weight`.

    Returns
    -------
    list of dict
        Chunks, each with ``refs`` (roles) and ``literals`` that appear in it.
    """
    ctx.page = section['page']
    text = rst_to_text(section['body'], ctx)
    if len(text) < 20:
        return []
    roles, literals = code_refs(section['body'])
    url = f"{url_base}/{section['url_page']}.html" + (f"#{section['anchor']}"
                                                      if section['anchor'] else '')
    tags = tagger.tags(section['path'], *section['headings'])
    chunks = []
    for part, piece in enumerate(split_text(text, DOC_MAX)):
        chunks.append({
            'id': f"{kind}:{section['page']}#{section['anchor']}:{part}", 'kind': kind,
            'path': section['path'], 'page': section['url_page'], 'title': section['title'],
            'location': section['location'], 'url': url, 'instruments': tags,
            'weight': doc_weight(section) if weight is None else weight,
            'refs': [r for r in roles if f"`{r['display']}`" in piece],
            'literals': [lit for lit in literals if f'`{lit}' in piece],
            'text': piece})
    return chunks
