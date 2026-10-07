"use strict";
/* PianoJuke tablet UI. Polls /api/nowplaying; every control is a plain tap. */

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fold = (s) => String(s ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
const icon = (name, cls = "") => `<svg class="ic ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const SCREENS = ["now", "browse", "queue", "settings"];

function clock(sec) {
  if (sec == null || !isFinite(sec)) return "";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
}

const prefs = {
  get(key, fallback) {
    try { const v = localStorage.getItem("pianojuke." + key); return v == null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem("pianojuke." + key, JSON.stringify(value)); } catch { /* private mode */ } },
};

const S = {
  screen: "now",
  st: null,            // last /api/nowplaying
  stAt: 0,             // performance.now() when it arrived
  tracks: [],
  byId: new Map(),
  composers: [],
  libVersion: null,
  libLoadedAt: 0,
  composer: null,
  query: "",
  filter: "all",
  queue: null,
  settings: null,
  seenRev: { queue: null, settings: null },
  volHoldUntil: 0,
  fails: 0,
  errorSeen: undefined,
  lastTouch: Date.now(),
  autoFsDone: false,
};

// ------------------------------------------------------------------ server

async function api(path, body) {
  const init = { method: body === undefined ? "GET" : "POST", cache: "no-store", headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `The piano answered ${res.status}`);
  return data;
}

/** POST an action that answers with the now-playing status. */
async function act(path, body) {
  try {
    const st = await api(path, body ?? {});
    if (st && st.state) applyStatus(st);
    return st;
  } catch (e) {
    toast(e.message, "warn");
    return null;
  }
}

let pollTimer = null;
async function poll() {
  clearTimeout(pollTimer);
  try {
    applyStatus(await api("/api/nowplaying"));
    S.fails = 0;
    setOnline(true);
  } catch {
    S.fails += 1;
    if (S.fails >= 2) setOnline(false);
  }
  pollTimer = setTimeout(poll, document.hidden ? 10000 : 1500);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

function setOnline(ok) {
  $("#offline").hidden = ok;
  $("#conn").classList.toggle("down", !ok);
}

function applyStatus(st) {
  S.st = st;
  S.stAt = performance.now();
  if (st.library_version !== S.libVersion) loadLibrary();
  const err = st.error;
  if (S.errorSeen !== undefined && err && err.at !== S.errorSeen) toast(`Couldn't play that file: ${err.message}`, "warn");
  S.errorSeen = err ? err.at : null;
  renderNow();
  const badge = $("#rail-queue");
  badge.hidden = !st.queue_length;
  badge.textContent = st.queue_length;
  if (S.screen === "queue" && st.rev !== S.seenRev.queue) loadQueue();
  if (S.screen === "settings" && st.rev !== S.seenRev.settings) loadSettings();
  if (S.screen === "browse") markPlaying();
}

// ------------------------------------------------------------- now playing

const SOURCE = { rotation: "Rotation", queue: "From the queue", manual: "Chosen in Browse" };

