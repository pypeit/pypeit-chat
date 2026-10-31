"""
Docs <-> code cross-references, parameter code facts and consistency checks
(DESIGN Section 6.4).

From docs to code:

- Python roles (``:func:``, ``:class:``...) captured before cleaning;
- inline literals that name a known code symbol;
- a script's help section (``usage: pypeit_setup``) -> its script class;
- a spectrograph page or HOWTO -> ``pypeit/spectrographs/<family>.py``.

From each parameter to code (parsed with ``ast``): the ParSet definition and
code default, the functions that read it, and spectrograph overrides
(``default_pypeit_par`` and the setup-dependent ``config_specific_par``).

The reverse links are added, and mismatches between docs and code are
returned as consistency findings.
"""
import ast
import re
import tomllib
from collections import defaultdict
from pathlib import Path

from pypeit_chat.ingest.code import code_files, module_name, parse
from pypeit_chat.ingest.rst import fenced_blocks
from pypeit_chat.ingest.params import (bracket, canonical, doc_value, ini_settings, literal,
                                       same_value)

USAGE_RE = re.compile(r'^\s*\$?\s*usage:\s+(\S+)', re.M)
#: Languages of the code examples whose pypeit imports are checked.
PY_LANGS = ('', 'python', 'py', 'ipython', 'pycon')
FROM_IMPORT_RE = re.compile(r'^[ \t]*(?:>>>[ \t]*)?from[ \t]+(pypeit(?:\.\w+)*)[ \t]+import[ \t]+'
                            r'(?:\(([\w\s,]+)\)|([\w \t,]+))', re.M)
IMPORT_RE = re.compile(r'^\s*(?:>>>\s*)?import\s+(pypeit(?:\.\w+)*)', re.M)
SPEC_METHODS = ('default_pypeit_par', 'config_specific_par')
#: Link reasons in priority order (lower index = more relevant).
REASONS = ('definition', 'role', 'script', 'use', 'override', 'literal', 'spectrograph')
MAX_DOC_TO_CODE = 8
MAX_CODE_TO_DOC = 12
MAX_USES = 12


def historical(path):
    """True for the notes of a past release (``doc/releases/1.18.0.rst``)."""
    return path.startswith('doc/releases/') and 'dev' not in Path(path).stem


def subscript_chain(node):
    """
    Keys of a subscript chain: ``par['a']['b']`` -> (``par`` node, ['a', 'b']).
    Non-constant keys are None.
    """
    keys = []
    while isinstance(node, ast.Subscript):
        sl = node.slice
        keys.insert(0, sl.value if isinstance(sl, ast.Constant) and isinstance(sl.value, str)
                    else None)
        node = node.value
    return node, keys


def outer_subscripts(tree):
    """
    Yield (node, enclosing function/class stack) for every subscript chain
    that is not itself part of a longer chain.
    """
    def walk(node, stack, inner):
        if isinstance(node, ast.Subscript) and not inner:
            yield node, stack
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack = stack + [node]
        for name, child in ast.iter_fields(node):
            children = child if isinstance(child, list) else [child]
            for c in children:
                if isinstance(c, ast.AST):
                    yield from walk(c, stack, isinstance(node, ast.Subscript) and name == 'value')
    yield from walk(tree, [], False)


def parset_facts(root):
    """
    Defaults, options and dtypes set in each ParSet's ``__init__``.

    Parameters
    ----------
    root : Path
        Checkout root.

    Returns
    -------
    dict
        ParSet name -> key -> {``default``, ``options``, ``dtypes``: each
        {``src``, ``value``, ``literal``, ``line``}} plus ``path``.
    """
    facts = {}
    for path, rel in code_files(root):
        if not rel.startswith('pypeit/par/'):
            continue
        lines, tree = parse(path, rel)
        if tree is None:
            continue
        src = '\n'.join(lines)
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            init = next((n for n in cls.body if isinstance(n, ast.FunctionDef)
                         and n.name == '__init__'), None)
            if init is None:
                continue
            for node in ast.walk(init):
                if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                    continue
                t = node.targets[0]
                if not (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                        and t.value.id in ('defaults', 'options', 'dtypes')
                        and isinstance(t.slice, ast.Constant)
                        and isinstance(t.slice.value, str)):
                    continue
                value, ok = literal(node.value)
                kind = 'default' if t.value.id == 'defaults' else t.value.id
                entry = facts.setdefault(cls.name, {}).setdefault(t.slice.value, {'path': rel})
                entry[kind] = {'src': ast.get_source_segment(src, node.value),
                               'value': canonical(value) if ok else None,
                               'literal': ok, 'line': node.lineno}
    return facts


