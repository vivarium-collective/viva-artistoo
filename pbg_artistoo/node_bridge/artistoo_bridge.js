/*
 * artistoo_bridge.js
 * -------------------
 * Long-lived Node.js bridge driving a REAL Artistoo Cellular Potts Model.
 *
 * This is the genuine upstream simulator (github:ingewortel/artistoo, the
 * `Artistoo` CommonJS bundle) — not a reimplementation. A Python process
 * spawns this script once and speaks a newline-delimited JSON protocol over
 * stdin/stdout:
 *
 *   ->  {"cmd":"init","config":{...}}          build CPM + constraints + seed cells
 *   ->  {"cmd":"step","mcs":N,"params":{...}}   update params, run N Monte-Carlo steps
 *   ->  {"cmd":"grid"}                          return the compact cell-id field
 *   ->  {"cmd":"quit"}                          exit
 *
 * Every reply is a single JSON line: {"ok":true, ...} or {"ok":false,"error":...}.
 *
 * Two build modes:
 *   - LEGACY single cell kind (config has no `kinds`): background + one cell
 *     type, 1-pixel random/grid seeding. Used by ArtistooProcess.
 *   - MULTI kind (config.kinds is an array): index 0 = medium/background,
 *     kinds 1..n from `kinds`, a full (n+1)x(n+1) adhesion matrix `J`, explicit
 *     block seeds, and boundary-length statistics (homotypic vs heterotypic).
 *     Used by CPMSortingProcess for the Glazier & Graner (1993) simulations.
 *
 * The CPM persists across `step` calls, so time-stepping is genuine.
 */
"use strict";

const path = require("path");

function loadArtistoo() {
  const candidates = [
    path.join(__dirname, "node_modules", "Artistoo", "build", "artistoo-cjs.js"),
    process.env.ARTISTOO_CJS,
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      return require(c);
    } catch (e) {
      /* try next */
    }
  }
  return require("Artistoo/build/artistoo-cjs.js");
}

const CPM = loadArtistoo();

// ---- simulation state (one CPM per bridge process) ----
let C = null;
let gm = null;
let constraints = {};
let fieldSize = [50, 50];
let torus = [true, true];
let nKinds = 1;
let computeBoundaryFlag = false;

function num(v, d) {
  return v === undefined || v === null || Number.isNaN(Number(v)) ? d : Number(v);
}

