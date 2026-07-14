#!/usr/bin/env python
"""Reproduce three Glazier & Graner (1993) CPM simulations as a demo report.

*J. A. Glazier and F. Graner, "Simulation of the differential adhesion driven
rearrangement of biological cells", Phys. Rev. E 47, 2128 (1993).*

Runs the REAL Artistoo Cellular Potts Model (via the Node.js bridge) through
process-bigraph Composites for three of the paper's differential-adhesion
regimes, and renders demo/report.html with:

  * sticky navigation + surface-tension metric cards
  * an animated, type-coloured cell-field viewer (light vs dark cells)
  * the heterotypic-boundary-fraction order parameter over time
  * an interactive bigraph architecture diagram (bigraph-viz2)
  * a collapsible PBG document tree

Everything except the Plotly CDN script is inlined. Opens in the browser.
"""

from __future__ import annotations

import html
import json
import os
import time
import webbrowser
from pathlib import Path

from process_bigraph import Composite, gather_emitter_results

from pbg_artistoo.core import build_core
from pbg_artistoo.composites.glazier_graner import (
    glazier_graner_checkerboard,
    glazier_graner_cell_sorting,
    glazier_graner_high_temperature,
)

try:
    from bigraph_viz2 import emit_html as bgv_emit_html
except Exception:  # pragma: no cover
    bgv_emit_html = None

HERE = Path(__file__).resolve().parent
OUT = HERE / "report.html"

# shared run size — keep MCS high enough to see sorting/intercalation
FIELD = 85
NCELLS = 110
SNAPSHOTS = 22
STEPS = 8  # MCS per snapshot -> 176 MCS total

CONFIGS = [
    {
        "id": "checkerboard",
        "title": "Checkerboard (Fig. 7)",
        "subtitle": "Negative surface tension, γ_ld = −3",
        "accent": "#7c3aed",
        "fig": "Fig. 7",
        "expect": "up",
        "description": (
            "J_ll=10, J_dd=8, J_ld=6, J_lM=J_dM=12, T=10, λ=1. Because the "
            "heterotypic surface tension γ_ld is negative, unlike cells "
            "prefer to touch: light and dark cells intercalate and the "
            "heterotypic boundary fraction RISES toward a checkerboard."
        ),
        "gen": glazier_graner_checkerboard,
    },
    {
        "id": "sorting",
        "title": "Cell sorting / engulfment (Fig. 12)",
        "subtitle": "Technau–Holstein hydra energies, γ_ld = +3",
        "accent": "#059669",
        "fig": "Fig. 12",
        "expect": "down",
        "description": (
            "J_ll=14, J_dd=2, J_ld=11, J_lM=J_dM=16, T=10, λ=1. Cohesive "
            "dark cells (low J_dd) minimise their surface by clustering to the "
            "interior, engulfed by a light-cell monolayer. The heterotypic "
            "boundary fraction FALLS as the aggregate sorts."
        ),
        "gen": glazier_graner_cell_sorting,
    },
    {
        "id": "hightemp",
        "title": "High-temperature mixing (Fig. 9 / Table II)",
        "subtitle": "Sorting energies at T = 40",
        "accent": "#dc2626",
        "fig": "Fig. 9",
        "expect": "mix",
        "description": (
            "The same cell-sorting energies, but T=40. Thermal fluctuations now "
            "exceed the surface-tension barriers, cell boundaries crumple, and "
            "the aggregate mixes rather than cleanly sorting — the paper's "
            "mixing transition (bulk moments blow up in Table II)."
        ),
        "gen": glazier_graner_high_temperature,
    },
]


