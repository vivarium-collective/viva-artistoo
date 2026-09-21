#!/usr/bin/env python
"""Reproduce the Glazier & Graner (1993) sorting investigation runs.

Runs each registered composite for seeds 1-3 (176 MCS), writes per-run trajectory
CSVs into each study's runs/ dir, a per-study summary.json, and a heterotypic-
fraction-vs-MCS chart. Demo-style direct Composite execution via the Node bridge.

    python workspace/investigations/glazier-graner-sorting/run_investigation.py

Requires Node.js + the workspace venv (pbg_artistoo, matplotlib).
"""
import csv, json, statistics
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pbg_artistoo.core import build_core
from process_bigraph import Composite
from pbg_artistoo.composites.glazier_graner import (
    glazier_graner_cell_sorting, glazier_graner_checkerboard, glazier_graner_high_temperature,
)

INV = Path(__file__).resolve().parent
SEEDS = [1, 2, 3]
FIELD, NCELLS, SNAPSHOTS, STEPS = 85, 110, 22, 8  # 176 MCS total

STUDIES = {
    "gg-01-differential-adhesion-sorting": [
        ("glazier_graner_cell_sorting", glazier_graner_cell_sorting, "Cell sorting (Fig 12)", "#059669"),
    ],
    "gg-02-sign-and-temperature-controls": [
        ("glazier_graner_checkerboard", glazier_graner_checkerboard, "Checkerboard (Fig 7)", "#7c3aed"),
        ("glazier_graner_high_temperature", glazier_graner_high_temperature, "High-T mixing (Fig 9)", "#dc2626"),
    ],
}


def run_one(gen, seed):
    core = build_core()
    doc = gen(n_cells=NCELLS, field_size=FIELD, interval=float(STEPS), seed=seed)
    sim = Composite({"state": doc}, core=core)
    rows = []
    for i in range(SNAPSHOTS):
        sim.run(float(STEPS))
        r = sim.state["readouts"]
        rows.append({
            "mcs": (i + 1) * STEPS,
            "heterotypic_fraction": round(float(r["heterotypic_fraction"]), 5),
            "total_boundary": round(float(r["total_boundary"]), 2),
            "light_count": int(r["light_count"]), "dark_count": int(r["dark_count"]),
            "mean_connectedness": round(float(r["mean_connectedness"]), 5),
        })
    return rows


for slug, comps in STUDIES.items():
    sdir = INV / "studies" / slug
    (sdir / "runs").mkdir(parents=True, exist_ok=True)
    (sdir / "charts").mkdir(parents=True, exist_ok=True)
    summary = {}
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for comp, gen, label, color in comps:
        hf0s, hfNs, connNs, tbNs = [], [], [], []
        for seed in SEEDS:
            rows = run_one(gen, seed)
            with open(sdir / "runs" / f"{comp}_seed{seed}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
            mcs = [r["mcs"] for r in rows]; hf = [r["heterotypic_fraction"] for r in rows]
            ax.plot(mcs, hf, color=color, alpha=0.55, lw=1.4, label=label if seed == SEEDS[0] else None)
            hf0s.append(hf[0]); hfNs.append(hf[-1])
            connNs.append(rows[-1]["mean_connectedness"]); tbNs.append(rows[-1]["total_boundary"])
            print(f"{comp} seed{seed}: hf {hf[0]:.4f} -> {hf[-1]:.4f}  conn {rows[-1]['mean_connectedness']:.3f}")
        summary[comp] = {
            "composite": f"pbg_artistoo.composites.glazier_graner.{comp}", "seeds": SEEDS,
            "mcs_total": max(mcs), "sampling_mcs": mcs[1] - mcs[0],
            "hf_initial_mean": round(statistics.mean(hf0s), 4), "hf_final_mean": round(statistics.mean(hfNs), 4),
            "hf_final_std": round(statistics.pstdev(hfNs), 4),
            "hf_delta_mean": round(statistics.mean([b - a for a, b in zip(hf0s, hfNs)]), 4),
            "hf_final_per_seed": [round(x, 4) for x in hfNs],
            "mean_connectedness_final_mean": round(statistics.mean(connNs), 4),
            "total_boundary_final_mean": round(statistics.mean(tbNs), 1),
        }
    ax.set_xlabel("Monte-Carlo steps (MCS)"); ax.set_ylabel("heterotypic boundary fraction")
    ax.set_title(f"{slug}: heterotypic fraction vs MCS (3 seeds each)")
    ax.legend(loc="best", fontsize=8); ax.grid(alpha=0.25); fig.tight_layout()
    fig.savefig(sdir / "charts" / "heterotypic_fraction_vs_mcs.png", dpi=120); plt.close(fig)
    (sdir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"wrote {slug} summary + chart")
