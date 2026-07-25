"""Composite generators reproducing Glazier & Graner (1993).

*J. A. Glazier and F. Graner, "Simulation of the differential adhesion driven
rearrangement of biological cells", Phys. Rev. E 47, 2128 (1993).*

Three of the paper's simulations, each driving the REAL Artistoo CPM through
:class:`~pbg_artistoo.processes.CPMSortingProcess`:

* **Checkerboard** (Fig. 7): negative heterotypic surface tension
  (gamma_ld = -3) makes unlike cells intercalate.
* **Cell sorting / engulfment** (Fig. 12): the Technau-Holstein hydra energies
  make cohesive dark cells sort to the interior.
* **High-temperature mixing** (Fig. 9 / Table II): the sorting energies at
  T = 40, where thermal fluctuations dissolve the sorted state.

The medium is the lattice background (kind 0); light = kind 1, dark = kind 2.
"""

from __future__ import annotations

try:
    from viva_superpowers.composite_generator import composite_generator
except Exception:  # pragma: no cover
    def composite_generator(**_kwargs):
        def _wrap(fn):
            return fn

        return _wrap


def _sorting_document(cfg, interval):
    return {
        "cpm": {
            "_type": "process",
            "address": "local:CPMSortingProcess",
            "config": cfg,
            "interval": float(interval),
            "inputs": {
                "temperature": ["setpoints", "temperature"],
                "light_target_volume": ["setpoints", "light_target_volume"],
                "dark_target_volume": ["setpoints", "dark_target_volume"],
            },
            "outputs": {
                "cell_count": ["readouts", "cell_count"],
                "light_count": ["readouts", "light_count"],
                "dark_count": ["readouts", "dark_count"],
                "heterotypic_fraction": ["readouts", "heterotypic_fraction"],
                "total_boundary": ["readouts", "total_boundary"],
                "mean_connectedness": ["readouts", "mean_connectedness"],
                "centroids": ["readouts", "centroids"],
                "cell_types": ["readouts", "cell_types"],
                "cell_volumes": ["readouts", "cell_volumes"],
            },
        },
        "setpoints": {
            "temperature": float(cfg.get("temperature", 10.0)),
            "light_target_volume": float(cfg.get("light_volume", 40.0)),
            "dark_target_volume": float(cfg.get("dark_volume", 40.0)),
        },
        "readouts": {
            "cell_count": int(cfg.get("n_cells", 120)),
            "light_count": 0,
            "dark_count": 0,
            "heterotypic_fraction": 0.0,
            "total_boundary": 0.0,
            "mean_connectedness": 1.0,
            "centroids": {},
            "cell_types": {},
            "cell_volumes": {},
        },
        "emitter": {
            "_type": "step",
            "address": "local:RAMEmitter",
            "config": {
                "emit": {
                    "heterotypic_fraction": "float",
                    "total_boundary": "float",
                    "light_count": "integer",
                    "dark_count": "integer",
                    "mean_connectedness": "float",
                    "centroids": "map[string,list[float]]",
                    "cell_types": "map[string,integer]",
                    "cell_volumes": "map[string,float]",
                    "time": "float",
                }
            },
            "inputs": {
                "heterotypic_fraction": ["readouts", "heterotypic_fraction"],
                "total_boundary": ["readouts", "total_boundary"],
                "light_count": ["readouts", "light_count"],
                "dark_count": ["readouts", "dark_count"],
                "mean_connectedness": ["readouts", "mean_connectedness"],
                "centroids": ["readouts", "centroids"],
                "cell_types": ["readouts", "cell_types"],
                "cell_volumes": ["readouts", "cell_volumes"],
                "time": ["global_time"],
            },
        },
    }