def run_config(cfg):
    core = build_core()
    doc = cfg["gen"](n_cells=NCELLS, field_size=FIELD, interval=float(STEPS), seed=1)
    sim = Composite({"state": doc}, core=core)
    proc = sim.state["cpm"]["instance"]

    frames = []
    series = {"time": [], "heterotypic_fraction": [], "total_boundary": [],
              "light_count": [], "dark_count": [], "mean_connectedness": []}

    t0 = time.perf_counter()
    for i in range(SNAPSHOTS):
        sim.run(float(STEPS))
        r = sim.state["readouts"]
        series["time"].append((i + 1) * STEPS)
        series["heterotypic_fraction"].append(round(float(r["heterotypic_fraction"]), 4))
        series["total_boundary"].append(round(float(r["total_boundary"]), 1))
        series["light_count"].append(int(r["light_count"]))
        series["dark_count"].append(int(r["dark_count"]))
        series["mean_connectedness"].append(round(float(r["mean_connectedness"]), 4))
        grid = proc.get_grid()
        # store [x, y, id, kind] — kind drives colour, id draws cell borders
        frames.append({"t": (i + 1) * STEPS, "w": grid["field_size"][0],
                       "h": grid["field_size"][1], "cells": grid["cells"]})
    elapsed = time.perf_counter() - t0

    tensions = proc.surface_tensions()

    diagram = ""
    if bgv_emit_html is not None:
        try:
            diagram = bgv_emit_html(doc, height="440px", inspector=True,
                                    dedupe=(cfg["id"] != CONFIGS[0]["id"]),
                                    id=f"bgv_{cfg['id']}")
        except Exception as e:  # pragma: no cover
            diagram = f"<p class='muted'>diagram unavailable: {html.escape(str(e))}</p>"

    proc.close()
    r = sim.state["readouts"]
    return {
        "cfg": cfg,
        "series": series,
        "frames": frames,
        "diagram": diagram,
        "elapsed": elapsed,
        "tensions": tensions,
        "final_hf": round(float(r["heterotypic_fraction"]), 3),
        "hf0": series["heterotypic_fraction"][0],
        "n_light": int(r["light_count"]),
        "n_dark": int(r["dark_count"]),
        "doc": doc,
    }


# ---- HTML rendering -------------------------------------------------------

def json_tree(obj, depth=0):
    if isinstance(obj, dict):
        if not obj:
            return "<span class='j-brace'>{}</span>"
        open_attr = "" if depth < 2 else " data-collapsed='1'"
        rows = "".join(
            f"<div class='j-row'><span class='j-key'>{html.escape(str(k))}</span>"
            f"<span class='j-colon'>: </span>{json_tree(v, depth + 1)}</div>"
            for k, v in obj.items()
        )
        return (f"<span class='j-node'{open_attr}><span class='j-toggle'>{{…}}</span>"
                f"<div class='j-children'>{rows}</div></span>")
    if isinstance(obj, list):
        if len(obj) <= 6 and all(isinstance(x, (int, float, str, bool)) for x in obj):
            return "<span class='j-brack'>[" + ", ".join(_leaf(x) for x in obj) + "]</span>"
        rows = "".join(f"<div class='j-row'>{json_tree(x, depth + 1)}</div>" for x in obj)
        return (f"<span class='j-node'><span class='j-toggle'>[…]</span>"
                f"<div class='j-children'>{rows}</div></span>")
    return _leaf(obj)


def _leaf(x):
    if isinstance(x, bool):
        return f"<span class='j-bool'>{str(x).lower()}</span>"
    if x is None:
        return "<span class='j-null'>null</span>"
    if isinstance(x, (int, float)):
        return f"<span class='j-num'>{x}</span>"
    return f"<span class='j-str'>\"{html.escape(str(x))}\"</span>"


def sanitize_doc(doc):
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if k != "instance"}
        if isinstance(o, list):
            return [clean(v) for v in o]
        if isinstance(o, (int, float, str, bool)) or o is None:
            return o
        return str(o)
    return clean(doc)


TREND = {
    "up": ("↑ rises", "unlike cells intercalate"),
    "down": ("↓ falls", "cells sort / engulf"),
    "mix": ("↓ falls, boundaries crumple", "thermal mixing"),
}


