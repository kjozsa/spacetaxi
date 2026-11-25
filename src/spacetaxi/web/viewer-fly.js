// ---------- fly mode: a person flies one taxi in the arena, the page is its /bot client ----------
// Joined after viewer.js and viewer-live.js on /fly (spacetaxi/viewer.py): its round plays in the
// live view, and it plugs into the player's `hooks`.
// The page says `hello` with `"client": "human"`, waits in the queue (`queued`: position and
// `starts_in`), and answers every `tick` at once with a `move` for that tick from the keys held
// at that moment (no key: ax 0, ay 0). Its round plays in the live view above with its own taxi
// marked; after `round_end` it shows the results while the server puts it back into the queue,
// as for any bot. LEAVE disconnects. The debug hook gets: `fly` (join, connecting, queued,
// playing, over), `id`, `you` (the player's index in the round), `me` (its taxi from the latest
// tick: fx, fy, vx, vy, state, pad, money, respawn_in, tick), `queue`, `results`, `moves` (moves
// sent), `lastMove` and `keys` (held).
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

hooks.start = startFly;
hooks.you = () => fly.index;
