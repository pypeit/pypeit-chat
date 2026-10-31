"""
Tests of ingest: RST parsing, parameters, code chunks, tagging, and the
docs-code cross-references and consistency checks (DESIGN 6, 12).
"""
import textwrap

from pypeit_chat.ingest import code, params, rst, xref
from pypeit_chat.ingest.rst import RstContext
from pypeit_chat.ingest.tags import Tagger


def by_id(built):
    return {c['id']: c for c in built[0]}


# --- RST ---------------------------------------------------------------------


def test_make_id_matches_docutils():
    assert rst.make_id('instr_par-keck_lris_red') == 'instr-par-keck-lris-red'
    assert rst.make_id('2. Inspect the outputs') == 'inspect-the-outputs'
    assert rst.make_id('Über Größe') == 'uber-groe'


def test_heading_levels_follow_order_of_appearance():
    lines = textwrap.dedent("""
        =====
        Title
        =====

        Alpha
        =====

        Alpha one
        ---------

        Beta
        ====
    """).split('\n')
    heads = rst.find_headings(lines)
    assert [h['raw'] for h in heads] == ['Title', 'Alpha', 'Alpha one', 'Beta']
    styles = []
    for h in heads:
        if h['style'] not in styles:
            styles.append(h['style'])
    assert [styles.index(h['style']) + 1 for h in heads] == [1, 2, 3, 2]


def test_includes_expanded_and_include_only_files_not_pages(built):
    chunks = by_id(built)
    run = chunks['doc:setup#run-setup:0']
    assert 'usage: pypeit_setup' in run['text']
    assert not any(c['path'].startswith(('doc/help/', 'doc/include/')) for c in built[0])


def test_anchor_from_label_else_heading(built):
    chunks = by_id(built)
    assert chunks['doc:setup#run-setup:0']['url'].endswith('/en/2.0.1/setup.html#run-setup')
    assert chunks['doc:skysub#global:0']['url'].endswith('/skysub.html#global')
    assert chunks['doc:skysub#global:0']['location'] == 'Sky Subtraction › Global'


def test_release_notes_link_to_whatsnew_and_are_down_weighted(built):
    c = by_id(built)['doc:releases/2.0.1#:0']
    assert '/whatsnew.html' in c['url']
    assert c['weight'] == 0.5


def test_roles_cleaned_to_text():
    ctx = RstContext()
    ctx.labels['setup'] = 'Setup'
    s = rst.inline('See :func:`~pypeit.core.skysub.global_skysub`, :ref:`setup` and '
                   ':class:`Spec <pypeit.specobj.SpecObj>` with ``x``.', ctx)
    assert s == 'See `global_skysub`, Setup and `Spec` with `x`.'


def test_code_refs_capture_roles_and_literals():
    roles, literals = rst.code_refs([':func:`~pypeit.core.skysub.global_skysub` uses '
                                     '``bspline_spacing`` and ``-s``'])
    assert roles == [{'role': 'func', 'target': 'pypeit.core.skysub.global_skysub',
                      'display': 'global_skysub'}]
    assert literals == ['bspline_spacing']


def test_split_never_inside_code():
    para = 'word ' * 50
    code = '```python\n' + '\n'.join(f'x{i} = {i}' for i in range(60)) + '\n```'
    pieces = rst.split_text(f'{para}\n\n{code}\n\n{para}', maxlen=300)
    assert all(len(p) <= 300 for p in pieces)
    for p in pieces:
        assert p.count('```') % 2 == 0


def test_code_blocks_become_fences(built):
    text = by_id(built)['doc:skysub#local:0']['text']
    assert '```python' in text and 'skysub.global_skysub(image)' in text


# --- Parameters ----------------------------------------------------------------


def test_parameter_paths(built):
    chunks = by_id(built)
    assert chunks['param:SkySubPar.bspline_spacing']['paths'] == [['reduce', 'skysub']]
    combine = chunks['param:ProcessImagesPar.combine']['paths']
    assert ['scienceframe', 'process'] in combine
    assert ['calibrations', 'arcframe', 'process'] in combine
    tell = chunks['param:TelluricPar.pix_shift_bounds']['paths']
    assert sorted(tell) == [['sensfunc', 'IR'], ['telluric']]


def test_every_parameter_chunk_names_its_parset(built):
    snr = [c for c in built[0] if c['kind'] == 'param' and c['param'] == 'snr_thresh']
    assert sorted(c['parclass'] for c in snr) == ['AlignPar', 'EdgeTracePar', 'FindObjPar']
    for c in snr:
        assert f"ParSet {c['parclass']}" in c['text']
        assert c['parclass'] in c['title']


