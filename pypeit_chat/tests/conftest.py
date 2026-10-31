"""
Shared fixtures: a small fake PypeIt checkout and an index built from it.

The fake checkout has the features the tests need: includes, labels,
Python roles (one stale), a script help section, ParSet tables reached by
several paths, three ``snr_thresh`` ParSets (one with a deliberately wrong
documented default), spectrograph overrides with inheritance and a
``config_specific_par``, and a decorated function.
"""
import textwrap
from pathlib import Path

import pytest

from pypeit_chat.config import Settings
from pypeit_chat.index import Index
from pypeit_chat.ingest.build import make_chunks, write_index
from pypeit_chat.llm import LLMClient
from pypeit_chat.tests import mock_llm

TAG = '2.0.1'
SHA = 'abc123def456'


def table(rows):
    """Render an RST simple table (Key, Type, Options, Default, Description)."""
    header = ['Key', 'Type', 'Options', 'Default', 'Description']
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(5)]
    border = '  '.join('=' * w for w in widths)

    def line(r):
        return '  '.join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    return '\n'.join([border, line(header), border] + [line(r) for r in rows] + [border])


def parset(name, label, rows):
    return f"""
.. _{label}:

{name} Keywords
{'-' * (len(name) + 9)}

Class Instantiation: :class:`~pypeit.par.pypeitpar.{name}`

{table(rows)}
"""


def ref(par):
    return f':class:`~pypeit.par.pypeitpar.{par}`'


PYPEIT_PAR = f"""
.. _parameters:

=====================
User-level Parameters
=====================

PypeIt uses parameter sets.

Current PypeItPar Parameter Hierarchy
=====================================

Text.
""" + parset('PypeItPar', 'pypeitpar', [
    ['``rdx``', ref('ReduxPar'), '..', '`ReduxPar Keywords`_', 'Reduction.'],
    ['``calibrations``', ref('CalibrationsPar'), '..', '`CalibrationsPar Keywords`_', 'Calib.'],
    ['``scienceframe``', ref('FrameGroupPar'), '..', '`FrameGroupPar Keywords`_', 'Science.'],
    ['``reduce``', ref('ReducePar'), '..', '`ReducePar Keywords`_', 'Reduce.'],
    ['``sensfunc``', ref('SensFuncPar'), '..', '`SensFuncPar Keywords`_', 'Sensfunc.'],
    ['``telluric``', ref('TelluricPar'), '..', '`TelluricPar Keywords`_', 'Telluric.'],
]) + parset('ReduxPar', 'reduxpar', [
    ['``spectrograph``', 'str', '..', '..', 'Spectrograph that provided the data.'],
    ['``detnum``', 'int, list', '..', '..', 'Restrict reduction to a list of detector indices.'],
]) + parset('CalibrationsPar', 'calibrationspar', [
    ['``alignment``', ref('AlignPar'), '..', '`AlignPar Keywords`_', 'Alignment.'],
    ['``arcframe``', ref('FrameGroupPar'), '..', '`FrameGroupPar Keywords`_', 'Arcs.'],
    ['``slitedges``', ref('EdgeTracePar'), '..', '`EdgeTracePar Keywords`_', 'Edges.'],
]) + parset('FrameGroupPar', 'framegrouppar', [
    ['``exprng``', 'list', '..', '``None, None``', 'Exposure time range.'],
    ['``process``', ref('ProcessImagesPar'), '..', '`ProcessImagesPar Keywords`_', 'Proc.'],
]) + parset('ProcessImagesPar', 'processimagespar', [
    ['``combine``', 'str', '``median``, ``mean``', '``mean``', 'Method used to combine frames.'],
]) + parset('ReducePar', 'reducepar', [
    ['``findobj``', ref('FindObjPar'), '..', '`FindObjPar Keywords`_', 'Object finding.'],
    ['``skysub``', ref('SkySubPar'), '..', '`SkySubPar Keywords`_', 'Sky subtraction.'],
]) + parset('FindObjPar', 'findobjpar', [
    ['``snr_thresh``', 'int, float', '..', '10.0',
     'S/N threshold for object finding in wavelength direction smashed image.'],
    ['``trace_npoly``', 'int', '..', '5', 'Order of the object trace polynomial.'],
]) + parset('SkySubPar', 'skysubpar', [
    ['``bspline_spacing``', 'int, float', '..', '0.6',
     'Break-point spacing for the bspline sky subtraction fits.'],
    ['``sky_sigrej``', 'float', '..', '3.0', 'Rejection parameter for local sky subtraction.'],
]) + parset('AlignPar', 'alignpar', [
    ['``snr_thresh``', 'int, float', '..', '1.0', 'S/N ratio threshold for alignment traces.'],
]) + parset('EdgeTracePar', 'edgetracepar', [
    ['``snr_thresh``', 'int, float', '..', '4.0', 'S/N threshold for slit edges.'],
]) + parset('SensFuncPar', 'sensfuncpar', [
    ['``IR``', ref('TelluricPar'), '..', '`TelluricPar Keywords`_', 'IR telluric.'],
]) + parset('TelluricPar', 'telluricpar', [
    ['``pix_shift_bounds``', 'tuple', '..', '(-5.0, 5.0)', 'Pixel shift bounds.'],
]) + """
.. _instr_par:

Instrument-Specific Default Configuration
=========================================

The following lists the changes for each instrument.

.. _instr_par-shane_kast_blue:

Shane Kast blue (``shane_kast_blue``)
-------------------------------------
Alterations to the default parameters are:

.. code-block:: ini

  [reduce]
      [[skysub]]
          sky_sigrej = 2.0

.. _instr_par-keck_lris_red:

KECK LRISr (``keck_lris_red``)
------------------------------
Alterations to the default parameters are:

.. code-block:: ini

  [rdx]
      spectrograph = keck_lris_red
  [reduce]
      [[findobj]]
          snr_thresh = 5.0
      [[skysub]]
          bspline_spacing = 0.8
          sky_sigrej = 4.0
"""

