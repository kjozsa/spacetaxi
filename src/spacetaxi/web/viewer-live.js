// ---------- live mode: the arenas of `spacetaxi serve`, from its /spectate socket ----------
// Joined after viewer.js on /watch and /fly (spacetaxi/viewer.py); it plugs into the player's `hooks`.
// Each arena slot (1 to the server's max) holds its latest round, in the replay's compact shape
// (see compact_tick in viewer.py) plus `arena` and `over`. The picker shows one slot or follows
// the arena whose round started last. The view stays a tick or two behind the newest tick, so
// the taxis glide between ticks; it jumps ahead when it falls further behind.
const LIVE_STATES = { flying: FLYING, landed: LANDED, crashed: CRASHED };
const LIVE_LAG = 3;          // ticks behind the newest one before the view jumps ahead
const SPLASH_AFTER = 5000;   // ms the podium stays up after the last round ends, before the splash
const live = { slots: {}, running: new Set(), wave: new Set(), latest: 0, follow: true, slot: 0,
               arenas: 0, idleSince: -Infinity, waiting: 0 };

// Follow mode (/watch?bot=NAME, or the picker's FOLLOW NAME for the name /mission stored): the
// view goes to the arena whose round has the bot (names ignore case), stays on the arena it
// last flew in while it sits a wave out, and behaves as FOLLOW LATEST while no connected bot
// has the name (not in a running round, the next wave's plan or its waitlist, as in /mission).
const NAME_KEY = "spacetaxi.name";   // shared with /mission and /lessons
const followBot = { name: null, on: false, slot: 0, board: null, outSince: 0 };
const followEl = document.getElementById("followNote"), seatEl = document.getElementById("seat");
const SITS_OUT_AFTER = 500;   // ms a wave runs without the bot before the banner says so

const sameName = n => n.toLowerCase() === followBot.name.toLowerCase();
const hasName = names => names.some(sameName);
function isFollowed(player) {
  return followBot.on && sameName(player);
}

// The running arena slot whose round has the followed bot, or 0.
function followedSlot() {
  for (const k of live.running) if (hasName(live.slots[k].players)) return k;
  return 0;
}

// The arena to show while following: the bot's round, else the arena it last flew in while it
// sits a wave out, else (not connected) the latest.
function followSlot() {
  return followedSlot() || (followConnected() !== false && followBot.slot) || live.latest;
}

// Whether a connected bot has the followed name; null before the first /leaderboard.json.
function followConnected() {
  if (followedSlot()) return true;
  const b = followBot.board;
  if (!b) return null;
  return b.arenas.some(a => hasName(a.players)) || b.next_wave.arenas.some(hasName)
    || hasName(b.next_wave.waitlist);
}

// The banner on the stage and the seat line on the splash. A wave's round_start and round_end
// messages arrive one arena at a time, so the bot sits out only when no round of the wave has
// it, for SITS_OUT_AFTER.
function followUpdate() {
  const st = window.spacetaxi;
  st.followed = followBot.on ? followBot.name : null;
  let note = null, seat = "";
  if (followBot.on) {
    const name = followBot.name.toUpperCase(), connected = followConnected();
    const out = connected && live.running.size
      && ![...live.wave].some(k => hasName(live.slots[k].players));
    if (!out) followBot.outSince = 0;
    else if (!followBot.outSince) {
      followBot.outSince = performance.now();
      setTimeout(followUpdate, SITS_OUT_AFTER);
    }
    if (connected === false) note = `${name} IS NOT CONNECTED`;
    else if (out && performance.now() - followBot.outSince >= SITS_OUT_AFTER)
      note = `${name} SITS OUT THIS WAVE`;
    const next = followBot.board && followBot.board.next_wave;
    if (next) {
      const i = next.arenas.findIndex(hasName), w = next.waitlist.findIndex(sameName);
      if (i >= 0) seat = `${name}: ARENA ${i + 1} IN WAVE ${next.wave}`;
      else if (w >= 0) {
        const seats = next.arenas.reduce((n, names) => n + names.length, 0);
        seat = `${name}: WAITLIST #${w + 1}, PLAYS IN WAVE ${next.wave + 1 + Math.floor(w / seats)} AT THE LATEST`;
      }
    }
  }
  st.followNote = note;
  followEl.textContent = note || "";
  followEl.hidden = !note;
  seatEl.textContent = seat;
}

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
  followBot.on = value === "bot";
  window.spacetaxi.follow = live.follow;
  showSlot(followBot.on ? followSlot() : live.follow ? live.latest : Number(value));
  roundSel.value = value;
  followUpdate();
  markFollowed();
}

