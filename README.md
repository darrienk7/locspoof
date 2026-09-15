# locspoof

Terminal location spoofer for iOS 17+, wrapping [pymobiledevice3](https://github.com/doronz88/pymobiledevice3).

Opens the tunnel once, holds the DVT LocationSimulation channel open, and lets
you type coordinates repeatedly — instead of re-running two commands per point.

## Requirements

- iPhone in **Developer Mode**, plugged in, unlocked, and trusted
- A `.venv` in this folder with `pymobiledevice3` installed

## Run

**macOS** — no sudo. pymobiledevice3 piggybacks Apple's native tunnel via
`remotepairingd`, which needs no privileges.

```
./run.sh
```

**Windows** — the classic tunnel builds a TUN interface, so it needs Administrator.

```
run.bat
```
# OR 

Either platform, from an already-privileged shell:

```
.venv/bin/python main.py          # macOS / Linux
.venv\Scripts\python.exe main.py  # Windows
```

## At the prompt

```
loc> 40.690008, -74.045843          set location (Statue of Liberty)
loc> 35.6762 139.6503               commas optional
loc> 40.690008, -74.045843 as home  set it and bookmark it
loc> home                           go to a bookmark (exact, case-sensitive)
loc> save home                      bookmark where you are now
loc> list                           show bookmarks; 'delete <name>', 'back'
loc> noise                          drift status: radius, ticks, last offset
loc> noise off                      stop drifting, snap to the exact coordinate
loc> noise on                       resume
loc> noise 10                       change radius to +/-10 m, live
loc> clear                          restore real GPS, stay connected
loc> q                              clear, close tunnel, exit
```

Quitting always restores your real GPS.

Bookmarks are optional — plain coordinates teleport in a single input. They live
in a SQLite file at `data/locations.sqlite3`, which is gitignored. Names are
exact and case-sensitive: `Home` and `home` are different bookmarks. Names that
collide with a command (`clear`, `noise`, `list`, `save`, `q`, …) or that parse
as coordinates are refused, because the prompt would never reach them.

## GPS noise

A real receiver never reports a perfectly still point. After a successful spoof,
locspoof rewrites the location once a second with a small random offset so the
blue dot behaves like GPS instead of a pin.

Jitter is always computed from the **anchor** — the coordinate you typed — never
from the previous jittered point. That distinction is the whole design: feeding
output back in would be a random walk that wanders off (~160 m after 10k ticks
in testing).

Each axis (north, east) sums three components, all scaled by `radius_m`:

| component | role |
| --- | --- |
| first-order Gauss-Markov | slow wandering bias, 30 s correlation time |
| white Gaussian | per-sample scatter |
| reflected random walk | bounded low-frequency drift |

The Gauss-Markov term uses the exact discrete transition, so behavior does not
depend on sample rate — `dt` is measured from `perf_counter`, not assumed.
A plain random walk is unbounded, so that component reflects at a symmetric
limit; the sum is then clipped to `+/-radius_m` per axis. Offsets in meters
convert to degrees with a `cos(latitude)` correction for longitude.

Tuning lives in `NoiseParameters` as fractions of the radius. These are
deliberate choices, not a calibrated iPhone receiver model.

## Flags

| flag | meaning |
| --- | --- |
| `--debug` | echo raw tunnel output and internal trace to stderr |
| `--noise M` | drift radius in meters (default 3) |
| `--noise-interval S` | seconds between samples (default 1) |
| `--no-noise` | disable drift entirely — a perfectly static point |
| `--sudo-tunnel` | macOS only: force the classic root tunnel (run under `sudo`) |
| `-h`, `--help` | print usage and exit without touching the device |

Unknown options are refused rather than ignored, so a typo like `--nosie 5`
stops instead of silently running with the default radius.

## Layout

```
main.py                    entry point: flags, wiring, exit codes
cli/prompt.py              the REPL — presentation only
core/models.py             Coordinate, Device, TunnelInfo
core/tunnel_manager.py     start-tunnel subprocess lifecycle
core/device_manager.py     RSD + DVT session
core/location_service.py   set/clear, noise ticker, routes (later)
core/location_store.py     SQLite bookmarks
core/noise.py              the GPS error model
tests/                     unittest suite; runs without a device
data/                      locations.sqlite3 (gitignored)
```

Nothing in `core/` prints, and no pymobiledevice3 object crosses out of it —
`Device`, `Coordinate` and `SavedLocation` are the boundary. The pymobiledevice3
import is deferred behind `TYPE_CHECKING`, so the whole suite runs on a machine
with no iPhone and no tunnel:

```
python -m pytest tests -q
```