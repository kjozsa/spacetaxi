// Spacetaxi replay player: neon style (panel A of docs/design/style-test.html) with its effects:
// thruster flames, explosions, landing dust, pickup sparkles, cash popups, CRT screen, podium,
// and chiptune sound effects synthesised with Web Audio (no audio files).
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

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const crt = document.getElementById("crt");
const splash = document.getElementById("splash");

// The debug hook: the round and tick on screen, frames drawn in the last second, whether the
// CRT screen is on, the podium (top 3 names) when shown, a counter for every effect and every
// sound, whether the sound is muted and whether the INSERT COIN splash has been clicked.
const fx = { flames: 0, explosions: 0, dust: 0, pickups: 0, cash: 0, shakes: 0 };
const sfx = { hum: 0, boom: 0, chime: 0, cash: 0, jingle: 0 };
window.spacetaxi = { round: 0, tick: 0, fps: 0, scanlines: true, podium: null, fx, sfx,
                     muted: false, started: false, thrusting: 0 };

let R = null;            // the current round, with its layout and static layer
let tf = 0;              // position in the round: index into R.ticks plus a fraction
let playing = false, speed = 1, last = 0;
let fired = -1;          // the last tick index whose events have been turned into effects
let started = false;     // the splash has been clicked: the round may play, the sound is on
let parts = [], floaters = [], jumpers = [], rings = [], shake = 0;
const MAX_PARTS = 2500;
const frameTimes = [];
// A slow parallax starfield: three layers, the far ones smaller, dimmer and slower.
const stars = Array.from({ length: 260 }, (_, n) => {
  const z = [0.25, 0.5, 1][n % 3] * (0.8 + Math.random() * 0.4);
  return { x: Math.random() * SW, y: HUD + Math.random() * (SH - HUD - BAR), z };
});

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
    shown: data.players.map(() => 0),   // the leaderboard's money, counting up to the real one
  };
}

function selectRound(index) {
  R = prepareRound(ROUNDS[index]);
  roundSel.value = String(index);
  scrub.max = String(Math.max(0, R.ticks.length - 1));
  seek(0);
}

// Jumping to a position does not replay the effects of the ticks skipped over; position 0
// fires the first tick's events on the next frame.
function seek(position) {
  tf = Math.max(0, Math.min(position, Math.max(0, R.ticks.length - 1)));
  fired = tf === 0 ? -1 : Math.floor(tf);
  parts = []; floaters = []; jumpers = []; rings = []; shake = 0;
  if (R.ticks.length) R.shown = R.ticks[Math.floor(tf)].x.map(t => t[5]);
}

// ---------- effects ----------
function cellCenter(k, i) {
  const t = R.ticks[i].x[k], C = R.C;
  return { x: R.OX + t[0] * C + C / 2, y: R.OY + t[1] * C + C / 2 + 4 * (C / 32) };
}

function burst(x, y, n, color, spd, life, size, grav = 0) {
  for (let j = 0; j < n; j++) {
    const a = Math.random() * Math.PI * 2, v = Math.random() * spd;
    parts.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, life, max: life, color,
                 size: size * (0.5 + Math.random()), grav });
  }
}

