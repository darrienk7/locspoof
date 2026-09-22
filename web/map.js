// The map: shows where the phone is being told it is, and lets you click to
// move there.
//
// Tiles come from OpenStreetMap's public servers. That's fine for personal
// use, but their policy says commercial services can lose access at any time -
// switch TILE_URL to a commercial provider (MapTiler, Stadia, ...) before
// selling this.

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

// Zoom used when jumping to a new location, unless already zoomed in further.
const FOCUS_ZOOM = 16;

const PIN_SVG = `
<svg width="28" height="40" viewBox="0 0 28 40" xmlns="http://www.w3.org/2000/svg">
  <path d="M14 0C6.3 0 0 6.2 0 13.9 0 24.3 14 40 14 40s14-15.7 14-26.1C28 6.2 21.7 0 14 0z"
        fill="#e5484d"/>
  <circle cx="14" cy="14" r="5.5" fill="#fff"/>
</svg>`;

function createMap(container, { onPick }) {
  const map = L.map(container, { worldCopyJump: true }).setView([20, 0], 2);
  L.tileLayer(TILE_URL, { maxZoom: 19, attribution: TILE_ATTRIBUTION }).addTo(map);

  const pinIcon = L.divIcon({
    className: "pin",
    html: PIN_SVG,
    iconSize: [28, 40],
    iconAnchor: [14, 39],
  });

  // Overlays are non-interactive so a click anywhere - even on the pin -
  // falls through to the map and can pick a new spot.
  let anchorPin = null;
  let reportedDot = null;
  let driftCircle = null;
  let lastAnchor = null;

  map.on("click", (event) => {
    const { lat, lng } = event.latlng.wrap();   // keep longitude in -180..180

    const box = document.createElement("div");
    box.className = "map-popup";
    const coords = document.createElement("div");
    coords.className = "coords";
    coords.textContent = `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
    const move = document.createElement("button");
    move.textContent = "Move here";
    move.onclick = () => {
      map.closePopup();
      onPick(lat, lng);
    };
    box.append(coords, move);

    L.popup({ closeButton: true }).setLatLng(event.latlng).setContent(box).openOn(map);
  });

  function update(state) {
    const anchor = state.anchor;
    const reported = state.current;
    const noise = state.noise;

    if (anchor) {
      const at = [anchor.latitude, anchor.longitude];
      if (anchorPin) anchorPin.setLatLng(at);
      else anchorPin = L.marker(at, { icon: pinIcon, interactive: false, keyboard: false }).addTo(map);
    } else if (anchorPin) {
      anchorPin.remove();
      anchorPin = null;
    }

    if (anchor && noise.enabled) {
      const at = [anchor.latitude, anchor.longitude];
      if (driftCircle) driftCircle.setLatLng(at).setRadius(noise.radius_m);
      else driftCircle = L.circle(at, {
        radius: noise.radius_m,
        color: "#2563eb",
        weight: 1,
        dashArray: "4 4",
        fillOpacity: 0.08,
        interactive: false,
      }).addTo(map);
    } else if (driftCircle) {
      driftCircle.remove();
      driftCircle = null;
    }

    if (reported) {
      const at = [reported.latitude, reported.longitude];
      if (reportedDot) reportedDot.setLatLng(at);
      else reportedDot = L.circleMarker(at, {
        radius: 7,
        color: "#ffffff",
        weight: 2.5,
        fillColor: "#1a73e8",
        fillOpacity: 1,
        interactive: false,
      }).addTo(map);
    } else if (reportedDot) {
      reportedDot.remove();
      reportedDot = null;
    }

    // Follow only when the anchor itself moves - not on every drift tick,
    // which would fight the user whenever they pan around.
    const key = anchor ? `${anchor.latitude},${anchor.longitude}` : null;
    if (key && key !== lastAnchor) {
      map.setView([anchor.latitude, anchor.longitude], Math.max(map.getZoom(), FOCUS_ZOOM));
    }
    lastAnchor = key;
  }

  // The debug banner, window resizes and the mobile layout all change the
  // map's size after it's created; Leaflet has to be told.
  new ResizeObserver(() => map.invalidateSize()).observe(container);

  return { update };
}
