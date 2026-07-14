/*
 * artistoo_bridge.js
 * -------------------
 * Long-lived Node.js bridge driving a REAL Artistoo Cellular Potts Model.
 *
 * This is the genuine upstream simulator (github:ingewortel/artistoo, the
 * `Artistoo` CommonJS bundle) — not a reimplementation. The Python
 * `ArtistooProcess` spawns this script once and speaks a newline-delimited
 * JSON protocol over stdin/stdout:
 *
 *   ->  {"cmd":"init","config":{...}}      build CPM + constraints + seed cells
 *   ->  {"cmd":"step","mcs":N,"params":{...}}  update params, run N Monte-Carlo steps
 *   ->  {"cmd":"grid"}                      return the compact cell-id field
 *   ->  {"cmd":"quit"}                      exit
 *
 * Every reply is a single JSON line: {"ok":true, ...} or {"ok":false,"error":...}.
 *
 * The CPM persists across `step` calls, so time-stepping is genuine — each
 * step continues the same Monte-Carlo trajectory rather than restarting.
 */
"use strict";

const path = require("path");

// Locate the Artistoo CJS bundle relative to this script's node_modules.
function loadArtistoo() {
  const candidates = [
    path.join(__dirname, "node_modules", "Artistoo", "build", "artistoo-cjs.js"),
    // when installed in a shared cache dir
    process.env.ARTISTOO_CJS,
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      return require(c);
    } catch (e) {
      /* try next */
    }
  }
  // last resort: normal module resolution
  return require("Artistoo/build/artistoo-cjs.js");
}

const CPM = loadArtistoo();

// ---- simulation state (module-level; one CPM per bridge process) ----
let C = null;
let gm = null;
let constraints = {}; // name -> constraint instance for runtime param updates
let fieldSize = [50, 50];

function num(v, d) {
  return v === undefined || v === null || Number.isNaN(Number(v)) ? d : Number(v);
}

function buildModel(config) {
  fieldSize = config.field_size || [50, 50];
  const T = num(config.T, 20);
  const seed = num(config.seed, 42);
  const torus = config.torus === undefined ? [true, true] : config.torus;

  // adhesion matrix J: index 0 = background, 1 = cell kind
  const J_bc = num(config.J_bg_cell, 20);
  const J_cc = num(config.J_cell_cell, 0);
  const J = [
    [0, J_bc],
    [J_bc, J_cc],
  ];

  C = new CPM.CPM(fieldSize, { torus: torus, seed: seed, T: T, J: J });

  const adh = new CPM.Adhesion({ J: J });
  C.add(adh);
  constraints.adhesion = adh;

  const vc = new CPM.VolumeConstraint({
    LAMBDA_V: [0, num(config.LAMBDA_V, 50)],
    V: [0, num(config.V, 200)],
  });
  C.add(vc);
  constraints.volume = vc;

  const pc = new CPM.PerimeterConstraint({
    LAMBDA_P: [0, num(config.LAMBDA_P, 2)],
    P: [0, num(config.P, 180)],
  });
  C.add(pc);
  constraints.perimeter = pc;

  // Activity constraint (motility) — the Act model. Optional; only added when
  // it would do something, but kept referenced so `motility` input can raise it.
  const maxAct = num(config.MAX_ACT, 30);
  const lambdaAct = num(config.LAMBDA_ACT, 0);
  const ac = new CPM.ActivityConstraint({
    LAMBDA_ACT: [0, lambdaAct],
    MAX_ACT: [0, maxAct],
    ACT_MEAN: config.ACT_MEAN || "geometric",
  });
  C.add(ac);
  constraints.activity = ac;

  gm = new CPM.GridManipulator(C);

  const nCells = Math.max(0, Math.floor(num(config.n_cells, 1)));
  const layout = config.seed_layout || "random";
  if (layout === "grid") {
    // place on a coarse grid so initial cells are separated deterministically
    const cols = Math.ceil(Math.sqrt(nCells));
    const dx = Math.floor(fieldSize[0] / (cols + 1));
    const dy = Math.floor(fieldSize[1] / (cols + 1));
    let placed = 0;
    for (let r = 1; r <= cols && placed < nCells; r++) {
      for (let c = 1; c <= cols && placed < nCells; c++) {
        gm.seedCellAt(1, [c * dx, r * dy]);
        placed++;
      }
    }
  } else {
    for (let i = 0; i < nCells; i++) gm.seedCell(1);
  }
}