// The effects of the events in tick index i, at the taxis' positions in that tick.
function fireEvents(i) {
  const k32 = R.C / 32;
  for (const ev of R.ticks[i].e) {
    const k = R.data.players.indexOf(ev.taxi);
    if (k < 0) continue;
    const { x, y } = cellCenter(k, i), color = COLORS[k % COLORS.length];
    if (ev.type === "crash") {
      fx.explosions++;
      sound("boom", "boom");
      burst(x, y, 70, color, 7 * k32, 50, 4 * k32, 0.12 * k32);
      burst(x, y, 30, "#ffffff", 4 * k32, 30, 3 * k32);
      burst(x, y, 20, "#ff8a3d", 3 * k32, 40, 5 * k32);
      rings.push({ x, y, color, age: 0, life: 28, r: 70 * k32 });
      if (!reduceMotion) { shake = 14; fx.shakes++; }
    } else if (ev.type === "landed") {
      fx.dust++;
      for (let s = -1; s <= 1; s += 2) for (let j = 0; j < 6; j++)
        parts.push({ x, y: y + 12 * k32, vx: s * (1 + Math.random() * 2) * k32, vy: -Math.random() * 0.6 * k32,
                     life: 22, max: 22, color: "#9a94c4", size: 2.5 * k32, grav: 0 });
    } else if (ev.type === "pickup") {
      fx.pickups++;
      sound("chime", "chime");
      burst(x, y, 20, "#ffd23f", 3 * k32, 30, 2.5 * k32);
      // The passenger jumps in from the nearest waiting spot on the pad under the taxi.
      const t = R.ticks[i].x[k];
      let from = x, best = Infinity;
      for (const [at, spot] of i > 0 ? R.ticks[i - 1].p : []) {
        const pad = R.padById[at];
        if (!pad || pad.y !== t[1] + 1 || Math.abs(spot - t[0]) >= best) continue;
        best = Math.abs(spot - t[0]); from = R.OX + spot * R.C + R.C / 2;
      }
      jumpers.push({ x0: from, x1: x, y, age: 0, life: 24 });
    } else if (ev.type === "deliver") {
      fx.cash++;
      sound("cash", "cash");
      floaters.push({ x, y: y - 10 * k32, text: `+$${ev.fare}`, life: 70, color: "#7dff5a" });
      burst(x, y, 26, "#7dff5a", 4 * k32, 40, 3 * k32, 0.05 * k32);
    } else if (ev.type === "respawn") {
      burst(x, y, 24, color, 2.5 * k32, 30, 2 * k32);
    }
  }
}

// Thruster flames, opposite to the move taxi p plays between tick index i and i+1; `step` is
// how many particles to emit this frame (a fraction is emitted with that probability).
function emitFlames(i, x, y, p, step) {
  const m = R.ticks[i].m ? R.ticks[i].m[p] : null;
  if (!m || (!m[0] && !m[1])) return;
  const color = COLORS[p % COLORS.length], k32 = R.C / 32;
  const n = Math.floor(step) + (Math.random() < step - Math.floor(step) ? 1 : 0);
  for (let j = 0; j < n; j++) {
    fx.flames++;
    if (m[1]) {
      const dir = -m[1];   // ay = -1 (thrust up) blows the flame down
      parts.push({ x: x + (Math.random() - 0.5) * 8 * k32, y: y + dir * 10 * k32,
                   vx: (Math.random() - 0.5) * 0.6 * k32, vy: dir * (2 + Math.random() * 2) * k32,
                   life: 16, max: 16, color: j % 2 ? "#ffd23f" : color, size: 3 * k32, grav: 0 });
    }
    if (m[0]) {
      const dir = -m[0];
      parts.push({ x: x + dir * 15 * k32, y: y + (Math.random() - 0.5) * 4 * k32,
                   vx: dir * (2 + Math.random() * 2) * k32, vy: (Math.random() - 0.5) * 0.5 * k32,
                   life: 12, max: 12, color, size: 2.5 * k32, grav: 0 });
    }
  }
}

