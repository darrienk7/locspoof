// locspoof web app.
//
// Commands go out over HTTP; state comes back over the WebSocket. The page
// never computes state itself - it renders whatever the last snapshot said,
// so two open tabs always agree.

const $ = (id) => document.getElementById(id);

const el = {
  dot: $("dot"), device: $("device"),
  debugBanner: $("debugBanner"), debugTitle: $("debugTitle"), debugDetail: $("debugDetail"),
  lat: $("lat"), lon: $("lon"), name: $("name"), set: $("set"),
  anchor: $("anchor"), current: $("current"), offset: $("offset"), ticks: $("ticks"),
  saveName: $("saveName"), save: $("save"), clear: $("clear"),
  noiseOn: $("noiseOn"), radius: $("radius"), interval: $("interval"),
  applyNoise: $("applyNoise"), noiseState: $("noiseState"),
  bookmarks: $("bookmarks"), quit: $("quit"), toast: $("toast"),
  closed: $("closed"), closedTitle: $("closedTitle"), closedDetail: $("closedDetail"),
};

let closed = false;
let debugMode = false;

// After this many failed reconnects (about 10 seconds), assume locspoof has
// stopped rather than retrying forever.
const MAX_RECONNECTS = 8;
let failedReconnects = 0;
// Don't clobber a number the user is mid-edit with a pushed snapshot.
const editing = new Set();
for (const input of [el.radius, el.interval]) {
  input.addEventListener("focus", () => editing.add(input.id));
  input.addEventListener("blur", () => editing.delete(input.id));
}

// ---------------------------------------------------------------- helpers

let toastTimer;
function toast(message, bad = false) {
  el.toast.textContent = message;
  el.toast.classList.toggle("bad", bad);
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, bad ? 5200 : 2600);
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  let payload = {};
  try { payload = await response.json(); } catch { /* empty body */ }
  if (!response.ok) throw new Error(payload.detail || `request failed (${response.status})`);
  return payload;
}

