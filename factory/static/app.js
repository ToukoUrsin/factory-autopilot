// Factory Autopilot on the real plant-room scan (SuperSplat scene 8be1404d).
// The splat is rendered by @playcanvas/supersplat-viewer; robots, cameras, sensors and valve
// actuators are PlayCanvas entities placed at surveyed equipment positions (see scene-map.js).
import { createViewer, pc } from './vendor/splat-viewer.js';
import { GRID, STATION_MAP, CAMERA_MAP } from './scene-map.js';

const $ = (id) => document.getElementById(id);
const SPLAT_URL = 'https://d28zzqy0iyovbz.cloudfront.net/8be1404d/v1/meta.json';
const POSTER_URL = 'https://s3-eu-west-1.amazonaws.com/images.playcanvas.com/splat/8be1404d/v1/xl.webp';
const STATION_IDS = Object.keys(STATION_MAP);
const FLOOR = -3.95;
const ov = new URLSearchParams(location.search).get('ov')?.split(',').map(Number);
const OVERVIEW = ov?.length === 6 ? { position: ov.slice(0, 3), target: ov.slice(3), fov: 70 } : { position: [-8, 4.5, -8], target: [-20, -3, -20], fov: 70 };
const ARM_SPEED = 9; // world units per second
const params = new URLSearchParams(location.search);

const state = {
  data: null, policy: 'learned', run: null, idx: 0, simT: 0, playing: false,
  incident: null, focus: null, stationStatus: {}, values: {}, view: params.get('view'),
};

// ---------- navigation (A* on Phylax's clearance-expanded walk grid) ----------
const { origin: GO, cellSize: GC, rows: GR } = GRID;
const W = GR[0].length, H = GR.length;
const cellOf = (x, z) => [Math.floor((x - GO[0]) / GC), Math.floor((z - GO[1]) / GC)];
const centre = (i, j) => [GO[0] + (i + 0.5) * GC, GO[1] + (j + 0.5) * GC];
const open = (i, j) => j >= 0 && j < H && i >= 0 && i < W && GR[j][i] === '.';
function clearLine(a, b) {
  const n = Math.max(2, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 0.12));
  for (let k = 0; k <= n; k++) { const [i, j] = cellOf(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n); if (!open(i, j)) return false; }
  return true;
}
function findPath(from, to) {
  const [si, sj] = cellOf(...from), [ti, tj] = cellOf(...to);
  if (!open(si, sj) || !open(ti, tj)) return [from, to];
  const key = (i, j) => j * W + i, came = new Map([[key(si, sj), -1]]), g = new Map([[key(si, sj), 0]]);
  const heap = [[0, si, sj]];
  const push = (n) => { heap.push(n); let c = heap.length - 1; while (c > 0) { const p = (c - 1) >> 1; if (heap[p][0] <= heap[c][0]) break; [heap[p], heap[c]] = [heap[c], heap[p]]; c = p; } };
  const pop = () => { const top = heap[0], last = heap.pop(); if (heap.length) { heap[0] = last; let c = 0; for (;;) { const l = 2 * c + 1, r = l + 1; let m = c; if (l < heap.length && heap[l][0] < heap[m][0]) m = l; if (r < heap.length && heap[r][0] < heap[m][0]) m = r; if (m === c) break; [heap[m], heap[c]] = [heap[c], heap[m]]; c = m; } } return top; };
  while (heap.length) {
    const [, i, j] = pop();
    if (i === ti && j === tj) break;
    for (const [di, dj, cost] of [[1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, 1.414], [1, -1, 1.414], [-1, 1, 1.414], [-1, -1, 1.414]]) {
      const ni = i + di, nj = j + dj;
      if (!open(ni, nj) || (di && dj && (!open(i + di, j) || !open(i, j + dj)))) continue;
      const ng = g.get(key(i, j)) + cost, k = key(ni, nj);
      if (ng < (g.get(k) ?? Infinity)) { g.set(k, ng); came.set(k, key(i, j)); push([ng + Math.hypot(ti - ni, tj - nj), ni, nj]); }
    }
  }
  if (!came.has(key(ti, tj))) return [from, to];
  const cells = []; for (let k = key(ti, tj); k !== -1; k = came.get(k)) cells.push(centre(k % W, Math.floor(k / W)));
  cells.reverse(); cells[0] = from; cells[cells.length - 1] = to;
  const out = [cells[0]]; let a = 0; // string-pull
  while (a < cells.length - 1) { let b = cells.length - 1; while (b > a + 1 && !clearLine(cells[a], cells[b])) b--; out.push(cells[b]); a = b; }
  return out;
}

// ---------- scene objects ----------
let viewer, app, camEntity;
const stations = {}, arms = {}, cams = {};

function material(hex, { emissive = 0, opacity = 1, metal = 0.6, gloss = 0.6, additive = false } = {}) {
  const c = new pc.Color().fromString(hex), m = new pc.StandardMaterial();
  m.diffuse = c; m.emissive = new pc.Color(c.r * emissive, c.g * emissive, c.b * emissive);
  m.useMetalness = true; m.metalness = metal; m.gloss = gloss;
  if (opacity < 1 || additive) { m.opacity = opacity; m.blendType = additive ? pc.BLEND_ADDITIVE : pc.BLEND_NORMAL; m.depthWrite = false; m.cull = pc.CULLFACE_NONE; }
  m.update(); return m;
}
function part(parent, type, mat, pos = [0, 0, 0], scale = [1, 1, 1], euler = [0, 0, 0]) {
  const e = new pc.Entity(); e.addComponent('render', { type, material: mat, castShadows: false });
  e.setLocalPosition(...pos); e.setLocalScale(...scale); e.setLocalEulerAngles(...euler); parent.addChild(e); return e;
}
let ringMesh = null;
function ringPart(parent, mat, pos, scale = 1) {
  ringMesh ??= pc.Mesh.fromGeometry(app.graphicsDevice, new pc.TorusGeometry({ tubeRadius: 0.035, ringRadius: 1, segments: 64, sides: 8 }));
  const e = new pc.Entity(); e.addComponent('render', { meshInstances: [new pc.MeshInstance(ringMesh, mat)], castShadows: false });
  e.setLocalPosition(...pos); e.setLocalScale(scale, scale, scale); parent.addChild(e); return e;
}
function node(parent, pos = [0, 0, 0]) { const e = new pc.Entity(); e.setLocalPosition(...pos); parent.addChild(e); return e; }

const M = {};
function buildMaterials() {
  Object.assign(M, {
    chassis: material('#2a3134', { metal: 0.7, gloss: 0.55 }), orange: material('#f08a2a', { emissive: 0.08, metal: 0.35, gloss: 0.7 }),
    grey: material('#c9d0d2', { metal: 0.8, gloss: 0.7 }), dark: material('#15191b', { metal: 0.4, gloss: 0.4 }),
    mint: material('#f2f2f2', { emissive: 1.1, metal: 0 }), amber: material('#ffc766', { emissive: 1.4, metal: 0 }), red: material('#ff5a1f', { emissive: 1.6, metal: 0 }),
    blue: material('#8cbcff', { emissive: 1.2, metal: 0 }), beamMint: material('#ffffff', { opacity: 0.55, emissive: 0.8 }),
    beamRed: material('#ff5a1f', { opacity: 0.9, emissive: 1.2 }), cone: material('#ffffff', { opacity: 0.08, emissive: 1, additive: true }),
    coneHot: material('#cfe2ff', { opacity: 0.35, emissive: 1.5, additive: true }), pad: material('#ff5a1f', { opacity: 0.05, emissive: 1, additive: true }), valve: material('#d6312f', { emissive: 0.25, metal: 0.4, gloss: 0.6 }),
  });
}
const statusMat = (s) => s === 'alarm' || s === 'blocked' ? M.red : s === 'warn' ? M.amber : M.mint;