def test_parameter_chunk_has_pypeit_block(built):
    c = by_id(built)['param:SkySubPar.bspline_spacing']
    assert '[reduce]\n    [[skysub]]\n        bspline_spacing = 0.6' in c['text']
    assert c['doc_default'] == '0.6'


def test_override_chunks(built):
    c = by_id(built)['override:keck_lris_red:0']
    assert c['spectrograph'] == 'keck_lris_red'
    assert c['url'].endswith('pypeit_par.html#instr-par-keck-lris-red')
    assert c['settings']['[reduce][findobj] snr_thresh'] == '5.0'
    assert c['weight'] == 0.6


def test_parse_ini_nesting():
    s = params.parse_ini(['[a]', '    x = 1', '    [[b]]', '        y = 2, 3,', '[c]', '  z=4'])
    assert s == {('a', 'x'): '1', ('a', 'b', 'y'): '2, 3,', ('c', 'z'): '4'}


def test_same_value_normalization():
    assert params.same_value('(1, 2, 3),', params.canonical([(1, 2, 3)]))
    assert params.same_value('None, 60.0,', params.canonical([None, 60]))
    assert params.same_value(':10,75:', ':10,75:')
    assert params.same_value('(-5.0, 5.0)', params.canonical((-5.0, 5.0)))
    assert params.same_value(None, '')
    assert params.same_value('5', 5.0)
    assert not params.same_value('4.0', 5.0)


# --- Code ----------------------------------------------------------------------


def test_code_line_ranges_include_decorators(built, fake_root):
    c = by_id(built)['code:pypeit.core.skysub.global_skysub']
    lines = (fake_root / c['path']).read_text().splitlines()
    assert lines[c['start'] - 1] == '@traced'
    assert lines[c['end'] - 1].strip() == 'return model'
    assert c['url'].endswith(f"pypeit/core/skysub.py#L{c['start']}-L{c['end']}")


def test_class_chunk_lists_methods_and_tests_skipped(built):
    chunks = by_id(built)
    assert '# Methods: __init__, global_skysub, find_objects' in \
        chunks['code:pypeit.find_objects.FindObjects']['text']
    assert not any(c['path'].startswith('pypeit/tests/') for c in built[0])
    assert chunks['code:pypeit.core.skysub']['codetype'] == 'module'


def test_tagger():
    t = Tagger(['keck_lris', 'keck_nirspec', 'shane_kast', 'gemini_gmos'])
    assert t.tags('keck_lris_red') == ['keck_lris']
    assert t.tags('my LRIS data') == ['keck_lris']
    assert t.tags('Keck/NIRSPEC and Shane-Kast') == ['keck_nirspec', 'shane_kast']
    assert t.tags('the overlords') == []


# --- Cross-references and consistency ---------------------------------------------


def test_code_defaults_parsed(built):
    p = built[1]['params']
    assert p['SkySubPar.bspline_spacing']['code_default'] == '0.6'
    assert p['FindObjPar.snr_thresh']['code_default'] == '10.0'
    assert p['AlignPar.snr_thresh']['code_default'] == '1.0'
    assert p['EdgeTracePar.snr_thresh']['code_default'] == '5.0'
    assert p['FindObjPar.snr_thresh']['definition'] == \
        'code:pypeit.par.pypeitpar.FindObjPar.__init__'


def test_parameter_uses_and_overrides(built):
    p = built[1]['params']['SkySubPar.bspline_spacing']
    assert p['uses'] == ['code:pypeit.find_objects.FindObjects.global_skysub']
    found = {(o['spectrograph'], o['method'], o['value']) for o in p['overrides']}
    assert ('KeckLRISSpectrograph', 'default_pypeit_par', '0.8') in found
    assert ('keck_lris_red', 'config_specific_par', '1.0') in found
    snr = built[1]['params']['FindObjPar.snr_thresh']
    assert [(o['spectrograph'], o['value']) for o in snr['overrides']] == [('keck_lris_red', '5.0')]
    assert built[1]['params']['AlignPar.snr_thresh']['overrides'] == []