DOCS = {
    'index.rst': """
        .. include:: include/links.rst

        ======
        PypeIt
        ======

        PypeIt is a Python package for semi-automated reduction of
        astronomical spectroscopic data.

        .. toctree::

           setup
           skysub
    """,
    'include/links.rst': """
        .. _astropy: https://www.astropy.org
    """,
    'help/pypeit_setup.rst': """
        .. code-block:: console

            $ pypeit_setup -h
            usage: pypeit_setup [-h] [-s SPECTROGRAPH] [-r ROOT]

            Parse data files to construct a pypeit file
    """,
    'setup.rst': """
        .. _setup:

        =====
        Setup
        =====

        Overview
        ========

        Run ``pypeit_setup`` to make a PypeIt file.  The script is
        implemented by :class:`~pypeit.scripts.setup.Setup`.

        .. _run-setup:

        Run pypeit_setup
        ================

        The usage is:

        .. include:: help/pypeit_setup.rst

        Stale reference: :func:`pypeit.fluxcalibrate.apply_flux_calib`.
    """,
    'skysub.rst': """
        ===============
        Sky Subtraction
        ===============

        Global
        ======

        The global sky model is fit by :func:`~pypeit.core.skysub.global_skysub`
        with break-points every ``bspline_spacing`` pixels; see :ref:`setup`.

        Local
        -----

        Local sky subtraction refines the model near each object.  Set:

        .. code-block:: ini

            [reduce]
                [[skysub]]
                    bspline_spacing = 0.8
                    old_spacing = 2

        Not a PypeIt file:

        .. code-block:: ini

            [pypi]
                username = me

        .. code-block:: python

            from pypeit.core import skysub
            from pypeit.core.skysub import global_skysub, traced
            from pypeit.oldmodule import gone
            import pypeit.find_objects
            skysub.global_skysub(image)
    """,
    'tutorials/lris_howto.rst': """
        ================
        Keck-LRIS HOWTO
        ================

        Setup
        =====

        For Keck/LRIS red data run pypeit_setup with ``-s keck_lris_red``.
    """,
    'releases/2.0.1.rst': """
        Version 2.0.1
        =============

        - Fixed sky subtraction bugs in :func:`pypeit.old.removed_function`.
    """,
    'releases/2.1.0dev.rst': """
        Version 2.1.0dev
        ================

        - Tests for :func:`pypeit.qa.gone_function`.
    """,
    'pypeit_par.rst': PYPEIT_PAR,
}