function buildStation(id) {
  const s = STATION_MAP[id], a = s.object.anchor, d = s.dock;
  const root = node(app.root, [0, 0, 0]);
  // Sensor puck clipped onto the surveyed component, with a light beam down to the floor.
  const sensor = part(root, 'sphere', M.mint, a, [0.2, 0.2, 0.2]);
  const beamH = a[1] - FLOOR;
  const beam = part(root, 'cylinder', M.beamMint, [a[0], FLOOR + beamH / 2, a[2]], [0.025, beamH, 0.025]);
  const ring = ringPart(root, M.beamMint, [a[0], FLOOR + 0.06, a[2]]);
  // Valve actuator (handwheel + lever) mounted on the equipment, turned by the arm.
  const toDock = new pc.Vec3(d[0] - a[0], 0, d[2] - a[2]).normalize();
  const he = s.object.half_extents || [0.4, 0.4, 0.4];
  const off = Math.min(Math.max(he[0], he[2]) + 0.25, 1.4);
  const valvePos = [a[0] + toDock.x * off, Math.min(a[1], FLOOR + 3.2), a[2] + toDock.z * off];
  const hasValve = !['Exit Sign', 'Manual Call Point'].includes(s.object.component_name);
  const valve = node(root, valvePos); valve.enabled = hasValve;
  valve.lookAt(new pc.Vec3(d[0], valvePos[1], d[2]));
  part(valve, 'cylinder', M.grey, [0, 0, -0.18], [0.12, 0.36, 0.12], [90, 0, 0]);
  const wheel = node(valve, [0, 0, -0.4]);
  part(wheel, 'torus', M.valve, [0, 0, 0], [0.55, 0.55, 0.55], [90, 0, 0]);
  part(wheel, 'box', M.valve, [0, 0, 0], [0.05, 0.5, 0.05]);
  part(wheel, 'box', M.valve, [0, 0, 0], [0.5, 0.05, 0.05]);
  const lever = node(valve, [0.45, 0, -0.2]);
  part(lever, 'box', M.dark, [0, 0.35, 0], [0.09, 0.7, 0.09]);
  part(lever, 'sphere', M.amber, [0, 0.72, 0], [0.18, 0.18, 0.18]);
  // Docking mark on the floor where an arm parks to work.
  const pad = ringPart(root, M.beamMint, [d[0], FLOOR + 0.04, d[2]], 0.9);
  stations[id] = { id, root, sensor, beam, ring, pad, valve, wheel, lever, valveWorld: valve.getPosition().clone(), spin: 0, status: 'ok', anchor: a, dock: d };
}

function buildArm(id, dockId) {
  const d = STATION_MAP[dockId].dock;
  const root = node(app.root, [d[0], FLOOR, d[2]]);
  root.setLocalScale(ARM_SCALE, ARM_SCALE, ARM_SCALE);
  // AMR chassis
  part(root, 'box', M.chassis, [0, 0.42, 0], [1.9, 0.55, 1.5]);
  part(root, 'box', M.orange, [0, 0.72, 0], [1.95, 0.07, 1.55]);
  for (const [x, z] of [[0.75, 0.62], [-0.75, 0.62], [0.75, -0.62], [-0.75, -0.62]]) part(root, 'cylinder', M.dark, [x, 0.25, z], [0.5, 0.18, 0.5], [0, 0, 90]);
  const lamp = part(root, 'sphere', M.mint, [0.7, 0.85, 0.5], [0.16, 0.16, 0.16]);
  // Six-axis-style arm: turret → shoulder → upper → elbow → forearm → wrist → gripper.
  const turret = node(root, [0, 0.75, 0]);
  part(turret, 'cylinder', M.grey, [0, 0.2, 0], [0.9, 0.4, 0.9]);
  const shoulder = node(turret, [0, 0.55, 0]);
  part(shoulder, 'sphere', M.orange, [0, 0, 0], [0.55, 0.55, 0.55]);
  part(shoulder, 'box', M.orange, [0, L1 / 2, 0], [0.32, L1, 0.32]);
  const elbow = node(shoulder, [0, L1, 0]);
  part(elbow, 'sphere', M.grey, [0, 0, 0], [0.42, 0.42, 0.42]);
  part(elbow, 'box', M.orange, [0, L2 / 2, 0], [0.25, L2, 0.25]);
  const wrist = node(elbow, [0, L2, 0]);
  part(wrist, 'cylinder', M.dark, [0, 0.1, 0], [0.26, 0.2, 0.26]);
  const fingerA = part(wrist, 'box', M.grey, [0.1, 0.32, 0], [0.06, 0.34, 0.14]);
  const fingerB = part(wrist, 'box', M.grey, [-0.1, 0.32, 0], [0.06, 0.34, 0.14]);
  const tool = part(wrist, 'sphere', M.amber, [0, 0.45, 0], [0.12, 0.12, 0.12]);
  arms[id] = { id, root, turret, shoulder, elbow, wrist, fingerA, fingerB, tool, lamp, path: [], station: dockId, heading: 0,
    pose: { yaw: 0, a: 0.25, b: 1.6 }, goal: { yaw: 0, a: 0.25, b: 1.6 }, working: 0, task: 'idle' };
}
const L1 = 2.0, L2 = 1.9, ARM_SCALE = 0.62;

function buildCamera(id) {
  const c = CAMERA_MAP[id], p = c.position;
  const root = node(app.root, [p[0], FLOOR, p[2]]);
  const mastH = p[1] - FLOOR;
  part(root, 'cylinder', M.grey, [0, mastH / 2, 0], [0.14, mastH, 0.14]);
  part(root, 'cylinder', M.dark, [0, 0.05, 0], [0.8, 0.1, 0.8]);
  const head = node(app.root, p);
  part(head, 'box', M.chassis, [0, 0, 0], [0.55, 0.42, 0.85]);
  part(head, 'cylinder', M.dark, [0, 0, -0.5], [0.32, 0.2, 0.32], [90, 0, 0]);
  const led = part(head, 'sphere', M.red, [0.2, 0.18, 0.3], [0.09, 0.09, 0.09]);
  const cone = part(head, 'cone', M.cone, [0, 0, -3.2], [2.8, 6, 2.8], [90, 0, 0]);
  cams[id] = { id, root, head, cone, led, flash: 0, aim: STATION_MAP[c.covers[0]].object.anchor };
}

// ---------- arm motion ----------
function solveReach(arm, world) {
  const base = arm.root.getPosition(), sh = arm.shoulder.getPosition();
  const dx = world[0] - base.x, dz = world[2] - base.z;
  const yaw = Math.atan2(dx, dz) * 180 / Math.PI - arm.heading;
  const l1 = L1 * ARM_SCALE, l2 = L2 * ARM_SCALE;
  const dist = Math.max(0.2, Math.hypot(dx, dz) - 0.25), h = world[1] - sh.y;
  const r = Math.min(Math.hypot(dist, h), l1 + l2 - 0.05), phi = Math.atan2(dist, h);
  const alpha = Math.acos(Math.min(1, (l1 * l1 + r * r - l2 * l2) / (2 * l1 * r)));
  const beta = Math.acos(Math.min(1, Math.max(-1, (l1 * l1 + l2 * l2 - r * r) / (2 * l1 * l2))));
  return { yaw, a: phi - alpha, b: Math.PI - beta };
}
function sendArm(arm, stationId) {
  const from = arm.root.getPosition(), to = STATION_MAP[stationId].dock;
  arm.path = findPath([from.x, from.z], [to[0], to[2]]).slice(1);
  arm.station = stationId; arm.task = 'driving'; arm.goal = { yaw: 0, a: 0.25, b: 1.6 };
}
function updateArm(arm, dt, time) {
  const pos = arm.root.getPosition();
  if (arm.path.length && !arm.piloted) {
    const [tx, tz] = arm.path[0], dx = tx - pos.x, dz = tz - pos.z, len = Math.hypot(dx, dz), step = ARM_SPEED * dt * simSpeedFactor();
    const want = Math.atan2(dx, dz) * 180 / Math.PI;
    let dh = ((want - arm.heading + 540) % 360) - 180; arm.heading += Math.sign(dh) * Math.min(Math.abs(dh), 400 * dt);
    arm.root.setEulerAngles(0, arm.heading, 0);
    if (len <= step) { arm.root.setPosition(tx, FLOOR, tz); arm.path.shift(); if (!arm.path.length && arm.task === 'driving') arm.task = 'parked'; }
    else arm.root.setPosition(pos.x + dx / len * step, FLOOR, pos.z + dz / len * step);
  }
  if (arm.task === 'working' && !arm.path.length) {
    const st = stations[arm.station];
    const v = st.valveWorld; arm.goal = solveReach(arm, [v.x, v.y, v.z]);
    st.spin += dt * 220; st.wheel.setLocalEulerAngles(0, 0, st.spin); st.lever.setLocalEulerAngles(0, 0, -Math.min(70, st.spin / 6));
    arm.working -= dt; if (arm.working <= 0) { arm.task = 'parked'; arm.goal = { yaw: arm.goal.yaw, a: 0.35, b: 1.3 }; }
  }
  const k = 1 - Math.exp(-dt * 5);
  for (const key of ['yaw', 'a', 'b']) arm.pose[key] += (arm.goal[key] - arm.pose[key]) * k;
  arm.turret.setLocalEulerAngles(0, arm.pose.yaw, 0);
  arm.shoulder.setLocalEulerAngles(arm.pose.a * 180 / Math.PI, 0, 0);
  arm.elbow.setLocalEulerAngles(arm.pose.b * 180 / Math.PI, 0, 0);
  arm.wrist.setLocalEulerAngles(arm.task === 'working' ? 20 : 0, time * (arm.task === 'working' ? 300 : 0), 0);
  const grip = arm.task === 'working' ? 0.06 : 0.12; arm.fingerA.setLocalPosition(grip, 0.32, 0); arm.fingerB.setLocalPosition(-grip, 0.32, 0);
  arm.lamp.render.material = arm.task === 'driving' ? M.amber : arm.task === 'working' ? M.red : M.mint;
}