// Particles (additive), passengers jumping in and the floating `+$fare` popups.
function drawEffects(dt) {
  const s = dt / 16, k32 = R.C / 32;
  ctx.globalCompositeOperation = "lighter";
  for (const q of parts) {
    q.x += q.vx * s; q.y += q.vy * s; q.vy += q.grav * s; q.life -= s;
    ctx.globalAlpha = Math.max(0, q.life / q.max); ctx.fillStyle = q.color;
    ctx.fillRect(q.x - q.size / 2, q.y - q.size / 2, q.size, q.size);
  }
  for (const g of rings) {
    g.age += s;
    const a = g.age / g.life;
    ctx.globalAlpha = Math.max(0, 1 - a); ctx.strokeStyle = g.color; ctx.lineWidth = 6 * (1 - a) + 1;
    ctx.beginPath(); ctx.arc(g.x, g.y, g.r * Math.sqrt(a), 0, 7); ctx.stroke();
  }
  rings = rings.filter(g => g.age < g.life);
  ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
  parts = parts.filter(q => q.life > 0);
  if (parts.length > MAX_PARTS) parts = parts.slice(-MAX_PARTS);

  for (const j of jumpers) {
    j.age += s;
    const g = Math.min(1, j.age / j.life), x = j.x0 + (j.x1 - j.x0) * g;
    const y = j.y - Math.sin(g * Math.PI) * 26 * k32, r = (1 - g * 0.6) * k32;
    ctx.save(); ctx.globalAlpha = 1 - g * 0.5; ctx.strokeStyle = "#f4f0ff"; ctx.lineWidth = 2 * k32;
    ctx.shadowColor = "#ffffff"; ctx.shadowBlur = 12;
    ctx.beginPath(); ctx.arc(x, y - 12 * r, 4 * r, 0, 7); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x, y - 8 * r); ctx.lineTo(x, y + 2 * r);
    ctx.moveTo(x - 6 * r, y - 10 * r); ctx.lineTo(x, y - 5 * r); ctx.lineTo(x + 6 * r, y - 10 * r);
    ctx.moveTo(x - 4 * r, y + 8 * r); ctx.lineTo(x, y + 2 * r); ctx.lineTo(x + 4 * r, y + 8 * r);
    ctx.stroke(); ctx.restore();
  }
  jumpers = jumpers.filter(j => j.age < j.life);

  ctx.font = `${Math.round(28 * Math.max(1, k32))}px ${FONT}`;
  ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  for (const fl of floaters) {
    fl.y -= dt * 0.05; fl.life -= s;
    ctx.globalAlpha = Math.max(0, Math.min(1, fl.life / 30));
    ctx.shadowColor = fl.color; ctx.shadowBlur = 16; ctx.fillStyle = fl.color;
    ctx.fillText(fl.text, fl.x, fl.y);
  }
  ctx.shadowBlur = 0; ctx.globalAlpha = 1;
  floaters = floaters.filter(f => f.life > 0);
}

// ---------- sound: chiptune effects synthesised with Web Audio ----------
// The AudioContext is created on the splash click (browsers block audio before one). Every
// sound goes through `master`, whose gain is 0 when muted; the counters in `sfx` count the
// sounds triggered, muted or not.
let audio = null, master = null, humGain = null, noiseBuf = null;
const VOLUME = 0.5;

function startAudio() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  try {
    audio = new AC();
    master = audio.createGain();
    master.gain.value = window.spacetaxi.muted ? 0 : VOLUME;
    master.connect(audio.destination);
    noiseBuf = audio.createBuffer(1, audio.sampleRate, audio.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let j = 0; j < d.length; j++) d[j] = Math.random() * 2 - 1;
    // The thrust hum: one shared voice, two detuned low sawtooths through a lowpass, its
    // volume set every frame from the number of thrusting taxis.
    const lp = audio.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 260;
    humGain = audio.createGain(); humGain.gain.value = 0;
    lp.connect(humGain).connect(master);
    for (const f of [55, 55.7]) {
      const o = audio.createOscillator(); o.type = "sawtooth"; o.frequency.value = f;
      o.connect(lp); o.start();
    }
    if (audio.state === "suspended") audio.resume().catch(() => {});
  } catch (e) {
    audio = null;
  }
}

// A square/triangle/sawtooth note from `at` (seconds, audio time) for `dur`, optionally sliding.
function tone(type, freq, at, dur, vol, slideTo) {
  const o = audio.createOscillator(), g = audio.createGain();
  o.type = type; o.frequency.setValueAtTime(freq, at);
  if (slideTo) o.frequency.exponentialRampToValueAtTime(slideTo, at + dur);
  g.gain.setValueAtTime(vol, at); g.gain.exponentialRampToValueAtTime(0.001, at + dur);
  o.connect(g).connect(master); o.start(at); o.stop(at + dur + 0.05);
}

// A burst of white noise through a lowpass whose cutoff falls from `from` to `to` Hz.
function noise(at, dur, vol, from, to) {
  const src = audio.createBufferSource(), lp = audio.createBiquadFilter(), g = audio.createGain();
  src.buffer = noiseBuf;
  lp.type = "lowpass"; lp.frequency.setValueAtTime(from, at);
  lp.frequency.exponentialRampToValueAtTime(to, at + dur);
  g.gain.setValueAtTime(vol, at); g.gain.exponentialRampToValueAtTime(0.001, at + dur);
  src.connect(lp).connect(g).connect(master); src.start(at); src.stop(at + dur + 0.05);
}

