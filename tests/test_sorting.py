"""Tests for the Glazier & Graner (1993) multi-type CPMSortingProcess."""

import pytest
from process_bigraph import Composite, gather_emitter_results

from pbg_artistoo import CPMSortingProcess
from pbg_artistoo.core import build_core
from pbg_artistoo.processes import aggregate_seeds
from pbg_artistoo.composites.glazier_graner import (
    glazier_graner_checkerboard,
    glazier_graner_cell_sorting,
    glazier_graner_high_temperature,
)
from pbg_artistoo.runtime import artistoo_available

requires_artistoo = pytest.mark.skipif(
    not artistoo_available(),
    reason="Node.js / Artistoo not installed",
)


def _proc(**cfg):
    core = build_core()
    return CPMSortingProcess(cfg, core)


def test_ports():
    p = _proc()
    # light and dark are driven by SEPARATE setpoints (genuine multi-type)
    assert set(p.inputs()) == {
        "temperature", "light_target_volume", "dark_target_volume",
    }
    outs = p.outputs()
    assert outs["heterotypic_fraction"].startswith("overwrite[")
    assert outs["cell_types"].startswith("overwrite[map[")
    assert outs["light_count"] == "integer"


def test_surface_tensions_match_paper():
    # cell-sorting energies: J_ll=14, J_dd=2, J_ld=11, J_lM=J_dM=16
    p = _proc(J_ll=14, J_dd=2, J_ld=11, J_lM=16, J_dM=16)
    g = p.surface_tensions()
    assert g["gamma_ld"] == 3.0     # 11 - (2+14)/2
    assert g["gamma_lM"] == 9.0     # 16 - 14/2
    assert g["gamma_dM"] == 15.0    # 16 - 2/2


def test_aggregate_seeds_counts():
    seeds, n_light, n_dark = aggregate_seeds(80, 80, 50, 3, 0.5, seed=1)
    assert len(seeds) == 50
    assert n_light + n_dark == 50
    for kind, x, y, half in seeds:
        assert kind in (1, 2) and half == 3
        assert 3 <= x < 77 and 3 <= y < 77
    # deterministic in seed
    seeds2, nl2, nd2 = aggregate_seeds(80, 80, 50, 3, 0.5, seed=1)
    assert (n_light, n_dark) == (nl2, nd2)


def test_document_structures():
    for gen in (glazier_graner_checkerboard, glazier_graner_cell_sorting,
                glazier_graner_high_temperature):
        doc = gen(n_cells=40, field_size=60, interval=2.0)
        assert doc["cpm"]["address"] == "local:CPMSortingProcess"
        assert "heterotypic_fraction" in doc["cpm"]["outputs"]
    # checkerboard has negative gamma_ld; sorting positive
    cb = glazier_graner_checkerboard()["cpm"]["config"]
    assert cb["J_ld"] - (cb["J_dd"] + cb["J_ll"]) / 2 == -3.0
    cs = glazier_graner_cell_sorting()["cpm"]["config"]
    assert cs["J_ld"] - (cs["J_dd"] + cs["J_ll"]) / 2 == 3.0
    ht = glazier_graner_high_temperature()["cpm"]["config"]
    assert ht["temperature"] == 40.0


def test_generators_registered():
    from pbg_superpowers.composite_generator import _REGISTRY

    for name in ("glazier_graner_checkerboard", "glazier_graner_cell_sorting",
                 "glazier_graner_high_temperature"):
        assert any(eid.endswith("." + name) for eid in _REGISTRY), name


@requires_artistoo
def test_checkerboard_raises_heterotypic_fraction():
    core = build_core()
    # equal cell sizes here isolates the ADHESION effect (negative gamma_ld);
    # per-type size divergence is covered separately above.
    doc = glazier_graner_checkerboard(n_cells=80, field_size=75,
                                      light_volume=40, dark_volume=40,
                                      interval=2.0, seed=3)
    sim = Composite({"state": doc}, core=core)
    sim.run(2.0)
    hf0 = sim.state["readouts"]["heterotypic_fraction"]
    sim.run(30.0)
    hf1 = sim.state["readouts"]["heterotypic_fraction"]
    # negative gamma_ld -> unlike contact increases
    assert hf1 > hf0
    assert 0.0 <= hf1 <= 1.0
    proc = sim.state["cpm"]["instance"]
    proc.close()


@requires_artistoo
def test_per_type_setpoints_drive_distinct_sizes():
    # light and dark given very different target areas via separate ports
    core = build_core()
    doc = glazier_graner_checkerboard(n_cells=80, field_size=75,
                                      light_volume=60, dark_volume=24,
                                      interval=4.0, seed=3)
    sim = Composite({"state": doc}, core=core)
    sim.run(60.0)
    r = sim.state["readouts"]
    vols, types = r["cell_volumes"], r["cell_types"]
    light = [vols[c] for c in vols if types.get(c) == 1]
    dark = [vols[c] for c in vols if types.get(c) == 2]
    assert light and dark
    mean_light = sum(light) / len(light)
    mean_dark = sum(dark) / len(dark)
    # the two types settle at clearly different areas — the whole point
    assert mean_light > 1.7 * mean_dark
    proc = sim.state["cpm"]["instance"]
    proc.close()


@requires_artistoo
def test_cell_sorting_lowers_heterotypic_fraction():
    core = build_core()
    doc = glazier_graner_cell_sorting(n_cells=100, field_size=80, interval=4.0, seed=1)
    sim = Composite({"state": doc}, core=core)
    sim.run(4.0)
    hf0 = sim.state["readouts"]["heterotypic_fraction"]
    sim.run(80.0)
    r = sim.state["readouts"]
    hf1 = r["heterotypic_fraction"]
    # positive gamma_ld -> cells sort, unlike contact decreases
    assert hf1 < hf0
    # per-cell type snapshot propagated for the full dynamic cell set
    assert len(r["cell_types"]) == r["light_count"] + r["dark_count"]
    assert set(r["cell_types"].values()) <= {1, 2}
    frames = list(gather_emitter_results(sim).values())[0]
    assert len(frames) > 1
    proc = sim.state["cpm"]["instance"]
    proc.close()