// ---------- replay ----------
const PLAN_SPEED = { idle: 6, active: 1.1 }; // simulated days per real second
function simSpeedFactor() { return 1; }
function currentRun() { return state.data.replays[state.policy] || state.data.final; }
const pad3 = (n) => String(n).padStart(3, '0');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ALERT_LEVEL = { detected: 'crit', blocked: 'crit', unsafe: 'crit', evidence_gap: 'crit', overdue: 'crit', missed: 'crit', warning: 'warn', recapture: 'warn' };

function resetReplay() {
  state.run = currentRun(); state.idx = 0; state.simT = 0; state.incident = null; state.feed = []; state.visionDone = false; state.hold = false;
  for (const st of Object.values(stations)) st.status = 'ok';
  const homes = { 'ARM-01': 'S01', 'ARM-02': 'S07' };
  for (const [id, home] of Object.entries(homes)) { const a = arms[id], d = STATION_MAP[home].dock; a.root.setPosition(d[0], FLOOR, d[2]); a.path = []; a.task = 'idle'; a.station = home; }
  $('ticks').innerHTML = state.run.events.filter((e) => e.status === 'detected').map((e) => `<i data-t="${e.t}" style="left:${(e.t / 1.8).toFixed(2)}%"></i>`).join('');
  $('event-log').innerHTML = '';
  pushFeed({ day: 0, robot: 'TEAM', station_id: '', status: 'info', message: `${state.policy === 'learned' ? 'team N+' + state.data.accepted_policy.generation + ' (learned)' : 'team N (baseline)'} on unseen exam run ${state.run.seed}` });
  for (const v of visionLog) pushFeed(v);
  state.viewGen = state.policy === 'learned' ? state.data.accepted_policy.generation : 0; drawGens();
}

function telemetryAt(t) {
  const tel = state.run?.telemetry || state.data.final.telemetry; if (!tel) return;
  const tick = Math.floor(t * tel.ticks_per_day);
  for (const s of tel.streams) {
    const v = s.values[Math.min(tick, s.values.length - 1)];
    const span = s.alarm - s.normal, frac = span ? (v - s.normal) / span : 0;
    state.values[s.station_id] = state.values[s.station_id] || {};
    state.values[s.station_id][s.channel] = { v, unit: s.unit, label: s.label, frac, level: frac >= 0.95 ? 'alarm' : frac >= 0.5 ? 'warn' : 'ok' };
  }
}

function pushFeed(e) {
  const lvl = e.lvl || ALERT_LEVEL[e.status] || 'info';
  if (lvl === 'info' && !['TEAM', 'EXAM'].includes(e.robot) && e.status !== 'closed') return;
  state.feed = [{ ...e, lvl }, ...(state.feed || [])].slice(0, 3);
  const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
  $('event-log').innerHTML = state.feed.map((f) => `<div class="event ${f.lvl}"><span class="dot"></span><div style="min-width:0"><div class="msg" title="${f.message}">${cap(f.message)}</div><div class="sub">day ${f.day} · ${f.robot}${f.station_id ? ' · ' + f.station_id : ''}</div></div></div>`).join('');
}

function applyEvent(e) {
  const st = stations[e.station_id];
  pushFeed(e); flashNode(e.robot);
  if (st) {
    if (e.status === 'warning') st.status = 'warn';
    if (e.status === 'detected') { st.status = 'alarm'; focusStation(e.station_id); }
    if (e.status === 'blocked' || e.status === 'unsafe') { st.status = 'blocked'; focusStation(e.station_id); }
    if (e.status === 'closed') st.status = 'ok';
  }
  if (e.robot?.startsWith('ARM') && arms[e.robot]) {
    const arm = arms[e.robot];
    if (e.status === 'dispatch') { sendArm(arm, e.station_id); focusStation(e.station_id); }
    if (e.status === 'action' || e.status === 'recapture') { if (arm.station !== e.station_id) sendArm(arm, e.station_id); arm.task = 'working'; arm.working = 2.4; }
  }
  if (e.robot?.startsWith('CAM') && cams[e.robot]) {
    const c = cams[e.robot]; c.flash = 1.2; c.aim = STATION_MAP[e.station_id].object.anchor;
    const tile = CCTV.find((t) => t.station === e.station_id) || CCTV[0]; tile.flash = 1.2; flashNode('VISION');
  }
  state.incident = ['closed', 'evidence_gap', 'missed'].includes(e.status) ? null : e.incident_id || state.incident;
  const active = Object.values(stations).filter((s) => s.status !== 'ok').length;
  $('feed-count').textContent = `${active} active`;
}

function stepReplay(dt) {
  if (!state.playing || !state.run || state.hold) return;
  const busy = state.incident || Object.values(arms).some((a) => a.path.length || a.task === 'working');
  state.simT += dt * (busy ? PLAN_SPEED.active : PLAN_SPEED.idle) * (state.speedMul || 1);
  if (state.policy === 'learned' && !state.visionDone && state.simT >= (state.visionAt || 26)) { state.visionDone = true; visionWorkOrder(); }
  const evs = state.run.events;
  while (state.idx < evs.length && evs[state.idx].t <= state.simT) applyEvent(evs[state.idx++]);
  const day = Math.min(180, state.simT);
  $('progress').style.width = (day / 1.8) + '%'; $('day-label').textContent = `${Math.floor(day)} / 180 days`;
  document.querySelectorAll('#ticks i').forEach((i) => i.classList.toggle('done', Number(i.dataset.t) <= day));
  if (state.simT >= 180 && state.idx >= evs.length) {
    state.playing = false; state.simT = 180; $('run').querySelector('span').textContent = '↻ Replay';
    const m = state.run.metrics;
    pushFeed({ day: 180, robot: 'EXAM', station_id: '', status: m.unsafe_actions ? 'unsafe' : 'info', message: `six months done · score ${m.score} · ${m.unsafe_actions} unsafe actions` });
  }
}

// ---------- camera ----------
function focusStation(id) {
  const i = STATION_IDS.indexOf(id); if (i < 0 || state.focus === id) return;
  state.focus = id; viewer.selectAnnotation(i);
}
function overview() { state.focus = null; viewer.resetCamera(); }

// ---------- overlay tags ----------
const tagEls = {};
function tag(key, cls, onClick) {
  if (!tagEls[key]) { const el = document.createElement('div'); el.className = 'tag ' + cls; if (onClick) el.addEventListener('click', onClick); $('tags').append(el); tagEls[key] = el; }
  return tagEls[key];
}
const tmpW = new pc.Vec3(), tmpS = new pc.Vec3();
function place(el, world, html, cls) {
  const cam = camEntity.camera, cp = camEntity.getPosition(), fw = camEntity.forward;
  tmpW.set(...world);
  const depth = (tmpW.x - cp.x) * fw.x + (tmpW.y - cp.y) * fw.y + (tmpW.z - cp.z) * fw.z, dist = tmpW.distance(cp);
  if (depth < 0.3 || dist > 34) { el.style.display = 'none'; return; }
  cam.worldToScreen(tmpW, tmpS);
  const canvas = app.graphicsDevice.canvas, r = canvas.getBoundingClientRect();
  const x = tmpS.x * r.width / canvas.clientWidth + r.left, y = tmpS.y * r.height / canvas.clientHeight + r.top;
  const minX = document.body.classList.contains('hud-off') ? 0 : 480;
  if (x < minX || y < 70 || x > innerWidth + 50 || y > innerHeight - 50) { el.style.display = 'none'; return; }
  el.style.display = ''; el.style.transform = `translate(${x}px,${y - 14}px) translate(-50%,-100%)`; el.style.zIndex = String(1000 - Math.round(dist * 10));
  if (el.__html !== html) { el.innerHTML = html; el.__html = html; }
  if (el.__cls !== cls) { el.className = cls; el.__cls = cls; }
}
function stationLevel(id) {
  const st = stations[id], vals = Object.values(state.values[id] || {});
  if (st.status !== 'ok') return st.status;
  return vals.some((v) => v.level === 'alarm') ? 'alarm' : vals.some((v) => v.level === 'warn') ? 'warn' : 'ok';
}
function drawTags() {
  for (const id of STATION_IDS) {
    const st = stations[id], s = STATION_MAP[id], vals = state.values[id] || {}, status = stationLevel(id);
    const readings = Object.values(vals).map((v) => `${v.label} <b>${v.v}</b> ${v.unit}`).join(' · ');
    const el = tag(id, 'tag', () => focusStation(id));
    if (status === 'ok' && state.focus !== id) el.style.display = 'none';
    else place(el, [s.object.anchor[0], s.object.anchor[1] + 0.9, s.object.anchor[2]],
      `<b>${id}</b> ${s.object.component_name}${status === 'blocked' ? ' · <b>GATE BLOCKED</b>' : ''}<br><i>${readings || 'no stream'}</i>`,
      `tag ${status}${state.focus === id ? ' active' : ''}`);
    st.sensor.render.material = statusMat(status);
    const beam = status === 'ok' ? M.beamMint : M.beamRed; st.ring.render.meshInstances[0].material = beam; st.beam.render.material = beam;
  }
  for (const arm of Object.values(arms)) { const p = arm.root.getPosition(); place(tag(arm.id, 'tag robot'), [p.x, p.y + 3.4, p.z], `<b>${arm.id}</b> ${arm.piloted || arm.id.startsWith('ROVER') ? 'Nemotron: ' + (arm.pilotText || '…') : arm.task + (arm.task !== 'idle' ? ' → ' + arm.station : '')}`, 'tag robot'); }
}

