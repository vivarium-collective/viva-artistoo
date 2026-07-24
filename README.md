# pbg-artistoo

<!-- BEGIN dashboard -->
> ## 📊 [**Live dashboard →**](https://vivarium-collective.github.io/pbg-artistoo/dashboard/)
> Browse every investigation & study interactively, or read the [published investigation reports](https://vivarium-collective.github.io/pbg-artistoo/). Auto-published from `main` on every merge.
<!-- END dashboard -->

A [process-bigraph](https://github.com/vivarium-collective/process-bigraph)
wrapper for **[Artistoo](https://github.com/ingewortel/artistoo)** — the
JavaScript library for **Cellular Potts Model (CPM)** simulations.

This wrapper drives the **real Artistoo simulator**. Because Artistoo is a
JavaScript library, `pbg-artistoo` runs it inside a persistent **Node.js
subprocess** and bridges to it over a newline-delimited JSON protocol. Every
`update()` continues the *same* Monte-Carlo trajectory — nothing here
reimplements the Potts Hamiltonian; the genuine library computes it.

```
┌────────────────────┐  setpoints (stdin JSON)   ┌─────────────────────────┐
│  ArtistooProcess   │ ────────────────────────► │  node artistoo_bridge   │
│  (process-bigraph) │                           │  real Artistoo CPM      │
│                    │ ◄──────────────────────── │  (monteCarloStep × N)    │
└────────────────────┘   cell stats (stdout JSON)└─────────────────────────┘
```

## What it wraps

Artistoo's Cellular Potts Model: cells are sets of lattice sites that evolve by
Metropolis dynamics under an energy (Hamiltonian) built from constraints. The
wrapper exposes the classic **Act model** (adhesion + volume + perimeter +
activity) so cells adhere, hold a target size, and actively migrate.

| Artistoo API | Wrapped as |
|---|---|
| `CPM.CPM([w,h], {T, J, torus, seed})` | `config`: `field_size`, `temperature`, `seed`, `torus`, `adhesion_*` |
| `Adhesion({J})` | `adhesion_bg_cell`, `adhesion_cell_cell` |
| `VolumeConstraint({V, LAMBDA_V})` | `target_volume` (input), `lambda_volume` |
| `PerimeterConstraint({P, LAMBDA_P})` | `target_perimeter` (input), `lambda_perimeter` |
| `ActivityConstraint({MAX_ACT, LAMBDA_ACT})` | `max_act`, `motility` (input) |
| `GridManipulator.seedCell / seedCellAt` | `n_cells`, `seed_layout` |
| `C.monteCarloStep()` | one unit of `interval` |
| `CentroidsWithTorusCorrection`, `PixelsByCell`, `Connectedness`, `BorderPixelsByCell` | output ports |

## Installation

`pbg-artistoo` needs **Node.js** (v18+) available on `PATH`. Artistoo itself is
installed automatically on first run (via `npm install` into the bridge dir, or
a writable cache dir), so a plain `pip install` works — but you can also
pre-install it (see below) to avoid the first-run delay.

```bash
# From PyPI (once published):
pip install pbg-artistoo
# or with uv:
uv pip install pbg-artistoo

# For development (editable):
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Optional: pre-install the Node/Artistoo dependency
cd pbg_artistoo/node_bridge && npm install
```

> Once installed, the process registers automatically via
> `bigraph_schema.package.discover` — no manual `register_link()` calls are
> needed. (Use a regular `pip install .`, not `-e`, for the discovery path to
> see the package; hatchling's editable install omits the `top_level.txt` that
> distribution-keyed discovery relies on.)

## Quick start

```python
from process_bigraph import Composite, allocate_core, gather_emitter_results
from pbg_artistoo.composites.cell_migration import artistoo_cell_migration

core = allocate_core()
doc = artistoo_cell_migration(n_cells=6, field_size=60, target_volume=200,
                              lambda_act=200, interval=1.0, seed=42)
sim = Composite({"state": doc}, core=core)
sim.run(50.0)               # 50 Monte-Carlo steps of the real CPM

readouts = sim.state["readouts"]
print(readouts["cell_count"], readouts["total_volume"])
print(readouts["centroids"])       # {cell_id: [x, y]}

results = gather_emitter_results(sim)   # full time series
```

Or drive the `Process` directly:

```python
from pbg_artistoo import ArtistooProcess
core = allocate_core()
core.register_link("ArtistooProcess", ArtistooProcess)
proc = ArtistooProcess({"n_cells": 3, "field_size": 40, "lambda_act": 200}, core)
delta = proc.update({"temperature": 20.0, "target_volume": 150.0,
                     "target_perimeter": 135.0, "motility": 200.0}, interval=5.0)
print(delta["total_volume"], delta["centroids"])
proc.close()
```

## API reference

### `ArtistooProcess` — inputs (setpoints a sibling process may write)

| Port | Type | Meaning |
|---|---|---|
| `temperature` | `float` | Metropolis temperature `T` |
| `target_volume` | `float` | Preferred cell volume `V` |
| `target_perimeter` | `float` | Preferred perimeter `P` |
| `motility` | `float` | Activity strength `LAMBDA_ACT` |

### `ArtistooProcess` — outputs

| Port | Type | Semantics |
|---|---|---|
| `cell_count` | `integer` | **delta** — net births/deaths (composes additively) |
| `total_volume` | `float` | **delta** — change in occupied lattice sites |
| `total_perimeter` | `float` | **delta** — change in total border pixels |
| `mean_connectedness` | `float` | **delta** — change in mean connected fraction |
| `centroids` | `overwrite[map[string,list[float]]]` | absolute per-cell `[x, y]` (sensor snapshot) |
| `cell_volumes` | `overwrite[map[string,float]]` | absolute per-cell volume (sensor snapshot) |

### Port design

Scalar aggregates are emitted as **additive deltas** so a sibling process (a
growth model driving `target_volume`, a division process changing
`cell_count`) composes correctly — writing `+3` and `−1` to `cell_count` nets
to `+2`, the whole point of process-bigraph.

Per-cell `centroids` / `cell_volumes` are genuine **sensor snapshots**: the CPM
is their sole author and the cell set is *dynamic* (cells appear and disappear
at runtime). A plain `map[string,float]` only propagates keys that already
exist in the store, so newborn cells would silently vanish — hence
`overwrite[map[...]]`, the sensor exception. This is the only place `overwrite`
is used.

## `CPMSortingProcess` — two-type differential adhesion

A second process reproduces the **Glazier & Graner (1993)** extended-Potts
model: a mixed aggregate of *light* (kind 1) and *dark* (kind 2) cells in a
*medium* (background), with a full cell-type adhesion matrix `J` and an area
constraint. Behaviour is set entirely by the relative surface tensions

```
γ_ld = J_ld − (J_dd + J_ll)/2      γ_lM = J_lM − J_ll/2      γ_dM = J_dM − J_dd/2
```

There is **no motility** — fluctuations come from the finite-temperature
Metropolis dynamics, exactly as in the paper.

| Port (direction) | Type | Meaning |
|---|---|---|
| `temperature` (in) | `float` | Metropolis `T` — the live knob for the mixing transition |
| `target_volume` (in) | `float` | preferred cell area (paper: ~40) |
| `light_count`, `dark_count`, `cell_count` (out) | `integer` | per-type count deltas |
| `heterotypic_fraction` (out) | `overwrite[float]` | fraction of cell–cell boundary between UNLIKE types — the paper's **sorting order parameter** |
| `total_boundary`, `mean_connectedness` (out) | `overwrite[float]` | boundary length; mean connected fraction |
| `centroids`, `cell_types`, `cell_volumes` (out) | `overwrite[map[...]]` | per-cell absolute snapshots |

Config carries the adhesion matrix as scalars: `J_ll`, `J_dd`, `J_ld`, `J_lM`,
`J_dM`, plus `n_cells`, `dark_fraction`, `field_width/height`, `target_volume`,
`lambda_volume`, `temperature`, `seed`.

## Composite generators

Discoverable generators (visible in the vivarium-workbench Composites tab):

- **`artistoo_cell_migration`** — migrating Act-model cells (adhesion + volume
  + perimeter + activity).
- **`artistoo_cell_sorting`** — non-motile adhesive cells relaxing toward a
  compact aggregate.
- **`glazier_graner_checkerboard`** — Fig. 7: negative γ_ld (=−3) → unlike
  cells intercalate (heterotypic fraction rises).
- **`glazier_graner_cell_sorting`** — Fig. 12: Technau–Holstein hydra energies
  (γ_ld=+3, γ_dM=15) → dark cells sort to the interior (heterotypic fraction
  falls).
- **`glazier_graner_high_temperature`** — Fig. 9 / Table II: the sorting
  energies at T=40 → thermal mixing / crumpling.

## Reproducing Glazier & Graner (1993)

The demo report reproduces three simulations from the founding CPM paper —
*J. A. Glazier and F. Graner, "Simulation of the differential adhesion driven
rearrangement of biological cells", Phys. Rev. E **47**, 2128 (1993)* — using
the exact published J-matrix values:

| Regime (figure) | J_ll, J_dd, J_ld, J_lM=J_dM | γ_ld | T | Behaviour |
|---|---|---|---|---|
| Checkerboard (Fig. 7) | 10, 8, 6, 12 | **−3** | 10 | intercalation, hetero-fraction ↑ |
| Cell sorting (Fig. 12) | 14, 2, 11, 16 | **+3** | 10 | dark engulfed, hetero-fraction ↓ |
| High-T mixing (Fig. 9) | 14, 2, 11, 16 | +3 | **40** | crumpling / mixing |

```bash
source .venv/bin/activate
python demo/demo_report.py       # writes demo/report.html and opens it
```

The report renders each with an animated, **type-coloured** cell-field viewer
(light = amber, dark = navy, with cell borders), the heterotypic-fraction order
parameter over time, a bigraph architecture diagram, and a collapsible PBG
document tree.

## Limitations & assumptions

- **Node.js required.** Artistoo is a JS library; the wrapper shells out to a
  Node subprocess. No Node → `NodeUnavailableError` with install instructions.
- **2D, single migrating cell kind** (plus background). Artistoo supports 3D
  and multiple cell kinds; those are natural extensions of the bridge config.
- **`interval` maps to Monte-Carlo steps** (rounded to ≥1). One composite tick
  = one MCS by default.
- The bridge runs one CPM per `ArtistooProcess` instance. Each instance owns a
  Node subprocess; call `.close()` (or let `__del__` handle it) to release it.
- Determinism follows Artistoo's Mersenne-Twister `seed`, but subprocess
  scheduling does not affect results — the trajectory is seed-determined.