function renderNow() {
  const st = S.st;
  if (!st) return;
  const t = st.track;
  const rot = st.rotation;
  const gap = st.state === "gap";

  let composer = "", title = "", sub = "";
  if (t) {
    composer = t.composer;
    title = t.title;
    sub = [t.year, SOURCE[st.source]].filter(Boolean).join(" · ");
    if (!st.running) sub = "Starting…";
  } else if (gap && st.up_next) {
    composer = "Up next · " + st.up_next.composer;
    title = st.up_next.title;
    sub = st.up_next.year ? String(st.up_next.year) : "";
  } else if (st.state === "quiet") {
    title = "Quiet hours";
    sub = `Rotation continues at ${st.quiet_until || "the end of quiet hours"}. Pieces you choose still play.`;
  } else if (gap) {
    title = st.resuming ? "Resuming rotation" : "Between pieces";
  } else {
    title = "Nothing playing";
    sub = rot.enabled ? st.message : "Start rotation, or choose a piece in Browse.";
  }
  $("#np-composer").textContent = composer;
  $("#np-title").textContent = title;
  $("#np-sub").textContent = sub;

  $("#np-progress").hidden = st.state !== "playing";
  $("#np-gap").hidden = !gap;

  // Status chips
  const chips = [];
  if (rot.enabled) {
    chips.push(`<span class="chip accent">${icon("shuffle")}Rotation · cycle ${rot.cycle} · ${rot.played} of ${rot.total}</span>`);
  } else {
    chips.push(`<span class="chip">${icon("shuffle")}Rotation off</span>`);
  }
  if (st.quiet_active) chips.push(`<span class="chip">${icon("moon")}Quiet hours until ${esc(st.quiet_until)}</span>`);
  if (st.night_active) chips.push(`<span class="chip">${icon("moon")}Night limit · velocity ${st.velocity_ceiling}</span>`);
  if (st.queue_length) chips.push(`<span class="chip">${icon("queue")}${st.queue_length} in queue</span>`);
  if (!st.midi.connected) chips.push(`<span class="chip warn">${icon("alert")}Piano MIDI not connected</span>`);
  else if (st.midi.fallback) chips.push(`<span class="chip warn">${icon("alert")}MIDI on ${esc(st.midi.port)}</span>`);
  $("#np-chips").innerHTML = chips.join("");

  // Controls
  const rotBtn = $("#np-rotation");
  rotBtn.classList.toggle("on", rot.enabled);
  rotBtn.setAttribute("aria-pressed", rot.enabled);
  $("#np-rotation-label").textContent = rot.enabled ? "Rotation on" : "Rotation off";
  $("#np-skip").disabled = !(st.state === "playing" || gap || st.queue_length || rot.enabled);
  const fav = $("#np-fav");
  fav.disabled = !t;
  fav.classList.toggle("on", !!(t && t.favorite));
  fav.setAttribute("aria-pressed", !!(t && t.favorite));
  $("#np-never").disabled = !t;

  if (performance.now() > S.volHoldUntil) showVolume(st.volume);

  // Up next
  let next = "";
  if (st.state === "playing") {
    if (st.up_next) {
      const where = st.up_next.source === "queue" ? "from the queue" : "rotation";
      next = `Up next (${where}): <b>${esc(st.up_next.composer)} – ${esc(st.up_next.title)}</b>`;
    } else if (!rot.enabled) {
      next = "Rotation is off, so the piano stops after this piece.";
    }
  } else if (st.state === "quiet" && st.up_next) {
    next = `First after quiet hours: <b>${esc(st.up_next.composer)} – ${esc(st.up_next.title)}</b>`;
  }
  $("#np-next").innerHTML = next;
  tick();
}

function tick() {
  const st = S.st;
  if (!st) return;
  const dt = (performance.now() - S.stAt) / 1000;
  if (st.state === "playing") {
    const dur = st.duration;
    const el = st.running ? Math.min(st.elapsed + dt, dur ?? Infinity) : st.elapsed;
    $("#np-elapsed").textContent = clock(el);
    $("#np-remaining").textContent = dur ? "−" + clock(dur - el) : "";
    $("#np-fill").style.width = dur ? `${Math.min(100, (el / dur) * 100).toFixed(2)}%` : "0%";
  } else if (st.state === "gap") {
    const left = Math.max(0, (st.next_in ?? 0) - dt);
    $("#np-gap-text").textContent = `${st.resuming ? "Resuming in" : "Next piece in"} ${clock(left)}`;
  }
}

// Volume: the slider sends while dragging (throttled); polls don't fight the finger.
const vol = $("#np-vol");
let volSentAt = 0;
let volTimer = null;
function showVolume(v) {
  vol.value = v;
  vol.style.setProperty("--pct", `${v}%`);
  $("#np-vol-value").textContent = v;
}
function sendVolume(v) {
  S.volHoldUntil = performance.now() + 2500;
  showVolume(v);
  clearTimeout(volTimer);
  const send = () => {
    volSentAt = performance.now();
    api("/api/volume", { volume: v }).catch((e) => toast(e.message, "warn"));
  };
  if (performance.now() - volSentAt > 150) send();
  else volTimer = setTimeout(send, 150);
}
vol.addEventListener("input", () => sendVolume(Number(vol.value)));

// ------------------------------------------------------------------ browse

async function loadLibrary() {
  if (S.libLoading) return;
  S.libLoading = true;
  try {
    const lib = await api("/api/library");
    S.libVersion = lib.version;
    S.libLoadedAt = Date.now();
    S.tracks = lib.tracks.map((t) => ({ ...t, hay: fold(`${t.composer} ${t.title} ${t.year ?? ""}`) }));
    S.byId = new Map(S.tracks.map((t) => [t.id, t]));
    S.composers = lib.composers;
    if (S.composer && !S.composers.some((c) => c.name === S.composer)) S.composer = null;
    renderBrowse();
  } catch {
    /* the poll loop reports connectivity */
  } finally {
    S.libLoading = false;
  }
}