CODE = {
    'pypeit/__init__.py': '"""PypeIt package."""\n',
    'pypeit/par/__init__.py': '',
    'pypeit/par/pypeitpar.py': '''
        """Parameter sets."""
        from collections import OrderedDict


        class ParSet:
            pass


        class FindObjPar(ParSet):
            """Object finding parameters."""
            def __init__(self, snr_thresh=None, trace_npoly=None):
                defaults = OrderedDict.fromkeys(['snr_thresh'])
                options = OrderedDict()
                dtypes = OrderedDict()
                defaults['snr_thresh'] = 10.0
                dtypes['snr_thresh'] = [int, float]
                defaults['trace_npoly'] = 5
                dtypes['trace_npoly'] = int


        class AlignPar(ParSet):
            """Alignment parameters."""
            def __init__(self, snr_thresh=None):
                defaults = OrderedDict()
                dtypes = OrderedDict()
                defaults['snr_thresh'] = 1.0
                dtypes['snr_thresh'] = [int, float]


        class EdgeTracePar(ParSet):
            """Edge tracing parameters."""
            def __init__(self, snr_thresh=None):
                defaults = OrderedDict()
                dtypes = OrderedDict()
                defaults['snr_thresh'] = 5.0
                dtypes['snr_thresh'] = [int, float]


        class SkySubPar(ParSet):
            """Sky subtraction parameters."""
            def __init__(self, bspline_spacing=None, sky_sigrej=None):
                defaults = OrderedDict()
                dtypes = OrderedDict()
                defaults['bspline_spacing'] = 0.6
                dtypes['bspline_spacing'] = [int, float]
                defaults['sky_sigrej'] = 3.0
                dtypes['sky_sigrej'] = float


        class ProcessImagesPar(ParSet):
            """Image processing parameters."""
            def __init__(self, combine=None):
                defaults = OrderedDict()
                options = OrderedDict()
                defaults['combine'] = 'mean'
                options['combine'] = ['median', 'mean']


        class TelluricPar(ParSet):
            """Telluric parameters."""
            def __init__(self, pix_shift_bounds=None):
                defaults = OrderedDict()
                defaults['pix_shift_bounds'] = (-5.0,5.0)


        class FrameGroupPar(ParSet):
            """Frame group."""
            def __init__(self, exprng=None):
                defaults = OrderedDict()
                defaults['exprng'] = [None, None]


        class ReduxPar(ParSet):
            """Reduction."""
            def __init__(self, spectrograph=None, detnum=None):
                defaults = OrderedDict()
                defaults['spectrograph'] = None
                defaults['detnum'] = None
    ''',
    'pypeit/core/__init__.py': '',
    'pypeit/core/skysub.py': '''
        """Sky subtraction routines."""
        import functools


        def traced(func):
            return func


        @traced
        def global_skysub(image, bsp=0.6):
            """
            Fit the global sky with a bspline.

            Parameters
            ----------
            image : numpy.ndarray
                The image.
            """
            model = image * 0
            model += bsp
            return model
    ''',
    'pypeit/find_objects.py': '''
        """Object finding."""
        from pypeit.core import skysub


        class FindObjects:
            """Find objects and subtract the sky."""

            def __init__(self, par):
                self.par = par

            def global_skysub(self, image):
                """Run the global sky subtraction."""
                spacing = self.par['reduce']['skysub']['bspline_spacing']
                return skysub.global_skysub(image, bsp=spacing)

            def find_objects(self, image):
                """Find objects above the S/N threshold."""
                return self.par['reduce']['findobj']['snr_thresh']
    ''',
    'pypeit/spectrographs/__init__.py': '',
    'pypeit/spectrographs/spectrograph.py': '''
        """Spectrograph base class."""


        class Spectrograph:
            """Base class."""
            name = None

            @classmethod
            def default_pypeit_par(cls):
                """Default parameters."""
                return {}

            def config_specific_par(self, scifile, inp_par=None):
                """Setup-dependent parameters."""
                return inp_par
    ''',
    'pypeit/spectrographs/keck_lris.py': '''
        """Keck LRIS."""
        from pypeit.spectrographs import spectrograph


        class KeckLRISSpectrograph(spectrograph.Spectrograph):
            """Keck LRIS base."""

            @classmethod
            def default_pypeit_par(cls):
                """LRIS defaults."""
                par = super().default_pypeit_par()
                par['reduce']['skysub']['bspline_spacing'] = 0.8
                par['reduce']['skysub']['sky_sigrej'] = 3.5
                return par


        class KeckLRISRSpectrograph(KeckLRISSpectrograph):
            """Keck LRIS red."""
            name = 'keck_lris_red'

            @classmethod
            def default_pypeit_par(cls):
                """LRIS red defaults."""
                par = super().default_pypeit_par()
                par['reduce']['findobj']['snr_thresh'] = 5.0
                return par

            def config_specific_par(self, scifile, inp_par=None):
                """Setup-dependent LRIS red parameters."""
                par = self.default_pypeit_par() if inp_par is None else inp_par
                par['reduce']['skysub']['bspline_spacing'] = 1.0
                return par
    ''',
    'pypeit/spectrographs/shane_kast.py': '''
        """Shane Kast, with per-arm overrides applied in a loop."""
        from pypeit.spectrographs import spectrograph


        class ShaneKastSpectrograph(spectrograph.Spectrograph):
            """Kast base."""
            _skysub_overrides = {}

            @classmethod
            def default_pypeit_par(cls):
                """Kast defaults."""
                par = super().default_pypeit_par()
                par['reduce']['skysub']['sky_sigrej'] = 5.0
                for k, v in cls._skysub_overrides.items():
                    par['reduce']['skysub'][k] = v
                return par


        class ShaneKastBlueSpectrograph(ShaneKastSpectrograph):
            """Kast blue."""
            name = 'shane_kast_blue'
            _skysub_overrides = dict(sky_sigrej=2.0)
    ''',
    'pypeit/scripts/__init__.py': '',
    'pypeit/scripts/setup.py': '''
        """The pypeit_setup script."""


        class Setup:
            """Make a PypeIt file."""

            @classmethod
            def entry_point(cls):
                """Run it."""
                return 0
    ''',
    'pypeit/tests/test_skip.py': 'def test_x():\n    pass\n',
}

