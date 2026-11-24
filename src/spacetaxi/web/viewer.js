// Spacetaxi replay player: neon style (panel A of docs/design/style-test.html) with its effects:
// thruster flames, explosions, landing dust, pickup sparkles, cash popups, CRT screen, podium,
// and chiptune sound effects synthesised with Web Audio (no audio files).
// The replay is embedded in the page as JSON: one entry per round, see spacetaxi/viewer.py.
// In live mode (`spacetaxi serve`'s /watch) the rounds come from the /spectate socket instead,
// see the end of this file; the one embedded round is the arena's empty map. In fly mode
// (/fly) the page is a /bot client itself: a person flies one taxi with the arrow keys or the
// touch buttons, and its rounds come from the bot socket.
"use strict";

const ROUNDS = JSON.parse(document.getElementById("replay").textContent);
const MODE = document.body.dataset.mode;
const FLY = MODE === "fly";
const LIVE = MODE === "live" || FLY;
const SW = 1920, SH = 1080, HUD = 120, BAR = 72;
const AW = MODE === "live" ? SW - 420 : SW;   // the width the arena is laid out in, beside the leaderboard
const FONT = "'Press Start 2P', monospace";
const COLORS = ["#2ef2ff", "#ff3df0", "#ffd23f", "#7dff5a", "#ff8a3d", "#a98bff", "#ff5a6e", "#ffffff"];
const SPEEDS = { "1": 0.5, "2": 1, "3": 2, "4": 4 };
const FLYING = 0, LANDED = 1, CRASHED = 2;

const stage = document.getElementById("stage");
const cv = document.getElementById("board"), ctx = cv.getContext("2d");
const playBtn = document.getElementById("play");
const soundBtn = document.getElementById("sound");
const scrub = document.getElementById("scrub");
const roundSel = document.getElementById("round");
const tickLabel = document.getElementById("tickLabel");

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const crt = document.getElementById("crt");
const splash = document.getElementById("splash");

// The debug hook: the round and tick on screen, frames drawn in the last second, whether the
// CRT screen is on, the podium (top 3 names) when shown, a counter for every effect and every
// sound, whether the sound is muted, how many taxis thrust right now (the hum's volume),
// whether the INSERT COIN splash has been clicked (the round may play, the sound is on) and
// whether the AudioContext has been set up, whether the debug overlay (key D) is on and the
// text of each taxi's debug bubble ("" for none, in player order; all "" with the overlay off),
// each taxi's drawn centre on the 1920x1080 stage (`[x, y]`, null when crashed, in player
// order), the texts of the destination tags drawn, their boxes on the stage (`[x, y, w, h]`, in
// the same order) and the ids of the pads pulsing as the
// destination of a carried passenger, the human players of the round and the players whose
// taxi has the HUMAN tag or (fly mode) the YOU marker this frame. Live: `mapName` and `map`
// (its rows) of the map drawn (the round's, from its round_start), `nextMap` the next wave's.
const fx = { flames: 0, explosions: 0, dust: 0, pickups: 0, cash: 0, shakes: 0 };
const sfx = { hum: 0, boom: 0, chime: 0, cash: 0, jingle: 0, fanfare: 0 };
window.spacetaxi = { round: 0, tick: 0, fps: 0, scanlines: true, podium: null, fx, sfx,
                     muted: false, started: false, audio: false, thrusting: 0,
                     overlay: false, bubbles: [], taxis: [], tags: [], tagBoxes: [], pulsing: [],
                     humans: [], marked: [] };

let R = null;            // the current round, with its layout and static layer
let tf = 0;              // position in the round: index into R.ticks plus a fraction
let playing = false, speed = 1, last = 0;
let fired = -1;          // the last tick index whose events have been turned into effects
let parts = [], floaters = [], jumpers = [], rings = [], shake = 0;
const MAX_PARTS = 2500;
const frameTimes = [];
// A slow parallax starfield: three layers, the far ones smaller, dimmer and slower.
const stars = Array.from({ length: 260 }, (_, n) => {
  const z = [0.25, 0.5, 1][n % 3] * (0.8 + Math.random() * 0.4);
  return { x: Math.random() * SW, y: HUD + Math.random() * (SH - HUD - BAR), z };
});

// ---------- layout: 1920x1080, scaled to the window keeping proportions ----------
// On /fly the server's page bar (spacetaxi.nav) sits at the bottom of the window: the stage fits above it.
function fit() {
  const nav = document.querySelector("nav.sitenav");
  const h = window.innerHeight - (nav ? nav.offsetHeight : 0);
  const s = Math.min(window.innerWidth / SW, h / SH);
  stage.style.transform = `scale(${s})`;
  stage.style.left = `${(window.innerWidth - SW * s) / 2}px`;
  stage.style.top = `${(h - SH * s) / 2}px`;
}
window.addEventListener("resize", fit);
window.addEventListener("DOMContentLoaded", fit);   // the page bar comes after this script