// ---------- cameras + Nemotron VL detection ----------
const CCTV = ['S01', 'S02', 'S04', 'S05', 'S07', 'S08'].map((sid, i) => ({ id: `CAM-0${i + 1}`, station: sid, frame: null, result: null, state: 'idle', flash: 0 }));
const visionLog = [];
function buildCams() {
  $('cams').innerHTML = CCTV.map((c) => `<div class="cam" data-id="${c.id}"><img alt=""><svg viewBox="0 0 1000 1000" preserveAspectRatio="none"></svg><span class="lbl">${c.id} · ${c.station}</span><span class="st">offline</span></div>`).join('');
  $('cams').querySelectorAll('.cam').forEach((el) => el.addEventListener('click', () => { const c = CCTV.find((x) => x.id === el.dataset.id); focusStation(c.station); openCam(c); }));
}
const isIssue = (d) => ['warn', 'crit'].includes(d.severity) || (d.severity === 'info' && d.issue && !/^(none|no |ignore)/i.test(d.issue));
const isOverlay = (d) => /overlay|robot arm|sensor beacon|handwheel/i.test(d.label);
function drawCam(c) {
  const el = document.querySelector(`.cam[data-id="${c.id}"]`); if (!el) return;
  if (c.frame && el.querySelector('img').src !== c.frame) el.querySelector('img').src = c.frame;
  const dets = c.result?.ok ? c.result.detections.filter((d) => !isOverlay(d)) : [];
  el.querySelector('svg').innerHTML = dets.map((d) => { const [x0, y0, x1, y1] = d.box, bad = isIssue(d); return `<rect x="${x0}" y="${y0}" width="${Math.max(4, x1 - x0)}" height="${Math.max(4, y1 - y0)}" fill="none" stroke="${bad ? '#ff5a1f' : 'rgba(255,255,255,.75)'}" stroke-width="${bad ? 14 : 7}" vector-effect="non-scaling-stroke" style="stroke-width:${bad ? 2 : 1}px"/>`; }).join('');
  const issues = dets.filter(isIssue);
  el.classList.toggle('scan', c.state === 'scanning'); el.classList.toggle('issue', issues.length > 0); el.classList.toggle('focus', state.focus === c.station);
  el.querySelector('.st').textContent = c.state === 'scanning' ? 'scanning' : c.result?.ok ? `${dets.length} objects${issues.length ? ' · ' + issues.length + ' issue' : ''}` : c.state === 'error' ? 'VL error' : 'live';
}
async function captureCams() {
  for (let i = 0; i < CCTV.length; i++) {
    viewer.selectAnnotation(STATION_IDS.indexOf(CCTV[i].station)); // fly to the camera's pose, let it settle and re-sort
    await sleep(1700);
    const shot = await viewer.captureFrame({ width: 448, height: 252, supersample: 1 });
    const bytes = Uint8ClampedArray.from(atob(shot.data), (ch) => ch.charCodeAt(0));
    const cv = document.createElement('canvas'); cv.width = shot.width; cv.height = shot.height;
    cv.getContext('2d').putImageData(new ImageData(bytes, shot.width, shot.height), 0, 0);
    CCTV[i].frame = cv.toDataURL('image/jpeg', 0.85); CCTV[i].state = 'live'; drawCam(CCTV[i]);
    $('loading-text').textContent = `Rendering camera views from the scan · ${i + 1}/6`;
  }
  viewer.selectAnnotation(null); viewer.resetCamera();
}
async function detectCams() {
  const cache = params.has('fresh') ? {} : await fetch('/api/detections').then((r) => r.json()).catch(() => ({}));
  state.verify = Object.fromEntries(Object.entries(cache).filter(([k]) => k.startsWith('verify-')).map(([k, v]) => [k.slice(7), v]));
  const run = async (c) => {
    c.state = 'scanning'; drawCam(c); $('vision-status').textContent = 'Nemotron 3.5 Super VL · scanning';
    let res = cache[c.id];
    const post = () => fetch('/api/detect', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ camera: c.id, station: c.station, image: c.frame }) }).then((r) => r.json()).catch((e) => ({ ok: false, error: String(e) }));
    if (!res) { await sleep(400); res = await post(); if (!res.ok) res = await post(); }
    else await sleep(700 + Math.random() * 700); // replay of a recorded VL call
    c.result = res; c.state = res.ok ? 'live' : 'error'; drawCam(c); flashNode('VISION');
    if (res.ok) {
      const issues = res.detections.filter((d) => !isOverlay(d) && isIssue(d));
      if (issues.length) {
        const ev = { day: Math.floor(state.simT), robot: `${c.id} → VISION → PLANNER`, station_id: c.station, status: 'vision', lvl: 'vision', message: `${issues[0].label}: ${issues[0].issue}` };
        visionLog.push(ev); pushFeed(ev); flashNode('ALEX');
        if (issues.length && stations[c.station].status === 'ok') stations[c.station].status = 'warn';
      }
    }
  };
  for (let i = 0; i < CCTV.length; i += 2) await Promise.all(CCTV.slice(i, i + 2).map(run));
  const calls = CCTV.filter((c) => c.result?.call?.latency_s).map((c) => c.result.call.latency_s);
  $('vision-status').textContent = `Nemotron 3.5 Super VL · ${CCTV.filter((c) => c.result?.ok).length}/6 frames${calls.length ? ' · ' + (calls.reduce((a, b) => a + b, 0) / calls.length).toFixed(1) + ' s each' : ''}`;
}

// ---------- measurements + benchmarks ----------
function buildMeasures() {
  $('measures').innerHTML = STATION_IDS.map((id) => `<div class="m" data-id="${id}"><span>${id} ${STATION_MAP[id].object.component_name.split(' ')[0].toLowerCase()}</span><b>—</b><svg viewBox="0 0 100 16" preserveAspectRatio="none"></svg></div>`).join('');
  $('measures').querySelectorAll('.m').forEach((el) => el.addEventListener('click', () => { focusStation(el.dataset.id); openSensors(el.dataset.id); }));
}
function drawMeasures() {
  $('measures').querySelectorAll('.m').forEach((el) => {
    const id = el.dataset.id, vals = Object.values(state.values[id] || {}); if (!vals.length) return;
    const v = vals.reduce((a, b) => (b.frac > a.frac ? b : a));
    const tel = state.run.telemetry || state.data.final.telemetry, s0 = tel.streams.find((x) => x.station_id === id && x.label === v.label), n = Math.max(2, Math.floor(state.simT * tel.ticks_per_day) + 1);
    if (s0) { const lo = Math.min(s0.normal, s0.alarm), hi = Math.max(s0.normal, s0.alarm), span = (hi - lo) || 1, from = Math.max(0, n - 80); let d = ''; for (let i = from; i < n; i++) d += `${i === from ? 'M' : 'L'}${((i - from) / 79 * 100).toFixed(1)},${(15 - Math.max(0, Math.min(1, (s0.values[i] - lo + span * .25) / (span * 1.5))) * 14).toFixed(1)}`; el.querySelector('svg').innerHTML = `<path d="${d}" fill="none" stroke="${v.level === 'ok' ? '#9a9a9a' : '#ff5a1f'}" stroke-width="1.2" vector-effect="non-scaling-stroke"/>`; }
    const txt = `${v.v} ${v.unit}`; if (el.querySelector('b').textContent !== txt) el.querySelector('b').textContent = txt;
    el.className = `m ${stationLevel(id)}${state.focus === id ? ' focus' : ''}`;
  });
}
function drawBench() {
  const gens = state.data.generations, rows = [
    ['Score', (h) => h.score, 'max'], ['Resp. days', (h) => h.avg_response_days, 'min'], ['Unsafe acts', (h) => h.unsafe_actions, 'min'],
  ];
  let html = '<span></span>' + gens.map((g, i) => `<span class="h ${i === state.viewGen ? 'cur' : ''} ${g.accepted ? '' : 'rej'}">${i ? 'N+' + i : 'N'}${g.accepted ? '' : ' ✕'}</span>`).join('');
  for (const [label, f, best] of rows) {
    const vals = gens.map((g) => f(g.heldout)), mx = Math.max(...vals, 1), accepted = gens.map((g, i) => [i, g]).filter(([, g]) => g.accepted).map(([i]) => vals[i]);
    const target = best === 'max' ? Math.max(...accepted) : Math.min(...accepted);
    html += `<span class="k">${label}</span>` + vals.map((v, i) => `<div class="v ${gens[i].accepted && v === target ? 'best' : ''} ${gens[i].accepted ? '' : 'rej'}"><i style="width:${(v / mx * 100).toFixed(0)}%"></i><span>${Number.isInteger(v) ? v : v.toFixed(1)}</span></div>`).join('');
  }
  $('bench').innerHTML = html;
}