PYPROJECT = """
[project]
name = "pypeit"

[project.scripts]
pypeit_setup = "pypeit.scripts.setup:Setup.entry_point"
"""


def make_fake_pypeit(root):
    """Write the fake checkout under ``root`` and return it."""
    root = Path(root)
    for rel, text in DOCS.items():
        p = root / 'doc' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip('\n') if rel != 'pypeit_par.rst' else text)
    for rel, text in CODE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip('\n'))
    (root / 'pyproject.toml').write_text(PYPROJECT)
    return root


@pytest.fixture(scope='session')
def fake_root(tmp_path_factory):
    """A fake PypeIt checkout."""
    return make_fake_pypeit(tmp_path_factory.mktemp('PypeIt'))


@pytest.fixture(scope='session')
def built(fake_root):
    """(chunks, xrefs, findings, tagger) for the fake checkout."""
    return make_chunks(fake_root, TAG, SHA, 'https://pypeit.readthedocs.io/en')


def test_settings(tmp_path, **changes):
    """Settings for tests: mock model, files under ``tmp_path``, no .env."""
    values = dict(chat_model='mock', index_dir=tmp_path / 'index',
                  db_path=tmp_path / 'db.sqlite3', corpus_dir=tmp_path / 'nocorpus',
                  paused_file=tmp_path / 'PAUSED', hash_salt='salt')
    values.update(changes)
    return Settings.load(env={}, dotenv=tmp_path / 'no.env', **values)


# Not a test: keep pytest from collecting the helper above.
test_settings.__test__ = False


@pytest.fixture(scope='session')
def index_dir(built, tmp_path_factory):
    """An index of the fake checkout with mock embeddings."""
    chunks, xrefs, findings, tagger = built
    out = tmp_path_factory.mktemp('index')
    settings = test_settings(out)
    client = LLMClient(settings, transport=mock_llm.transport())
    from pypeit_chat.index import search_text
    vectors = client.embed([search_text(c) for c in chunks])
    write_index(out, chunks, vectors, xrefs, findings,
                {'tag': TAG, 'sha': SHA, 'has_embeddings': True,
                 'embed_model': 'nomic-embed-text', 'instruments': tagger.families})
    return out


@pytest.fixture(scope='session')
def index(index_dir):
    """The loaded index of the fake checkout."""
    return Index(index_dir)
