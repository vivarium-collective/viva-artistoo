"""Tests for ArtistooProcess.

Shape tests always run. Tests that actually drive the real Artistoo simulator
skip (rather than fail) when Node.js / Artistoo is not available, so CI without
the JS toolchain stays green.
"""

import pytest
from process_bigraph import allocate_core

from pbg_artistoo import ArtistooProcess
from pbg_artistoo.runtime import artistoo_available

requires_artistoo = pytest.mark.skipif(
    not artistoo_available(),
    reason="Node.js / Artistoo not installed",
)


def _proc(**cfg):
    core = allocate_core()
    core.register_link("ArtistooProcess", ArtistooProcess)
    return ArtistooProcess(cfg, core)


def test_ports_are_dicts():
    p = _proc()
    assert isinstance(p.inputs(), dict)
    assert isinstance(p.outputs(), dict)


def test_expected_ports():
    p = _proc()
    assert set(p.inputs()) == {
        "temperature",
        "target_volume",
        "target_perimeter",
        "motility",
    }
    outs = p.outputs()
    assert {"cell_count", "total_volume", "centroids", "cell_volumes"} <= set(outs)
    # per-cell snapshots must be overwrite (dynamic-key sensor exception)
    assert outs["centroids"].startswith("overwrite[")
    assert outs["cell_volumes"].startswith("overwrite[")
    # scalar aggregates must be plain (additive-delta) types
    assert outs["cell_count"] == "integer"
    assert outs["total_volume"] == "float"


def test_initial_state_seeds_setpoints():
    p = _proc(n_cells=3, target_volume=180.0, lambda_act=150.0)
    st = p.initial_state()
    assert st["temperature"] == 20.0
    assert st["target_volume"] == 180.0
    assert st["motility"] == 150.0
    assert st["cell_count"] == 3


@requires_artistoo
def test_update_drives_real_cpm():
    p = _proc(field_width=40, field_height=40, n_cells=3,
              target_volume=150.0, lambda_volume=50.0, lambda_act=200.0,
              seed_layout="grid")
    setpoints = {
        "temperature": 20.0,
        "target_volume": 150.0,
        "target_perimeter": 135.0,
        "motility": 200.0,
    }
    # accumulate several intervals; cells should grow toward target volume
    total = 0.0
    for _ in range(6):
        upd = p.update(setpoints, interval=3.0)
        total += upd["total_volume"]
    assert set(upd) == {
        "cell_count", "total_volume", "total_perimeter",
        "mean_connectedness", "centroids", "cell_volumes",
    }
    # 3 cells x ~150 sites, grown from 3 seed pixels -> big positive delta
    assert total > 300
    assert len(upd["centroids"]) == 3
    for vol in upd["cell_volumes"].values():
        assert vol > 50  # cells actually expanded
    p.close()


@requires_artistoo
def test_get_grid():
    p = _proc(field_width=30, field_height=30, n_cells=2, seed_layout="grid")
    p.update({"temperature": 20.0, "target_volume": 100.0,
              "target_perimeter": 90.0, "motility": 0.0}, interval=5.0)
    grid = p.get_grid()
    assert grid["field_size"] == [30, 30]
    assert len(grid["cells"]) > 0
    for x, y, cid, kind in grid["cells"][:5]:
        assert 0 <= x < 30 and 0 <= y < 30 and cid != 0 and kind >= 1
    p.close()