// ---------- agents and sub-agents ----------
const NODES = [
  { id: 'CAMS', label: '6 cameras', sub: 'rendered from scan', x: 70, y: 22, gen: 0, heat: ['CAM-N', 'CAM-W', 'CAM-E'] },
  { id: 'SENSOR', label: 'Sensors', sub: '11 live streams', x: 220, y: 22, gen: 0 },
  { id: 'VISION', label: 'Vision', sub: 'Nemotron 3.5 Super VL', x: 70, y: 92, gen: 0 },
  { id: 'ALEX', label: 'Planner', sub: 'Nemotron 3 Super', x: 220, y: 92, gen: 0 },
  { id: 'NANO', label: 'Verifier', sub: 'Nemotron Nano', x: 370, y: 92, gen: 1 },
  { id: 'DISPATCH', label: 'Dispatch', sub: 'work orders', x: 220, y: 160, gen: 0 },
  { id: 'PILOT', label: 'Pilot', sub: 'Nemotron 3.5 Lightning', x: 70, y: 160, gen: 0, heat: ['ROVER-1'] },
  { id: 'ARM-01', label: 'ARM-01', x: 150, y: 226, gen: 0 }, { id: 'ARM-02', label: 'ARM-02', x: 250, y: 226, gen: 0 }, { id: 'ROVER-1', label: 'ROVER-1', x: 50, y: 226, gen: 0 },
];
const EDGES = [['CAMS', 'VISION'], ['SENSOR', 'ALEX'], ['VISION', 'ALEX'], ['ALEX', 'NANO', 1], ['ALEX', 'DISPATCH'], ['NANO', 'DISPATCH', 1], ['DISPATCH', 'ARM-01'], ['DISPATCH', 'ARM-02'], ['ALEX', 'PILOT'], ['PILOT', 'ROVER-1'], ['PILOT', 'ARM-01']];
const TOOLS = [{ key: 'deadline_sweep', label: 'deadline sweep', gen: 2, x: 375, y: 150 }, { key: 'reorder_buffer', label: 'reorder buffer', gen: 3, x: 375, y: 182 }, { key: 'fast_track', label: 'fast-track', gen: 4, x: 375, y: 214, rejected: true }];
const nodeHeat = {};
function buildArch() {
  const by = Object.fromEntries(NODES.map((n) => [n.id, n]));
  let svg = '<defs><marker id="arr" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#666"/></marker></defs>';
  for (const [a, b, g = 0] of EDGES) {
    const A = by[a], B = by[b], horiz = A.y === B.y;
    const [x1, y1, x2, y2] = horiz ? [A.x + (B.x > A.x ? 58 : -58), A.y, B.x + (B.x > A.x ? -60 : 60), B.y] : [A.x, A.y + 16, B.x, B.y - 18];
    svg += `<path class="e" data-to="${b}" data-gen="${g}" d="M${x1},${y1} ${horiz ? `L${x2},${y2}` : `C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}`}"/>`;
  }
  svg += `<text x="375" y="132" fill="#777" font-size="8" text-anchor="middle" font-family="monospace">tools added by learning</text>`;
  for (const t of TOOLS) svg += `<g class="tool" data-gen="${t.gen}" data-rej="${t.rejected ? 1 : 0}"><rect x="${t.x - 56}" y="${t.y - 11}" width="112" height="22" rx="11"/><text x="${t.x}" y="${t.y + 3}">${t.label}</text></g>`;
  for (const n of NODES) { const w = n.sub ? 116 : 70, h = n.sub ? 32 : 24; svg += `<g class="n" data-id="${n.id}" data-gen="${n.gen}"><rect x="${n.x - w / 2}" y="${n.y - h / 2}" width="${w}" height="${h}" rx="8"/><text x="${n.x}" y="${n.y + (n.sub ? -2 : 4)}">${n.label}</text>${n.sub ? `<text class="s" x="${n.x}" y="${n.y + 10}">${n.sub}</text>` : ''}</g>`; }
  $('arch').innerHTML = svg;
  $('arch').querySelectorAll('.n').forEach((el) => el.addEventListener('click', () => openAgent(el.dataset.id)));
}
function drawGens() {
  const gens = state.data.generations;
  $('gens').innerHTML = gens.map((g, i) => `<button data-g="${i}" class="${i === state.viewGen ? 'on' : ''} ${g.accepted ? '' : 'rej'}">${i ? 'N+' + i : 'N'}</button>`).join('');
  $('gens').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { state.viewGen = Number(b.dataset.g); drawGens(); }));
  const vg = state.viewGen, g = gens[vg];
  document.querySelectorAll('#arch .n, #arch .e').forEach((el) => { const gen = Number(el.dataset.gen); el.classList.toggle('off', gen > Math.min(vg, 3)); el.classList.toggle('new', gen === vg && vg > 0); });
  document.querySelectorAll('#arch .tool').forEach((el) => { const gen = Number(el.dataset.gen), rej = el.dataset.rej === '1'; el.classList.toggle('off', rej ? vg !== gen : gen > vg); el.classList.toggle('new', !rej && gen === vg); el.classList.toggle('rej', rej && vg === gen); });
  const prev = gens[Math.max(0, vg - 1)].heldout, h = g.heldout;
  const curKey = vg ? g.edit?.key : null;
  document.querySelectorAll('#ledger .lg').forEach((el) => el.classList.toggle('cur', el.dataset.key === curKey));
  $('gen-note').innerHTML = vg ? `<b>N+${vg}:</b> ${g.accepted ? 'kept' : '<span class="hot">rejected</span>'} · ${g.reason.replace(/^(accepted|rejected): /, '')}` : '<b>N:</b> baseline team, no second check before robot actions.';
  drawBench();
}
function flashNode(robot) {
  const id = NODES.find((n) => n.id === robot || n.heat?.includes(robot))?.id || robot;
  nodeHeat[id] = 1.3; if (id !== 'ALEX' && ['VISION', 'NANO', 'SENSOR', 'DISPATCH'].includes(id)) nodeHeat.ALEX = Math.max(nodeHeat.ALEX || 0, 0.7);
  if (id.startsWith('ARM')) nodeHeat.DISPATCH = 1.3;
}
function drawArch(dt) {
  for (const n of NODES) {
    nodeHeat[n.id] = Math.max(0, (nodeHeat[n.id] || 0) - dt);
    const live = nodeHeat[n.id] > 0 || (arms[n.id] && !['idle', 'parked'].includes(arms[n.id].task));
    const el = document.querySelector(`#arch .n[data-id="${n.id}"]`); if (el && el.classList.contains('live') !== !!live) el.classList.toggle('live', !!live);
    document.querySelectorAll(`#arch .e[data-to="${n.id}"]`).forEach((e) => e.classList.toggle('live', !!live));
  }
}

function buildLedger() {
  const pretty = { verifier_role: 'Verifier sub-agent', deadline_sweep: 'Deadline sweep', reorder_buffer: 'Reorder tool', fast_track: 'Fast-track', dual_confirmation: 'Dual confirmation' };
  const base = state.data.heldout_after;
  $('ledger').innerHTML = (state.data.ablation || []).map((a) => {
    if (a.rejected) return `<div class="lg rej" data-key="${a.key}"><span class="ic">✕</span><span class="nm">${pretty[a.key] || a.label}<small>${a.kind}</small></span><span class="pt">rejected</span><span class="why">faster (${a.response_with} d) but ${a.unsafe_with} unsafe actions · ${a.score_drop} pts</span></div>`;
    const why = a.unsafe_without > base.unsafe_actions ? `without it: ${a.unsafe_without} unsafe actions` : a.overdue_without > base.overdue ? `without it: ${a.overdue_without} overdue jobs` : a.stockouts_without > base.stockouts ? `without it: ${a.stockouts_without} stock-outs` : `without it: ${a.gaps_without} evidence gaps`;
    return `<div class="lg ${a.score_drop < 2 ? 'marg' : ''}" data-key="${a.key}"><span class="ic">✓</span><span class="nm">${pretty[a.key] || a.label}<small>${a.kind}${a.score_drop < 2 ? ' · marginal' : ''}</small></span><span class="pt">−${a.score_drop}</span><span class="why">${why}</span></div>`;
  }).join('');
}