// ---------- one round: layout and the static layer (walls, pads) ----------
function prepareRound(data) {
  const map = data.map, H = map.length, W = map[0].length;
  const C = Math.min(Math.floor(AW / W), Math.floor((SH - HUD - BAR) / H));
  const OX = Math.floor((AW - W * C) / 2), OY = HUD + Math.floor((SH - HUD - BAR - H * C) / 2);
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
    humans: new Set(data.humans || []),
    house: new Set(data.house || []),
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
// A taxi's centre on the stage in tick index i: its position is in cells (see viewer.py).
function cellCenter(k, i) {
  const t = R.ticks[i].x[k], C = R.C;
  return { x: R.OX + t[0] * C, y: R.OY + t[1] * C };
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
      sound("boom");
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
      sound("chime");
      burst(x, y, 20, "#ffd23f", 3 * k32, 30, 2.5 * k32);
      // The passenger jumps in from the nearest waiting spot on the pad under the taxi.
      const t = R.ticks[i].x[k], cx = Math.floor(t[0]), cy = Math.floor(t[1]);
      let from = x, best = Infinity;
      for (const [at, spot] of i > 0 ? R.ticks[i - 1].p : []) {
        const pad = R.padById[at];
        if (!pad || pad.y !== cy + 1 || Math.abs(spot - cx) >= best) continue;
        best = Math.abs(spot - cx); from = R.OX + spot * R.C + R.C / 2;
      }
      jumpers.push({ x0: from, x1: x, y, age: 0, life: 24 });
    } else if (ev.type === "deliver") {
      fx.cash++;
      sound("cash");
      floaters.push({ x, y: y - 10 * k32, text: `+$${ev.fare}`, life: 70, color: "#7dff5a" });
      burst(x, y, 26, "#7dff5a", 4 * k32, 40, 3 * k32, 0.05 * k32);
    } else if (ev.type === "respawn") {
      burst(x, y, 24, color, 2.5 * k32, 30, 2 * k32);
    }
  }
}

// Whether taxi p thrusts between tick index i and i+1.
function thrusts(i, p) {
  const m = R.ticks[i].m ? R.ticks[i].m[p] : null;
  return Boolean(m && (m[0] || m[1]));
}

// Thruster flames, opposite to the move taxi p plays between tick index i and i+1; `step` is
// how many particles to emit this frame (a fraction is emitted with that probability).
function emitFlames(i, x, y, p, step) {
  if (!thrusts(i, p)) return;
  const m = R.ticks[i].m[p];
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
  if (!window.AudioContext) return;
  audio = new AudioContext();
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
  if (audio.state === "suspended") audio.resume();
  window.spacetaxi.audio = true;
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
  fanfare(t) {   // a milestone reached: a short rising call
    [784, 988, 1175].forEach((f, j) => tone("square", f, t + j * 0.08, 0.1, 0.12));
    tone("square", 1568, t + 0.24, 0.35, 0.14, 1760);
  },
  jingle(t, end) {   // a rising arpeggio at the start of a round, a falling one at its end
    if (!end) {
      [523, 659, 784, 1047].forEach((f, j) => tone("square", f, t + j * 0.11, 0.14, 0.13));
      tone("triangle", 1047, t + 0.44, 0.4, 0.15);
    } else {
      [1047, 784, 659, 523].forEach((f, j) => tone("square", f, t + j * 0.14, 0.18, 0.13));
      tone("triangle", 262, t + 0.56, 0.6, 0.2);
    }
  },
};

