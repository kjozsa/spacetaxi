// Spacetaxi replay player: neon style (panel A of docs/design/style-test.html), no effects.
// The replay is embedded in the page as JSON: one entry per round, see spacetaxi/viewer.py.
"use strict";

const ROUNDS = JSON.parse(document.getElementById("replay").textContent);
const SW = 1920, SH = 1080, HUD = 120, BAR = 72;
const FONT = "'Press Start 2P', monospace";
const COLORS = ["#2ef2ff", "#ff3df0", "#ffd23f", "#7dff5a", "#ff8a3d", "#a98bff", "#ff5a6e", "#ffffff"];
const SPEEDS = { "1": 0.5, "2": 1, "3": 2, "4": 4 };
const FLYING = 0, LANDED = 1, CRASHED = 2;

const stage = document.getElementById("stage");
const cv = document.getElementById("board"), ctx = cv.getContext("2d");
const playBtn = document.getElementById("play");
const scrub = document.getElementById("scrub");
const roundSel = document.getElementById("round");
const tickLabel = document.getElementById("tickLabel");

// The debug hook: the round and tick on screen, and frames drawn in the last second.
window.spacetaxi = { round: 0, tick: 0, fps: 0 };

let R = null;            // the current round, with its layout and static layer
let tf = 0;              // position in the round: index into R.ticks plus a fraction
let playing = false, speed = 1, last = 0;
const frameTimes = [];
const stars = Array.from({ length: 220 }, () =>
  ({ x: Math.random() * SW, y: HUD + Math.random() * (SH - HUD - BAR), z: Math.random() * .8 + .2 }));

// ---------- layout: 1920x1080, scaled to the window keeping proportions ----------
function fit() {
  const s = Math.min(window.innerWidth / SW, window.innerHeight / SH);
  stage.style.transform = `scale(${s})`;
  stage.style.left = `${(window.innerWidth - SW * s) / 2}px`;
  stage.style.top = `${(window.innerHeight - SH * s) / 2}px`;
}
window.addEventListener("resize", fit);

// ---------- one round: layout and the static layer (walls, pads) ----------
function prepareRound(data) {
  const map = data.map, H = map.length, W = map[0].length;
  const C = Math.min(Math.floor(SW / W), Math.floor((SH - HUD - BAR) / H));
  const OX = Math.floor((SW - W * C) / 2), OY = HUD + Math.floor((SH - HUD - BAR - H * C) / 2);
  const isWall = (x, y) => x < 0 || y < 0 || x >= W || y >= H || map[y][x] === "#";
  const st = document.createElement("canvas"); st.width = W * C; st.height = H * C;
  const s = st.getContext("2d");
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (isWall(x, y)) {
    s.fillStyle = "#0b0820"; s.fillRect(x * C, y * C, C, C);
    s.fillStyle = "rgba(120,90,255,.06)";
    for (let i = 4; i < C; i += 8) s.fillRect(x * C + i, y * C, 1, C);
  }
  const edge = () => {
    s.beginPath();
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (isWall(x, y)) {
      const X = x * C, Y = y * C;
      if (!isWall(x, y - 1) && y > 0) { s.moveTo(X, Y + .5); s.lineTo(X + C, Y + .5); }
      if (!isWall(x, y + 1) && y < H - 1) { s.moveTo(X, Y + C - .5); s.lineTo(X + C, Y + C - .5); }
      if (!isWall(x - 1, y) && x > 0) { s.moveTo(X + .5, Y); s.lineTo(X + .5, Y + C); }
      if (!isWall(x + 1, y) && x < W - 1) { s.moveTo(X + C - .5, Y); s.lineTo(X + C - .5, Y + C); }
    }
    s.stroke();
  };
  s.lineCap = "square";
  s.strokeStyle = "#7a5cff"; s.lineWidth = 2; s.shadowColor = "#7a5cff"; s.shadowBlur = 18; edge(); edge();
  s.shadowBlur = 0; s.strokeStyle = "#cbb8ff"; s.lineWidth = 1; edge();
  const k = C / 32;
  for (const p of data.pads) {
    const X = p.x * C, Y = p.y * C, w = p.len * C;
    s.fillStyle = "#1a1300"; s.fillRect(X, Y, w, C);
    s.shadowColor = "#ffd23f"; s.shadowBlur = 22; s.fillStyle = "#ffd23f"; s.fillRect(X + 2, Y, w - 4, 5 * k);
    s.shadowBlur = 0; s.fillStyle = "#fff4c2"; s.fillRect(X + 2, Y, w - 4, 2 * k);
    s.font = `${Math.round(20 * k)}px ${FONT}`; s.textAlign = "center"; s.textBaseline = "middle";
    s.fillStyle = "#ffd23f"; s.shadowColor = "#ffd23f"; s.shadowBlur = 10;
    s.fillText(p.id, X + w / 2, Y + C * 0.62); s.shadowBlur = 0;
  }
  const names = data.players.map(n => n.toUpperCase());
  return {
    data, ticks: data.ticks, W, H, C, OX, OY, static: st, names,
    padById: Object.fromEntries(data.pads.map(p => [p.id, p])),
    facing: data.players.map(() => 1),
  };
}