// Each arena option names its round's players; an arena without a round in the current wave
// (`live.wave`, the slots whose round started since the last time none ran) is EMPTY.
function liveOptions() {
  const value = followBot.on ? "bot" : live.follow ? "follow" : String(live.slot);
  const opts = [["follow", "FOLLOW LATEST"]];
  if (followBot.name) opts.push(["bot", `FOLLOW ${followBot.name.toUpperCase()}`]);
  for (let k = 1; k <= live.arenas; k++) {
    const d = live.slots[k];
    const names = d && live.wave.has(k) ? d.players.map(n => n.toUpperCase()).join(" ") : "";
    opts.push([String(k), `ARENA ${k} \u00b7 ${names || "EMPTY"}`]);
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
    live.arenas = msg.arenas; live.slots = {}; live.running.clear(); live.wave.clear();
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
    if (!live.running.size) live.wave.clear();   // all arenas of a wave start together
    live.wave.add(slot);
    live.running.add(slot);
    live.latest = slot;
    if (followBot.name && hasName(msg.players)) followBot.slot = slot;
    if (followBot.on) {
      // the new round, or another arena: the arena already on screen is not restarted
      const target = followSlot();
      if (target === slot || target !== live.slot) showSlot(target);
    } else if (live.follow || live.slot === slot) showSlot(slot);
    liveOptions();
    followUpdate();
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
    followUpdate();
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
  followBot.board = data;
  showPlan(data.next_wave);
  followUpdate();
  showPitstop(data.pitstop);
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
      span.dataset.name = name;
      col.appendChild(span);
    }
    cols.appendChild(col);
  });
  const wait = next.waitlist.map(n => n.toUpperCase()).join(", ");
  planEl.replaceChildren(head, cols, ...(wait ? [div("wait", `WAITING: ${wait}`)] : []));
  lbNext.replaceChildren(div("head", ""),
    ...next.arenas.map((names, i) => div("", `${i + 1}: ${names.map(n => n.toUpperCase()).join(", ")}`)),
    ...(wait ? [div("wait", `WAIT: ${wait}`)] : []));
  markFollowed();
  planClock();
}

// The followed bot's name stands out in its arena's column of the plan.
function markFollowed() {
  for (const span of planEl.querySelectorAll(".col span"))
    span.classList.toggle("me", isFollowed(span.dataset.name));
}

// The next pit stop (docs/lessons.md): how many participants have reached its milestone, of the
// half of them it waits for; DUE once they have. Gone once the operator opens its lesson.
const pitEl = document.getElementById("pit"), lbPit = document.getElementById("lbPit");
function showPitstop(stop) {
  window.spacetaxi.pitstop = stop || null;
  if (!stop || !stop.participants) {
    pitEl.replaceChildren(); lbPit.replaceChildren();
    return;
  }
  const ms = stop.milestone.toUpperCase().replace(/_/g, " ");
  const head = stop.due ? `PIT STOP ${stop.number} DUE` : `PIT STOP ${stop.number} AT ${ms}`;
  const sub = stop.due ? `${stop.reached} OF ${stop.participants} HAVE ${ms}`
                       : `${stop.reached} OF ${stop.need} NEEDED`;
  const build = () => {
    const meter = document.createElement("div"); meter.className = "meter";
    for (let i = 0; i < stop.need; i++) {
      const seg = document.createElement("i"); if (i < stop.reached) seg.className = "on";
      meter.appendChild(seg);
    }
    const a = document.createElement("div"); a.textContent = head;
    const b = document.createElement("div"); b.textContent = sub;
    return [a, b, meter];
  };
  for (const el of [pitEl, lbPit]) {
    el.classList.toggle("due", stop.due);
    el.replaceChildren(...build());
  }
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
                                    milestones: [], followed: null, followNote: null });
  // ?bot=NAME follows that bot from the start, silently and without the INSERT COIN click (a
  // participant's laptop); plain /watch only offers the name /mission stored, as an option.
  // A value no bot could be named (see NAME_RE) is ignored, as if not given.
  const valid = v => (v = (v || "").trim(), NAME_RE.test(v) ? v : "");
  const asked = valid(new URLSearchParams(location.search).get("bot"));
  let stored = "";
  try { stored = valid(localStorage.getItem(NAME_KEY)); } catch (err) { /* private mode */ }
  followBot.name = asked || stored || null;
  if (asked) {
    followBot.on = true; live.follow = false; window.spacetaxi.follow = false;
    window.spacetaxi.started = true;
    splash.querySelector(".coin").hidden = true;   // as if clicked: nothing to click between waves
    setMuted(true);
  }
  showSlot(0);
  followUpdate();
  liveOptions();
  connectLive();
  loadLeaderboard();
  setInterval(loadLeaderboard, 3000);
  setInterval(planClock, 250);
  addEventListener("resize", fitRows);
}

Object.assign(hooks, { start: startLive, advance: liveAdvance, pick: pickArena, idle: liveIdle,
                       followed: isFollowed });