function passesFilter(t) {
  return S.filter === "fav" ? t.favorite : S.filter === "never" ? t.never : true;
}

function visibleTracks() {
  const words = fold(S.query).split(/\s+/).filter(Boolean);
  return S.tracks.filter((t) => {
    if (!passesFilter(t)) return false;
    if (words.length) return words.every((w) => t.hay.includes(w));
    return !S.composer || t.composer === S.composer;
  });
}

function renderBrowse() {
  // A composer with nothing under the current filter would leave an empty list.
  if (S.composer && !S.tracks.some((t) => t.composer === S.composer && passesFilter(t))) S.composer = null;
  renderComposers();
  renderPieces();
}

function renderComposers() {
  const counts = new Map();
  for (const t of S.tracks) if (passesFilter(t)) counts.set(t.composer, (counts.get(t.composer) || 0) + 1);
  let total = 0;
  counts.forEach((n) => { total += n; });
  const searching = !!S.query.trim();
  const rows = [`<li><button class="${!S.composer && !searching ? "on" : ""}" data-composer="">All composers <span class="n">${total}</span></button></li>`];
  for (const c of S.composers) {
    const n = counts.get(c.name);
    if (!n) continue;
    const on = S.composer === c.name && !searching ? "on" : "";
    rows.push(`<li><button class="${on}" data-composer="${esc(c.name)}">${esc(c.name)} <span class="n">${n}</span></button></li>`);
  }
  $("#composers").innerHTML = rows.join("");
}

function pieceRow(t, showComposer) {
  const cur = S.st && S.st.track ? S.st.track.id : null;
  const meta = [showComposer ? t.composer : null, t.year, clock(t.duration)].filter(Boolean).map(esc).join(" · ");
  const cls = ["piece", t.never ? "never" : "", t.id === cur ? "playing" : ""].filter(Boolean).join(" ");
  return `<li class="${cls}" data-id="${esc(t.id)}">
    <div class="p-main"><div class="p-title">${esc(t.title)}</div><div class="p-meta">${meta}</div></div>
    <button class="btn icon${t.favorite ? " on" : ""}" data-act="fav" aria-label="Favorite" aria-pressed="${t.favorite}">${icon("heart", "heart")}</button>
    <button class="btn icon warn${t.never ? " on" : ""}" data-act="never" aria-label="Never play" aria-pressed="${t.never}">${icon("ban")}</button>
    <button class="btn icon" data-act="queue" aria-label="Add to queue">${icon("plus")}</button>
    <button class="btn icon primary" data-act="play" aria-label="Play now">${icon("play")}</button>
  </li>`;
}

function renderPieces() {
  const list = visibleTracks();
  const searching = !!S.query.trim();
  const filterName = { fav: "Favorites", never: "Never-play list" }[S.filter];
  const count = `${list.length} ${list.length === 1 ? "piece" : "pieces"}`;
  let head;
  if (searching) head = `${count} matching “${esc(S.query.trim())}”`;
  else head = `${esc(S.composer || "All composers")} · ${count}`;
  if (filterName) head += ` · ${filterName}`;
  $("#pieces-head").innerHTML = head;
  const showComposer = searching || !S.composer;
  let empty = "No pieces match.";
  if (!S.tracks.length) empty = "The library is empty. Put MIDI files in the midi folder, then tap Rescan in Settings.";
  else if (S.filter === "fav" && !searching) empty = `No favorites yet. Tap ${icon("heart", "heart")} on a piece to add it.`;
  else if (S.filter === "never" && !searching) empty = "Nothing is on the never-play list.";
  $("#pieces").innerHTML = list.length ? list.map((t) => pieceRow(t, showComposer)).join("") : `<li class="empty">${empty}</li>`;
}

function markPlaying() {
  const cur = S.st && S.st.track ? S.st.track.id : null;
  $$("#pieces .piece").forEach((li) => li.classList.toggle("playing", li.dataset.id === cur));
}