# ---- Fig. 7: checkerboard (negative surface tension) ----------------------
@composite_generator(
    name="glazier_graner_checkerboard",
    description=(
        "Glazier & Graner 1993 Fig. 7 — checkerboard. Negative heterotypic "
        "surface tension (gamma_ld = -3; J_ll=10, J_dd=8, J_ld=6, J_lM=J_dM=12) "
        "makes light and dark cells intercalate. Heterotypic boundary RISES."
    ),
    parameters={
        "n_cells": {"type": "integer", "default": 120,
                    "description": "Total cells in the aggregate"},
        "field_size": {"type": "integer", "default": 95,
                       "description": "Square lattice edge length"},
        "light_volume": {"type": "float", "default": 58.0,
                         "description": "Target area of light cells"},
        "dark_volume": {"type": "float", "default": 26.0,
                        "description": "Target area of dark cells (Honda: ciliated cells are smaller)"},
        "temperature": {"type": "float", "default": 10.0,
                        "description": "Metropolis temperature T"},
        "interval": {"type": "float", "default": 2.0,
                     "description": "Monte-Carlo steps per tick"},
        "seed": {"type": "integer", "default": 3,
                 "description": "RNG / type-assignment seed"},
    },
)
def glazier_graner_checkerboard(core=None, *, n_cells=120, field_size=95,
                                light_volume=58.0, dark_volume=26.0,
                                temperature=10.0, interval=2.0, seed=3):
    # Honda's quail-oviduct epithelium (the paper's checkerboard motivation)
    # has large gland (light) and small ciliated (dark) cells — distinct
    # per-type target areas, driven through separate setpoint ports.
    cfg = {
        "field_width": int(field_size), "field_height": int(field_size),
        "n_cells": int(n_cells), "dark_fraction": 0.5,
        "light_volume": float(light_volume), "dark_volume": float(dark_volume),
        "lambda_volume": 1.0,
        "temperature": float(temperature), "seed": int(seed), "seed_half": 3,
        "J_ll": 10.0, "J_dd": 8.0, "J_ld": 6.0, "J_lM": 12.0, "J_dM": 12.0,
    }
    return _sorting_document(cfg, interval)


# ---- Fig. 12: cell sorting / engulfment -----------------------------------
@composite_generator(
    name="glazier_graner_cell_sorting",
    description=(
        "Glazier & Graner 1993 Fig. 12 — cell sorting. Technau-Holstein hydra "
        "energies (J_ll=14, J_dd=2, J_ld=11, J_lM=J_dM=16; gamma_ld=3, "
        "gamma_dM=15) make cohesive dark cells sort to the interior, engulfed "
        "by a light-cell monolayer. Heterotypic boundary FALLS."
    ),
    parameters={
        "n_cells": {"type": "integer", "default": 120,
                    "description": "Total cells in the aggregate"},
        "field_size": {"type": "integer", "default": 95,
                       "description": "Square lattice edge length"},
        "temperature": {"type": "float", "default": 10.0,
                        "description": "Metropolis temperature T"},
        "interval": {"type": "float", "default": 2.0,
                     "description": "Monte-Carlo steps per tick"},
        "seed": {"type": "integer", "default": 1,
                 "description": "RNG / type-assignment seed"},
    },
)
def glazier_graner_cell_sorting(core=None, *, n_cells=120, field_size=95,
                                temperature=10.0, interval=2.0, seed=1):
    cfg = {
        "field_width": int(field_size), "field_height": int(field_size),
        "n_cells": int(n_cells), "dark_fraction": 0.5,
        "light_volume": 40.0, "dark_volume": 40.0, "lambda_volume": 1.0,
        "temperature": float(temperature), "seed": int(seed), "seed_half": 3,
        "J_ll": 14.0, "J_dd": 2.0, "J_ld": 11.0, "J_lM": 16.0, "J_dM": 16.0,
    }
    return _sorting_document(cfg, interval)


# ---- Fig. 9 / Table II: high-temperature mixing ---------------------------
@composite_generator(
    name="glazier_graner_high_temperature",
    description=(
        "Glazier & Graner 1993 Fig. 9 / Table II — high-temperature mixing. "
        "The cell-sorting energies at T=40, where thermal fluctuations exceed "
        "the surface-tension barriers and the sorted state dissolves into a "
        "mixed, fluctuating aggregate."
    ),
    parameters={
        "n_cells": {"type": "integer", "default": 120,
                    "description": "Total cells in the aggregate"},
        "field_size": {"type": "integer", "default": 95,
                       "description": "Square lattice edge length"},
        "temperature": {"type": "float", "default": 40.0,
                        "description": "Metropolis temperature T (high)"},
        "interval": {"type": "float", "default": 2.0,
                     "description": "Monte-Carlo steps per tick"},
        "seed": {"type": "integer", "default": 1,
                 "description": "RNG / type-assignment seed"},
    },
)
def glazier_graner_high_temperature(core=None, *, n_cells=120, field_size=95,
                                    temperature=40.0, interval=2.0, seed=1):
    cfg = {
        "field_width": int(field_size), "field_height": int(field_size),
        "n_cells": int(n_cells), "dark_fraction": 0.5,
        "light_volume": 40.0, "dark_volume": 40.0, "lambda_volume": 1.0,
        "temperature": float(temperature), "seed": int(seed), "seed_half": 3,
        "J_ll": 14.0, "J_dd": 2.0, "J_ld": 11.0, "J_lM": 16.0, "J_dM": 16.0,
    }
    return _sorting_document(cfg, interval)