async function send(path, body, okMessage) {
  try {
    const result = await post(path, body);
    if (result.warning) toast(result.warning, true);
    else if (okMessage) toast(debugMode ? `${okMessage} (simulated)` : okMessage);
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}

const fmt = (point) =>
  point ? `${point.latitude.toFixed(6)}, ${point.longitude.toFixed(6)}` : "—";

// Accept "lat, lon" or "lat lon" pasted into either box.
function splitPastedPair(source, other) {
  const parts = source.value.trim().split(/[,\s]+/).filter(Boolean);
  if (parts.length === 2 && parts.every((p) => !isNaN(parseFloat(p)))) {
    source.value = parts[0];
    other.value = parts[1];
  }
}
el.lat.addEventListener("input", () => splitPastedPair(el.lat, el.lon));
el.lon.addEventListener("input", () => splitPastedPair(el.lon, el.lat));

// ---------------------------------------------------------------- render

function render(state) {
  debugMode = Boolean(state.debug);
  el.debugBanner.hidden = !debugMode;
  document.title = debugMode ? "DEBUG MODE · locspoof" : "locspoof";
  if (debugMode) {
    el.debugTitle.textContent = state.debug.title;
    el.debugDetail.textContent = state.debug.detail;
  }

  const device = state.device;
  el.dot.className = debugMode ? "dot debug" : "dot live";
  el.device.textContent = debugMode
    ? "simulated phone"
    : device ? `${device.product_type} · iOS ${device.ios_version}` : "no device";

  el.anchor.textContent = fmt(state.anchor);
  el.current.textContent = fmt(state.current);

  const noise = state.noise;
  if (state.anchor && noise.tick_count > 0) {
    const n = noise.offset_north_m, e = noise.offset_east_m;
    el.offset.textContent =
      `${n >= 0 ? "+" : ""}${n.toFixed(2)} N  ${e >= 0 ? "+" : ""}${e.toFixed(2)} E` +
      `  (${noise.offset_magnitude_m.toFixed(2)}\u00a0m)`;
  } else {
    el.offset.textContent = "—";
  }
  el.ticks.textContent = state.anchor ? String(noise.tick_count) : "—";

  el.noiseOn.checked = noise.enabled;
  if (!editing.has("radius")) el.radius.value = noise.radius_m;
  if (!editing.has("interval")) el.interval.value = noise.interval_s;

  if (!noise.enabled) {
    el.noiseState.textContent = "Off — reporting the exact anchor.";
  } else if (noise.active) {
    el.noiseState.textContent =
      `Drifting ±${noise.radius_m}m every ${noise.interval_s}s.`;
  } else if (state.anchor) {
    el.noiseState.textContent = "Stopped — the last write failed.";
  } else {
    el.noiseState.textContent = "Armed — set a location to start drifting.";
  }

  el.save.disabled = !state.anchor;
  el.clear.disabled = !state.anchor;

  renderBookmarks(state.bookmarks);
  map.update(state);
}

function renderBookmarks(bookmarks) {
  el.bookmarks.replaceChildren();
  if (!bookmarks.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "none yet";
    el.bookmarks.append(empty);
    return;
  }
  for (const bookmark of bookmarks) {
    const row = document.createElement("li");

    const name = document.createElement("span");
    name.className = "bm-name";
    name.textContent = bookmark.name;            // textContent, never innerHTML

    const coord = document.createElement("span");
    coord.className = "bm-coord";
    coord.textContent = `${bookmark.latitude}, ${bookmark.longitude}`;

    const actions = document.createElement("div");
    actions.className = "bm-actions";

    const go = document.createElement("button");
    go.textContent = "Go";
    go.onclick = () => send("/api/goto", { name: bookmark.name }, `moved to ${bookmark.name}`);

    const remove = document.createElement("button");
    remove.className = "danger";
    remove.textContent = "Delete";
    remove.onclick = () =>
      send("/api/bookmarks/delete", { name: bookmark.name }, `deleted ${bookmark.name}`);

    actions.append(go, remove);
    row.append(name, coord, actions);
    el.bookmarks.append(row);
  }
}

// ---------------------------------------------------------------- actions

function moveTo(latitude, longitude, name = null) {
  return send(
    "/api/location",
    { latitude, longitude, name },
    `moved to ${latitude.toFixed(6)}, ${longitude.toFixed(6)}`,
  );
}

el.set.onclick = async () => {
  const latitude = parseFloat(el.lat.value);
  const longitude = parseFloat(el.lon.value);
  if (!isFinite(latitude) || !isFinite(longitude)) {
    toast("enter a latitude and a longitude", true);
    return;
  }
  const name = el.name.value.trim();
  if (await moveTo(latitude, longitude, name || null)) el.name.value = "";
};

const map = createMap($("map"), {
  onPick: (latitude, longitude) => {
    el.lat.value = latitude.toFixed(6);
    el.lon.value = longitude.toFixed(6);
    moveTo(latitude, longitude);
  },
});

el.save.onclick = async () => {
  const name = el.saveName.value.trim();
  if (!name) { toast("name the bookmark first", true); return; }
  if (await send("/api/bookmarks", { name }, `saved ${name}`)) el.saveName.value = "";
};

el.clear.onclick = () => send("/api/location/clear", {}, "cleared — real GPS restored");

el.noiseOn.onchange = () =>
  send("/api/noise", { enabled: el.noiseOn.checked },
       el.noiseOn.checked ? "drift on" : "drift off — back to the exact anchor");

el.applyNoise.onclick = () =>
  send("/api/noise", {
    radius_m: parseFloat(el.radius.value),
    interval_s: parseFloat(el.interval.value),
  }, "drift settings applied");

function showClosed(title, detail) {
  closed = true;
  el.closedTitle.textContent = title;
  el.closedDetail.textContent = detail;
  el.closed.hidden = false;
  el.dot.className = "dot";
  el.device.textContent = "closed";
  document.querySelectorAll("button, input").forEach((node) => { node.disabled = true; });
}

function showAppClosed() {
  showClosed(
    "locspoof has closed",
    debugMode
      ? "It was in debug mode, so no phone was affected. You can close this tab."
      : "Your real GPS has been restored. You can close this tab.",
  );
}

el.quit.onclick = async () => {
  await send("/api/shutdown", {});
  showAppClosed();
};

for (const input of [el.lat, el.lon, el.name]) {
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") el.set.click(); });
}
el.saveName.addEventListener("keydown", (event) => {
  if (event.key === "Enter") el.save.click();
});

// ---------------------------------------------------------------- socket

function connect() {
  const socket = new WebSocket(`ws://${location.host}/ws`);

  socket.onopen = () => { failedReconnects = 0; };
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.closing) {
      showAppClosed();
      return;
    }
    render(message);
  };
  socket.onclose = () => {
    if (closed) return;
    failedReconnects += 1;
    if (failedReconnects > MAX_RECONNECTS) {
      showClosed("locspoof isn't running", "Start it again, then reload this page.");
      return;
    }
    el.dot.className = "dot dead";
    el.device.textContent = "reconnecting…";
    setTimeout(connect, 1200);
  };
  socket.onerror = () => socket.close();
}

connect();