$("#composers").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-composer]");
  if (!b) return;
  S.composer = b.dataset.composer || null;
  if (S.query) { S.query = ""; $("#search").value = ""; $("#search-clear").hidden = true; }
  renderBrowse();
  $("#pieces").scrollTop = 0;
});

let searchTimer = null;
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  $("#search-clear").hidden = !e.target.value;
  searchTimer = setTimeout(() => {
    S.query = e.target.value;
    renderBrowse();
    $("#pieces").scrollTop = 0;
  }, 120);
});
$("#search").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });

$("#filter").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-filter]");
  if (!b) return;
  S.filter = b.dataset.filter;
  $$("#filter button").forEach((x) => x.classList.toggle("on", x === b));
  renderBrowse();
  $("#pieces").scrollTop = 0;
});

$("#pieces").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const t = S.byId.get(btn.closest("li").dataset.id);
  if (!t) return;
  switch (btn.dataset.act) {
    case "play":
      if (await act("/api/play", { id: t.id })) show("now");
      break;
    case "queue":
      await addToQueue(t);
      break;
    case "fav":
      await setFlags(t.id, { favorite: !t.favorite });
      break;
    case "never":
      await setFlags(t.id, { never: !t.never });
      break;
  }
});

async function addToQueue(t) {
  try {
    const r = await api("/api/queue", { id: t.id });
    toast(r.started ? `Playing ${t.title}` : `Added to queue · ${r.queue.length} waiting`);
    poll();
  } catch (e) {
    toast(e.message, "warn");
  }
}

async function setFlags(id, flags) {
  try {
    const r = await api("/api/track", { id, ...flags });
    const t = S.byId.get(id);
    if (t) Object.assign(t, { favorite: r.favorite, never: r.never });
    if (S.st && S.st.track && S.st.track.id === id) Object.assign(S.st.track, { favorite: r.favorite, never: r.never });
    if ("never" in flags) toast(r.never ? `${r.title} won't play in rotation` : `${r.title} is back in rotation`);
    else if (r.favorite) toast(`${r.title} added to favorites`);
    if (S.screen === "browse") {
      const li = $(`#pieces li[data-id="${CSS.escape(id)}"]`);
      if (li && passesFilter(t)) li.outerHTML = pieceRow(t, !!S.query.trim() || !S.composer);
      else renderBrowse();
    }
    if (S.screen === "settings") loadSettings();
    renderNow();
    return r;
  } catch (e) {
    toast(e.message, "warn");
    return null;
  }
}

// ------------------------------------------------------------------- queue

async function loadQueue() {
  try {
    S.queue = await api("/api/queue");
    S.seenRev.queue = S.st ? S.st.rev : null;
    renderQueue();
  } catch { /* offline banner covers it */ }
}

const ENDED = { skipped: "skipped", stopped: "stopped", replaced: "interrupted", error: "couldn't play", shutdown: "cut off by a restart" };

function renderQueue() {
  const q = S.queue;
  if (!q) return;
  const n = q.queue.length;
  $("#queue").innerHTML = n
    ? q.queue.map((t, i) => `<li data-i="${i}" data-id="${esc(t.id)}">
        <span class="q-n">${i + 1}</span>
        <div class="p-main"><div class="p-title">${esc(t.title)}</div><div class="p-meta">${[t.composer, clock(t.duration)].filter(Boolean).map(esc).join(" · ")}</div></div>
        <button class="btn icon" data-q="up" aria-label="Move up" ${i === 0 ? "disabled" : ""}>${icon("up")}</button>
        <button class="btn icon" data-q="down" aria-label="Move down" ${i === n - 1 ? "disabled" : ""}>${icon("down")}</button>
        <button class="btn icon" data-q="remove" aria-label="Remove">${icon("x")}</button>
      </li>`).join("")
    : `<li class="empty">The queue is empty. Add pieces from Browse with ${icon("plus")}.</li>`;
  $("#q-clear").disabled = !n;
  const rn = q.rotation_next;
  $("#q-after").innerHTML = q.rotation_enabled
    ? `${n ? "After the queue, rotation" : "Rotation"} continues${rn ? ` with <b>${esc(rn.composer)} – ${esc(rn.title)}</b>` : ""}.`
    : `${n ? "After the queue the piano stops" : "Nothing else is lined up"}. Rotation is off.`;

  const playingId = S.st && S.st.track ? S.st.track.id : null;
  $("#history").innerHTML = q.history.length
    ? q.history.map((h, i) => {
      const now = i === 0 && !h.ended && h.id === playingId;
      const ended = now ? "playing now" : ENDED[h.ended] || "";
      const meta = [h.composer, (h.started || "").slice(11, 16), ended].filter(Boolean).map(esc).join(" · ");
      return `<li class="${now ? "playing" : ""}" data-id="${esc(h.id)}">
        <div class="p-main"><div class="p-title">${esc(h.title)}</div><div class="p-meta when">${meta}</div></div>
        <button class="btn icon" data-h="queue" aria-label="Add to queue">${icon("plus")}</button>
        <button class="btn icon primary" data-h="play" aria-label="Play again">${icon("play")}</button>
      </li>`;
    }).join("")
    : `<li class="empty">Nothing has played yet.</li>`;
}