const SOUNDS = {
  boom(t) { noise(t, 0.7, 0.9, 1800, 60); tone("square", 140, t, 0.45, 0.35, 30); },
  chime(t) { tone("square", 988, t, 0.12, 0.12); tone("square", 1319, t + 0.07, 0.25, 0.12); },
  cash(t) {   // the register: a mechanical clack, then the bell
    noise(t, 0.06, 0.5, 6000, 2000);
    tone("triangle", 2093, t + 0.06, 0.5, 0.25); tone("square", 1568, t + 0.06, 0.12, 0.08);
    tone("triangle", 2637, t + 0.1, 0.45, 0.18);
  },
  jingleStart(t) {
    [523, 659, 784, 1047].forEach((f, j) => tone("square", f, t + j * 0.11, 0.14, 0.13));
    tone("triangle", 1047, t + 0.44, 0.4, 0.15);
  },
  jingleEnd(t) {
    [1047, 784, 659, 523].forEach((f, j) => tone("square", f, t + j * 0.14, 0.18, 0.13));
    tone("triangle", 262, t + 0.56, 0.6, 0.2);
  },
};

function sound(name, counter) {
  sfx[counter]++;
  if (!audio || window.spacetaxi.muted) return;
  try { SOUNDS[name](audio.currentTime + 0.01); } catch (e) { /* no sound is better than a crash */ }
}

// The hum follows the number of taxis thrusting right now (none while paused).
function updateHum(n) {
  const was = window.spacetaxi.thrusting;
  if (n === was) return;
  if (n && !was) sfx.hum++;
  window.spacetaxi.thrusting = n;
  if (!humGain) return;
  const vol = n ? Math.min(0.32, 0.1 + 0.06 * n) : 0;
  humGain.gain.setTargetAtTime(vol, audio.currentTime, 0.04);
}

function setMuted(on) {
  window.spacetaxi.muted = on;
  if (master) master.gain.setTargetAtTime(on ? 0 : VOLUME, audio.currentTime, 0.02);
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
    ctx.fillText(fitText(`$${Math.round(R.shown[k])}`, colW - 16), x, 92); ctx.shadowBlur = 0;
  });

  ctx.font = `16px ${FONT}`; ctx.textAlign = "right";
  tickerAt(i, 3).forEach(([txt, col], j) => {
    ctx.globalAlpha = 1 - j * 0.3; ctx.fillStyle = col;
    ctx.fillText(fitText(txt, SW - 32 - x1 - 16), SW - 32, 38 + j * 30);
  });
  ctx.globalAlpha = 1; ctx.textAlign = "left";
}

// The end-of-round podium: the top 3 by money, the winner in the middle on the tallest step.
function drawPodium(tk) {
  const order = R.data.players.map((_, k) => k).sort((a, b) => tk.x[b][5] - tk.x[a][5] || a - b).slice(0, 3);
  window.spacetaxi.podium = order.map(k => R.data.players[k]);
  const cx = SW / 2, base = R.OY + R.H * R.C / 2 + 230, stepW = 300;
  ctx.fillStyle = "rgba(4,3,11,.82)"; ctx.fillRect(cx - 560, base - 520, 1120, 560);
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.font = `48px ${FONT}`; ctx.fillStyle = "#ffd23f"; ctx.shadowColor = "#ffd23f"; ctx.shadowBlur = 24;
  ctx.fillText("ROUND OVER", cx, base - 460); ctx.shadowBlur = 0;
  const slots = [[0, 220], [-1, 160], [1, 110]];   // place: [column, step height]
  order.forEach((k, place) => {
    const [col, h] = slots[place], x = cx + col * stepW, color = COLORS[k % COLORS.length];
    ctx.fillStyle = "#0b0820"; ctx.fillRect(x - stepW / 2 + 10, base - h, stepW - 20, h);
    ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.shadowColor = color; ctx.shadowBlur = 18;
    ctx.strokeRect(x - stepW / 2 + 10, base - h, stepW - 20, h);
    ctx.font = `56px ${FONT}`; ctx.fillStyle = color; ctx.fillText(String(place + 1), x, base - h / 2);
    drawTaxi(x, base - h - 120, color, 1, 0, 2.6);
    ctx.font = `18px ${FONT}`; ctx.fillStyle = color; ctx.shadowBlur = 10;
    ctx.fillText(fitText(R.names[k], stepW - 24), x, base - h - 66);
    ctx.font = `26px ${FONT}`; ctx.fillStyle = "#ffffff"; ctx.shadowBlur = 8;
    ctx.fillText(`$${tk.x[k][5]}`, x, base - h - 30); ctx.shadowBlur = 0;
  });
  ctx.textBaseline = "alphabetic";
}