function sound(name, ...args) {
  sfx[name]++;
  if (audio && !window.spacetaxi.muted) SOUNDS[name](audio.currentTime + 0.01, ...args);
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

// The M key and the bar's sound button both come here, so the button always shows the state.
function setMuted(on) {
  window.spacetaxi.muted = on;
  soundBtn.textContent = on ? "SOUND OFF" : "SOUND ON";
  soundBtn.setAttribute("aria-pressed", String(!on));
  if (master) master.gain.setTargetAtTime(on ? 0 : VOLUME, audio.currentTime, 0.02);
}

// ---------- drawing ----------
// A taxi's position between tick i and i+1; no interpolation across a crash or a respawn.
function taxiPos(i, f, k) {
  const ticks = R.ticks, a = ticks[i].x[k], b = ticks[Math.min(i + 1, ticks.length - 1)].x[k];
  const jump = a[3] === CRASHED || b[3] === CRASHED;
  const g = jump ? 0 : f;
  return { x: a[0] + (b[0] - a[0]) * g, y: a[1] + (b[1] - a[1]) * g, state: a[3], carry: a[4], vx: a[2],
           to: a[6] };
}

// A destination tag: `text` in a dark box outlined in `color`, its baseline at y.
function tag(x, y, text, color, k) {
  const m = Math.max(1, k);
  ctx.save();
  ctx.font = `${Math.round(13 * m)}px ${FONT}`; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  const w = ctx.measureText(text).width + 12 * m, h = 20 * m;
  ctx.fillStyle = "rgba(7,6,15,.85)"; ctx.fillRect(x - w / 2, y - 14 * m, w, h);
  ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.shadowColor = color; ctx.shadowBlur = 10;
  ctx.strokeRect(x - w / 2, y - 14 * m, w, h);
  ctx.fillStyle = color; ctx.fillText(text, x, y + 1);
  ctx.restore();
  window.spacetaxi.tags.push(text);
  window.spacetaxi.tagBoxes.push([x - w / 2, y - 14 * m, w, h]);
}

// The destination pad of a carried passenger: a green outline around the pad and the cell
// above it, pulsing.
function drawPulse(pad, tnow) {
  const C = R.C, k = C / 32, a = 0.65 + 0.35 * Math.sin(tnow / 160);
  const x = R.OX + pad.x * C - 4 * k, y = R.OY + (pad.y - 1) * C - 4 * k, w = pad.len * C + 8 * k, h = 2 * C + 8 * k;
  ctx.save(); ctx.globalAlpha = a; ctx.fillStyle = "rgba(125,255,90,.14)"; ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = "#7dff5a"; ctx.lineWidth = 3 * Math.max(1, k);
  ctx.shadowColor = "#7dff5a"; ctx.shadowBlur = 24;
  ctx.strokeRect(x, y, w, h);
  ctx.restore();
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
  ctx.fillText(FLY ? `ROUND ${R.data.round}` : LIVE ? `ARENA ${R.data.arena} R${R.data.round}`
                    : `ROUND ${R.data.round}/${ROUNDS[ROUNDS.length - 1].round}`, 32, 44);
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

  if (FLY) { drawGauges(); return; }
  ctx.font = `16px ${FONT}`; ctx.textAlign = "right";
  tickerAt(i, 3).forEach(([txt, col], j) => {
    ctx.globalAlpha = 1 - j * 0.3; ctx.fillStyle = col;
    ctx.fillText(fitText(txt, SW - 32 - x1 - 16), SW - 32, 38 + j * 30);
  });
  ctx.globalAlpha = 1; ctx.textAlign = "left";
}

// Fly mode: the player's VY and VX against the soft-landing limits (green: safe), as in play.
function gauge(x, y, label, v, limit, downOnly) {
  const w = 200, h = 12, m = R.data.rules.max_speed, px = n => x + (n + m) / (2 * m) * w;
  ctx.fillStyle = "#1a1530"; ctx.fillRect(x, y, w, h);
  ctx.fillStyle = "rgba(125,255,90,.25)";
  if (downOnly) ctx.fillRect(px(-m), y, px(limit) - px(-m), h);
  else ctx.fillRect(px(-limit), y, px(limit) - px(-limit), h);
  const safe = downOnly ? v <= limit : Math.abs(v) <= limit;
  ctx.fillStyle = safe ? "#7dff5a" : "#ff5a6e"; ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 10;
  ctx.fillRect(px(Math.max(-m, Math.min(m, v))) - 3, y - 5, 6, h + 10); ctx.shadowBlur = 0;
  ctx.font = `14px ${FONT}`; ctx.textAlign = "left"; ctx.fillStyle = "#9a94c4";
  ctx.fillText(`${label} ${v}`, x, y - 12);
}
function drawGauges() {
  const me = window.spacetaxi.me, rules = R.data.rules;
  if (!me || !R.ticks.length) return;
  gauge(1450, 46, "VY", me.vy, rules.safe_landing_vy, true);
  gauge(1690, 46, "VX", me.vx, rules.safe_landing_vx, false);
  ctx.font = `14px ${FONT}`; ctx.textAlign = "left";
  ctx.fillStyle = me.state === "landed" ? "#7dff5a" : me.state === "crashed" ? "#ff5a6e" : "#9a94c4";
  ctx.fillText(me.state.toUpperCase() + (me.pad ? ` ON ${me.pad}` : "")
               + (me.state === "crashed" ? ` ${me.respawn_in}` : ""), 1450, 100);
}

// Fly mode: the player's own taxi gets a pulsing dashed white ring and a YOU arrow above its
// name (and above its TO tag when it carries a passenger); bots' taxis have neither.
function drawYou(x, y, tnow, k, carrying) {
  const a = 0.6 + 0.4 * Math.sin(tnow / 140), m = Math.max(1, k);
  ctx.save();
  ctx.globalAlpha = a; ctx.strokeStyle = "#ffffff"; ctx.shadowColor = "#ffffff"; ctx.shadowBlur = 16;
  ctx.lineWidth = 3 * m; ctx.setLineDash([8 * k, 6 * k]);
  ctx.beginPath(); ctx.arc(x, y, 24 * k, 0, 7); ctx.stroke(); ctx.setLineDash([]);
  ctx.globalAlpha = 1; ctx.fillStyle = "#ffffff";
  const ty = y - 14 * k - (carrying ? 60 : 30) * m;
  ctx.beginPath(); ctx.moveTo(x - 9 * m, ty - 12 * m); ctx.lineTo(x + 9 * m, ty - 12 * m); ctx.lineTo(x, ty);
  ctx.fill();
  ctx.font = `${Math.round(12 * m)}px ${FONT}`; ctx.textAlign = "center";
  ctx.fillText("YOU", x, ty - 16 * m);
  ctx.restore();
}

// The end-of-round podium: the top 3 by money, the winner in the middle on the tallest step.
function drawPodium(tk) {
  const order = R.data.players.map((_, k) => k).sort((a, b) => tk.x[b][5] - tk.x[a][5] || a - b).slice(0, 3);
  window.spacetaxi.podium = order.map(k => R.data.players[k]);
  const cx = AW / 2, base = R.OY + R.H * R.C / 2 + 230, stepW = 300;
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

// A move's `debug` (see docs/rules.md, `move`): free text, or an object with `say` and `path`.
function debugText(d) {
  if (typeof d === "string") return d;
  return d && typeof d.say === "string" ? d.say : "";
}
function debugPath(d) {
  return d && typeof d === "object" && Array.isArray(d.path) ? d.path : [];
}

// The debug overlay: each taxi's planned path as a dotted neon line from the taxi, and its
// debug text in a bubble above its name, both in its colour. `at` holds the taxis' centres.
function drawOverlay(tk, at) {
  const C = R.C, k = C / 32;
  const bubbles = R.data.players.map(() => "");
  ctx.save();
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  for (const [p, x, y] of at) {
    const d = tk.d ? tk.d[p] : null, path = debugPath(d), color = COLORS[p % COLORS.length];
    if (path.length) {
      ctx.strokeStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 12;
      ctx.lineWidth = 4 * k; ctx.setLineDash([0, 10 * k]);
      ctx.beginPath(); ctx.moveTo(x, y);
      for (const [px, py] of path) ctx.lineTo(R.OX + px * C + C / 2, R.OY + py * C + C / 2);
      ctx.stroke();
      ctx.setLineDash([]); ctx.shadowBlur = 0;
    }
  }
  ctx.font = `14px ${FONT}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const [p, x, y] of at) {
    const d = tk.d ? tk.d[p] : null, color = COLORS[p % COLORS.length];
    const text = fitText(debugText(d).replace(/\s+/g, " ").trim(), 420);
    if (!text) continue;
    bubbles[p] = text;
    const w = ctx.measureText(text).width + 24, h = 30, bx = Math.max(4, Math.min(AW - w - 4, x - w / 2));
    const by = Math.max(HUD + 4, y - 14 * k - 60 - h);   // above the name and the TO tag
    ctx.fillStyle = "rgba(10,8,25,.92)"; ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.shadowColor = color; ctx.shadowBlur = 12;
    ctx.beginPath(); ctx.roundRect(bx, by, w, h, 6); ctx.fill(); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x - 6, by + h); ctx.lineTo(x, by + h + 8); ctx.lineTo(x + 6, by + h);
    ctx.fill(); ctx.shadowBlur = 0;
    ctx.fillStyle = color; ctx.fillText(text, bx + w / 2, by + h / 2 + 1);
  }
  ctx.restore();
  window.spacetaxi.bubbles = bubbles;
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
  window.spacetaxi.bubbles = R.data.players.map(() => "");
  window.spacetaxi.taxis = R.data.players.map(() => null);
  window.spacetaxi.tags = [];
  window.spacetaxi.tagBoxes = [];
  window.spacetaxi.pulsing = [];
  window.spacetaxi.humans = [...R.humans];
  window.spacetaxi.marked = [];
  if (!ticks.length) return;
  const i = Math.floor(tf), f = tf - i, tk = ticks[i], C = R.C, k = C / 32;
  // About one flame particle per taxi every 16 ms of replay time.
  const flameStep = playing ? Math.min(6, dt / 16) : 0;

  // The destination pad of every passenger carried right now pulses.
  const pulsing = [...new Set(tk.x.filter(t => t[3] !== CRASHED && t[6]).map(t => t[6]))].sort();
  for (const id of pulsing) if (R.padById[id]) drawPulse(R.padById[id], tnow);
  window.spacetaxi.pulsing = pulsing;

  // The tags of the passengers waiting on one pad stack upwards in spot order, bobbing
  // together, so they never overlap; a lone tag stays above its passenger.
  const stacks = new Map();   // pad id -> [spot, text] of its tagged passengers
  for (const [at, spot, to, fare] of tk.p) {
    const pad = R.padById[at]; if (!pad) continue;
    const x = R.OX + spot * C + C / 2, y = R.OY + pad.y * C - 3 * k;
    drawPassenger(x, y, tnow, spot, k);
    if (to == null) continue;
    if (!stacks.has(at)) stacks.set(at, []);
    stacks.get(at).push([spot, `TO ${to} $${fare}`]);
  }
  for (const [at, list] of stacks) {
    const pad = R.padById[at], m = Math.max(1, k);
    list.sort((a, b) => a[0] - b[0]);
    const x = list.length === 1 ? R.OX + list[0][0] * C + C / 2 : R.OX + (pad.x + pad.len / 2) * C;
    const y = R.OY + pad.y * C - 39 * k + Math.sin(tnow / 180 + list[0][0]) * 1.5 * k;
    list.forEach(([, text], n) => tag(x, y - n * 24 * m, text, "#ffd23f", k));
  }

  ctx.font = `14px ${FONT}`; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  const at = [];   // [player, x, y] of every taxi on screen
  for (let p = 0; p < R.data.players.length; p++) {
    const pos = taxiPos(i, f, p); if (pos.state === CRASHED) continue;
    if (pos.vx) R.facing[p] = Math.sign(pos.vx);
    const color = COLORS[p % COLORS.length];
    const x = R.OX + pos.x * C, y = R.OY + pos.y * C;
    if (flameStep) emitFlames(i, x, y, p, flameStep);
    at.push([p, x, y]);
    window.spacetaxi.taxis[p] = [x, y];
    drawTaxi(x, y, color, R.facing[p], pos.carry, 1.15 * k);
    ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 8;
    ctx.fillText(R.names[p], x, y - 14 * k - 6); ctx.shadowBlur = 0;
    if (pos.to) tag(x, y - 14 * k - 34, `TO ${pos.to}`, "#7dff5a", k);
    if (FLY && p === fly.index) {
      drawYou(x, y, tnow, k, Boolean(pos.to));
      window.spacetaxi.marked.push(R.data.players[p]);
    } else if (R.humans.has(R.data.players[p])) {
      tag(x, y + 14 * k + 26, "HUMAN", "#ffffff", k);
      window.spacetaxi.marked.push(R.data.players[p]);
    } else if (R.house.has(R.data.players[p])) {
      tag(x, y + 14 * k + 26, "HOUSE", "#ffffff", k);
      window.spacetaxi.marked.push(R.data.players[p]);
    }
  }
  drawEffects(dt);
  if (window.spacetaxi.overlay) drawOverlay(tk, at);

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (i === ticks.length - 1 && !FLY && (!LIVE || R.data.over)) drawPodium(tk);
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
  if (LIVE) liveAdvance(dt, now);
  else if (playing) {
    tf += dt * speed / R.data.rules.tick_ms;
    if (tf >= end) { tf = end; setPlaying(false); sound("jingle", true); }
  }
  if (window.spacetaxi.started && R.ticks.length) while (fired < Math.floor(tf)) fireEvents(++fired);
  let thrusting = 0;
  if (playing && R.ticks.length) {
    const i = Math.floor(tf);
    R.ticks[i].x.forEach((t, p) => { if (t[3] !== CRASHED && thrusts(i, p)) thrusting++; });
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
  tickLabel.textContent = LIVE && !R.ticks.length ? "WAITING"
    : `TICK ${tick}/${R.data.rules.round_ticks}`;
  requestAnimationFrame(frame);
}

// ---------- controls ----------
// The round does not play before the splash is clicked; playing from its first tick sounds the
// start jingle.
function setPlaying(on) {
  if (LIVE) return;   // a live round plays as it comes
  if (on && !window.spacetaxi.started) return;
  if (on && tf >= R.ticks.length - 1) seek(0);
  if (on && !playing && tf === 0) sound("jingle", false);
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
soundBtn.addEventListener("click", () => setMuted(!window.spacetaxi.muted));
scrub.addEventListener("input", () => seek(Number(scrub.value)));
roundSel.addEventListener("change", () => {
  if (LIVE) { pickArena(roundSel.value); roundSel.blur(); return; }
  setPlaying(false); selectRound(Number(roundSel.value));
});
window.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target instanceof HTMLInputElement) return;   // typing a name
  if (LIVE && (e.key === " " || SPEEDS[e.key])) return;
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
  } else if (e.key === "d" || e.key === "D") {
    e.preventDefault();
    window.spacetaxi.overlay = !window.spacetaxi.overlay;
  }
});
function setScanlines(on) {
  window.spacetaxi.scanlines = on;
  crt.hidden = !on;
}

// INSERT COIN: the click turns the sound on and starts the round.
splash.addEventListener("click", () => {
  if (window.spacetaxi.started) return;
  window.spacetaxi.started = true;
  splash.hidden = !LIVE || liveIdle(performance.now());
  startAudio();
  if (R) setPlaying(true);
});

if (!LIVE) ROUNDS.forEach((r, idx) => {
  const opt = document.createElement("option");
  opt.value = String(idx); opt.textContent = `ROUND ${r.round}`;
  roundSel.appendChild(opt);
});
fit();
setSpeed(1);
const fontReady = document.fonts ? document.fonts.load(`16px ${FONT}`).catch(() => {}) : Promise.resolve();
fontReady.then(() => {
  if (FLY) startFly();
  else if (LIVE) startLive();
  else {
    selectRound(0);
    setPlaying(window.spacetaxi.started);
  }
  requestAnimationFrame(t => { last = t; frame(t); });
});

// ---------- live mode: the arenas of `spacetaxi serve`, from its /spectate socket ----------
// Each arena slot (1 to the server's max) holds its latest round, in the replay's compact shape
// (see compact_tick in viewer.py) plus `arena` and `over`. The picker shows one slot or follows
// the arena whose round started last. The view stays a tick or two behind the newest tick, so
// the taxis glide between ticks; it jumps ahead when it falls further behind.
const LIVE_STATES = { flying: FLYING, landed: LANDED, crashed: CRASHED };
const LIVE_LAG = 3;          // ticks behind the newest one before the view jumps ahead
const SPLASH_AFTER = 5000;   // ms the podium stays up after the last round ends, before the splash
const live = { slots: {}, running: new Set(), latest: 0, follow: true, slot: 0, arenas: 0,
               idleSince: -Infinity, waiting: 0 };

function compactTick(players, tick, upc) {
  const byId = Object.fromEntries((tick.taxis || []).map(t => [t.id, t]));
  const moves = tick.moves || {}, move = n => moves[n] || {};
  return {
    t: tick.tick,
    x: players.map(n => {
      const t = byId[n];
      if (!t) return [0.5, 0.5, 0, CRASHED, 0, 0, null];
      const fine = "fx" in t && "fy" in t, p = t.passenger;
      return [fine ? t.fx / upc : t.x + 0.5, fine ? t.fy / upc : t.y + 0.5, t.vx,
              LIVE_STATES[t.state] ?? FLYING, p ? 1 : 0, t.money, p ? (p.to ?? null) : null];
    }),
    p: (tick.passengers || []).map(p => [p.at, p.spot_x, p.to ?? null, p.fare ?? null]),
    e: tick.events || [],
    m: players.map(n => [move(n).ax || 0, move(n).ay || 0]),
    d: players.map(n => move(n).debug ?? null),
  };
}

function liveIdle(now) {
  return live.running.size === 0 && now - live.idleSince > SPLASH_AFTER;
}

// Show arena slot `slot` (its latest round, or the empty map), a tick behind its newest tick.
function showSlot(slot) {
  live.slot = slot;
  R = prepareRound(live.slots[slot] || ROUNDS[0]);
  R.jingled = Boolean(R.data.over);
  seek(Math.max(0, R.ticks.length - 2));
  window.spacetaxi.arena = slot;
  window.spacetaxi.mapName = R.data.map_name || null;
  window.spacetaxi.map = R.data.map;
}

// The picker: "follow" or an arena slot number.
function pickArena(value) {
  live.follow = value === "follow";
  window.spacetaxi.follow = live.follow;
  showSlot(live.follow ? live.latest : Number(value));
  roundSel.value = value;
}

function liveOptions() {
  const value = live.follow ? "follow" : String(live.slot);
  const opts = [["follow", "FOLLOW LATEST"]];
  for (let k = 1; k <= live.arenas; k++) {
    const d = live.slots[k];
    opts.push([String(k), `ARENA ${k}` + (d && !d.over ? ` R${d.round}` : " IDLE")]);
  }
  roundSel.replaceChildren(...opts.map(([v, text]) => {
    const o = document.createElement("option"); o.value = v; o.textContent = text; return o;
  }));
  roundSel.value = value;
  window.spacetaxi.running = [...live.running].sort((a, b) => a - b);
}

function liveAdvance(dt, now) {
  const end = R.ticks.length - 1;
  splash.hidden = FLY || (window.spacetaxi.started && !liveIdle(now));
  window.spacetaxi.idle = live.running.size === 0;
  if (end < 0) { playing = false; return; }
  if (end - tf > LIVE_LAG) { tf = end - 1; fired = Math.floor(tf); }
  tf = Math.min(end, tf + dt / R.data.rules.tick_ms);
  if (!window.spacetaxi.started) fired = Math.floor(tf);   // no effects behind the splash
  const over = Boolean(R.data.over) && tf >= end;
  if (over && !R.jingled) { R.jingled = true; sound("jingle", true); }
  playing = !over;
}

// Milestone pop-ups: one line per award ("ANNA · PAID · P3 · +15"), stacked, each for a few
// seconds, with a fanfare. The debug hook keeps the texts shown in `milestones`.
const MILESTONE_MS = 4500, MAX_TOASTS = 4;
const toastsEl = document.getElementById("toasts");
function showMilestone(msg) {
  const text = [msg.name.toUpperCase(), msg.milestone.toUpperCase().replace(/_/g, " "),
                `P${msg.place}`, `+${msg.points}`].join(" \u00b7 ");
  window.spacetaxi.milestones.push(text);
  const el = document.createElement("div");
  el.className = "toast" + (msg.place === 1 ? " first" : "");
  el.textContent = text;
  toastsEl.appendChild(el);
  while (toastsEl.children.length > MAX_TOASTS) toastsEl.firstElementChild.remove();
  setTimeout(() => el.remove(), MILESTONE_MS);
  sound("fanfare");
}

function onLive(msg) {
  if (msg.type === "milestone") { showMilestone(msg); return; }
  if (msg.type === "spectate") {
    live.arenas = msg.arenas; live.slots = {}; live.running.clear();
    liveOptions();
    return;
  }
  const slot = msg.arena;
  if (msg.type === "round_start") {
    const rules = { ...ROUNDS[0].rules, ...(msg.rules || {}) };
    live.slots[slot] = { round: msg.round, arena: slot, map_name: msg.map_name,
                         map: msg.map, pads: msg.pads,
                         players: msg.players, humans: msg.humans || [],
                         house: msg.house || [], rules, ticks: [],
                         over: false };
    live.running.add(slot);
    live.latest = slot;
    liveOptions();
    if (live.follow || live.slot === slot) showSlot(slot);
    return;
  }
  const d = live.slots[slot];
  if (!d || d.round !== msg.round) return;
  if (msg.type === "tick") {
    if (d.ticks.length && d.ticks[d.ticks.length - 1].t >= msg.tick) return;
    d.ticks.push(compactTick(d.players, msg, d.rules.units_per_cell));
    if (msg.tick === 1 && R.data === d) sound("jingle", false);
  } else if (msg.type === "round_end") {
    d.over = true;
    live.running.delete(slot);
    if (!live.running.size) live.idleSince = performance.now();
    liveOptions();
    loadLeaderboard();
  }
}

function connectLive() {
  const sock = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/spectate`);
  sock.onmessage = e => { try { onLive(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  sock.onopen = () => { window.spacetaxi.connected = true; };
  sock.onclose = () => {
    window.spacetaxi.connected = false;
    live.running.clear();
    setTimeout(connectLive, 1000);
  };
}

// The leaderboard panel, and the queue length and the next wave's map on the splash, from
// /leaderboard.json: at every round end and every few seconds.
const lbRows = document.getElementById("lbRows"), lbSub = document.getElementById("lbSub");
const queueEl = document.getElementById("queue"), nextMapEl = document.getElementById("nextMap");
async function loadLeaderboard() {
  let data;
  try {
    const res = await fetch("/leaderboard.json", { cache: "no-store" });
    data = await res.json();
  } catch (err) {
    return;
  }
  live.waiting = data.waiting;
  window.spacetaxi.waiting = data.waiting;
  window.spacetaxi.leaderboard = data.leaderboard.map(r => r.name);
  queueEl.textContent = `${data.waiting} ${data.waiting === 1 ? "BOT" : "BOTS"} IN THE QUEUE`;
  window.spacetaxi.nextMap = data.next_map;
  nextMapEl.textContent = `NEXT MAP: ${data.next_map.toUpperCase()}`;
  lbSub.textContent = `WAVE ${data.wave} \u00b7 ${data.rounds} ROUNDS \u00b7 AVG $`;
  window.spacetaxi.wave = data.wave;
  showPlan(data.next_wave);
  if (!data.leaderboard.length) {
    const li = document.createElement("li"); li.className = "empty";
    li.textContent = "WAITING FOR THE FIRST ROUND";
    lbRows.replaceChildren(li);
    fitRows();
    return;
  }
  lbRows.replaceChildren(...data.leaderboard.map((r, n) => {
    const li = document.createElement("li");
    for (const [cls, text] of [["rank", `${n + 1}.`], ["name", r.name.toUpperCase()],
                               ...(r.human ? [["human", "HUMAN"]] : []),
                               ...(r.house ? [["house", "HOUSE"]] : []),
                               ["avg", `${r.avg_money < 0 ? "-" : ""}$${Math.abs(Math.round(r.avg_money))}`]]) {
      const span = document.createElement("span"); span.className = cls; span.textContent = text;
      li.appendChild(span);
    }
    return li;
  }));
  fitRows();
}

// Every leaderboard row shows: when they do not fit at full size, they shrink to fit.
function fitRows() {
  const n = lbRows.children.length;
  lbRows.style.removeProperty("--fs");
  lbRows.style.removeProperty("--pad");
  if (!n || lbRows.scrollHeight <= lbRows.clientHeight) return;
  const per = lbRows.clientHeight / n - 1;               // a row's height, less its border
  const fs = Math.max(6, Math.min(16, Math.floor(per * 0.55)));
  lbRows.style.setProperty("--fs", `${fs}px`);
  lbRows.style.setProperty("--pad", `${Math.max(0, Math.floor((per - fs) / 2))}px`);
}

// The plan of the next wave: on the splash (NEXT WAVE n, its map and the countdown, a column
// per arena, the waitlist) and in the compact box under the leaderboard. The countdown runs
// down between polls.
const planEl = document.getElementById("plan"), lbNext = document.getElementById("lbNext");
const plan = { data: null, at: 0 };
function showPlan(next) {
  plan.data = next;
  plan.at = performance.now();
  window.spacetaxi.nextWave = next;
  const div = (cls, text) => {
    const el = document.createElement("div"); if (cls) el.className = cls; el.textContent = text;
    return el;
  };
  if (!next.arenas.length) {
    planEl.replaceChildren();
    lbNext.replaceChildren();
    return;
  }
  const head = div("head", ""), cols = div("cols", "");
  next.arenas.forEach((names, i) => {
    const col = div("col", "");
    const b = document.createElement("b"); b.textContent = `ARENA ${i + 1}`; col.appendChild(b);
    for (const name of names) {
      const span = document.createElement("span"); span.textContent = name.toUpperCase();
      col.appendChild(span);
    }
    cols.appendChild(col);
  });
  const wait = next.waitlist.map(n => n.toUpperCase()).join(", ");
  planEl.replaceChildren(head, cols, ...(wait ? [div("wait", `WAITING: ${wait}`)] : []));
  lbNext.replaceChildren(div("head", ""),
    ...next.arenas.map((names, i) => div("", `${i + 1}: ${names.map(n => n.toUpperCase()).join(", ")}`)),
    ...(wait ? [div("wait", `WAIT: ${wait}`)] : []));
  planClock();
}

function planClock() {
  if (!plan.data || !plan.data.arenas.length) return;
  const d = plan.data, map = d.map.toUpperCase();
  const left = Math.max(0, Math.ceil(d.starts_in - (performance.now() - plan.at) / 1000));
  planEl.firstChild.textContent = `NEXT WAVE ${d.wave} \u00b7 ${map} \u00b7 IN ${left} S`;
  lbNext.firstChild.textContent = `NEXT WAVE ${d.wave} \u00b7 ${map} \u00b7 ~${left} S`;
}

// /watch: the page links in the bar fade out after NAV_IDLE_MS without mouse movement or
// touch, and come back on the next one.
const NAV_IDLE_MS = 3000;
function idleNav() {
  const nav = document.querySelector("#bar .barnav");
  let timer = 0;
  const wake = () => {
    nav.classList.remove("idle");
    clearTimeout(timer);
    timer = setTimeout(() => nav.classList.add("idle"), NAV_IDLE_MS);
  };
  for (const ev of ["mousemove", "pointerdown", "touchstart"])
    window.addEventListener(ev, wake, { passive: true });
  wake();
}

function startLive() {
  document.body.classList.add("live");
  idleNav();
  document.getElementById("lb").hidden = false;
  splash.querySelector(".hint").textContent = "C SCREEN \u00b7 M OR SOUND BUTTON MUTE \u00b7 D DEBUG";
  Object.assign(window.spacetaxi, { live: true, arena: 0, follow: true, running: [], idle: true,
                                    waiting: 0, leaderboard: [], connected: false,
                                    milestones: [] });
  showSlot(0);
  liveOptions();
  connectLive();
  loadLeaderboard();
  setInterval(loadLeaderboard, 3000);
  setInterval(planClock, 250);
  addEventListener("resize", fitRows);
}

// ---------- fly mode: a person flies one taxi in the arena, the page is its /bot client ----------
// The page says `hello` with `"client": "human"`, waits in the queue (`queued`: position and
// `starts_in`), and answers every `tick` at once with a `move` for that tick from the keys held
// at that moment (no key: ax 0, ay 0). Its round plays in the live view above with its own taxi
// marked; after `round_end` it shows the results while the server puts it back into the queue,
// as for any bot. LEAVE disconnects. The debug hook gets: `fly` (join, connecting, queued,
// playing, over), `id`, `you` (the player's index in the round), `me` (its taxi from the latest
// tick: fx, fy, vx, vy, state, pad, money, respawn_in, tick), `queue`, `results`, `moves` (moves
// sent), `lastMove` and `keys` (held).
const NAME_RE = /^[A-Za-z0-9_-]{1,12}$/;
const FLY_KEYS = ["ArrowUp", "ArrowLeft", "ArrowRight"];
const fly = { sock: null, id: null, index: -1, round: null, error: "", countdown: 0 };
const held = new Set();
const joinForm = document.getElementById("join"), nameInput = document.getElementById("name");
const joinMsg = document.getElementById("joinMsg"), statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results"), resultRows = document.getElementById("resultRows");
const leaveBtn = document.getElementById("leave"), touchEl = document.getElementById("touch");

function heldMove() {
  const ax = (held.has("ArrowRight") ? 1 : 0) - (held.has("ArrowLeft") ? 1 : 0);
  return [ax, held.has("ArrowUp") ? -1 : 0];
}

function setHeld(key, on) {
  if (on) held.add(key); else held.delete(key);
  window.spacetaxi.keys = [...held].sort();
  document.querySelectorAll(`#touch [data-key="${key}"]`).forEach(b => b.classList.toggle("on", on));
}

function releaseAll() {
  for (const key of [...held]) setHeld(key, false);
}

function showStatus(text) {
  clearInterval(fly.countdown);
  statusEl.textContent = text;
  statusEl.hidden = false;
}

function showJoin(message) {
  window.spacetaxi.fly = "join";
  window.spacetaxi.connected = false;
  releaseAll();
  live.running.clear();
  joinMsg.textContent = message || "";
  joinForm.hidden = false;
  statusEl.hidden = true; clearInterval(fly.countdown); leaveBtn.hidden = true; touchEl.hidden = true;
  nameInput.focus();
}

function showResults(results) {
  window.spacetaxi.results = results;
  const order = [...results].sort((a, b) => b.money - a.money);
  resultRows.replaceChildren(...order.map((r, n) => {
    const tr = document.createElement("tr");
    if (r.id === fly.id) tr.className = "you";
    for (const v of [`${n + 1}.`, r.id.toUpperCase() + (r.id === fly.id ? " (YOU)" : ""),
                     `${r.money < 0 ? "-" : ""}$${Math.abs(r.money)}`, r.deliveries, r.crashes]) {
      const td = document.createElement("td"); td.textContent = String(v); tr.appendChild(td);
    }
    return tr;
  }));
  resultsEl.hidden = false;
}

function connectFly(name) {
  const sock = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/bot`);
  Object.assign(fly, { sock, id: null, index: -1, round: null, error: "" });
  window.spacetaxi.fly = "connecting";
  joinForm.hidden = true; resultsEl.hidden = true;
  leaveBtn.hidden = false; touchEl.hidden = false;
  showStatus("CONNECTING ...");
  sock.onopen = () => sock.send(JSON.stringify({ type: "hello", name, protocol: 1, client: "human" }));
  sock.onmessage = e => {
    if (fly.sock !== sock) return;
    let msg;
    try { msg = JSON.parse(e.data); } catch (err) { return; }
    onFly(msg, sock);
  };
  sock.onclose = () => {
    if (fly.sock !== sock) return;   // left on purpose
    fly.sock = null;
    showJoin(fly.error || "DISCONNECTED FROM THE SERVER");
  };
}

function onFly(msg, sock) {
  const st = window.spacetaxi;
  if (msg.type === "welcome") {
    fly.id = msg.id; st.id = msg.id; st.connected = true; st.fly = "queued";
    showStatus("IN THE QUEUE");
  } else if (msg.type === "queued") {
    st.queue = { position: msg.position, waiting: msg.waiting, starts_in: msg.starts_in };
    if (st.fly !== "over") st.fly = "queued";
    // `starts_in` is a snapshot: count it down here until the next `queued` or `round_start`
    const at = performance.now() + msg.starts_in * 1000;
    const left = () => Math.max(0, Math.ceil((at - performance.now()) / 1000));
    const text = () => `IN THE QUEUE: ${msg.position} OF ${msg.waiting} · STARTS IN ${left()} S`;
    showStatus(text());
    fly.countdown = setInterval(() => { statusEl.textContent = text(); }, 250);
  } else if (msg.type === "round_start") {
    const rules = { ...ROUNDS[0].rules, ...(msg.rules || {}) };
    fly.round = { round: msg.round, arena: 1, map_name: msg.map_name, map: msg.map,
                  pads: msg.pads, players: msg.players,
                  humans: [], rules, ticks: [], over: false };
    fly.index = msg.players.indexOf(fly.id);
    live.slots[1] = fly.round;
    live.running.add(1);
    Object.assign(st, { fly: "playing", you: fly.index, me: null, results: null, queue: null });
    resultsEl.hidden = true; statusEl.hidden = true; clearInterval(fly.countdown);
    showSlot(1);
  } else if (msg.type === "tick") {
    const d = fly.round;
    if (!d || d.over) return;
    // Answer first: the move must reach the server within deadline_ms of the tick.
    const [ax, ay] = heldMove();
    sock.send(JSON.stringify({ type: "move", tick: msg.tick, ax, ay }));
    st.moves++;
    st.lastMove = { tick: msg.tick, ax, ay };
    const mine = (msg.taxis || []).find(t => t.id === fly.id);
    if (mine) st.me = { fx: mine.fx, fy: mine.fy, vx: mine.vx, vy: mine.vy, state: mine.state,
                        pad: mine.pad, money: mine.money, respawn_in: mine.respawn_in, tick: msg.tick };
    if (d.ticks.length && d.ticks[d.ticks.length - 1].t >= msg.tick) return;
    const tk = compactTick(d.players, msg, d.rules.units_per_cell);
    if (fly.index >= 0) tk.m[fly.index] = [ax, ay];   // the flames of the player's own move
    d.ticks.push(tk);
    if (msg.tick === 1 && R.data === d) sound("jingle", false);
  } else if (msg.type === "round_end") {
    if (fly.round) fly.round.over = true;
    live.running.delete(1);
    live.idleSince = performance.now();
    st.fly = "over";
    showResults(msg.results || []);
  } else if (msg.type === "error") {
    fly.error = msg.message || msg.code || "ERROR";
  }
}

function leaveFly() {
  const sock = fly.sock;
  fly.sock = null;
  if (sock) sock.close();
  resultsEl.hidden = true;
  showJoin("");
}

function startFly() {
  document.body.classList.add("fly");
  splash.hidden = true;
  Object.assign(window.spacetaxi, { live: true, fly: "join", id: null, you: -1, me: null, queue: null,
                                    results: null, moves: 0, lastMove: null, keys: [],
                                    connected: false });
  showSlot(0);
  try { nameInput.value = localStorage.getItem("spacetaxi-name") || ""; } catch (err) { /* private mode */ }
  joinForm.addEventListener("submit", e => {
    e.preventDefault();
    const name = nameInput.value.trim();
    if (!NAME_RE.test(name)) { joinMsg.textContent = "NAME: 1 TO 12 OF A-Z a-z 0-9 _ -"; return; }
    try { localStorage.setItem("spacetaxi-name", name); } catch (err) { /* private mode */ }
    if (!window.spacetaxi.started) { window.spacetaxi.started = true; startAudio(); }
    nameInput.blur();
    connectFly(name);
  });
  leaveBtn.addEventListener("click", () => { leaveBtn.blur(); leaveFly(); });
  window.addEventListener("keydown", e => {
    if (e.target instanceof HTMLInputElement || e.ctrlKey || e.metaKey || e.altKey) return;
    if (FLY_KEYS.includes(e.key) || e.key === "ArrowDown") e.preventDefault();
    if (FLY_KEYS.includes(e.key) && !held.has(e.key)) setHeld(e.key, true);
  });
  window.addEventListener("keyup", e => { if (held.has(e.key)) setHeld(e.key, false); });
  window.addEventListener("blur", releaseAll);
  touchEl.querySelectorAll("button").forEach(b => {
    const key = b.dataset.key;
    b.addEventListener("contextmenu", e => e.preventDefault());
    b.addEventListener("pointerdown", e => {
      e.preventDefault();
      if (b.setPointerCapture) b.setPointerCapture(e.pointerId);
      setHeld(key, true);
    });
    for (const ev of ["pointerup", "pointerleave", "pointercancel", "lostpointercapture"])
      b.addEventListener(ev, () => setHeld(key, false));
  });
  showJoin("");
}