$("#queue").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-q]");
  if (!b) return;
  const li = b.closest("li");
  const i = Number(li.dataset.i);
  try {
    if (b.dataset.q === "remove") S.queue = await api("/api/queue/remove", { index: i, id: li.dataset.id });
    else S.queue = await api("/api/queue/move", { from: i, to: b.dataset.q === "up" ? i - 1 : i + 1 });
    renderQueue();
    poll();
  } catch (err) {
    toast(err.message, "warn");
    loadQueue();
  }
});

$("#history").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-h]");
  if (!b) return;
  const id = b.closest("li").dataset.id;
  const t = S.byId.get(id) || { id, title: "that piece" };
  if (b.dataset.h === "play") {
    if (await act("/api/play", { id })) show("now");
  } else {
    await addToQueue(t);
    loadQueue();
  }
});

// ---------------------------------------------------------------- settings

const GAPS = [0, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600];

function fmtGap(v) {
  if (v < 60) return `${v} s`;
  const m = Math.floor(v / 60), s = v % 60;
  return s ? `${m}m ${s}s` : `${m} min`;
}

function stepValue(kind, value, dir) {
  const limits = S.settings || { min_velocity: 1, max_velocity: 127 };
  if (kind === "gap") {
    let i = GAPS.findIndex((g) => g >= value);
    if (i < 0) i = GAPS.length - 1;
    if (GAPS[i] !== value && dir < 0) i += 1;   // between presets: step to the neighbour
    return GAPS[Math.min(GAPS.length - 1, Math.max(0, i + dir))];
  }
  if (kind === "weight") return Math.min(5, Math.max(1, value + dir));
  if (kind === "ceiling") return Math.min(limits.max_velocity, Math.max(limits.min_velocity, value + dir * 5));
  if (kind === "time") {
    const [h, m] = value.split(":").map(Number);
    let t = h * 60 + m;
    t = dir > 0 ? Math.floor(t / 15) * 15 + 15 : Math.ceil(t / 15) * 15 - 15;
    t = (t + 1440) % 1440;
    return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
  }
  return value;
}

function fmtSetting(kind, v) {
  if (kind === "gap") return fmtGap(v);
  if (kind === "weight") return `${v}×`;
  return String(v);
}

async function loadSettings() {
  try {
    S.settings = await api("/api/settings");
    S.seenRev.settings = S.st ? S.st.rev : null;
    renderSettings();
  } catch { /* offline banner covers it */ }
}

function renderSettings() {
  const v = S.settings;
  if (!v) return;
  const s = v.settings;
  $$("#screen-settings .stepper").forEach((el) => {
    el.querySelector("output").textContent = fmtSetting(el.dataset.kind, s[el.dataset.setting]);
  });
  $$("#screen-settings .switch[data-setting]").forEach((el) => el.setAttribute("aria-checked", !!s[el.dataset.setting]));
  $("#set-rotation").setAttribute("aria-checked", v.rotation.enabled);
  $("#set-cycle").textContent = `Cycle ${v.rotation.cycle}: ${v.rotation.played} of ${v.rotation.total} played · ${v.rotation.favorites} ${v.rotation.favorites === 1 ? "favorite" : "favorites"}`;
  const m = v.midi;
  $("#set-midi").textContent = m.connected ? `${m.port}${m.fallback ? " (fallback: expected port not found)" : ""}` : `Not connected${m.error ? ": " + m.error : ""}`;
  $("#set-midi-dot").classList.toggle("ok", m.connected && !m.fallback);
  $("#set-library").textContent = `${v.library_count} pieces in ${v.midi_dir}`;
  $("#set-minvel").textContent = v.min_velocity;
  $("#set-clock").textContent = v.server_time;
  $("#set-never").innerHTML = v.never.length
    ? v.never.map((t) => `<li data-id="${esc(t.id)}"><div class="p-main"><div class="p-title">${esc(t.title)}</div><div class="p-meta">${esc(t.composer)}</div></div>
        <button class="btn" data-allow>Allow</button></li>`).join("")
    : `<li class="empty">Nothing here. Tap ${icon("ban")} on a piece to keep it out of rotation.</li>`;
  $$("#screen-settings .switch[data-local]").forEach((el) => el.setAttribute("aria-checked", prefs.get(el.dataset.local, true)));
}