function draw(tnow, dt) {
  const ticks = R.ticks;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#04030b"; ctx.fillRect(0, 0, SW, SH);
  // A short screen shake after a crash (never with prefers-reduced-motion).
  const sx = shake ? (Math.random() - .5) * shake : 0, sy = shake ? (Math.random() - .5) * shake : 0;
  shake = Math.max(0, shake - dt * 0.04);
  ctx.translate(sx, sy);
  for (const s of stars) {
    s.x -= s.z * dt * 0.012; if (s.x < 0) s.x += SW;
    const d = Math.max(1, s.z * 2.2);
    ctx.fillStyle = `rgba(200,210,255,${0.2 + s.z * 0.6})`; ctx.fillRect(s.x, s.y, d, d);
  }
  ctx.drawImage(R.static, R.OX, R.OY);
  window.spacetaxi.podium = null;
  if (!ticks.length) return;
  const i = Math.floor(tf), f = tf - i, tk = ticks[i], C = R.C, k = C / 32;
  // About one flame particle per taxi every 16 ms of replay time.
  const flameStep = playing ? Math.min(6, dt / 16) : 0;

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
    if (flameStep) emitFlames(i, x, y, p, flameStep);
    drawTaxi(x, y, color, R.facing[p], pos.carry, 1.35 * k);
    ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 8;
    ctx.fillText(R.names[p], x, y - 18 * k - 6); ctx.shadowBlur = 0;
  }
  drawEffects(dt);

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (i === ticks.length - 1) drawPodium(tk);
  ctx.textAlign = "left";
  // The leaderboard counts up (or down) to the real money.
  R.shown = R.shown.map((v, p) => {
    const target = tk.x[p][5], d = target - v;
    return Math.abs(d) <= 1 ? target : v + Math.sign(d) * Math.max(1, Math.abs(d) * Math.min(1, dt / 120));
  });
  drawHud(i, tk);
}

// ---------- loop ----------
function frame(now) {
  const dt = Math.min(100, now - last); last = now;
  const end = Math.max(0, R.ticks.length - 1);
  if (playing) {
    tf += dt * speed / R.data.rules.tick_ms;
    if (tf >= end) { tf = end; setPlaying(false); sound("jingleEnd", "jingle"); }
  }
  if (started && R.ticks.length) while (fired < Math.floor(tf)) fireEvents(++fired);
  let thrusting = 0;
  if (playing && R.ticks.length) {
    const tk = R.ticks[Math.floor(tf)];
    tk.x.forEach((t, p) => {
      const m = tk.m ? tk.m[p] : null;
      if (t[3] !== CRASHED && m && (m[0] || m[1])) thrusting++;
    });
  }
  updateHum(thrusting);
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
// The round does not play before the splash is clicked; playing from its first tick sounds the
// start jingle.
function setPlaying(on) {
  if (on && !started) return;
  if (on && tf >= R.ticks.length - 1) seek(0);
  if (on && !playing && tf === 0) sound("jingleStart", "jingle");
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
  } else if (e.key === "c" || e.key === "C") {
    e.preventDefault();
    setScanlines(!window.spacetaxi.scanlines);
  } else if (e.key === "m" || e.key === "M") {
    e.preventDefault();
    setMuted(!window.spacetaxi.muted);
  }
});
function setScanlines(on) {
  window.spacetaxi.scanlines = on;
  crt.hidden = !on;
}

// INSERT COIN: the click turns the sound on and starts the round.
splash.addEventListener("click", () => {
  if (started) return;
  started = true;
  window.spacetaxi.started = true;
  splash.hidden = true;
  startAudio();
  if (R) setPlaying(true);
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
  setPlaying(started);
  requestAnimationFrame(t => { last = t; frame(t); });
});