class SpanMap:
    """Find the innermost code chunk holding a line of a file."""

    def __init__(self, chunks):
        self.spans = defaultdict(list)
        self.modules = {}
        for c in chunks:
            if c['kind'] != 'code':
                continue
            if c['codetype'] == 'module':
                self.modules[c['path']] = c['id']
            else:
                self.spans[c['path']].append((c['end'] - c['start'], c['start'], c['end'],
                                              c['id']))
        for v in self.spans.values():
            v.sort()

    def find(self, path, line):
        """Chunk id holding ``line`` of ``path`` (module chunk if none)."""
        for _, a, b, cid in self.spans.get(path, []):
            if a <= line <= b:
                return cid
        return self.modules.get(path)


def script_classes(root):
    """
    Script name -> class path from ``pyproject.toml`` (``[project.scripts]``).

    Returns
    -------
    dict
        e.g. ``{'pypeit_setup': 'pypeit.scripts.setup.Setup'}``.
    """
    path = Path(root) / 'pyproject.toml'
    if not path.is_file():
        return {}
    scripts = tomllib.loads(path.read_text()).get('project', {}).get('scripts', {})
    out = {}
    for name, target in scripts.items():
        mod, _, obj = target.partition(':')
        out[name] = f"{mod}.{obj.split('.')[0]}" if obj else mod
    return out


class CodeScan:
    """
    One pass over the code: symbols, parameter subscripts and spectrograph
    classes.

    Parameters
    ----------
    root : Path
        Checkout root.
    """

    def __init__(self, root):
        self.symbols = set()
        self.classes = {}     # class name -> info
        self.chains = []      # (rel, line, keys, root text, store?, function stack names)
        for path, rel in code_files(root):
            lines, tree = parse(path, rel)
            if tree is None:
                continue
            src = '\n'.join(lines)
            module = module_name(rel)
            self.symbols.add(module)
            self._symbols(tree.body, module)
            for node, stack in outer_subscripts(tree):
                base, keys = subscript_chain(node)
                if not keys or any(k is None for k in keys):
                    continue
                self.chains.append({
                    'path': rel, 'line': node.lineno, 'keys': keys,
                    'root': ast.unparse(base), 'store': isinstance(node.ctx, ast.Store),
                    'value': None, 'stack': [s.name for s in stack]})
            if rel.startswith('pypeit/spectrographs/'):
                self._spectrographs(tree, rel, src)

    def _symbols(self, body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f'{prefix}.{node.name}'
                self.symbols.add(name)
                if isinstance(node, ast.ClassDef):
                    self._symbols(node.body, name)
                    for sub in ast.walk(node):
                        if (isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store)
                                and isinstance(sub.value, ast.Name) and sub.value.id == 'self'):
                            self.symbols.add(f'{name}.{sub.attr}')
            elif isinstance(node, (ast.Try, ast.If)):
                for part in ('body', 'orelse', 'finalbody'):
                    self._symbols(getattr(node, part, []), prefix)
                for handler in getattr(node, 'handlers', []):
                    self._symbols(handler.body, prefix)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # Re-exported names (e.g. pypeit.specutils.Spectrum).
                for alias in node.names:
                    self.symbols.add(f"{prefix}.{(alias.asname or alias.name).split('.')[0]}")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            self.symbols.add(f'{prefix}.{n.id}')

    def _spectrographs(self, tree, rel, src):
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            info = {'path': rel, 'module': module_name(rel), 'name': None,
                    'bases': [b.attr if isinstance(b, ast.Attribute) else getattr(b, 'id', None)
                              for b in cls.bases],
                    'methods': {}}
            for node in cls.body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id == 'name'
                        and isinstance(node.value, ast.Constant)):
                    info['name'] = node.value.value
                if isinstance(node, ast.FunctionDef) and node.name in SPEC_METHODS:
                    sets, dynamic, calls_super = [], [], False
                    for sub in ast.walk(node):
                        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                                and sub.func.attr == node.name):
                            calls_super = True
                        if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
                            base, keys = subscript_chain(sub.targets[0])
                            if keys and not all(keys):
                                # e.g. par['calibrations']['flatfield'][k] = v in a loop:
                                # anything under the constant prefix may change.
                                dynamic.append(keys[:keys.index(None)])
                            elif len(keys) >= 2 and isinstance(sub.targets[0], ast.Subscript):
                                value, ok = literal(sub.value)
                                sets.append({'keys': keys, 'line': sub.lineno,
                                             'src': ast.get_source_segment(src, sub.value),
                                             'value': canonical(value) if ok else None,
                                             'literal': ok})
                    info['methods'][node.name] = {'sets': sets, 'dynamic': dynamic,
                                                  'calls_super': calls_super,
                                                  'line': node.lineno}
            self.classes[cls.name] = info

    def method_owner(self, cls_name, method, _depth=0):
        """
        The class that defines ``method`` for ``cls_name`` (itself or the
        nearest ancestor), or None.
        """
        info = self.classes.get(cls_name)
        if info is None or _depth > 10:
            return None
        if method in info['methods']:
            return cls_name
        parent = next((b for b in info['bases'] if b in self.classes), None)
        return self.method_owner(parent, method, _depth + 1) if parent else None

    def dynamic_prefixes(self, cls_name, _depth=0):
        """
        Key prefixes under which ``default_pypeit_par`` (of the class or the
        ancestors it calls) assigns with non-constant keys, so the values
        there cannot be read from the code statically.

        Returns
        -------
        list of list of str
        """
        info = self.classes.get(cls_name)
        if info is None or _depth > 10:
            return []
        method = info['methods'].get('default_pypeit_par')
        parent = next((b for b in info['bases'] if b in self.classes), None)
        out = []
        if (method is None or method['calls_super']) and parent:
            out += self.dynamic_prefixes(parent, _depth + 1)
        if method is not None:
            out += method['dynamic']
        return out

    def effective_defaults(self, cls_name, _depth=0):
        """
        Settings made by ``default_pypeit_par`` for a class, following
        ``super()`` calls and inheritance.

        Returns
        -------
        dict
            ``.pypeit`` path string -> set entry (the last assignment wins).
        """
        info = self.classes.get(cls_name)
        if info is None or _depth > 10:
            return {}
        method = info['methods'].get('default_pypeit_par')
        parent = next((b for b in info['bases'] if b in self.classes), None)
        out = {}
        if method is None or method['calls_super']:
            if parent:
                out.update(self.effective_defaults(parent, _depth + 1))
        if method is not None:
            for s in sorted(method['sets'], key=lambda s: s['line']):
                out[bracket(s['keys'][:-1]) + ' ' + s['keys'][-1]] = dict(s, cls=cls_name)
        return out