function selectRound(index) {
  R = prepareRound(ROUNDS[index]);
  roundSel.value = String(index);
  scrub.max = String(Math.max(0, R.ticks.length - 1));
  seek(0);
}

function seek(position) {
  tf = Math.max(0, Math.min(position, Math.max(0, R.ticks.length - 1)));
}

// ---------- drawing ----------
// A taxi's position between tick i and i+1; no interpolation across a crash or a respawn.
function taxiPos(i, f, k) {
  const ticks = R.ticks, a = ticks[i].x[k], b = ticks[Math.min(i + 1, ticks.length - 1)].x[k];
  const jump = a[3] === CRASHED || b[3] === CRASHED;
  const g = jump ? 0 : f;
  return { x: a[0] + (b[0] - a[0]) * g, y: a[1] + (b[1] - a[1]) * g, state: a[3], carry: a[4], vx: a[2] };
}

function drawTaxi(x, y, color, facing, carry, scale) {
  ctx.save(); ctx.translate(x, y); ctx.scale(facing * scale, scale);
  ctx.shadowColor = color; ctx.shadowBlur = 20; ctx.strokeStyle = color; ctx.lineWidth = 2.5;
  ctx.fillStyle = "rgba(10,8,25,.9)";
  ctx.beginPath(); ctx.roundRect(-13, -5, 26, 12, 4); ctx.fill(); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(-6, -5); ctx.lineTo(-3, -11); ctx.lineTo(7, -11); ctx.lineTo(10, -5); ctx.stroke();
  ctx.shadowBlur = 0; ctx.fillStyle = color; ctx.fillRect(-11, 8, 5, 2); ctx.fillRect(6, 8, 5, 2);
  if (carry) {
    ctx.fillStyle = "#ffd23f"; ctx.shadowColor = "#ffd23f"; ctx.shadowBlur = 12;
    ctx.beginPath(); ctx.arc(2, -7, 2.5, 0, 7); ctx.fill();
  }
  ctx.restore();
}

function drawPassenger(x, y, tnow, seed, k) {
  const bob = Math.sin(tnow / 180 + seed) * 1.5 * k, wave = Math.sin(tnow / 120 + seed) * 4 * k;
  ctx.save(); ctx.shadowColor = "#ffffff"; ctx.shadowBlur = 12; ctx.strokeStyle = "#f4f0ff"; ctx.lineWidth = 2 * k;
  ctx.beginPath(); ctx.arc(x, y - 19 * k + bob, 4 * k, 0, 7); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x, y - 15 * k + bob); ctx.lineTo(x, y - 6 * k);
  ctx.moveTo(x, y - 6 * k); ctx.lineTo(x - 4 * k, y);
  ctx.moveTo(x, y - 6 * k); ctx.lineTo(x + 4 * k, y);
  ctx.moveTo(x, y - 12 * k); ctx.lineTo(x + 6 * k, y - 18 * k + wave);
  ctx.moveTo(x, y - 12 * k); ctx.lineTo(x - 5 * k, y - 9 * k);
  ctx.stroke(); ctx.restore();
}

