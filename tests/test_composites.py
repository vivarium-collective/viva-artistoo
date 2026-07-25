"""Tests for the discoverable composite generators."""

import pytest
from process_bigraph import Composite, allocate_core, gather_emitter_results

from pbg_artistoo import ArtistooProcess
from pbg_artistoo.composites.cell_migration import (
    artistoo_cell_migration,
    artistoo_cell_sorting,
)
from pbg_artistoo.runtime import artistoo_available

requires_artistoo = pytest.mark.skipif(
    not artistoo_available(),
    reason="Node.js / Artistoo not installed",
)


def test_generators_registered():
    # cheap protection against forgetting the side-effect import
    from viva_superpowers.composite_generator import _REGISTRY

    for name in ("artistoo_cell_migration", "artistoo_cell_sorting"):
        matches = [eid for eid in _REGISTRY if eid.endswith("." + name)]
        assert matches, f"{name} missing from registry; have {list(_REGISTRY)[:5]}"


def test_migration_document_structure():
    doc = artistoo_cell_migration(n_cells=4, field_size=40, interval=1.0)
    assert doc["cpm"]["address"] == "local:ArtistooProcess"
    assert doc["cpm"]["config"]["field_width"] == 40
    assert doc["cpm"]["config"]["n_cells"] == 4
    # every input port wired to a setpoint store
    assert set(doc["cpm"]["inputs"]) == {
        "temperature", "target_volume", "target_perimeter", "motility",
    }
    assert "emitter" in doc and doc["emitter"]["address"] == "local:RAMEmitter"


def test_sorting_document_structure():
    doc = artistoo_cell_sorting(n_cells=8, field_size=50, adhesion_cell_cell=5.0)
    assert doc["cpm"]["config"]["adhesion_cell_cell"] == 5.0
    assert doc["cpm"]["config"]["lambda_act"] == 0.0  # non-motile


def _core():
    core = allocate_core()
    core.register_link("ArtistooProcess", ArtistooProcess)
    return core


@requires_artistoo
def test_migration_runs():
    core = _core()
    doc = artistoo_cell_migration(n_cells=4, field_size=45, target_volume=140,
                                  lambda_act=200, interval=1.0, seed=11)
    sim = Composite({"state": doc}, core=core)
    sim.run(15.0)
    r = sim.state["readouts"]
    assert r["cell_count"] == 4
    assert r["total_volume"] > 300          # cells expanded from seeds
    assert len(r["centroids"]) == 4         # dynamic-key snapshot propagated
    assert len(r["cell_volumes"]) == 4
    frames = list(gather_emitter_results(sim).values())[0]
    assert len(frames) == 16                # t=0..15 inclusive
    assert frames[-1]["total_volume"] > frames[0]["total_volume"]


@requires_artistoo
def test_sorting_runs():
    core = _core()
    doc = artistoo_cell_sorting(n_cells=9, field_size=50, target_volume=110,
                                adhesion_cell_cell=5.0, interval=1.0, seed=5)
    sim = Composite({"state": doc}, core=core)
    sim.run(10.0)
    r = sim.state["readouts"]
    assert r["cell_count"] == 9
    assert r["mean_connectedness"] > 0.0