// ---------- Nemotron pilot: discrete drive commands ----------
const pilotLog = [];
const UNITS_PER_M = 3; // the scan is scaled 3x
function freeRay(x, z, deg) {
  const r = deg * Math.PI / 180; let d = 0;
  while (d < 30) { const [i, j] = cellOf(x + Math.sin(r) * (d + 0.15), z + Math.cos(r) * (d + 0.15)); if (!open(i, j)) break; d += 0.15; }
  return d / UNITS_PER_M;
}
function animate(ms, fn) { return new Promise((res) => { const t0 = performance.now(); const step = (now) => { const k = Math.min(1, (now - t0) / ms); fn(k); if (k < 1) requestAnimationFrame(step); else res(); }; requestAnimationFrame(step); }); }
async function pilotDrive(robot, sid, label) {
  const goal = STATION_MAP[sid].dock, last = [];
  robot.path = []; robot.task = 'driving'; robot.station = sid; robot.piloted = true;
  for (let call = 0; call < 24; call++) {
    const p = robot.root.getPosition(), dx = goal[0] - p.x, dz = goal[2] - p.z, dist = Math.hypot(dx, dz) / UNITS_PER_M;
    if (dist < 1.5) break;
    const bearing = ((Math.atan2(dx, dz) * 180 / Math.PI - robot.heading + 540) % 360) - 180;
    const rays = Object.fromEntries([-90, -45, 0, 45, 90].map((a) => [a, freeRay(p.x, p.z, robot.heading + a)]));
    const res = await fetch('/api/pilot', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ robot: robot.id, goal: label || sid, bearing, distance: dist, rays, last }) }).then((r) => r.json()).catch(() => ({ ok: false }));
    pilotLog.unshift({ robot: robot.id, goal: label || sid, bearing: Math.round(bearing), distance: dist.toFixed(1), rays, res, at: new Date().toLocaleTimeString() }); pilotLog.length = Math.min(pilotLog.length, 12);
    if (!res.ok) { robot.pilotText = 'pilot unavailable · route planner'; robot.piloted = false; sendArm(robot, sid); return false; }
    robot.pilotText = res.commands.join(' ▸ '); flashNode(robot.id); flashNode('PILOT');
    for (const cmd of res.commands) {
      last.push(cmd);
      if (cmd === 'stop') break;
      if (cmd === 'left' || cmd === 'right') { const h0 = robot.heading, h1 = h0 + (cmd === 'right' ? 45 : -45); await animate(260, (k) => { robot.heading = h0 + (h1 - h0) * k; robot.root.setEulerAngles(0, robot.heading, 0); }); continue; }
      const sign = cmd === 'forward' ? 1 : -1, r = robot.heading * Math.PI / 180, q = robot.root.getPosition().clone();
      let len = 1.2 * UNITS_PER_M; while (len > 0.2 && !clearLine([q.x, q.z], [q.x + Math.sin(r) * len * sign, q.z + Math.cos(r) * len * sign])) len -= 0.3;
      if (len <= 0.2) { robot.pilotText += ' · blocked'; break; }
      await animate(420, (k) => robot.root.setPosition(q.x + Math.sin(r) * len * sign * k, FLOOR, q.z + Math.cos(r) * len * sign * k));
    }
  }
  robot.piloted = false;
  const end = robot.root.getPosition();
  if (Math.hypot(goal[0] - end.x, goal[2] - end.z) / UNITS_PER_M >= 1.5) { robot.pilotText += ' · route planner finishes'; sendArm(robot, sid); return false; }
  robot.task = 'parked';
  return true;
}
async function patrol(robot) {
  const route = ['S08', 'S01', 'S05', 'S07', 'S04', 'S02'];
  for (let k = 0; ; k = (k + 1) % route.length) {
    if (document.hidden || state.hold) { await sleep(1000); continue; }
    await pilotDrive(robot, route[k], `${route[k]} ${STATION_MAP[route[k]].object.component_name}`);
    robot.pilotText = `arrived ${route[k]}`; await sleep(1200);
  }
}

// ---------- vision-triggered work order ----------
async function visionWorkOrder() {
  const cam = CCTV.find((c) => c.result?.ok && c.result.detections.some((d) => !isOverlay(d) && isIssue(d)));
  if (!cam) return;
  const finding = cam.result.detections.find((d) => !isOverlay(d) && isIssue(d)), day = Math.floor(state.simT), sid = cam.station;
  state.hold = true;
  const text = `${finding.label}: ${finding.issue}`;
  caption(`${cam.id} spots: ${finding.label}`, 'Nemotron 3.5 Super VL → planner opens a work order');
  pushFeed({ day, robot: `${cam.id} → vision → planner`, station_id: sid, status: 'vision', lvl: 'vision', message: text });
  flashNode('VISION'); await sleep(500); flashNode('ALEX'); cam.flash = 2;
  const wo = { day, robot: 'PLANNER', station_id: sid, status: 'warning', lvl: 'warn', message: `work order opened from camera: ${finding.label}` };
  pushFeed(wo); stations[sid].status = 'warn'; focusStation(sid);
  const arm = Object.values(arms).find((a) => ['idle', 'parked'].includes(a.task)) || arms['ARM-01'];
  await sleep(600); flashNode(arm.id);
  pushFeed({ day, robot: `${arm.id} · Nemotron Lightning pilot`, station_id: sid, status: 'vision', lvl: 'vision', message: `driving to ${sid} with forward / left / right commands` });
  caption(`${arm.id} driven by Nemotron`, 'Nemotron 3.5 Lightning outputs forward / left / right; obstacles come from the scanned floor');
  await pilotDrive(arm, sid, `${sid} ${finding.label}`);
  for (let t = 0; t < 80 && arm.path.length; t++) await sleep(100);
  arm.task = 'working'; arm.working = 2.6; await sleep(2700);
  caption('Evidence frame → Nemotron VL checks the arm is on site', '');
  const shot = await viewer.captureFrame({ width: 448, height: 252, supersample: 1 });
  const bytes = Uint8ClampedArray.from(atob(shot.data), (ch) => ch.charCodeAt(0));
  const cv = document.createElement('canvas'); cv.width = shot.width; cv.height = shot.height; cv.getContext('2d').putImageData(new ImageData(bytes, shot.width, shot.height), 0, 0);
  const frame = cv.toDataURL('image/jpeg', 0.85);
  cam.frame = frame; cam.state = 'scanning'; drawCam(cam);
  const cache = await fetch('/api/detections').then((r) => r.json()).catch(() => ({}));
  const res = (!params.has('fresh') && cache[`verify-${cam.id}`]) || await fetch('/api/verify', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ camera: cam.id, station: sid, finding: text, image: frame }) }).then((r) => r.json()).catch(() => ({ ok: false }));
  cam.state = 'live'; drawCam(cam); flashNode('VISION'); (state.verify ||= {})[cam.id] = res;
  const ok = res.ok && res.arm_on_site;
  pushFeed({ day, robot: 'VISION · evidence', station_id: sid, status: ok ? 'closed' : 'evidence_gap', lvl: ok ? 'vision' : 'crit', message: ok ? `arm on site confirmed — ${res.note || 'work order closed with evidence'}` : `evidence check failed — ${res.note || res.error || 'no answer'}` });
  stations[sid].status = 'ok';
  caption(ok ? 'Work order closed with a verified evidence frame' : 'Evidence check failed: work order stays open', ok ? '' : '');
  await sleep(1600); caption('');
  state.hold = false;
}