async function saveSetting(key, value) {
  const before = S.settings && S.settings.settings[key];
  if (S.settings) { S.settings.settings[key] = value; renderSettings(); }
  try {
    S.settings = await api("/api/settings", { [key]: value });
    renderSettings();
    poll();
  } catch (e) {
    if (S.settings) { S.settings.settings[key] = before; renderSettings(); }
    toast(e.message, "warn");
  }
}

$("#screen-settings").addEventListener("click", (e) => {
  const step = e.target.closest(".stepper [data-step]");
  if (step && S.settings) {
    const box = step.closest(".stepper");
    const key = box.dataset.setting;
    const next = stepValue(box.dataset.kind, S.settings.settings[key], Number(step.dataset.step));
    if (next !== S.settings.settings[key]) saveSetting(key, next);
    return;
  }
  const sw = e.target.closest(".switch[data-setting]");
  if (sw && S.settings) { saveSetting(sw.dataset.setting, !S.settings.settings[sw.dataset.setting]); return; }
  const local = e.target.closest(".switch[data-local]");
  if (local) {
    const on = !prefs.get(local.dataset.local, true);
    prefs.set(local.dataset.local, on);
    local.setAttribute("aria-checked", on);
    return;
  }
  const allow = e.target.closest("[data-allow]");
  if (allow) setFlags(allow.closest("li").dataset.id, { never: false });
});

// ----------------------------------------------------------------- actions

function confirmTap(btn, prompt, run) {
  if (btn.classList.contains("armed")) {
    disarm(btn);
    run();
    return;
  }
  btn.dataset.label = btn.innerHTML;
  btn.classList.add("armed");
  btn.textContent = prompt;
  btn.armTimer = setTimeout(() => disarm(btn), 3500);
}
function disarm(btn) {
  clearTimeout(btn.armTimer);
  if (!btn.classList.contains("armed")) return;
  btn.classList.remove("armed");
  btn.innerHTML = btn.dataset.label;
}

const actions = {
  stop: () => act("/api/stop"),
  skip: () => act("/api/skip"),
  rotation: async () => {
    const on = !(S.st && S.st.rotation.enabled);
    const st = await act(on ? "/api/rotation/on" : "/api/rotation/off");
    if (st && on && st.state === "quiet") toast(`Rotation is on. It starts when quiet hours end at ${st.quiet_until}.`);
    if (S.screen === "settings") loadSettings();
  },
  "fav-current": () => { const t = S.st && S.st.track; if (t) setFlags(t.id, { favorite: !t.favorite }); },
  "never-current": (btn) => confirmTap(btn, "Never play this?", async () => {
    const t = S.st && S.st.track;
    if (t && await setFlags(t.id, { never: true })) act("/api/skip");
  }),
  "vol-down": () => sendVolume(Math.max(0, Number(vol.value) - 5)),
  "vol-up": () => sendVolume(Math.min(100, Number(vol.value) + 5)),
  "queue-clear": (btn) => confirmTap(btn, "Tap again to clear", async () => {
    try { S.queue = await api("/api/queue/clear", {}); renderQueue(); poll(); } catch (e) { toast(e.message, "warn"); }
  }),
  "cycle-reset": (btn) => confirmTap(btn, "Tap again", async () => {
    try { await api("/api/rotation/reset", {}); toast("New cycle: every piece is unplayed again"); loadSettings(); poll(); }
    catch (e) { toast(e.message, "warn"); }
  }),
  panic: async () => {
    try { await api("/api/panic", {}); toast("Pedals up, all notes off"); } catch (e) { toast(e.message, "warn"); }
  },
  rescan: async () => {
    try {
      const r = await api("/api/library/rescan", {});
      toast(r.changed ? `Library updated: ${r.count} pieces` : `No changes: ${r.count} pieces`);
      loadLibrary();
      loadSettings();
    } catch (e) { toast(e.message, "warn"); }
  },
  "search-clear": () => {
    const input = $("#search");
    input.value = "";
    S.query = "";
    $("#search-clear").hidden = true;
    renderBrowse();
  },
  fullscreen: () => (isFullscreen() ? exitFullscreen() : enterFullscreen()),
};