// The latest pickup, deliver and crash events up to tick index i, newest first.
function tickerAt(i, n) {
  const out = [];
  for (let j = i; j >= 0 && out.length < n; j--) {
    const evs = R.ticks[j].e;
    for (let e = evs.length - 1; e >= 0 && out.length < n; e--) {
      const ev = evs[e], k = R.data.players.indexOf(ev.taxi);
      if (k < 0) continue;
      const name = R.names[k];
      if (ev.type === "pickup") out.push([`${name} PICKED UP`, "#ffd23f"]);
      else if (ev.type === "deliver") out.push([`${name} +$${ev.fare}`, "#7dff5a"]);
      else if (ev.type === "crash") out.push([`${name} CRASHED`, "#ff5a6e"]);
    }
  }
  return out;
}

function fitText(text, width) {
  if (ctx.measureText(text).width <= width) return text;
  while (text.length > 1 && ctx.measureText(text + ".").width > width) text = text.slice(0, -1);
  return text + ".";
}

function drawHud(i, tk) {
  const rules = R.data.rules, n = R.data.players.length;
  const g = ctx.createLinearGradient(0, 0, 0, HUD); g.addColorStop(0, "#120d2e"); g.addColorStop(1, "#07050f");
  ctx.fillStyle = g; ctx.fillRect(0, 0, SW, HUD);
  ctx.fillStyle = "#7a5cff"; ctx.shadowColor = "#7a5cff"; ctx.shadowBlur = 14; ctx.fillRect(0, HUD - 2, SW, 2); ctx.shadowBlur = 0;
  ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  const left = Math.max(0, rules.round_ticks - tk.t);
  const secs = Math.ceil(left * rules.tick_ms / 1000);
  ctx.font = `20px ${FONT}`; ctx.fillStyle = "#9a94c4";
  ctx.fillText(`ROUND ${R.data.round}/${ROUNDS[ROUNDS.length - 1].round}`, 32, 44);
  ctx.font = `40px ${FONT}`; ctx.fillStyle = "#ffffff"; ctx.shadowColor = "#2ef2ff"; ctx.shadowBlur = 12;
  ctx.fillText(`${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`, 32, 96); ctx.shadowBlur = 0;

  const x0 = 330, x1 = 1440, colW = Math.min(280, (x1 - x0) / Math.max(1, n));
  const order = R.data.players.map((_, k) => k).sort((a, b) => tk.x[b][5] - tk.x[a][5] || a - b);
  order.forEach((k, r) => {
    const x = x0 + r * colW, color = COLORS[k % COLORS.length];
    ctx.font = `${n > 4 ? 14 : 18}px ${FONT}`; ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 10;
    ctx.fillText(fitText(`${r + 1}. ${R.names[k]}`, colW - 16), x, 46);
    ctx.font = `${n > 4 ? 22 : 30}px ${FONT}`; ctx.fillStyle = "#ffffff"; ctx.shadowBlur = 8;
    ctx.fillText(fitText(`$${tk.x[k][5]}`, colW - 16), x, 92); ctx.shadowBlur = 0;
  });

  ctx.font = `16px ${FONT}`; ctx.textAlign = "right";
  tickerAt(i, 3).forEach(([txt, col], j) => {
    ctx.globalAlpha = 1 - j * 0.3; ctx.fillStyle = col;
    ctx.fillText(fitText(txt, SW - 32 - x1 - 16), SW - 32, 38 + j * 30);
  });
  ctx.globalAlpha = 1; ctx.textAlign = "left";
}

