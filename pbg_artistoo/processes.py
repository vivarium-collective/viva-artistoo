"""Process-bigraph wrapper for the real Artistoo Cellular Potts Model.

``ArtistooProcess`` bridges to the genuine upstream Artistoo simulator
(github:ingewortel/artistoo) running in a persistent Node.js subprocess. Each
``update()`` pushes the surrounding bigraph's setpoints (temperature, target
volume/perimeter, motility) into the live CPM, advances the Monte-Carlo
trajectory by ``interval`` Monte-Carlo steps, and reads real cell statistics
back out. Nothing here reimplements the Potts Hamiltonian — the JS library
computes it.

Port design (see README "Port design" for the rationale):

* Scalar aggregates that other processes can also contribute to
  (``cell_count``, ``total_volume``, ``total_perimeter``,
  ``mean_connectedness``) are emitted as **additive deltas** so a sibling
  growth/division process composes correctly.
* Per-cell spatial readouts (``centroids``, ``cell_volumes``) are genuine
  sensor snapshots the CPM alone owns, over a *dynamic* cell set. They use
  ``overwrite[map[...]]`` — the legitimate sensor exception — because a plain
  ``map`` only propagates pre-existing keys and cells are born/die at runtime.
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
from typing import Optional

from process_bigraph import Process

from .runtime import ensure_artistoo


def aggregate_seeds(width, height, n_cells, seed_half, dark_fraction, seed):
    """Compute block-seed placements packing ``n_cells`` into a disk.

    Returns ``(seeds, n_light, n_dark)`` where each seed is
    ``[kind, x, y, half]`` (kind 1 = light, 2 = dark). Cells are placed on a
    square grid, sorted by distance from centre, and the closest ``n_cells``
    are kept — giving the roughly-circular aggregate the Glazier & Graner
    initial condition uses. Type assignment is deterministic in ``seed``.
    """
    cx, cy = width / 2.0, height / 2.0
    spacing = 2 * int(seed_half) + 2
    pts = []
    half_span = max(width, height)
    steps = int(half_span // spacing) + 2
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            x = int(round(cx + i * spacing))
            y = int(round(cy + j * spacing))
            if seed_half <= x < width - seed_half and seed_half <= y < height - seed_half:
                pts.append((math.hypot(x - cx, y - cy), x, y))
    pts.sort(key=lambda p: p[0])
    chosen = pts[: int(n_cells)]
    rng = random.Random(int(seed))
    seeds, n_light, n_dark = [], 0, 0
    for _, x, y in chosen:
        if rng.random() < float(dark_fraction):
            seeds.append([2, x, y, int(seed_half)])
            n_dark += 1
        else:
            seeds.append([1, x, y, int(seed_half)])
            n_light += 1
    return seeds, n_light, n_dark


class ArtistooProcess(Process):
    """Time-stepped bridge to a real Artistoo CPM (Act model by default).

    Inputs
    ------
    temperature : float
        Metropolis temperature ``T`` of the Potts model (cell "activity").
    target_volume : float
        Preferred volume ``V`` of the cell kind (VolumeConstraint). A sibling
        growth/metabolism process can drive this.
    target_perimeter : float
        Preferred perimeter ``P`` (PerimeterConstraint).
    motility : float
        Activity strength ``LAMBDA_ACT`` (ActivityConstraint) — raises or
        lowers cell migration.

    Outputs
    -------
    cell_count : integer
        Delta in the number of live cells (accumulates additively).
    total_volume : float
        Delta in total occupied lattice sites (composes with growth).
    total_perimeter : float
        Delta in total border-pixel count.
    mean_connectedness : float
        Delta in the mean per-cell connected fraction (1.0 = fully connected).
    centroids : overwrite[map[string,list[float]]]
        Absolute per-cell [x, y] centroids (torus-corrected). Sensor snapshot.
    cell_volumes : overwrite[map[string,float]]
        Absolute per-cell volume (lattice-site count). Sensor snapshot.
    """

    config_schema = {
        # width/height as scalars — a list[integer] default would *concatenate*
        # with the supplied value in bigraph-schema, not replace it.
        "field_width": {"_type": "integer", "_default": 50},
        "field_height": {"_type": "integer", "_default": 50},
        "n_cells": {"_type": "integer", "_default": 5},
        "temperature": {"_type": "float", "_default": 20.0},
        "seed": {"_type": "integer", "_default": 42},
        "torus": {"_type": "boolean", "_default": True},
        "target_volume": {"_type": "float", "_default": 200.0},
        "lambda_volume": {"_type": "float", "_default": 50.0},
        "target_perimeter": {"_type": "float", "_default": 180.0},
        "lambda_perimeter": {"_type": "float", "_default": 2.0},
        "max_act": {"_type": "float", "_default": 30.0},
        "lambda_act": {"_type": "float", "_default": 0.0},
        "act_mean": {"_type": "string", "_default": "geometric"},
        "adhesion_bg_cell": {"_type": "float", "_default": 20.0},
        "adhesion_cell_cell": {"_type": "float", "_default": 0.0},
        "seed_layout": {"_type": "string", "_default": "random"},
        "node_path": {"_type": "string", "_default": ""},
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        self._proc: Optional[subprocess.Popen] = None
        self._prev = {
            "cell_count": 0,
            "total_volume": 0.0,
            "total_perimeter": 0.0,
            "mean_connectedness": 0.0,
        }

    # ---- ports -------------------------------------------------------------
    def inputs(self):
        return {
            "temperature": "float",
            "target_volume": "float",
            "target_perimeter": "float",
            "motility": "float",
        }

    def outputs(self):
        return {
            "cell_count": "integer",
            "total_volume": "float",
            "total_perimeter": "float",
            "mean_connectedness": "float",
            "centroids": "overwrite[map[string,list[float]]]",
            "cell_volumes": "overwrite[map[string,float]]",
        }

    def initial_state(self):
        n = int(self.config["n_cells"])
        return {
            # input setpoints so the composite starts with sane values
            "temperature": float(self.config["temperature"]),
            "target_volume": float(self.config["target_volume"]),
            "target_perimeter": float(self.config["target_perimeter"]),
            "motility": float(self.config["lambda_act"]),
            # output stores seeded to the just-seeded (1 px/cell) absolute state;
            # accumulated deltas then keep the store equal to the true absolute.
            "cell_count": n,
            "total_volume": float(n),
            "total_perimeter": float(n),
            "mean_connectedness": 1.0,
            "centroids": {},
            "cell_volumes": {},
        }

    # ---- node bridge lifecycle --------------------------------------------
    def _init_config(self) -> dict:
        c = self.config
        return {
            "field_size": [int(c["field_width"]), int(c["field_height"])],
            "T": float(c["temperature"]),
            "seed": int(c["seed"]),
            "torus": [bool(c["torus"]), bool(c["torus"])],
            "n_cells": int(c["n_cells"]),
            "V": float(c["target_volume"]),
            "LAMBDA_V": float(c["lambda_volume"]),
            "P": float(c["target_perimeter"]),
            "LAMBDA_P": float(c["lambda_perimeter"]),
            "MAX_ACT": float(c["max_act"]),
            "LAMBDA_ACT": float(c["lambda_act"]),
            "ACT_MEAN": str(c["act_mean"]),
            "J_bg_cell": float(c["adhesion_bg_cell"]),
            "J_cell_cell": float(c["adhesion_cell_cell"]),
            "seed_layout": str(c["seed_layout"]),
        }

    def _start(self):
        info = ensure_artistoo()
        env = dict(os.environ)
        env.update(info["env"])
        self._proc = subprocess.Popen(
            [info["node"], info["script"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        reply = self._command({"cmd": "init", "config": self._init_config()})
        stats = reply["stats"]
        self._prev = {
            "cell_count": stats["cell_count"],
            "total_volume": float(stats["total_volume"]),
            "total_perimeter": float(stats["total_perimeter"]),
            "mean_connectedness": float(stats["mean_connectedness"]),
        }

    def _command(self, obj: dict) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            raise RuntimeError("Artistoo node bridge is not running")
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"Artistoo node bridge died: {err.strip()}")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise RuntimeError(f"Artistoo bridge error: {reply.get('error')}")
        return reply

    # ---- simulation step ---------------------------------------------------
    def update(self, state, interval):
        if self._proc is None or self._proc.poll() is not None:
            self._start()

        params = {
            "T": float(state.get("temperature", self.config["temperature"])),
            "V": float(state.get("target_volume", self.config["target_volume"])),
            "P": float(state.get("target_perimeter", self.config["target_perimeter"])),
            "LAMBDA_ACT": float(state.get("motility", self.config["lambda_act"])),
        }
        mcs = max(1, int(round(interval)))
        stats = self._command({"cmd": "step", "mcs": mcs, "params": params})["stats"]

        d_count = int(stats["cell_count"]) - self._prev["cell_count"]
        d_vol = float(stats["total_volume"]) - self._prev["total_volume"]
        d_per = float(stats["total_perimeter"]) - self._prev["total_perimeter"]
        d_conn = float(stats["mean_connectedness"]) - self._prev["mean_connectedness"]
        self._prev = {
            "cell_count": int(stats["cell_count"]),
            "total_volume": float(stats["total_volume"]),
            "total_perimeter": float(stats["total_perimeter"]),
            "mean_connectedness": float(stats["mean_connectedness"]),
        }

        centroids = {
            str(cid): [float(v[0]), float(v[1])]
            for cid, v in stats["centroids"].items()
        }
        volumes = {str(cid): float(v) for cid, v in stats["volumes"].items()}

        return {
            "cell_count": d_count,
            "total_volume": d_vol,
            "total_perimeter": d_per,
            "mean_connectedness": d_conn,
            "centroids": centroids,
            "cell_volumes": volumes,
        }

    # ---- extras / cleanup --------------------------------------------------
    def get_grid(self) -> dict:
        """Return the compact cell-id field for visualization.

        ``{"field_size": [w, h], "cells": [[x, y, cell_id], ...]}`` — only
        non-background lattice sites. Starts the bridge if needed.
        """
        if self._proc is None or self._proc.poll() is not None:
            self._start()
        return self._command({"cmd": "grid"})["grid"]

    def close(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._command({"cmd": "quit"})
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class _BridgeMixin:
    """Shared Node.js subprocess plumbing for bridge processes."""

    def _spawn(self):
        info = ensure_artistoo()
        env = dict(os.environ)
        env.update(info["env"])
        self._proc = subprocess.Popen(
            [info["node"], info["script"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

    def _command(self, obj: dict) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            raise RuntimeError("Artistoo node bridge is not running")
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"Artistoo node bridge died: {err.strip()}")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise RuntimeError(f"Artistoo bridge error: {reply.get('error')}")
        return reply

    def get_grid(self) -> dict:
        """Return the compact cell-id field ``{field_size, cells:[[x,y,id,kind]]}``."""
        if self._proc is None or self._proc.poll() is not None:
            self._start()
        return self._command({"cmd": "grid"})["grid"]

    def close(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._command({"cmd": "quit"})
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class CPMSortingProcess(_BridgeMixin, Process):
    """Two-type differential-adhesion CPM — the Glazier & Graner (1993) model.

    Reproduces the extended large-Q Potts model of *Glazier & Graner, Phys.
    Rev. E 47, 2128 (1993)*: a mixed aggregate of "light" (kind 1) and "dark"
    (kind 2) cells in a "medium" (background, kind 0), with a full cell-type
    adhesion matrix ``J`` and an area constraint. Behaviour is set entirely by
    the relative surface tensions

        gamma_ld = J_ld - (J_dd + J_ll)/2
        gamma_lM = J_lM - J_ll/2
        gamma_dM = J_dM - J_dd/2

    A negative ``gamma_ld`` favours unlike-cell contact (checkerboard); a
    positive hierarchy drives cell sorting / engulfment. Raising ``temperature``
    drives the mixing transition. There is NO motility — fluctuations come from
    the finite-temperature Metropolis dynamics, exactly as in the paper.

    Inputs
    ------
    temperature : float
        Metropolis temperature ``T`` (global — the live knob for the mixing
        transition).
    light_target_volume : float
        Preferred area ``A`` of *light* (kind 1) cells.
    dark_target_volume : float
        Preferred area ``A`` of *dark* (kind 2) cells. Light and dark are
        driven independently, so a sibling process can grow/shrink one type
        without touching the other (the paper's checkerboard has unequal
        areas for the two types).

    Outputs
    -------
    cell_count, light_count, dark_count : integer
        Deltas in the number of live cells of each type (composable).
    heterotypic_fraction : overwrite[float]
        Fraction of cell-cell boundary that is between UNLIKE types — the
        paper's sorting order parameter (falls for sorting, rises for
        checkerboard). Absolute sensor reading.
    total_boundary : overwrite[float]
        Total mismatched-bond boundary length. Absolute sensor reading.
    mean_connectedness : overwrite[float]
        Mean per-cell connected fraction. Absolute sensor reading.
    centroids : overwrite[map[string,list[float]]]
        Absolute per-cell [x, y]. Sensor snapshot over a dynamic cell set.
    cell_types : overwrite[map[string,integer]]
        Per-cell type (1 = light, 2 = dark). Sensor snapshot.
    cell_volumes : overwrite[map[string,float]]
        Absolute per-cell area. Sensor snapshot.
    """

    config_schema = {
        "field_width": {"_type": "integer", "_default": 90},
        "field_height": {"_type": "integer", "_default": 90},
        "n_cells": {"_type": "integer", "_default": 120},
        "dark_fraction": {"_type": "float", "_default": 0.5},
        "light_volume": {"_type": "float", "_default": 40.0},
        "dark_volume": {"_type": "float", "_default": 40.0},
        "lambda_volume": {"_type": "float", "_default": 1.0},
        "temperature": {"_type": "float", "_default": 10.0},
        "seed": {"_type": "integer", "_default": 1},
        "seed_half": {"_type": "integer", "_default": 3},
        # adhesion matrix (medium=0, light=1, dark=2); Glazier-Graner J's
        "J_ll": {"_type": "float", "_default": 14.0},
        "J_dd": {"_type": "float", "_default": 2.0},
        "J_ld": {"_type": "float", "_default": 11.0},
        "J_lM": {"_type": "float", "_default": 16.0},
        "J_dM": {"_type": "float", "_default": 16.0},
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        self._proc: Optional[subprocess.Popen] = None
        seeds, n_light, n_dark = aggregate_seeds(
            int(self.config["field_width"]),
            int(self.config["field_height"]),
            int(self.config["n_cells"]),
            int(self.config["seed_half"]),
            float(self.config["dark_fraction"]),
            int(self.config["seed"]),
        )
        self._seeds = seeds
        self._n_light = n_light
        self._n_dark = n_dark
        self._prev = {"cell_count": 0, "light_count": 0, "dark_count": 0}

    def inputs(self):
        return {
            "temperature": "float",
            "light_target_volume": "float",
            "dark_target_volume": "float",
        }

    def outputs(self):
        return {
            "cell_count": "integer",
            "light_count": "integer",
            "dark_count": "integer",
            "heterotypic_fraction": "overwrite[float]",
            "total_boundary": "overwrite[float]",
            "mean_connectedness": "overwrite[float]",
            "centroids": "overwrite[map[string,list[float]]]",
            "cell_types": "overwrite[map[string,integer]]",
            "cell_volumes": "overwrite[map[string,float]]",
        }

    def initial_state(self):
        return {
            "temperature": float(self.config["temperature"]),
            "light_target_volume": float(self.config["light_volume"]),
            "dark_target_volume": float(self.config["dark_volume"]),
            "cell_count": self._n_light + self._n_dark,
            "light_count": self._n_light,
            "dark_count": self._n_dark,
            "heterotypic_fraction": 0.0,
            "total_boundary": 0.0,
            "mean_connectedness": 1.0,
            "centroids": {},
            "cell_types": {},
            "cell_volumes": {},
        }

    def _init_config(self) -> dict:
        c = self.config
        lv = float(c["light_volume"])
        dv = float(c["dark_volume"])
        lam = float(c["lambda_volume"])
        J_ll, J_dd, J_ld = float(c["J_ll"]), float(c["J_dd"]), float(c["J_ld"])
        J_lM, J_dM = float(c["J_lM"]), float(c["J_dM"])
        return {
            "field_size": [int(c["field_width"]), int(c["field_height"])],
            "T": float(c["temperature"]),
            "seed": int(c["seed"]),
            "torus": [False, False],
            "kinds": [
                {"V": lv, "LAMBDA_V": lam},  # 1 = light
                {"V": dv, "LAMBDA_V": lam},  # 2 = dark
            ],
            # index 0 = medium, 1 = light, 2 = dark
            "J": [
                [0.0, J_lM, J_dM],
                [J_lM, J_ll, J_ld],
                [J_dM, J_ld, J_dd],
            ],
            "seeds": self._seeds,
            "compute_boundary": True,
        }

    def _start(self):
        self._spawn()
        stats = self._command({"cmd": "init", "config": self._init_config()})["stats"]
        counts = {int(k): v for k, v in stats["counts_by_kind"].items()}
        self._prev = {
            "cell_count": int(stats["cell_count"]),
            "light_count": counts.get(1, 0),
            "dark_count": counts.get(2, 0),
        }

    def surface_tensions(self) -> dict:
        """Return the three Glazier-Graner surface tensions for this config."""
        c = self.config
        J_ll, J_dd, J_ld = float(c["J_ll"]), float(c["J_dd"]), float(c["J_ld"])
        J_lM, J_dM = float(c["J_lM"]), float(c["J_dM"])
        return {
            "gamma_ld": J_ld - (J_dd + J_ll) / 2.0,
            "gamma_lM": J_lM - J_ll / 2.0,
            "gamma_dM": J_dM - J_dd / 2.0,
        }

    def update(self, state, interval):
        if self._proc is None or self._proc.poll() is not None:
            self._start()

        params = {
            "T": float(state.get("temperature", self.config["temperature"])),
            "kind_V": {
                1: float(state.get("light_target_volume", self.config["light_volume"])),
                2: float(state.get("dark_target_volume", self.config["dark_volume"])),
            },
        }
        mcs = max(1, int(round(interval)))
        stats = self._command({"cmd": "step", "mcs": mcs, "params": params})["stats"]

        counts = {int(k): v for k, v in stats["counts_by_kind"].items()}
        cur = {
            "cell_count": int(stats["cell_count"]),
            "light_count": counts.get(1, 0),
            "dark_count": counts.get(2, 0),
        }
        deltas = {k: cur[k] - self._prev[k] for k in cur}
        self._prev = cur

        boundary = stats.get("boundary", {})
        centroids = {
            str(cid): [float(v[0]), float(v[1])]
            for cid, v in stats["centroids"].items()
        }
        cell_types = {str(cid): int(k) for cid, k in stats["kinds"].items()}
        volumes = {str(cid): float(v) for cid, v in stats["volumes"].items()}

        return {
            "cell_count": deltas["cell_count"],
            "light_count": deltas["light_count"],
            "dark_count": deltas["dark_count"],
            "heterotypic_fraction": float(boundary.get("heterotypic_fraction", 0.0)),
            "total_boundary": float(boundary.get("total", 0.0)),
            "mean_connectedness": float(stats["mean_connectedness"]),
            "centroids": centroids,
            "cell_types": cell_types,
            "cell_volumes": volumes,
        }