// ---- LEGACY single-kind model (ArtistooProcess) ----
function buildLegacy(config) {
  fieldSize = config.field_size || [50, 50];
  torus = config.torus === undefined ? [true, true] : config.torus;
  nKinds = 1;
  computeBoundaryFlag = false;

  const J_bc = num(config.J_bg_cell, 20);
  const J_cc = num(config.J_cell_cell, 0);
  const J = [
    [0, J_bc],
    [J_bc, J_cc],
  ];

  C = new CPM.CPM(fieldSize, {
    torus: torus,
    seed: num(config.seed, 42),
    T: num(config.T, 20),
    J: J,
  });

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

  const ac = new CPM.ActivityConstraint({
    LAMBDA_ACT: [0, num(config.LAMBDA_ACT, 0)],
    MAX_ACT: [0, num(config.MAX_ACT, 30)],
    ACT_MEAN: config.ACT_MEAN || "geometric",
  });
  C.add(ac);
  constraints.activity = ac;

  gm = new CPM.GridManipulator(C);

  const nCells = Math.max(0, Math.floor(num(config.n_cells, 1)));
  const layout = config.seed_layout || "random";
  if (layout === "grid") {
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

// ---- MULTI-kind model (CPMSortingProcess; Glazier & Graner) ----
function seedBlock(kind, cx, cy, half) {
  const id = C.makeNewCellID(kind);
  for (let x = cx - half; x <= cx + half; x++) {
    for (let y = cy - half; y <= cy + half; y++) {
      let px = x, py = y;
      if (torus[0]) px = ((x % fieldSize[0]) + fieldSize[0]) % fieldSize[0];
      if (torus[1]) py = ((y % fieldSize[1]) + fieldSize[1]) % fieldSize[1];
      if (px >= 0 && px < fieldSize[0] && py >= 0 && py < fieldSize[1]) {
        C.setpix([px, py], id);
      }
    }
  }
  return id;
}

function buildMulti(config) {
  fieldSize = config.field_size || [60, 60];
  torus = config.torus === undefined ? [false, false] : config.torus;
  const kinds = config.kinds; // 1-indexed list of kind params
  nKinds = kinds.length;
  computeBoundaryFlag = config.compute_boundary !== false;

  const J = config.J; // (nKinds+1) x (nKinds+1)

  C = new CPM.CPM(fieldSize, {
    torus: torus,
    seed: num(config.seed, 1),
    T: num(config.T, 10),
    J: J,
  });

  const adh = new CPM.Adhesion({ J: J });
  C.add(adh);
  constraints.adhesion = adh;

  const V = [0], LAMBDA_V = [0], P = [0], LAMBDA_P = [0];
  const MAX_ACT = [0], LAMBDA_ACT = [0];
  let anyPerim = false, anyAct = false;
  for (const k of kinds) {
    V.push(num(k.V, 40));
    LAMBDA_V.push(num(k.LAMBDA_V, 1));
    P.push(num(k.P, 0));
    LAMBDA_P.push(num(k.LAMBDA_P, 0));
    if (num(k.LAMBDA_P, 0) > 0) anyPerim = true;
    MAX_ACT.push(num(k.MAX_ACT, 0));
    LAMBDA_ACT.push(num(k.LAMBDA_ACT, 0));
    if (num(k.LAMBDA_ACT, 0) > 0) anyAct = true;
  }

  const vc = new CPM.VolumeConstraint({ LAMBDA_V: LAMBDA_V, V: V });
  C.add(vc);
  constraints.volume = vc;

  if (anyPerim) {
    const pc = new CPM.PerimeterConstraint({ LAMBDA_P: LAMBDA_P, P: P });
    C.add(pc);
    constraints.perimeter = pc;
  }
  if (anyAct) {
    const ac = new CPM.ActivityConstraint({
      LAMBDA_ACT: LAMBDA_ACT,
      MAX_ACT: MAX_ACT,
      ACT_MEAN: config.ACT_MEAN || "geometric",
    });
    C.add(ac);
    constraints.activity = ac;
  }

  gm = new CPM.GridManipulator(C);

  // explicit block seeds computed by the Python side: [kind, x, y, half]
  for (const s of config.seeds || []) {
    seedBlock(s[0], s[1], s[2], s[3]);
  }
}

// Apply runtime parameter overrides sent with a `step` command.
function applyParams(params) {
  if (!params) return;
  if (params.T !== undefined) C.T = Number(params.T);
  // legacy single-kind knobs
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
  // multi-kind per-kind volume-target overrides: {kind_V:{1:.., 2:..}}
  if (params.kind_V && constraints.volume) {
    for (const k in params.kind_V) constraints.volume.conf.V[+k] = Number(params.kind_V[k]);
  }
}

function computeBoundary() {
  // Second-nearest-neighbour bond scan (paper uses the 8-neighbourhood).
  // Count only unique bonds via forward offsets. Classify by cell type.
  const W = fieldSize[0], H = fieldSize[1];
  const off = [[1, 0], [0, 1], [1, 1], [1, -1]];
  let hetero = 0, cellMedium = 0;
  const homo = {}; // kind -> homotypic bonds
  for (let x = 0; x < W; x++) {
    for (let y = 0; y < H; y++) {
      const a = C.pixt([x, y]);
      const ka = a === 0 ? 0 : C.cellKind(a);
      for (const [dx, dy] of off) {
        let nx = x + dx, ny = y + dy;
        if (torus[0]) nx = ((nx % W) + W) % W;
        if (torus[1]) ny = ((ny % H) + H) % H;
        if (nx < 0 || nx >= W || ny < 0 || ny >= H) continue;
        const b = C.pixt([nx, ny]);
        if (b === a) continue; // same cell, interior bond
        const kb = b === 0 ? 0 : C.cellKind(b);
        if (ka === 0 || kb === 0) {
          cellMedium += 1; // cell-medium boundary
        } else if (ka !== kb) {
          hetero += 1; // heterotypic cell-cell boundary
        } else {
          homo[ka] = (homo[ka] || 0) + 1; // homotypic cell-cell boundary
        }
      }
    }
  }
  let homoTotal = 0;
  for (const k in homo) homoTotal += homo[k];
  const cellCell = hetero + homoTotal;
  return {
    hetero: hetero,
    homo: homo,
    homo_total: homoTotal,
    cell_medium: cellMedium,
    total: hetero + homoTotal + cellMedium,
    heterotypic_fraction: cellCell > 0 ? hetero / cellCell : 0.0,
  };
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
  const cents = {};
  const kinds = {};
  const countsByKind = {};
  let totalVolume = 0;
  let totalPerimeter = 0;
  let connSum = 0;
  let nCells = 0;

  for (const cid in pix) {
    const v = pix[cid].length;
    volumes[cid] = v;
    totalVolume += v;
    totalPerimeter += border[cid] ? border[cid].length : 0;
    if (centroids[cid]) cents[cid] = centroids[cid];
    const k = C.cellKind(cid);
    kinds[cid] = k;
    countsByKind[k] = (countsByKind[k] || 0) + 1;
    connSum += conn[cid] === undefined ? 1 : conn[cid];
    nCells += 1;
  }

  const out = {
    time: C.time,
    cell_count: nCells,
    counts_by_kind: countsByKind,
    total_volume: totalVolume,
    total_perimeter: totalPerimeter,
    mean_connectedness: nCells > 0 ? connSum / nCells : 0.0,
    centroids: cents,
    volumes: volumes,
    kinds: kinds,
  };
  if (computeBoundaryFlag) out.boundary = computeBoundary();
  return out;
}

function computeGrid() {
  const cells = [];
  for (let x = 0; x < fieldSize[0]; x++) {
    for (let y = 0; y < fieldSize[1]; y++) {
      const id = C.pixt([x, y]);
      if (id !== 0) cells.push([x, y, id, C.cellKind(id)]);
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
      case "init": {
        const config = msg.config || {};
        constraints = {};
        if (Array.isArray(config.kinds)) buildMulti(config);
        else buildLegacy(config);
        reply({ ok: true, stats: computeStats() });
        break;
      }
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
