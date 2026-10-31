"""
Spectrograph tagging: which instrument families a chunk or question is about.
"""
import re
from pathlib import Path

#: Modules in ``pypeit/spectrographs`` that are helpers, not instruments.
SPEC_HELPERS = {'__init__', 'spectrograph', 'util', 'opticalmodel', 'slitmask',
                'ramp_spectrograph'}


class Tagger:
    """
    Tag text with the instrument families (spectrograph module names) it
    mentions.

    A family matches by its full name with any suffix (``keck_lris_red``), a
    unique short name of 4+ characters (``lris``), or telescope + instrument
    (``Keck/NIRSPEC``, ``Shane-Kast``).

    Parameters
    ----------
    families : list of str
        Family names, e.g. ``['keck_lris', 'shane_kast']``.
    """

    def __init__(self, families):
        self.families = sorted(families)
        shorts = {}
        for fam in self.families:
            if '_' in fam:
                shorts.setdefault(fam.split('_', 1)[1], []).append(fam)
        self.short = {s: f[0] for s, f in shorts.items() if len(f) == 1 and len(s) >= 4}
        self.full_re = {f: re.compile(rf'(?<![a-z0-9]){f}') for f in self.families}
        self.short_re = {s: re.compile(rf'(?<![a-z0-9_]){s}[0-9br]?(?![a-z0-9])')
                         for s in self.short}
        self.pair_re = {f: re.compile(r'(?<![a-z0-9])' + f.split('_', 1)[0] + r'[\s/_-]+'
                                      + f.split('_', 1)[1] + r'(?![a-z])')
                        for f in self.families if '_' in f}

    @classmethod
    def from_root(cls, root):
        """Build from the spectrograph modules under ``root/pypeit/spectrographs``."""
        spec = Path(root) / 'pypeit' / 'spectrographs'
        return cls([p.stem for p in spec.glob('*.py') if p.stem not in SPEC_HELPERS])

    def tags(self, *texts):
        """
        Instrument families mentioned in any of ``texts``.

        Returns
        -------
        list of str
            Sorted family names.
        """
        text = ' '.join(t for t in texts if t).lower()
        found = {f for f, r in self.full_re.items() if r.search(text)}
        found |= {self.short[s] for s, r in self.short_re.items() if r.search(text)}
        found |= {f for f, r in self.pair_re.items() if r.search(text)}
        return sorted(found)
