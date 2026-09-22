# locspoof

Set your iPhone's GPS location from your computer. Click a spot on the map, or type
coordinates, and every app on the phone sees you there.

Works on macOS and Windows, with iPhones on iOS 17 or newer.

---

## What you need

- **An iPhone on iOS 17 or newer**, and a USB cable to connect it.
- **An internet connection the first time you connect a phone.** locspoof downloads
  a one-time component from Apple's developer tools and saves it for next time.
- **Python 3.10 or newer** on your computer — [python.org/downloads](https://www.python.org/downloads/).
  On Windows, tick **"Add python.exe to PATH"** during install.
- **Windows only:** Apple's device drivers. The easiest way to get them is to install
  **iTunes** or **Apple Devices** from the Microsoft Store.

### One-time iPhone setup

1. **Trust your computer.** Plug the phone in, unlock it, and tap **Trust** when asked.
2. **Turn on Developer Mode.** Settings → Privacy & Security → Developer Mode → On.
   The phone restarts; confirm when it asks.
   Don't see Developer Mode there? iOS hides it until a developer tool asks for it —
   see [Troubleshooting](#troubleshooting).

---

## Getting started

```bash
git clone <this repository>
cd locspoof
```

Then start it:

| macOS | Windows |
| --- | --- |
| `./run.sh` | double-click `run.bat` |

The first launch sets itself up, which takes a minute. After that it starts in a
few seconds. Windows will ask for administrator permission each time — that's
required to talk to the iPhone.

Your browser opens to **http://127.0.0.1:8765**. **Keep the terminal window open**
while you use it — closing it disconnects the phone.

The map needs an internet connection to load.

---

## Using it

**Set a location** — click anywhere on the map, then **Move here**. Or type a
latitude and longitude and click **Set location**; you can paste both at once, like
`40.690008, -74.045843`, into either box.

On the map, the **red pin** is where you asked to be and the **blue dot** is what
the phone is actually told — they differ slightly while drift is on.

**Save a bookmark** — type a name in **Bookmark as** before setting a location, or
click **Save current** to bookmark where you already are. Bookmarks appear at the
bottom; click **Go** to jump back to one.

**GPS drift** — a real phone's location is never perfectly still. With drift on,
your location wobbles a few meters, like real GPS. Turn it off for an exact,
fixed point. **Radius** sets how far it wanders.

**Clear** — hands your phone back its real GPS, and stays connected.

**Quit** — restores your real GPS and disconnects. Pressing Ctrl+C in the terminal
does the same.

**Closing the page also quits.** If no locspoof page is open for 30 seconds,
locspoof restores your real GPS and shuts down, so a forgotten session never leaves
your phone stuck somewhere. Reloading the page, or reopening it within 30 seconds,
keeps your session. Your real location always comes back when locspoof closes.

---

## Debug mode

If locspoof can't reach an iPhone, it still opens — in **debug mode**. A yellow
banner at the top says so and tells you why:

- **No Phone Connected** — nothing is plugged in, or the phone isn't unlocked and trusted.
- **Phone found but couldn't connect** — the banner shows the reason, most often
  Developer Mode being off.

Everything on the page works in debug mode, but nothing moves a real phone. Fix the
problem the banner describes, then restart locspoof.

To try the app with a phone plugged in but without moving it, start with `--debug`.

---

## Options

Add these after `./run.sh` or `run.bat`:

| option | what it does |
| --- | --- |
| `--port 8766` | use a different port, if 8765 is taken |
| `--no-browser` | don't open the browser automatically |
| `--debug` | start in debug mode even with a phone connected |
| `--verbose` | print connection details, for troubleshooting |
| `--help` | list these options |

---

## Troubleshooting

**"Port 8765 is already in use"** — locspoof is probably already running in another
window. Close it, or start with `--port 8766`.

**"Python 3.10 or newer is required"** — install it from python.org. On Windows,
reinstall and tick "Add python.exe to PATH".

**Stuck on "No Phone Connected" with the phone plugged in** — unlock the phone,
check for a Trust prompt, and try another cable or port. Charge-only cables don't
carry data. On Windows, make sure iTunes or Apple Devices is installed.

**Developer Mode is missing from Settings** — run locspoof once so it sets itself up,
then, with the phone plugged in and unlocked, run this from the locspoof folder:

```
.venv/bin/python -m pymobiledevice3 amfi reveal-developer-mode            # macOS
.venv\Scripts\python.exe -m pymobiledevice3 amfi reveal-developer-mode   # Windows
```

The option now appears under Settings → Privacy & Security.

**"Phone found but couldn't connect" on the first try** — the first connection
downloads a component from Apple, so check you're online. After that it's saved.

**macOS: "Phone found but couldn't connect", and Developer Mode is on** — try
`sudo ./run.sh --sudo-tunnel`, which uses a different connection method.

**Anything else** — run with `--verbose` and look at the terminal output.

---

## License

locspoof is free software, released under the
[GNU General Public License v3.0](LICENSE).

It's built on [pymobiledevice3](https://github.com/doronz88/pymobiledevice3), which is
also GPL-3.0. The map uses [Leaflet](https://leafletjs.com) (BSD-2-Clause, bundled in
`web/vendor/leaflet`) with map data © [OpenStreetMap](https://www.openstreetmap.org/copyright)
contributors.