def build_html(results):
    payload = {
        r["cfg"]["id"]: {
            "series": r["series"],
            "frames": r["frames"],
            "accent": r["cfg"]["accent"],
        }
        for r in results
    }
    data_json = json.dumps(payload)

    nav = "".join(
        f"<a href='#{r['cfg']['id']}'>{html.escape(r['cfg']['title'])}</a>"
        for r in results
    )

    sections = []
    for r in results:
        c = r["cfg"]
        cid = c["id"]
        g = r["tensions"]
        trend_label, trend_desc = TREND[c["expect"]]
        doc_tree = json_tree(sanitize_doc(r["doc"]))
        sections.append(f"""
        <section id="{cid}" style="--accent:{c['accent']}">
          <div class="sec-head">
            <h2>{html.escape(c['title'])}</h2>
            <p class="subtitle">{html.escape(c['subtitle'])}</p>
            <p class="desc">{html.escape(c['description'])}</p>
          </div>
          <div class="metrics">
            <div class="metric"><div class="m-val">{g['gamma_ld']:+.0f}</div><div class="m-lab">γ_ld</div></div>
            <div class="metric"><div class="m-val">{g['gamma_dM']:+.0f}</div><div class="m-lab">γ_dM</div></div>
            <div class="metric"><div class="m-val">{r['n_light']}/{r['n_dark']}</div><div class="m-lab">light / dark cells</div></div>
            <div class="metric"><div class="m-val">{r['hf0']:.2f}→{r['final_hf']:.2f}</div><div class="m-lab">heterotypic frac</div></div>
            <div class="metric"><div class="m-val">{r['elapsed']:.1f}s</div><div class="m-lab">wall-clock</div></div>
          </div>

          <div class="grid2">
            <div class="card">
              <h3>Cell field <span class="muted">(live Artistoo lattice · <span class="legL">light</span> / <span class="legD">dark</span>)</span></h3>
              <canvas id="cv_{cid}" width="380" height="380"></canvas>
              <div class="viewer-ctrl">
                <button class="play" data-cid="{cid}">▶ play</button>
                <input type="range" id="slider_{cid}" min="0" max="{len(r['frames'])-1}" value="0">
                <span class="tlabel" id="tlab_{cid}">t = 0</span>
              </div>
            </div>
            <div class="card">
              <h3>Heterotypic boundary fraction <span class="muted">(order parameter, {trend_label})</span></h3>
              <div id="chart_{cid}" style="height:320px"></div>
              <p class="muted" style="margin:6px 2px 0">Fraction of cell–cell boundary between UNLIKE types — {trend_desc}.</p>
            </div>
          </div>

          <div class="card">
            <h3>Bigraph architecture <span class="muted">(CPMSortingProcess → readouts → emitter)</span></h3>
            {r['diagram'] or "<p class='muted'>bigraph-viz2 not installed</p>"}
          </div>

          <details class="card">
            <summary><h3 style="display:inline">PBG document</h3></summary>
            <div class="jtree">{doc_tree}</div>
          </details>
        </section>
        """)

    return TEMPLATE.format(nav=nav, sections="\n".join(sections), data_json=data_json)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pbg-artistoo — Glazier &amp; Graner (1993) reproduction</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         color:#1f2937; background:#f8fafc; line-height:1.5; }}
  header.top {{ background:#0f172a; color:#fff; padding:34px 24px; }}
  header.top h1 {{ margin:0 0 6px; font-size:26px; }}
  header.top p {{ margin:0 0 8px; color:#cbd5e1; max-width:820px; }}
  header.top .cite {{ color:#93c5fd; font-style:italic; font-size:14px; }}
  header.top code {{ background:#1e293b; padding:2px 6px; border-radius:4px; color:#93c5fd; }}
  nav {{ position:sticky; top:0; z-index:10; background:#fff; border-bottom:1px solid #e2e8f0;
        padding:10px 24px; display:flex; gap:18px; flex-wrap:wrap; box-shadow:0 1px 3px rgba(0,0,0,.04); }}
  nav a {{ color:#334155; text-decoration:none; font-weight:600; font-size:14px; }}
  nav a:hover {{ color:#7c3aed; }}
  main {{ max-width:1080px; margin:0 auto; padding:24px; }}
  section {{ margin-bottom:52px; }}
  .sec-head h2 {{ margin:0 0 4px; font-size:22px; border-left:5px solid var(--accent); padding-left:12px; }}
  .subtitle {{ margin:0 0 6px 17px; color:var(--accent); font-weight:600; }}
  .desc {{ margin:0 0 16px 17px; color:#475569; max-width:820px; }}
  .metrics {{ display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px; }}
  .metric {{ background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:14px 18px;
            min-width:104px; text-align:center; }}
  .m-val {{ font-size:22px; font-weight:700; color:var(--accent); font-variant-numeric:tabular-nums; }}
  .m-lab {{ font-size:12px; color:#64748b; text-transform:uppercase; letter-spacing:.03em; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-bottom:18px; }}
  @media (max-width:820px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
  .card {{ background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:18px; margin-bottom:18px; }}
  .card h3 {{ margin:0 0 12px; font-size:16px; }}
  .muted {{ color:#94a3b8; font-weight:400; font-size:13px; }}
  .legL {{ color:#f59e0b; font-weight:700; }}
  .legD {{ color:#1e3a8a; font-weight:700; }}
  canvas {{ width:100%; max-width:380px; image-rendering:pixelated; border:1px solid #e2e8f0;
           border-radius:8px; background:#0b1220; display:block; margin:0 auto; }}
  .viewer-ctrl {{ display:flex; align-items:center; gap:10px; margin-top:12px; }}
  .viewer-ctrl input[type=range] {{ flex:1; accent-color:var(--accent); }}
  .viewer-ctrl button {{ background:var(--accent); color:#fff; border:none; border-radius:6px;
                        padding:6px 12px; cursor:pointer; font-weight:600; }}
  .tlabel {{ font-variant-numeric:tabular-nums; color:#475569; font-size:13px; min-width:56px; }}
  details.card summary {{ cursor:pointer; }}
  .jtree {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px;
           margin-top:12px; overflow-x:auto; }}
  .j-row {{ padding-left:16px; }}
  .j-key {{ color:#7c3aed; }} .j-str {{ color:#059669; }} .j-num {{ color:#2563eb; }}
  .j-bool {{ color:#d97706; }} .j-null {{ color:#94a3b8; }}
  .j-toggle {{ cursor:pointer; color:#64748b; user-select:none; }}
  .j-node[data-collapsed="1"] > .j-children {{ display:none; }}
  footer {{ text-align:center; color:#94a3b8; font-size:13px; padding:30px; }}
</style>
</head>
<body>
<header class="top">
  <h1>pbg-artistoo — differential-adhesion cell rearrangement</h1>
  <p>Reproducing three simulations from the foundational Cellular Potts Model paper.
     Each is the <strong>real Artistoo simulator</strong> running in a Node.js subprocess,
     driven from Python via <code>CPMSortingProcess</code> — a two-type (light / dark)
     aggregate in a medium, with the paper's full cell-type adhesion matrix and area
     constraint. No motility: fluctuations come from finite-temperature Metropolis dynamics.</p>
  <p class="cite">J. A. Glazier &amp; F. Graner, &ldquo;Simulation of the differential adhesion
     driven rearrangement of biological cells&rdquo;, Phys. Rev. E <strong>47</strong>, 2128 (1993).</p>
</header>
<nav>{nav}</nav>
<main>
{sections}
</main>
<footer>Generated by demo/demo_report.py · pbg-artistoo · real Artistoo (github:ingewortel/artistoo) via Node.js bridge</footer>

<script>
const DATA = {data_json};
const KIND_COLOR = {{1: [245,158,11], 2: [30,58,138]}};   // light=amber, dark=navy

function drawFrame(cid, idx) {{
  const d = DATA[cid];
  const fr = d.frames[idx];
  const cv = document.getElementById('cv_'+cid);
  const ctx = cv.getContext('2d');
  const px = cv.width / fr.w;
  ctx.fillStyle = '#0b1220';
  ctx.fillRect(0,0,cv.width,cv.height);
  // build id grid for border detection
  const W = fr.w, H = fr.h;
  const idg = new Int32Array(W*H);
  for (const c of fr.cells) idg[c[0]*H + c[1]] = c[2];
  for (const [x,y,id,kind] of fr.cells) {{
    const base = KIND_COLOR[kind] || [148,163,184];
    // darken pixels on a cell border so individual cells stay visible
    let border = false;
    if (x+1<W && idg[(x+1)*H+y]!==id) border=true;
    else if (y+1<H && idg[x*H+(y+1)]!==id) border=true;
    else if (x>0 && idg[(x-1)*H+y]!==id) border=true;
    else if (y>0 && idg[x*H+(y-1)]!==id) border=true;
    const f = border ? 0.55 : 1.0;
    ctx.fillStyle = 'rgb('+Math.round(base[0]*f)+','+Math.round(base[1]*f)+','+Math.round(base[2]*f)+')';
    ctx.fillRect(x*px, y*px, Math.ceil(px), Math.ceil(px));
  }}
  document.getElementById('tlab_'+cid).textContent = 't = ' + fr.t;
  document.getElementById('slider_'+cid).value = idx;
}}

function setupViewer(cid) {{
  const d = DATA[cid];
  const slider = document.getElementById('slider_'+cid);
  const btn = document.querySelector('.play[data-cid="'+cid+'"]');
  slider.addEventListener('input', e => drawFrame(cid, +e.target.value));
  let timer=null;
  btn.addEventListener('click', () => {{
    if (timer) {{ clearInterval(timer); timer=null; btn.textContent='▶ play'; return; }}
    btn.textContent='⏸ pause';
    timer = setInterval(() => {{
      const idx = (+slider.value + 1) % d.frames.length;
      drawFrame(cid, idx);
    }}, 180);
  }});
  drawFrame(cid, 0);
}}

function setupChart(cid) {{
  const d = DATA[cid], s = d.series;
  Plotly.newPlot('chart_'+cid, [
    {{x:s.time, y:s.heterotypic_fraction, name:'heterotypic fraction',
      line:{{color:d.accent,width:3}}, mode:'lines+markers', marker:{{size:5}}}},
    {{x:s.time, y:s.mean_connectedness, name:'mean connectedness', yaxis:'y2',
      line:{{color:'#94a3b8',width:2,dash:'dot'}}}},
  ], {{
    margin:{{l:52,r:52,t:10,b:40}}, legend:{{orientation:'h',y:1.16}},
    xaxis:{{title:'Monte-Carlo step'}},
    yaxis:{{title:'heterotypic fraction', rangemode:'tozero'}},
    yaxis2:{{title:'connectedness', overlaying:'y', side:'right', range:[0.8,1.02]}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
  }}, {{displayModeBar:false, responsive:true}});
}}

document.querySelectorAll('.j-toggle').forEach(t => {{
  t.addEventListener('click', () => {{
    const node = t.parentElement;
    node.setAttribute('data-collapsed', node.getAttribute('data-collapsed')==='1'?'0':'1');
  }});
}});

Object.keys(DATA).forEach(cid => {{ setupViewer(cid); setupChart(cid); }});
</script>
</body>
</html>
"""


def main():
    results = []
    for cfg in CONFIGS:
        print(f"running {cfg['id']} ...", flush=True)
        results.append(run_config(cfg))
    OUT.write_text(build_html(results))
    print(f"wrote {OUT}")
    webbrowser.open("file://" + os.path.abspath(OUT))


if __name__ == "__main__":
    main()