// ---------- inspect: cameras, agents, sensors ----------
const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
const callMeta = (c) => c ? `${esc(c.model)} · ${c.latency_s ?? '—'} s · ${c.prompt_tokens ?? '—'} in / ${c.completion_tokens ?? '—'} out tokens${c.attempts > 1 ? ' · ' + c.attempts + ' attempts' : ''}${c.at ? ' · ' + esc(c.at) : ''}` : '';
function openDrawer(html) { $('drawer-body').innerHTML = html; $('drawer').classList.add('on'); if (viewer) viewer.state.inputEnabled = false; }
function closeDrawer() { $('drawer').classList.remove('on'); if (viewer) viewer.state.inputEnabled = true; }
function openCam(c) {
  const r = c.result, dets = r?.ok ? r.detections : [];
  const boxes = dets.map((d) => { const [x0, y0, x1, y1] = d.box, bad = isIssue(d); return `<rect x="${x0}" y="${y0}" width="${Math.max(4, x1 - x0)}" height="${Math.max(4, y1 - y0)}" fill="none" stroke="${bad ? '#ff5a1f' : 'rgba(255,255,255,.85)'}" style="stroke-width:${bad ? 3 : 1.5}px" vector-effect="non-scaling-stroke"/>`; }).join('');
  const labels = dets.map((d) => `<span class="blabel ${isIssue(d) ? 'bad' : ''}" style="left:${d.box[0] / 10}%;top:${d.box[1] / 10}%">${esc(d.label)}</span>`).join('');
  const verify = state.verify?.[c.id];
  openDrawer(`<h2>${c.id} · ${c.station} ${esc(STATION_MAP[c.station].object.component_name)}</h2><div class="sub">frame rendered from the Gaussian splat scan · labelled by Nemotron 3.5 Super VL on Crusoe</div>
    <div class="shot"><img src="${c.frame || ''}"><svg viewBox="0 0 1000 1000" preserveAspectRatio="none">${boxes}</svg>${labels}</div>
    ${r?.ok ? `<div class="trace"><span class="k">MODEL SUMMARY</span><div>${esc(r.summary)}</div><div class="meta" style="margin-top:6px">route to planner: ${r.route_to_planner ? 'yes' : 'no'} · ${callMeta(r.call)}</div></div>
    <table><tr><th>Label</th><th>Severity</th><th>Issue</th><th>Box (0–1000)</th></tr>${dets.map((d) => `<tr><td>${esc(d.label)}${isOverlay(d) ? ' <span class="meta">(our overlay, ignored)</span>' : ''}</td><td><span class="sev ${d.severity}">${d.severity}</span></td><td>${esc(d.issue || '—')}</td><td class="meta">${d.box.map((v) => Math.round(v)).join(', ')}</td></tr>`).join('')}</table>` : `<div class="trace">No detection yet: ${esc(r?.error || c.state)}</div>`}
    ${verify ? `<h3>Evidence check after the work order</h3><div class="trace">arm on site: <b>${verify.arm_on_site ? 'yes' : 'no'}</b> — ${esc(verify.note)}<div class="meta">${callMeta(verify.call)}</div></div>` : ''}`);
}
function openSensors(focus) {
  const tel = state.run.telemetry || state.data.final.telemetry, n = Math.max(2, Math.floor(state.simT * tel.ticks_per_day) + 1);
  const rows = tel.streams.map((s0) => {
    const lo = Math.min(s0.normal, s0.alarm), hi = Math.max(s0.normal, s0.alarm), span = (hi - lo) || 1, y = (v) => 40 - Math.max(0, Math.min(1, (v - lo + span * .25) / (span * 1.5))) * 38;
    let d = ''; const step = Math.max(1, Math.floor(s0.values.length / 300)); for (let i = 0; i < n; i += step) d += `${i ? 'L' : 'M'}${(i / (s0.values.length - 1) * 600).toFixed(1)},${y(s0.values[i]).toFixed(1)}`;
    const v = s0.values[Math.min(n - 1, s0.values.length - 1)];
    return `<tr style="${s0.station_id === focus ? 'background:rgba(255,90,31,.12)' : ''}"><td>${s0.station_id}</td><td>${esc(s0.label)}</td><td><b>${v}</b> ${esc(s0.unit)}</td><td class="meta">normal ${s0.normal} · alarm ${s0.alarm}</td><td style="width:50%"><svg viewBox="0 0 600 42" preserveAspectRatio="none" style="width:100%;height:42px"><line x1="0" x2="600" y1="${y(s0.alarm)}" y2="${y(s0.alarm)}" stroke="#ff5a1f" stroke-dasharray="4 4"/><path d="${d}" fill="none" stroke="#ccc" stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg></td></tr>`;
  }).join('');
  openDrawer(`<h2>Sensors</h2><div class="sub">11 simulated streams, one reading every 6 hours · day ${Math.floor(state.simT)} of 180 · dashed line = alarm level</div><table><tr><th>Station</th><th>Stream</th><th>Now</th><th>Limits</th><th>History</th></tr>${rows}</table>`);
}
function openAgent(id) {
  const d = state.data, ev = (state.run?.events || []).slice(0, state.idx);
  if (id === 'ALEX') {
    const gens = d.generations.slice(1).map((g, i) => `<div class="trace"><span class="k">GENERATION N+${i + 1} · audit of training runs → Nemotron 3 Super picks the next change</span>
      <pre>audit: ${esc(JSON.stringify(g.audit))}\nrule-based order: ${esc((g.proposal?.auditor_order || []).join(', '))}\nnemotron order:   ${esc((g.proposal?.model_order || []).join(', '))}\nrationale: ${esc(g.proposal?.rationale)}\nexpected effect: ${esc(g.proposal?.expected_effect)}\npicked: ${esc(g.proposal?.picked)}${g.proposal?.changed_pick ? ' (changed the rule-based pick)' : ' (same as rule-based pick)'}\ngate on 12 unseen runs: ${esc(g.reason)}</pre><div class="meta">${callMeta(g.proposal?.call)}</div></div>`).join('');
    openDrawer(`<h2>Planner · Nemotron 3 Super</h2><div class="sub">chooses which change to test next; the gate on unseen runs decides what is kept</div>${gens}<h3>Reviewer after the exam</h3><div class="trace">${esc(d.critique?.text)}<div class="meta">${callMeta(d.critique?.call)}</div></div>`);
  } else if (id === 'VISION' || id === 'CAMS') {
    openDrawer(`<h2>Vision · Nemotron 3.5 Super VL</h2><div class="sub">one call per camera frame; issues open work orders</div>${CCTV.map((c) => `<div class="trace" style="cursor:pointer" data-cam="${c.id}"><span class="k">${c.id} · ${c.station}</span><div>${esc(c.result?.summary || c.result?.error || c.state)}</div><div class="meta">${(c.result?.detections || []).map((x) => esc(x.label)).join(' · ')}<br>${callMeta(c.result?.call)}</div></div>`).join('')}`);
    document.querySelectorAll('#drawer [data-cam]').forEach((el) => el.addEventListener('click', () => openCam(CCTV.find((c) => c.id === el.dataset.cam))));
  } else if (id === 'PILOT' || id.startsWith('ROVER')) {
    openDrawer(`<h2>Pilot · Nemotron 3.5 Lightning</h2><div class="sub">egocentric state in → drive commands out · the browser refuses moves into obstacles</div>${pilotLog.length ? pilotLog.map((p) => `<div class="trace"><span class="k">${esc(p.robot)} → ${esc(p.goal)} · ${esc(p.at)}</span><pre>in:  bearing ${p.bearing}° · distance ${p.distance} m · free m ${Object.entries(p.rays).map(([a, v]) => a + ':' + v.toFixed(1)).join(' ')}\nout: ${p.res.ok ? esc(p.res.commands.join(' ▸ ')) + '\nwhy: ' + esc(p.res.reason) : 'failed: ' + esc(p.res.error || 'no answer')}</pre><div class="meta">${callMeta(p.res.call)}</div></div>`).join('') : '<div class="trace">No pilot calls yet.</div>'}`);
  } else if (id === 'NANO') {
    const v = ev.filter((e) => e.robot === 'NANO').slice(-12).reverse();
    openDrawer(`<h2>Verifier · Nemotron Nano role</h2><div class="sub">added by the team in N+1 · in the six-month twin its sign-offs are simulated, not live calls</div>${v.length ? v.map((e) => `<div class="trace"><span class="k">day ${e.day} · ${e.station_id}</span><div>${esc(e.message)}</div></div>`).join('') : '<div class="trace">Run the replay to see sign-offs and rejections.</div>'}`);
  } else if (id === 'DISPATCH' || id.startsWith('ARM')) {
    const w = ev.filter((e) => ['work_order', 'dispatch', 'action', 'closed', 'unsafe', 'blocked'].includes(e.status) && (!id.startsWith('ARM') || e.robot === id || e.status === 'work_order')).slice(-14).reverse();
    openDrawer(`<h2>${id === 'DISPATCH' ? 'Dispatch · work orders' : id}</h2><div class="sub">from the six-month replay${id.startsWith('ARM') ? ' · ' + esc(arms[id]?.task) + ' at ' + esc(arms[id]?.station) : ''}</div>${w.length ? w.map((e) => `<div class="trace"><span class="k">day ${e.day} · ${e.robot} · ${e.station_id}</span><div>${esc(e.message)}</div></div>`).join('') : '<div class="trace">Run the replay to see work orders.</div>'}`);
  } else if (id === 'SENSOR') openSensors();
}

