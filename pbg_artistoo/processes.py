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
import os
import subprocess
from typing import Optional

from process_bigraph import Process

from .runtime import ensure_artistoo


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