// Apply runtime parameter overrides sent with a `step` command.
function applyParams(params) {
  if (!params) return;
  if (params.T !== undefined) C.T = Number(params.T);
  if (params.V !== undefined && constraints.volume)
    constraints.volume.conf.V[1] = Number(params.V);
  if (params.LAMBDA_V !== undefined && constraints.volume)
    constraints.volume.conf.LAMBDA_V[1] = Number(params.LAMBDA_V);
  if (params.P !== undefined && constraints.perimeter)
    constraints.perimeter.conf.P[1] = Number(params.P);
  if (params.LAMBDA_P !== undefined && constraints.perimeter)
    constraints.perimeter.conf.LAMBDA_P[1] = Number(params.LAMBDA_P);
  if (params.LAMBDA_ACT !== undefined && constraints.activity)
    constraints.activity.conf.LAMBDA_ACT[1] = Number(params.LAMBDA_ACT);
}

function computeStats() {
  const pix = C.getStat(CPM.PixelsByCell);
  const centroids = C.getStat(CPM.CentroidsWithTorusCorrection);
  let conn = {};
  try {
    conn = C.getStat(CPM.Connectedness);
  } catch (e) {
    conn = {};
  }
  let border = {};
  try {
    border = C.getStat(CPM.BorderPixelsByCell);
  } catch (e) {
    border = {};
  }

  const volumes = {};
  const perimeters = {};
  const cents = {};
  let totalVolume = 0;
  let totalPerimeter = 0;
  let connSum = 0;
  let nCells = 0;

  for (const cid in pix) {
    const v = pix[cid].length;
    volumes[cid] = v;
    totalVolume += v;
    const p = border[cid] ? border[cid].length : 0;
    perimeters[cid] = p;
    totalPerimeter += p;
    if (centroids[cid]) cents[cid] = centroids[cid];
    connSum += conn[cid] === undefined ? 1 : conn[cid];
    nCells += 1;
  }

  return {
    time: C.time,
    cell_count: nCells,
    total_volume: totalVolume,
    total_perimeter: totalPerimeter,
    mean_connectedness: nCells > 0 ? connSum / nCells : 0.0,
    centroids: cents,
    volumes: volumes,
    perimeters: perimeters,
    kinds: Object.fromEntries(Object.keys(pix).map((cid) => [cid, C.cellKind(cid)])),
  };
}

// Compact cell-id field for visualization: only non-background pixels.
function computeGrid() {
  const cells = [];
  for (let x = 0; x < fieldSize[0]; x++) {
    for (let y = 0; y < fieldSize[1]; y++) {
      const id = C.pixt([x, y]);
      if (id !== 0) cells.push([x, y, id]);
    }
  }
  return { field_size: fieldSize, cells: cells };
}

// ---- newline-JSON command loop ----
let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let idx;
  while ((idx = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 1);
    if (line.trim() === "") continue;
    handle(line);
  }
});

function reply(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function handle(line) {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch (e) {
    reply({ ok: false, error: "bad json: " + e.message });
    return;
  }
  try {
    switch (msg.cmd) {
      case "init":
        buildModel(msg.config || {});
        reply({ ok: true, stats: computeStats() });
        break;
      case "step": {
        applyParams(msg.params);
        const mcs = Math.max(0, Math.floor(num(msg.mcs, 1)));
        for (let i = 0; i < mcs; i++) C.monteCarloStep();
        reply({ ok: true, stats: computeStats() });
        break;
      }
      case "stats":
        reply({ ok: true, stats: computeStats() });
        break;
      case "grid":
        reply({ ok: true, grid: computeGrid() });
        break;
      case "ping":
        reply({ ok: true, pong: true, artistoo: true });
        break;
      case "quit":
        reply({ ok: true, bye: true });
        process.exit(0);
        break;
      default:
        reply({ ok: false, error: "unknown cmd: " + msg.cmd });
    }
  } catch (e) {
    reply({ ok: false, error: String(e && e.stack ? e.stack : e) });
  }
}

process.stdin.on("end", () => process.exit(0));