// ---------- 1-minute demo ----------
let capTimer = null;
function caption(title, sub = '') {
  const el = $('caption'); clearTimeout(capTimer);
  if (!title) { el.classList.remove('on'); return; }
  el.innerHTML = `${title}${sub ? `<small>${sub}</small>` : ''}`; el.classList.add('on');
}
function setPolicy(p) {
  state.policy = p; document.querySelectorAll('[data-policy]').forEach((x) => x.classList.toggle('on', x.dataset.policy === p));
  resetReplay(); state.playing = false;
}
async function runUntilDone() { state.playing = true; while (state.playing) await sleep(100); }
async function demo() {
  const d = state.data, base = d.replays.baseline.metrics, learned = d.replays.learned.metrics, gens = d.generations;
  window.__demoPhase = 'intro';
  caption('A real scanned plant room, run by Nemotron agents', 'cameras, sensors and two robot arms · six months in under a minute'); await sleep(4000);
  // Act 1: baseline
  window.__demoPhase = 'act1'; setPolicy('baseline'); state.speedMul = 5;
  caption('Team N: robots act without a second check');
  const watch = setInterval(() => { const f = state.feed?.[0]; if (f && f.status === 'unsafe' && !f.__shown) { f.__shown = true; caption('<span class="hot">Unsafe action executed</span>', f.message); } }, 150);
  await runUntilDone(); clearInterval(watch);
  caption(`Team N after six months: score ${base.score}`, `${base.unsafe_actions} unsafe actions · ${base.overdue} overdue · ${base.evidence_gaps} evidence gaps · ${base.stockouts} stock-outs`); await sleep(3500);
  // Act 2: learning
  window.__demoPhase = 'act2'; overview();
  for (let g = 1; g < gens.length; g++) {
    state.viewGen = g; drawGens();
    const G = gens[g], label = G.candidate.policy.replace(/^N\+\d+\s*/, '');
    caption(`${G.accepted ? 'N+' + g + ' kept' : '<span class="hot">N+' + g + ' rejected</span>'}: ${label}`, G.accepted ? `+${G.gate.mean_delta} on 12 unseen runs · ${G.gate.wins} wins / ${G.gate.losses} losses` : `faster, but ${G.heldout.unsafe_actions} unsafe actions on unseen runs`);
    await sleep(g === gens.length - 1 ? 3400 : 2300);
  }
  state.viewGen = d.accepted_policy.generation; drawGens();
  caption('Each kept change is re-tested by removing it', 'verifier −16 pts · deadline sweep −10 · reorder tool −0.8 (marginal)'.replace('−16', '−' + (d.ablation?.[0]?.score_drop ?? 16))); await sleep(3200);
  // Act 3: learned team, same run
  window.__demoPhase = 'act3'; setPolicy('learned'); state.speedMul = 5; state.visionAt = 14;
  caption(`Team N+${d.accepted_policy.generation}, same six months`); await sleep(1200);
  await runUntilDone();
  window.__demoPhase = 'end';
  caption(`${d.heldout_before.score} → ${d.heldout_after.score} on 12 unseen runs`, `unsafe actions ${d.heldout_before.unsafe_actions} → ${d.heldout_after.unsafe_actions} · this run: ${base.score} → ${learned.score}`);
  window.__demoDone = true;
}

// ---------- boot ----------
function sceneSettings() {
  const view = (s) => ({ initial: { position: s.view.position, target: s.view.target, fov: 62 } });
  return {
    version: 2, tonemapping: 'linear', highPrecisionRendering: false, background: { color: [0, 0, 0] },
    postEffectSettings: { sharpness: { enabled: false, amount: 0 }, bloom: { enabled: false, intensity: 1, blurLevel: 2 }, grading: { enabled: false, brightness: 1, contrast: 1, saturation: 1, tint: [1, 1, 1] }, vignette: { enabled: false, intensity: 0.5, inner: 0.3, outer: 0.75, curvature: 1 }, fringing: { enabled: false, intensity: 0.5 } },
    cameras: [{ initial: OVERVIEW }],
    animTracks: [],
    annotations: STATION_IDS.map((id) => ({ position: STATION_MAP[id].object.anchor, title: id, text: STATION_MAP[id].object.component_name, camera: view(STATION_MAP[id]) })),
    startMode: 'default',
  };
}

async function boot() {
  const dataPromise = fetch('/api/demo').then((r) => r.json());
  buildCams(); buildMeasures();
  viewer = await createViewer({ container: $('splat'), settings: sceneSettings(), contentUrl: SPLAT_URL, posterUrl: POSTER_URL, ui: false, noanim: true, renderer: params.get('renderer') || 'webgl' });
  window.__viewer = viewer;
  viewer.state.showAnnotations = false;
  viewer.events.on('progress:changed', (v) => { $('loading-bar').style.width = v + '%'; $('loading-text').textContent = `Loading plant room scan · 14.2M splats · ${Math.round(v)}%`; });
  app = viewer.app; window.__app = app;
  const ready = new Promise((res) => (viewer.state.loaded ? res() : viewer.events.once('loaded:changed', res)));
  const d = await dataPromise; state.data = d;
  $('learned-btn').textContent = `Team N+${d.accepted_policy.generation}`;
  buildArch(); buildLedger();
  await ready;
  camEntity = app.root.findByName('camera');
  app.scene.ambientLight = new pc.Color(0.55, 0.58, 0.6);
  const sun = new pc.Entity('key-light'); sun.addComponent('light', { type: 'directional', color: new pc.Color(1, 0.97, 0.9), intensity: 1.6, castShadows: false }); sun.setEulerAngles(50, 30, 0); app.root.addChild(sun);
  buildMaterials();
  STATION_IDS.forEach(buildStation);
  buildArm('ARM-01', 'S01'); buildArm('ARM-02', 'S07'); buildArm('ROVER-1', 'S08');
  Object.keys(CAMERA_MAP).forEach(buildCamera);
  resetReplay(); telemetryAt(0);
  let time = 0, last = performance.now(), slow = 0;
  const tick = (now) => {
    const dt = Math.min((now - last) / 1000, 0.1); last = now; time += dt; slow += dt;
    stepReplay(dt); telemetryAt(state.simT);
    for (const arm of Object.values(arms)) updateArm(arm, dt, time);
    for (const st of Object.values(stations)) { const s = 1.1 + 0.2 * Math.sin(time * 3 + st.anchor[0]); st.ring.setLocalScale(s, s, s); }
    for (const c of Object.values(cams)) { c.head.lookAt(new pc.Vec3(...c.aim)); c.flash = Math.max(0, c.flash - dt); c.cone.render.material = c.flash > 0 ? M.coneHot : M.cone; c.led.enabled = c.flash > 0 || Math.sin(time * 4) > 0; }
    const day = Math.min(180, state.simT), hh = Math.floor((day % 1) * 24);
    $('clock').textContent = `Day ${Math.floor(day)} · ${String(hh).padStart(2, '0')}:00`;
    drawTags(); drawArch(dt);
    if (slow > 0.12) { slow = 0; drawMeasures(); for (const c of CCTV) { c.flash = Math.max(0, c.flash - 0.12); const el = document.querySelector(`.cam[data-id="${c.id}"]`); el?.classList.toggle('flash', c.flash > 0); el?.classList.toggle('focus', state.focus === c.station); } }
    app.renderNextFrame = true;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  await captureCams();
  viewer.state.inputEnabled = true;
  $('loading').classList.add('done');
  if (state.view && STATION_MAP[state.view]) focusStation(state.view);
  window.__ready = true;
  if (!params.has('nopatrol')) patrol(arms['ROVER-1']);
  detectCams().then(() => { window.__vision = true; if (params.has('demo')) demo(); else if (params.has('play')) play(); });
}

function play() {
  const label = $('run').querySelector('span');
  if (state.playing) { state.playing = false; label.textContent = '▶ Resume'; return; }
  if (!state.run || state.simT >= 180) resetReplay();
  state.playing = true; label.textContent = '❚❚ Pause';
}
function setHud(on) { document.body.classList.toggle('hud-off', !on); }
$('run').addEventListener('click', play);
$('drawer-x').addEventListener('click', closeDrawer);
$('hud-toggle').addEventListener('click', () => setHud(false));
$('hud-restore').addEventListener('click', () => setHud(true));
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'h' || e.key === 'H') setHud(document.body.classList.contains('hud-off'));
  if (e.key === ' ') { e.preventDefault(); play(); }
  if (e.key === 'Escape') { if ($('drawer').classList.contains('on')) closeDrawer(); else overview(); }
});
document.querySelectorAll('[data-policy]').forEach((b) => b.addEventListener('click', () => {
  state.policy = b.dataset.policy; document.querySelectorAll('[data-policy]').forEach((x) => x.classList.toggle('on', x === b));
  resetReplay(); state.playing = false; $('run').querySelector('span').textContent = '▶ Run six months';
}));
window.__state = state;
boot().catch((e) => { $('loading-text').textContent = 'Scene failed: ' + e.message; window.__errs.push({ message: String(e.stack || e) }); });