function draw(tnow, dt) {
  const ticks = R.ticks;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#04030b"; ctx.fillRect(0, 0, SW, SH);
  for (const s of stars) {
    s.x -= s.z * dt * 0.012; if (s.x < 0) s.x += SW;
    ctx.fillStyle = `rgba(200,210,255,${0.25 + s.z * 0.6})`; ctx.fillRect(s.x, s.y, s.z * 2, s.z * 2);
  }
  ctx.drawImage(R.static, R.OX, R.OY);
  if (!ticks.length) return;
  const i = Math.floor(tf), f = tf - i, tk = ticks[i], C = R.C, k = C / 32;

  for (const [at, spot] of tk.p) {
    const pad = R.padById[at]; if (!pad) continue;
    drawPassenger(R.OX + spot * C + C / 2, R.OY + pad.y * C - 3 * k, tnow, spot, k);
  }

  ctx.font = `14px ${FONT}`; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  for (let p = 0; p < R.data.players.length; p++) {
    const pos = taxiPos(i, f, p); if (pos.state === CRASHED) continue;
    if (pos.vx) R.facing[p] = Math.sign(pos.vx);
    const color = COLORS[p % COLORS.length];
    const x = R.OX + pos.x * C + C / 2, y = R.OY + pos.y * C + C / 2 + 4 * k;
    drawTaxi(x, y, color, R.facing[p], pos.carry, 1.35 * k);
    ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 8;
    ctx.fillText(R.names[p], x, y - 18 * k - 6); ctx.shadowBlur = 0;
  }

  if (i === ticks.length - 1) {
    const cy = R.OY + R.H * C / 2;
    ctx.fillStyle = "rgba(4,3,11,.8)"; ctx.fillRect(SW / 2 - 400, cy - 60, 800, 120);
    ctx.font = `56px ${FONT}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillStyle = "#ffd23f"; ctx.shadowColor = "#ffd23f"; ctx.shadowBlur = 24;
    ctx.fillText("ROUND OVER", SW / 2, cy); ctx.shadowBlur = 0;
    ctx.textBaseline = "alphabetic";
  }
  ctx.textAlign = "left";
  drawHud(i, tk);
}

// ---------- loop ----------
function frame(now) {
  const dt = Math.min(100, now - last); last = now;
  const end = Math.max(0, R.ticks.length - 1);
  if (playing) {
    tf += dt * speed / R.data.rules.tick_ms;
    if (tf >= end) { tf = end; setPlaying(false); }
  }
  frameTimes.push(now);
  while (frameTimes.length && frameTimes[0] <= now - 1000) frameTimes.shift();
  draw(now, playing ? dt * speed : dt * 0.25);
  const tick = R.ticks.length ? R.ticks[Math.floor(tf)].t : 0;
  window.spacetaxi.round = R.data.round;
  window.spacetaxi.tick = tick;
  window.spacetaxi.fps = frameTimes.length;
  if (Number(scrub.value) !== Math.floor(tf)) scrub.value = String(Math.floor(tf));
  tickLabel.textContent = `TICK ${tick}/${R.data.rules.round_ticks}`;
  requestAnimationFrame(frame);
}

// ---------- controls ----------
function setPlaying(on) {
  if (on && tf >= R.ticks.length - 1) seek(0);
  playing = on;
  playBtn.textContent = on ? "PAUSE" : "PLAY";
  playBtn.setAttribute("aria-pressed", String(on));
}
function setSpeed(v) {
  speed = v;
  document.querySelectorAll("[data-speed]").forEach(b =>
    b.setAttribute("aria-pressed", String(Number(b.dataset.speed) === v)));
}
playBtn.addEventListener("click", () => setPlaying(!playing));
document.querySelectorAll("[data-speed]").forEach(b =>
  b.addEventListener("click", () => setSpeed(Number(b.dataset.speed))));
scrub.addEventListener("input", () => seek(Number(scrub.value)));
roundSel.addEventListener("change", () => { setPlaying(false); selectRound(Number(roundSel.value)); });
window.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === " ") {
    e.preventDefault();
    if (document.activeElement) document.activeElement.blur();
    setPlaying(!playing);
  } else if (SPEEDS[e.key]) {
    e.preventDefault();
    setSpeed(SPEEDS[e.key]);
  }
});

ROUNDS.forEach((r, idx) => {
  const opt = document.createElement("option");
  opt.value = String(idx); opt.textContent = `ROUND ${r.round}`;
  roundSel.appendChild(opt);
});
fit();
setSpeed(1);
const fontReady = document.fonts ? document.fonts.load(`16px ${FONT}`).catch(() => {}) : Promise.resolve();
fontReady.then(() => {
  selectRound(0);
  setPlaying(false);
  requestAnimationFrame(t => { last = t; frame(t); });
});