def match_parsets(keys, key_index, paths):
    """
    ParSets a subscript chain refers to.

    Parameters
    ----------
    keys : list of str
        The chain's keys; the last is the parameter.
    key_index : dict
        Parameter key -> ParSet names that have it.
    paths : dict
        ParSet -> list of ``.pypeit`` paths.

    Returns
    -------
    list of str
        Matching ParSets.  A one-key chain matches only if the key belongs
        to a single ParSet.
    """
    cands = key_index.get(keys[-1], [])
    prefix = keys[:-1]
    if not prefix:
        return cands if len(cands) == 1 else []
    return [p for p in cands
            if any(len(path) >= len(prefix) and path[len(path) - len(prefix):] == prefix
                   for path in paths.get(p, []))]


def build(root, chunks, paths):
    """
    Compute cross-references, parameter facts and consistency findings.

    Parameters
    ----------
    root : Path
        Checkout root.
    chunks : list of dict
        All chunks (docs, params, overrides, code).  Doc chunks lose their
        temporary ``refs``/``literals`` fields; affected chunks gain
        ``flags``.
    paths : dict
        ParSet -> ``.pypeit`` paths, from
        :func:`pypeit_chat.ingest.params.parset_paths`.

    Returns
    -------
    xref : dict
        ``links`` (chunk id -> list of [target id, reason]), ``params``
        (``ParSet.key`` -> code facts) and ``spectrographs`` (name -> code
        chunks of its class and its ``default_pypeit_par`` and
        ``config_specific_par``, which may be inherited).
    findings : list of dict
        Consistency findings: ``type``, ``chunks`` and ``detail``.
    """
    root = Path(root)
    by_id = {c['id']: c for c in chunks}
    spans = SpanMap(chunks)
    scan = CodeScan(root)
    pfacts = parset_facts(root)
    scripts = script_classes(root)

    code_ids = {c['id'] for c in chunks if c['kind'] == 'code'}
    bare = defaultdict(list)
    for c in chunks:
        if c['kind'] == 'code' and c['codetype'] != 'module':
            bare[c['qualname'].split('.')[-1]].append(c['id'])

    links = defaultdict(dict)
    findings = []

    def link(a, b, reason):
        if a == b or a not in by_id or b not in by_id:
            return
        old = links[a].get(b)
        if old is None or REASONS.index(reason) < REASONS.index(old):
            links[a][b] = reason

    def flag(cid, text):
        if cid in by_id:
            by_id[cid].setdefault('flags', [])
            if text not in by_id[cid]['flags']:
                by_id[cid]['flags'].append(text)

    def resolve(name, strict=False):
        """
        Code chunk for a dotted or bare name, or None.  With ``strict``
        (inline literals), a bare name must look like an identifier:
        snake_case, or CamelCase with two or more capitals.
        """
        for cand in (name, f'pypeit.{name}'):
            if f'code:{cand}' in code_ids and name.lower() != 'pypeit':
                return f'code:{cand}'
        last = name.split('.')[-1]
        if strict and '_' not in last and '.' not in name and (
                sum(ch.isupper() for ch in last) < 2 or last.lower() == 'pypeit'):
            return None
        ids = bare.get(last, [])
        if len(ids) == 1 and len(last) >= 5:
            return ids[0]
        return None

    # --- Docs -> code -----------------------------------------------------
    for c in chunks:
        if c['kind'] not in ('doc', 'override'):
            continue
        for r in c.pop('refs', []):
            target = r['target']
            cid = f'code:{target}' if f'code:{target}' in code_ids else None
            if cid is None and target.startswith('pypeit.'):
                parent = target.rsplit('.', 1)[0]
                if target in scan.symbols:
                    cid = f'code:{parent}' if f'code:{parent}' in code_ids else None
                elif f'code:{parent}' in code_ids and by_id[f'code:{parent}']['codetype'] == 'class':
                    cid = f'code:{parent}'          # possibly inherited
                elif historical(c['path']):
                    pass                            # past release notes describe past code
                else:
                    findings.append({'type': 'stale_reference', 'chunks': [c['id']],
                                     'target': target,
                                     'detail': f":{r['role']}:`{target}` does not exist in "
                                               'the code at this release'})
                    flag(c['id'], f"Stale docs reference: `{target}` does not exist in "
                                  'the code at this release.')
            elif cid is None:
                cid = resolve(target)
            if cid:
                link(c['id'], cid, 'role')
        for lit in c.pop('literals', []):
            if lit in scripts:
                cid = resolve(scripts[lit])
                if cid:
                    link(c['id'], cid, 'script')
                continue
            cid = resolve(lit, strict=True)
            if cid:
                link(c['id'], cid, 'literal')
        for m in USAGE_RE.finditer(c['text']):
            if m.group(1) in scripts:
                cid = resolve(scripts[m.group(1)])
                if cid:
                    link(c['id'], cid, 'script')
        if c['path'].startswith('doc/spectrographs/') or (
                c['path'].startswith('doc/tutorials/') and 'howto' in c['path']):
            for fam in c.get('instruments', []):
                if f'code:pypeit.spectrographs.{fam}' in code_ids:
                    link(c['id'], f'code:pypeit.spectrographs.{fam}', 'spectrograph')

    # --- Parameters -> code ------------------------------------------------
    key_index = defaultdict(list)
    for par, keys in pfacts.items():
        for key in keys:
            key_index[key].append(par)
    params = {}
    param_chunk = {}
    for c in chunks:
        if c['kind'] == 'param':
            param_chunk[f"{c['parclass']}.{c['param']}"] = c['id']
    for par, keys in pfacts.items():
        for key, f in keys.items():
            entry = {'parset': par, 'key': key, 'paths': [bracket(p) for p in paths.get(par, [])],
                     'path': f['path'], 'uses': [], 'overrides': []}
            for kind in ('default', 'options', 'dtypes'):
                if kind in f:
                    entry[f'code_{kind}'] = f[kind]['src']
                    entry[f'code_{kind}_line'] = f[kind]['line']
            line = f.get('default', f.get('dtypes', {})).get('line')
            entry['definition'] = spans.find(f['path'], line) if line else None
            params[f'{par}.{key}'] = entry

    for ch in scan.chains:
        if ch['path'].startswith('pypeit/par/') or 'par' not in ch['root'].lower():
            continue
        for par in match_parsets(ch['keys'], key_index, paths):
            entry = params[f"{par}.{ch['keys'][-1]}"]
            cid = spans.find(ch['path'], ch['line'])
            if ch['store'] and ch['path'].startswith('pypeit/spectrographs/'):
                continue      # recorded below, with values
            if not ch['store'] and cid and cid not in entry['uses']:
                entry['uses'].append(cid)

    for cls_name, info in scan.classes.items():
        for method, m in info['methods'].items():
            for s in m['sets']:
                for par in match_parsets(s['keys'], key_index, paths):
                    entry = params[f"{par}.{s['keys'][-1]}"]
                    entry['overrides'].append({
                        'spectrograph': info['name'] or cls_name, 'class': cls_name,
                        'method': method, 'value': s['src'], 'line': s['line'],
                        'path': info['path'], 'chunk': spans.find(info['path'], s['line'])})

    for pkey, entry in params.items():
        entry['uses'] = entry['uses'][:MAX_USES]
        pid = param_chunk.get(pkey)
        if pid is None:
            continue
        if entry['definition']:
            link(pid, entry['definition'], 'definition')
        for cid in entry['uses'][:3]:
            link(pid, cid, 'use')
        for o in entry['overrides'][:3]:
            if o['chunk']:
                link(pid, o['chunk'], 'override')

    # --- Consistency: documented vs code defaults and options -------------
    for c in chunks:
        if c['kind'] != 'param':
            continue
        entry = params.get(f"{c['parclass']}.{c['param']}")
        f = pfacts.get(c['parclass'], {}).get(c['param'], {})
        if entry is None:
            findings.append({'type': 'param_missing_in_code', 'chunks': [c['id']],
                             'detail': f"{c['parclass']}.{c['param']} is documented but not "
                                       'defined in the code at this release'})
            flag(c['id'], f"Documented, but `{c['param']}` is not defined in "
                          f"{c['parclass']} in the code at this release.")
            continue
        d = f.get('default')
        if d and d['literal'] and not same_value(c['doc_default'], d['value']):
            detail = (f"{c['parclass']}.{c['param']}: docs default {c['doc_default']!r}, "
                      f"code default {d['src']} ({f['path']} L{d['line']})")
            findings.append({'type': 'default_mismatch', 'chunks': [c['id'], entry['definition']],
                             'detail': detail})
            flag(c['id'], 'Docs and code differ: ' + detail)
            flag(entry['definition'], 'Docs and code differ: ' + detail)
        o = f.get('options')
        if o and o['literal'] and c['doc_options']:
            code_opts = set(o['value']) if isinstance(o['value'], tuple) else {o['value']}
            doc_opts = doc_value(c['doc_options'])
            doc_opts = set(doc_opts) if isinstance(doc_opts, tuple) else {doc_opts}
            if code_opts != doc_opts:
                detail = (f"{c['parclass']}.{c['param']}: docs options {c['doc_options']!r}, "
                          f"code options {o['src']}")
                findings.append({'type': 'options_mismatch',
                                 'chunks': [c['id'], entry['definition']], 'detail': detail})
                flag(c['id'], 'Docs and code differ: ' + detail)

    # --- Consistency: .pypeit settings shown in the docs --------------------
    # Only blocks under a real top-level section ([rdx], [reduce]...): other
    # ini blocks in the docs are .pypirc, setup.cfg and the like.
    top = {p[0] for plist in paths.values() for p in plist if p}
    for c in chunks:
        if c['kind'] != 'doc':
            continue
        for path in ini_settings(c['text']):
            keys = list(path)
            if len(keys) < 2 or keys[0] not in top:
                continue
            shown = bracket(keys[:-1]) + ' ' + keys[-1]
            if keys[-1] not in key_index:
                detail = (f"{c['path']} shows the setting `{shown}`, but `{keys[-1]}` is not "
                          'a parameter in the code at this release')
                findings.append({'type': 'stale_setting', 'chunks': [c['id']], 'detail': detail})
                flag(c['id'], f"Stale docs: `{keys[-1]}` (shown as `{shown}`) is not a "
                              'parameter in the code at this release.')
                continue
            for par in match_parsets(keys, key_index, paths):
                definition = params[f'{par}.{keys[-1]}']['definition']
                if definition:
                    link(c['id'], definition, 'definition')

    # --- Consistency: pypeit imports in the docs' code examples ------------
    # (Not doc/dev/: developer how-tos show code that is yet to be written.)
    for c in chunks:
        if c['kind'] != 'doc' or historical(c['path']) or c['path'].startswith('doc/dev/'):
            continue
        for block in [code for lang, code in fenced_blocks(c['text']) if lang in PY_LANGS]:
            names = [m.group(1) for m in IMPORT_RE.finditer(block)]
            for m in FROM_IMPORT_RE.finditer(block):
                names += [f'{m.group(1)}.{n.strip().split()[0]}'
                          for n in (m.group(2) or m.group(3)).split(',') if n.strip()]
            for name in dict.fromkeys(names):
                if name in scan.symbols:
                    continue
                detail = (f"{c['path']} imports `{name}` in a code example, but it does not "
                          'exist in the code at this release')
                findings.append({'type': 'stale_import', 'chunks': [c['id']], 'target': name,
                                 'detail': detail})
                flag(c['id'], f'Stale docs: the code example imports `{name}`, which does '
                              'not exist in the code at this release.')

    # --- Consistency: documented vs code spectrograph defaults ------------
    by_name = {}
    for cls_name, info in scan.classes.items():
        if info['name']:
            by_name.setdefault(info['name'], cls_name)
    for c in chunks:
        if c['kind'] != 'override' or c['spectrograph'] not in by_name:
            continue
        cls_name = by_name[c['spectrograph']]
        info = scan.classes[cls_name]
        if 'default_pypeit_par' in info['methods']:
            link(c['id'], spans.find(info['path'], info['methods']['default_pypeit_par']['line']),
                 'definition')
        code = scan.effective_defaults(cls_name)
        dynamic = scan.dynamic_prefixes(cls_name)
        for path, doc_text in c.get('settings', {}).items():
            s = code.get(path)
            if s is None or not s['literal'] or same_value(doc_text, s['value']):
                continue
            if any(s['keys'][:len(d)] == d for d in dynamic):
                continue
            owner = scan.classes[s['cls']]
            cid = spans.find(owner['path'], s['line'])
            detail = (f"{c['spectrograph']} {path}: docs list {doc_text!r}, code sets "
                      f"{s['src']} ({owner['path']} L{s['line']})")
            findings.append({'type': 'override_mismatch', 'chunks': [c['id'], cid],
                             'detail': detail})
            flag(c['id'], 'Docs and code differ: ' + detail)
            flag(cid, 'Docs and code differ: ' + detail)

    # --- Spectrograph names -> their parameter methods ---------------------
    spectrographs = {}
    for name, cls_name in by_name.items():
        info = scan.classes[cls_name]
        entry = {'class': f"code:{info['module']}.{cls_name}"}
        for method in SPEC_METHODS:
            owner = scan.method_owner(cls_name, method)
            if owner:
                cid = f"code:{scan.classes[owner]['module']}.{owner}.{method}"
                if cid in code_ids:
                    entry[method] = cid
        spectrographs[name] = entry

    # --- Reverse links ------------------------------------------------------
    for a, targets in list(links.items()):
        for b, reason in targets.items():
            if by_id[a]['kind'] != 'code' and by_id[b]['kind'] == 'code':
                link(b, a, reason)

    # Multi-part chunks report the same finding once per part: merge them.
    unique = {}
    for f in findings:
        kept = unique.setdefault((f['type'], f['detail']), f)
        if kept is not f:
            kept['chunks'] += [c for c in f['chunks'] if c not in kept['chunks']]
    findings = list(unique.values())

    out_links = {}
    for a, targets in links.items():
        cap = MAX_CODE_TO_DOC if by_id[a]['kind'] == 'code' else MAX_DOC_TO_CODE
        ordered = sorted(targets.items(), key=lambda t: REASONS.index(t[1]))
        out_links[a] = [[b, r] for b, r in ordered[:cap]]
    return {'links': out_links, 'params': params, 'spectrographs': spectrographs}, findings