document.addEventListener("click", (e) => {
  const nav = e.target.closest("[data-nav]");
  if (nav) { show(nav.dataset.nav); return; }
  const b = e.target.closest("[data-action]");
  if (b && !b.disabled && actions[b.dataset.action]) actions[b.dataset.action](b, e);
});

// ----------------------------------------------------------------- screens

function show(screen) {
  if (!SCREENS.includes(screen)) screen = "now";
  S.screen = screen;
  $$(".screen").forEach((el) => el.classList.toggle("active", el.dataset.screen === screen));
  $$(".rail [data-nav]").forEach((b) => b.setAttribute("aria-current", b.dataset.nav === screen ? "page" : "false"));
  if (location.hash !== "#" + screen) history.replaceState(null, "", "#" + screen);
  if (screen === "browse") {
    if (Date.now() - S.libLoadedAt > 30000) loadLibrary();   // picks up lengths measured since
    else renderBrowse();
  } else if (screen === "queue") loadQueue();
  else if (screen === "settings") loadSettings();
  else renderNow();
}

// --------------------------------------------------------------- toast etc.

let toastTimer = null;
function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ""; }, kind === "warn" ? 4500 : 2600);
}

// Fullscreen: real install needs HTTPS, so also offer the Fullscreen API.
const standalone = () => matchMedia("(display-mode: fullscreen), (display-mode: standalone)").matches || navigator.standalone === true;
const canFullscreen = () => !!(document.documentElement.requestFullscreen || document.documentElement.webkitRequestFullscreen);
const isFullscreen = () => !!(document.fullscreenElement || document.webkitFullscreenElement);
function enterFullscreen() {
  const d = document.documentElement;
  try {
    const p = d.requestFullscreen ? d.requestFullscreen({ navigationUI: "hide" }) : d.webkitRequestFullscreen();
    if (p && p.catch) p.catch(() => {});
  } catch { /* not allowed here */ }
}
function exitFullscreen() {
  try {
    const p = document.exitFullscreen ? document.exitFullscreen() : document.webkitExitFullscreen();
    if (p && p.catch) p.catch(() => {});
  } catch { /* already out */ }
}

document.addEventListener("pointerdown", () => { S.lastTouch = Date.now(); }, { capture: true, passive: true });
document.addEventListener("click", () => {
  if (S.autoFsDone || standalone() || !canFullscreen() || isFullscreen()) return;
  S.autoFsDone = true;
  if (prefs.get("autoFullscreen", true)) enterFullscreen();
}, { capture: true });

function installHint() {
  const el = $("#install-hint");
  if (standalone()) { el.textContent = "Running as an installed app."; return; }
  if (!window.isSecureContext) {
    el.innerHTML = `Chrome only installs apps from HTTPS, and this page is plain HTTP. To launch fullscreen from the home screen:
      open <code>chrome://flags</code>, enable <b>Insecure origins treated as secure</b>, add <code>${esc(location.origin)}</code>,
      relaunch Chrome, then <b>⋮ → Add to Home screen → Install</b>. On an iPad, <b>Share → Add to Home Screen</b> works as is.`;
    return;
  }
  el.innerHTML = "Use <b>Install app</b> or <b>Add to Home Screen</b> in the browser menu to launch PianoJuke fullscreen.";
}

function railClock() {
  const d = new Date();
  $("#rail-clock").textContent = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  $("#fs-btn").hidden = standalone() || !canFullscreen();
}

// Wander back to Now Playing when the tablet is left on another screen.
setInterval(() => {
  if (S.screen !== "now" && prefs.get("autoHome", true) && Date.now() - S.lastTouch > 180000) {
    if (document.activeElement) document.activeElement.blur();
    show("now");
  }
  railClock();
}, 10000);

setInterval(tick, 250);
railClock();
installHint();
show(location.hash.slice(1));
poll();
if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
