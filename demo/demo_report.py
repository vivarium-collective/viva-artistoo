#!/usr/bin/env python
"""Generate a self-contained interactive demo report for pbg-artistoo.

Runs three real Artistoo Cellular Potts Model configurations through
process-bigraph Composites, captures time series + spatial cell-field
snapshots, and renders demo/report.html with:

  * sticky navigation + metric cards
  * Plotly time-series charts (volume, cell count, connectedness)
  * an animated 2D cell-field viewer (canvas, time slider, play/pause)
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

from process_bigraph import Composite, allocate_core

from pbg_artistoo import ArtistooProcess
from pbg_artistoo.composites.cell_migration import (
    artistoo_cell_migration,
    artistoo_cell_sorting,
)

try:
    from bigraph_viz2 import emit_html as bgv_emit_html
except Exception:  # pragma: no cover
    bgv_emit_html = None

HERE = Path(__file__).resolve().parent
OUT = HERE / "report.html"

CONFIGS = [
    {
        "id": "migration",
        "title": "Cell migration (Act model)",
        "subtitle": "Adhesion + volume + perimeter + activity",
        "accent": "#2563eb",
        "description": (
            "Motile cells crawl across the lattice driven by the activity "
            "constraint. The activity strength (motility) is a live input a "
            "sibling process could modulate."
        ),
        "doc": lambda: artistoo_cell_migration(
            n_cells=6, field_size=70, target_volume=200,
            lambda_act=220, max_act=40, temperature=20, interval=1.0, seed=42),
        "snapshots": 24,
        "steps_per_snapshot": 3,
    },
    {
        "id": "sorting",
        "title": "Differential-adhesion sorting",
        "subtitle": "Non-motile sticky cells relax to an aggregate",
        "accent": "#059669",
        "description": (
            "With activity switched off and low cell-cell contact energy, "
            "adhesion drives the population toward a compact, well-connected "
            "aggregate."
        ),
        "doc": lambda: artistoo_cell_sorting(
            n_cells=14, field_size=75, target_volume=120,
            adhesion_cell_cell=4, interval=1.0, seed=7),
        "snapshots": 24,
        "steps_per_snapshot": 3,
    },
    {
        "id": "fluid",
        "title": "High-temperature fluid regime",
        "subtitle": "Raised Metropolis T destabilizes membranes",
        "accent": "#d97706",
        "description": (
            "A high temperature setpoint makes cell boundaries fluctuate "
            "strongly — cells struggle to hold shape, illustrating the "
            "temperature input port's effect on the real Hamiltonian dynamics."
        ),
        "doc": lambda: artistoo_cell_migration(
            n_cells=6, field_size=70, target_volume=200,
            lambda_act=120, max_act=30, temperature=40, interval=1.0, seed=13),
        "snapshots": 24,
        "steps_per_snapshot": 3,
    },
]


def run_config(cfg):
    core = allocate_core()
    core.register_link("ArtistooProcess", ArtistooProcess)
    doc = cfg["doc"]()
    sim = Composite({"state": doc}, core=core)
    proc = sim.state["cpm"]["instance"]

    frames = []
    series = {"time": [], "total_volume": [], "cell_count": [],
              "mean_connectedness": [], "total_perimeter": []}
    per_cell = {}  # cell_id -> {"t":[], "v":[]}

    t0 = time.perf_counter()
    steps = cfg["steps_per_snapshot"]
    for i in range(cfg["snapshots"]):
        sim.run(float(steps))
        r = sim.state["readouts"]
        t = (i + 1) * steps
        series["time"].append(t)
        series["total_volume"].append(round(float(r["total_volume"]), 2))
        series["cell_count"].append(int(r["cell_count"]))
        series["mean_connectedness"].append(round(float(r["mean_connectedness"]), 4))
        series["total_perimeter"].append(round(float(r["total_perimeter"]), 2))
        for cid, v in r["cell_volumes"].items():
            per_cell.setdefault(cid, {"t": [], "v": []})
            per_cell[cid]["t"].append(t)
            per_cell[cid]["v"].append(round(float(v), 1))
        grid = proc.get_grid()
        frames.append({"t": t, "w": grid["field_size"][0],
                       "h": grid["field_size"][1], "cells": grid["cells"]})
    elapsed = time.perf_counter() - t0

    # architecture diagram from the composite document (whole doc so wires resolve)
    diagram = ""
    if bgv_emit_html is not None:
        try:
            diagram = bgv_emit_html(doc, height="440px", inspector=True,
                                    dedupe=(cfg["id"] != CONFIGS[0]["id"]),
                                    id=f"bgv_{cfg['id']}")
        except Exception as e:  # pragma: no cover
            diagram = f"<p class='muted'>diagram unavailable: {html.escape(str(e))}</p>"

    proc.close()
    final = sim.state["readouts"]
    return {
        "cfg": cfg,
        "series": series,
        "per_cell": per_cell,
        "frames": frames,
        "diagram": diagram,
        "elapsed": elapsed,
        "final_cells": int(final["cell_count"]),
        "final_volume": round(float(final["total_volume"]), 1),
        "doc": doc,
    }


# ---- HTML rendering -------------------------------------------------------

def json_tree(obj, depth=0):
    """Collapsible JSON tree; depth>=2 collapsed by default."""
    ind = "  " * depth
    if isinstance(obj, dict):
        if not obj:
            return "<span class='j-brace'>{}</span>"
        open_attr = "" if depth < 2 else " data-collapsed='1'"
        rows = []
        for k, v in obj.items():
            rows.append(
                f"<div class='j-row'><span class='j-key'>{html.escape(str(k))}</span>"
                f"<span class='j-colon'>: </span>{json_tree(v, depth + 1)}</div>"
            )
        inner = "".join(rows)
        return (f"<span class='j-node'{open_attr}><span class='j-toggle'>{{…}}</span>"
                f"<div class='j-children'>{inner}</div></span>")
    if isinstance(obj, list):
        if len(obj) <= 6 and all(isinstance(x, (int, float, str, bool)) for x in obj):
            return "<span class='j-brack'>[" + ", ".join(
                _leaf(x) for x in obj) + "]</span>"
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
    """Strip live instances so the document is JSON-serializable for the tree."""
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if k != "instance"}
        if isinstance(o, list):
            return [clean(v) for v in o]
        if isinstance(o, (int, float, str, bool)) or o is None:
            return o
        return str(o)
    return clean(doc)


def build_html(results):
    payload = {
        r["cfg"]["id"]: {
            "series": r["series"],
            "per_cell": r["per_cell"],
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
        doc_tree = json_tree(sanitize_doc(r["doc"]))
        sections.append(f"""
        <section id="{cid}" style="--accent:{c['accent']}">
          <div class="sec-head">
            <h2>{html.escape(c['title'])}</h2>
            <p class="subtitle">{html.escape(c['subtitle'])}</p>
            <p class="desc">{html.escape(c['description'])}</p>
          </div>
          <div class="metrics">
            <div class="metric"><div class="m-val">{r['final_cells']}</div><div class="m-lab">cells</div></div>
            <div class="metric"><div class="m-val">{r['final_volume']:.0f}</div><div class="m-lab">total volume (sites)</div></div>
            <div class="metric"><div class="m-val">{len(r['frames'])}</div><div class="m-lab">snapshots</div></div>
            <div class="metric"><div class="m-val">{r['elapsed']:.2f}s</div><div class="m-lab">wall-clock</div></div>
          </div>

          <div class="grid2">
            <div class="card">
              <h3>Cell field <span class="muted">(live Artistoo lattice)</span></h3>
              <canvas id="cv_{cid}" width="360" height="360"></canvas>
              <div class="viewer-ctrl">
                <button class="play" data-cid="{cid}">▶ play</button>
                <input type="range" id="slider_{cid}" min="0" max="{len(r['frames'])-1}" value="0">
                <span class="tlabel" id="tlab_{cid}">t = 0</span>
              </div>
            </div>
            <div class="card">
              <h3>Total volume &amp; connectedness</h3>
              <div id="chart_{cid}" style="height:300px"></div>
            </div>
          </div>

          <div class="card">
            <h3>Per-cell volume trajectories</h3>
            <div id="pcell_{cid}" style="height:280px"></div>
          </div>

          <div class="card">
            <h3>Bigraph architecture</h3>
            {r['diagram'] or "<p class='muted'>bigraph-viz2 not installed</p>"}
          </div>

          <details class="card">
            <summary><h3 style="display:inline">PBG document</h3></summary>
            <div class="jtree">{doc_tree}</div>
          </details>
        </section>
        """)

    return TEMPLATE.format(
        nav=nav,
        sections="\n".join(sections),
        data_json=data_json,
    )


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pbg-artistoo — Cellular Potts Model demo</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         color:#1f2937; background:#f8fafc; line-height:1.5; }}
  header.top {{ background:#0f172a; color:#fff; padding:34px 24px; }}
  header.top h1 {{ margin:0 0 6px; font-size:26px; }}
  header.top p {{ margin:0; color:#cbd5e1; max-width:760px; }}
  header.top code {{ background:#1e293b; padding:2px 6px; border-radius:4px; color:#93c5fd; }}
  nav {{ position:sticky; top:0; z-index:10; background:#fff; border-bottom:1px solid #e2e8f0;
        padding:10px 24px; display:flex; gap:18px; flex-wrap:wrap; box-shadow:0 1px 3px rgba(0,0,0,.04); }}
  nav a {{ color:#334155; text-decoration:none; font-weight:600; font-size:14px; }}
  nav a:hover {{ color:#2563eb; }}
  main {{ max-width:1080px; margin:0 auto; padding:24px; }}
  section {{ margin-bottom:52px; }}
  .sec-head h2 {{ margin:0 0 4px; font-size:22px; border-left:5px solid var(--accent); padding-left:12px; }}
  .subtitle {{ margin:0 0 6px 17px; color:var(--accent); font-weight:600; }}
  .desc {{ margin:0 0 16px 17px; color:#475569; max-width:760px; }}
  .metrics {{ display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px; }}
  .metric {{ background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:14px 20px;
            min-width:120px; text-align:center; }}
  .m-val {{ font-size:24px; font-weight:700; color:var(--accent); }}
  .m-lab {{ font-size:12px; color:#64748b; text-transform:uppercase; letter-spacing:.03em; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-bottom:18px; }}
  @media (max-width:820px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
  .card {{ background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:18px; margin-bottom:18px; }}
  .card h3 {{ margin:0 0 12px; font-size:16px; }}
  .muted {{ color:#94a3b8; font-weight:400; font-size:13px; }}
  canvas {{ width:100%; max-width:360px; image-rendering:pixelated; border:1px solid #e2e8f0;
           border-radius:8px; background:#0f172a; display:block; margin:0 auto; }}
  .viewer-ctrl {{ display:flex; align-items:center; gap:10px; margin-top:12px; }}
  .viewer-ctrl input[type=range] {{ flex:1; accent-color:var(--accent); }}
  .viewer-ctrl button {{ background:var(--accent); color:#fff; border:none; border-radius:6px;
                        padding:6px 12px; cursor:pointer; font-weight:600; }}
  .tlabel {{ font-variant-numeric:tabular-nums; color:#475569; font-size:13px; min-width:56px; }}
  details.card summary {{ cursor:pointer; }}
  .jtree {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px;
           margin-top:12px; overflow-x:auto; }}
  .j-row {{ padding-left:16px; }}
  .j-key {{ color:#7c3aed; }}
  .j-str {{ color:#059669; }}
  .j-num {{ color:#2563eb; }}
  .j-bool {{ color:#d97706; }}
  .j-null {{ color:#94a3b8; }}
  .j-toggle {{ cursor:pointer; color:#64748b; user-select:none; }}
  .j-children {{ }}
  .j-node[data-collapsed="1"] > .j-children {{ display:none; }}
  footer {{ text-align:center; color:#94a3b8; font-size:13px; padding:30px; }}
</style>
</head>
<body>
<header class="top">
  <h1>pbg-artistoo</h1>
  <p>process-bigraph wrapper for the <strong>Artistoo</strong> Cellular Potts Model.
     Each simulation below is the <strong>real Artistoo simulator</strong> running in a
     Node.js subprocess, driven step-by-step from Python via
     <code>ArtistooProcess</code>.</p>
</header>
<nav>{nav}</nav>
<main>
{sections}
</main>
<footer>Generated by demo/demo_report.py · pbg-artistoo · real Artistoo (github:ingewortel/artistoo) via Node.js bridge</footer>

<script>
const DATA = {data_json};

// sequential blue-cyan-green-yellow-red colormap indexed by cell rank
function palette(n) {{
  const stops = [[37,99,235],[6,182,212],[16,185,129],[234,179,8],[239,68,68]];
  const out = [];
  for (let i=0;i<n;i++) {{
    const f = n<=1 ? 0 : i/(n-1);
    const s = f*(stops.length-1), lo=Math.floor(s), hi=Math.min(lo+1,stops.length-1), t=s-lo;
    const c = stops[lo].map((v,k)=> Math.round(v+(stops[hi][k]-v)*t));
    out.push('rgb('+c[0]+','+c[1]+','+c[2]+')');
  }}
  return out;
}}

function idColorMap(frames) {{
  const ids = new Set();
  frames.forEach(fr => fr.cells.forEach(c => ids.add(c[2])));
  const sorted = [...ids].sort((a,b)=>a-b);
  const pal = palette(sorted.length);
  const map = {{}};
  sorted.forEach((id,i)=> map[id]=pal[i]);
  return map;
}}

function drawFrame(cid, idx) {{
  const d = DATA[cid];
  const fr = d.frames[idx];
  const cv = document.getElementById('cv_'+cid);
  const ctx = cv.getContext('2d');
  const px = cv.width / fr.w;
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0,0,cv.width,cv.height);
  if (!d._cmap) d._cmap = idColorMap(d.frames);
  for (const [x,y,id] of fr.cells) {{
    ctx.fillStyle = d._cmap[id] || '#fff';
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
  let timer=null, idx=0;
  btn.addEventListener('click', () => {{
    if (timer) {{ clearInterval(timer); timer=null; btn.textContent='▶ play'; return; }}
    btn.textContent='⏸ pause';
    timer = setInterval(() => {{
      idx = (+slider.value + 1) % d.frames.length;
      drawFrame(cid, idx);
      if (idx === d.frames.length-1) {{ /* loop */ }}
    }}, 160);
  }});
  drawFrame(cid, 0);
}}

function setupCharts(cid) {{
  const d = DATA[cid], s = d.series;
  Plotly.newPlot('chart_'+cid, [
    {{x:s.time, y:s.total_volume, name:'total volume', line:{{color:d.accent,width:2.5}}}},
    {{x:s.time, y:s.mean_connectedness, name:'mean connectedness', yaxis:'y2',
      line:{{color:'#94a3b8',width:2,dash:'dot'}}}},
  ], {{
    margin:{{l:48,r:48,t:10,b:36}}, legend:{{orientation:'h',y:1.15}},
    xaxis:{{title:'Monte-Carlo step'}}, yaxis:{{title:'sites'}},
    yaxis2:{{title:'connectedness', overlaying:'y', side:'right', range:[0,1.05]}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
  }}, {{displayModeBar:false, responsive:true}});

  const traces = Object.entries(d.per_cell).map(([id,pc]) => (
    {{x:pc.t, y:pc.v, name:'cell '+id, mode:'lines'}}
  ));
  Plotly.newPlot('pcell_'+cid, traces, {{
    margin:{{l:48,r:16,t:10,b:36}}, showlegend:true,
    xaxis:{{title:'Monte-Carlo step'}}, yaxis:{{title:'cell volume (sites)'}},
    paper_bgcolor:'#fff', plot_bgcolor:'#fff',
  }}, {{displayModeBar:false, responsive:true}});
}}

document.querySelectorAll('.j-toggle').forEach(t => {{
  t.addEventListener('click', () => {{
    const node = t.parentElement;
    node.setAttribute('data-collapsed', node.getAttribute('data-collapsed')==='1'?'0':'1');
  }});
}});

Object.keys(DATA).forEach(cid => {{ setupViewer(cid); setupCharts(cid); }});
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
