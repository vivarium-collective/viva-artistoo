"""Composite generators for Artistoo Cellular Potts models.

These are the dashboard-visible entry points. Each returns a process-bigraph
document wiring an :class:`~pbg_artistoo.processes.ArtistooProcess` to input
setpoint stores and an output emitter. Runs the REAL Artistoo simulator.
"""

from __future__ import annotations

try:
    from pbg_superpowers.composite_generator import composite_generator
except Exception:  # pragma: no cover - allow import without pbg_superpowers
    def composite_generator(**_kwargs):
        def _wrap(fn):
            return fn

        return _wrap


def _document(*, artistoo_config, interval, emit):
    """Assemble a standard single-CPM document."""
    return {
        "cpm": {
            "_type": "process",
            "address": "local:ArtistooProcess",
            "config": artistoo_config,
            "interval": float(interval),
            "inputs": {
                "temperature": ["setpoints", "temperature"],
                "target_volume": ["setpoints", "target_volume"],
                "target_perimeter": ["setpoints", "target_perimeter"],
                "motility": ["setpoints", "motility"],
            },
            "outputs": {
                "cell_count": ["readouts", "cell_count"],
                "total_volume": ["readouts", "total_volume"],
                "total_perimeter": ["readouts", "total_perimeter"],
                "mean_connectedness": ["readouts", "mean_connectedness"],
                "centroids": ["readouts", "centroids"],
                "cell_volumes": ["readouts", "cell_volumes"],
            },
        },
        "setpoints": {
            "temperature": float(artistoo_config.get("temperature", 20.0)),
            "target_volume": float(artistoo_config.get("target_volume", 200.0)),
            "target_perimeter": float(artistoo_config.get("target_perimeter", 180.0)),
            "motility": float(artistoo_config.get("lambda_act", 0.0)),
        },
        "readouts": {
            "cell_count": int(artistoo_config.get("n_cells", 5)),
            "total_volume": 0.0,
            "total_perimeter": 0.0,
            "mean_connectedness": 1.0,
            "centroids": {},
            "cell_volumes": {},
        },
        "emitter": {
            "_type": "step",
            "address": "local:RAMEmitter",
            "config": {
                "emit": {
                    "cell_count": "integer",
                    "total_volume": "float",
                    "total_perimeter": "float",
                    "mean_connectedness": "float",
                    "centroids": "map[string,list[float]]",
                    "cell_volumes": "map[string,float]",
                    "time": "float",
                }
            },
            "inputs": {
                "cell_count": ["readouts", "cell_count"],
                "total_volume": ["readouts", "total_volume"],
                "total_perimeter": ["readouts", "total_perimeter"],
                "mean_connectedness": ["readouts", "mean_connectedness"],
                "centroids": ["readouts", "centroids"],
                "cell_volumes": ["readouts", "cell_volumes"],
                "time": ["global_time"],
            },
        }
        if emit
        else {},
    }


@composite_generator(
    name="artistoo_cell_migration",
    description=(
        "Real Artistoo Cellular Potts model: migrating Act-model cells on a "
        "2D lattice. Cells adhere, keep a target volume/perimeter, and crawl "
        "via the activity constraint."
    ),
    parameters={
        "n_cells": {"type": "integer", "default": 5,
                    "description": "Number of migrating cells"},
        "field_size": {"type": "integer", "default": 60,
                       "description": "Square lattice edge length"},
        "target_volume": {"type": "float", "default": 200.0,
                          "description": "Preferred cell volume (lattice sites)"},
        "lambda_act": {"type": "float", "default": 200.0,
                       "description": "Activity/motility strength"},
        "max_act": {"type": "float", "default": 30.0,
                    "description": "Activity memory (higher = more persistent)"},
        "temperature": {"type": "float", "default": 20.0,
                        "description": "Metropolis temperature T"},
        "interval": {"type": "float", "default": 1.0,
                     "description": "Monte-Carlo steps per composite tick"},
        "seed": {"type": "integer", "default": 42,
                 "description": "RNG seed"},
    },
)
def artistoo_cell_migration(
    core=None,
    *,
    n_cells=5,
    field_size=60,
    target_volume=200.0,
    lambda_act=200.0,
    max_act=30.0,
    temperature=20.0,
    interval=1.0,
    seed=42,
):
    cfg = {
        "field_width": int(field_size),
        "field_height": int(field_size),
        "n_cells": int(n_cells),
        "temperature": float(temperature),
        "seed": int(seed),
        "target_volume": float(target_volume),
        "lambda_volume": 50.0,
        "target_perimeter": float(target_volume) * 0.9,
        "lambda_perimeter": 2.0,
        "max_act": float(max_act),
        "lambda_act": float(lambda_act),
        "act_mean": "geometric",
        "adhesion_bg_cell": 20.0,
        "adhesion_cell_cell": 0.0,
        "seed_layout": "grid",
    }
    return _document(artistoo_config=cfg, interval=interval, emit=True)


@composite_generator(
    name="artistoo_cell_sorting",
    description=(
        "Real Artistoo CPM demonstrating differential-adhesion cell sorting: "
        "non-motile adhesive cells relax toward a compact aggregate driven by "
        "the adhesion + volume constraints."
    ),
    parameters={
        "n_cells": {"type": "integer", "default": 12,
                    "description": "Number of adhesive cells"},
        "field_size": {"type": "integer", "default": 70,
                       "description": "Square lattice edge length"},
        "target_volume": {"type": "float", "default": 120.0,
                          "description": "Preferred cell volume"},
        "adhesion_cell_cell": {"type": "float", "default": 5.0,
                               "description": "Cell-cell contact energy (low = sticky)"},
        "interval": {"type": "float", "default": 1.0,
                     "description": "Monte-Carlo steps per composite tick"},
        "seed": {"type": "integer", "default": 7,
                 "description": "RNG seed"},
    },
)
def artistoo_cell_sorting(
    core=None,
    *,
    n_cells=12,
    field_size=70,
    target_volume=120.0,
    adhesion_cell_cell=5.0,
    interval=1.0,
    seed=7,
):
    cfg = {
        "field_width": int(field_size),
        "field_height": int(field_size),
        "n_cells": int(n_cells),
        "temperature": 15.0,
        "seed": int(seed),
        "target_volume": float(target_volume),
        "lambda_volume": 50.0,
        "target_perimeter": float(target_volume) * 1.2,
        "lambda_perimeter": 2.0,
        "max_act": 0.0,
        "lambda_act": 0.0,
        "act_mean": "geometric",
        "adhesion_bg_cell": 20.0,
        "adhesion_cell_cell": float(adhesion_cell_cell),
        "seed_layout": "grid",
    }
    return _document(artistoo_config=cfg, interval=interval, emit=True)
