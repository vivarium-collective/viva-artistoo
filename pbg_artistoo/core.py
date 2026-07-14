"""Workspace core builder.

The dashboard runs each composite in a subprocess that calls this
``build_core()`` to obtain a core with the workspace's own processes
registered. Editable installs are invisible to
``process_bigraph.allocate_core()``'s distribution-keyed auto-discovery, so we
register :class:`ArtistooProcess` explicitly here.
"""

from __future__ import annotations

from process_bigraph import allocate_core

from .processes import ArtistooProcess


def build_core(core=None):
    """Return a core with pbg-artistoo's processes registered."""
    if core is None:
        core = allocate_core()
    core.register_link("ArtistooProcess", ArtistooProcess)
    return core
