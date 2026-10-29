""" Size up the material a PypeIt chatbot could draw on.

Counts files, characters and approximate tokens (chars / 4) for the PypeIt
Sphinx docs (by directory), the Python source (split into docstrings and
code), and the development-suite PypeIt files.

Usage:
    conda run -n pypeit14 python pypeit_chat/survey_sources.py
"""
import ast
import os
from pathlib import Path

PYPEIT = Path('/Users/xavier/Projects/PypeIt/PypeIt')
DEV_SUITE = Path('/Users/xavier/Projects/PypeIt/PypeIt-development-suite')

# Rough characters-per-token for English prose and Python
CHARS_PER_TOKEN = 4.


def ntok(nchar):
    return nchar / CHARS_PER_TOKEN


def survey_docs():
    """ Characters of .rst per doc/ sub-directory (top-level pages as 'doc/') """
    doc = PYPEIT / 'doc'
    tally = {}
    for f in doc.rglob('*.rst'):
        rel = f.relative_to(doc)
        if rel.parts[0].startswith('_'):
            continue
        key = 'doc/' if len(rel.parts) == 1 else f'doc/{rel.parts[0]}/'
        # Single out the auto-generated parameter dump
        if f.name == 'pypeit_par.rst':
            key = 'doc/pypeit_par.rst'
        n, c = tally.get(key, (0, 0))
        tally[key] = (n+1, c+len(f.read_text(errors='ignore')))
    return tally


def survey_code():
    """ Characters of docstrings vs. everything else in pypeit/*.py """
    nfile, ntotal, ndoc = 0, 0, 0
    for f in (PYPEIT / 'pypeit').rglob('*.py'):
        src = f.read_text(errors='ignore')
        nfile += 1
        ntotal += len(src)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                ds = ast.get_docstring(node, clean=False)
                if ds:
                    ndoc += len(ds)
    return nfile, ntotal, ndoc


def survey_dev_suite():
    files = list(DEV_SUITE.rglob('*.pypeit'))
    return len(files), sum(os.path.getsize(f) for f in files)


def main():
    print(f'{"Source":32s} {"files":>6s} {"chars":>11s} {"~tokens":>10s}')
    total = 0
    for key, (n, c) in sorted(survey_docs().items(), key=lambda x: -x[1][1]):
        print(f'{key:32s} {n:6d} {c:11,d} {ntok(c):10,.0f}')
        total += c
    print(f'{"doc/ total":32s} {"":6s} {total:11,d} {ntok(total):10,.0f}')

    nfile, ntotal, ndoc = survey_code()
    print(f'{"pypeit/*.py (all)":32s} {nfile:6d} {ntotal:11,d} {ntok(ntotal):10,.0f}')
    print(f'{"pypeit/*.py (docstrings only)":32s} {"":6s} {ndoc:11,d} {ntok(ndoc):10,.0f}')

    n, c = survey_dev_suite()
    print(f'{"dev-suite *.pypeit":32s} {n:6d} {c:11,d} {ntok(c):10,.0f}')


if __name__ == '__main__':
    main()