def test_roles_scripts_and_spectrographs_linked(built):
    links = built[1]['links']
    assert ['code:pypeit.core.skysub.global_skysub', 'role'] in links['doc:skysub#global:0']
    assert ['code:pypeit.scripts.setup.Setup', 'script'] in links['doc:setup#run-setup:0']
    assert ['code:pypeit.spectrographs.keck_lris', 'spectrograph'] in \
        links['doc:tutorials/lris_howto#setup:0']
    # Reverse link, code -> docs.
    assert ['doc:skysub#global:0', 'role'] in links['code:pypeit.core.skysub.global_skysub']
    # Parameter -> definition first.
    assert links['param:FindObjPar.snr_thresh'][0] == \
        ['code:pypeit.par.pypeitpar.FindObjPar.__init__', 'definition']


def test_wrong_documented_default_flagged(built):
    findings = built[2]
    f = [x for x in findings if x['type'] == 'default_mismatch']
    assert len(f) == 1 and 'EdgeTracePar.snr_thresh' in f[0]['detail']
    assert by_id(built)['param:EdgeTracePar.snr_thresh']['flags']
    assert not by_id(built)['param:FindObjPar.snr_thresh'].get('flags')


def test_stale_reference_flagged(built):
    f = [x for x in built[2] if x['type'] == 'stale_reference']
    # Past release notes describe past code; in-development notes are checked.
    assert sorted(x['target'] for x in f) == ['pypeit.fluxcalibrate.apply_flux_calib',
                                              'pypeit.qa.gone_function']
    assert by_id(built)['doc:setup#run-setup:0']['flags']


def test_override_mismatch_follows_inheritance(built):
    f = [x for x in built[2] if x['type'] == 'override_mismatch']
    # sky_sigrej: docs 4.0, inherited code value 3.5.  bspline_spacing (0.8,
    # inherited) and snr_thresh (5.0) agree.
    assert len(f) == 1 and 'sky_sigrej' in f[0]['detail']
    assert 'code:pypeit.spectrographs.keck_lris.KeckLRISSpectrograph.default_pypeit_par' \
        in f[0]['chunks']


def test_match_parsets():
    key_index = {'snr_thresh': ['FindObjPar', 'AlignPar'], 'bspline_spacing': ['SkySubPar']}
    paths = {'FindObjPar': [['reduce', 'findobj']], 'AlignPar': [['calibrations', 'alignment']],
             'SkySubPar': [['reduce', 'skysub']]}
    assert xref.match_parsets(['reduce', 'findobj', 'snr_thresh'], key_index, paths) == \
        ['FindObjPar']
    assert xref.match_parsets(['findobj', 'snr_thresh'], key_index, paths) == ['FindObjPar']
    assert xref.match_parsets(['snr_thresh'], key_index, paths) == []      # ambiguous
    assert xref.match_parsets(['bspline_spacing'], key_index, paths) == ['SkySubPar']


def test_code_module_name():
    assert code.module_name('pypeit/core/skysub.py') == 'pypeit.core.skysub'
    assert code.module_name('pypeit/core/__init__.py') == 'pypeit.core'


def test_spectrograph_methods_follow_inheritance(built):
    spec = built[1]['spectrographs']['keck_lris_red']
    assert spec['default_pypeit_par'] == \
        'code:pypeit.spectrographs.keck_lris.KeckLRISRSpectrograph.default_pypeit_par'
    assert spec['config_specific_par'] == \
        'code:pypeit.spectrographs.keck_lris.KeckLRISRSpectrograph.config_specific_par'


def test_stale_docs_setting_flagged(built):
    f = [x for x in built[2] if x['type'] == 'stale_setting']
    assert [x['chunks'] for x in f] == [['doc:skysub#local:0']]
    assert 'old_spacing' in f[0]['detail']
    # The valid setting links the section to its ParSet definition.
    assert ['code:pypeit.par.pypeitpar.SkySubPar.__init__', 'definition'] in \
        built[1]['links']['doc:skysub#local:0']


def test_overrides_set_in_a_loop_are_not_compared(built):
    # shane_kast_blue's sky_sigrej (2.0 in the docs) is set by a loop over a
    # class attribute, after a literal 5.0: no mismatch may be reported.
    f = [x for x in built[2] if x['type'] == 'override_mismatch']
    assert not any('shane_kast_blue' in x['detail'] for x in f)


def test_stale_import_in_code_example_flagged(built):
    f = [x for x in built[2] if x['type'] == 'stale_import']
    assert [x['target'] for x in f] == ['pypeit.oldmodule.gone']
    assert f[0]['chunks'] == ['doc:skysub#local:0']


def test_fenced_blocks_pair_in_order():
    text = 'a\n```ini\n[x]\n```\nprose\n```python\nimport y\n```\n```\nz\n```'
    assert rst.fenced_blocks(text) == [('ini', '[x]'), ('python', 'import y'), ('', 'z')]
