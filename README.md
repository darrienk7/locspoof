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
loc> 40.690008, -74.045843     set location (Statue of Liberty)
loc> 35.6762 139.6503          commas optional
loc> noise                     drift status: radius, ticks, last offset
loc> noise off                 stop drifting, snap to the exact coordinate
loc> noise on                  resume
loc> noise 10                  change radius to +/-10 m, live
loc> clear                     restore real GPS, stay connected
loc> q                         clear, close tunnel, exit
```

Quitting always restores your real GPS.

## GPS noise

A real receiver never reports a perfectly still point. After a successful spoof,
locspoof rewrites the location once a second with a small random offset so the
blue dot behaves like GPS instead of a pin.

Jitter is always computed from the **anchor** — the coordinate you typed — never
from the previous jittered point. That distinction is the whole design: feeding
output back in would be a random walk that wanders off (~160 m after 10k ticks
in testing).

Current implementation is deliberately the simplest thing that works: a uniform
random offset per axis, converted from meters to degrees with a `cos(latitude)`
correction for longitude. No filtering, no correlation between samples, no
velocity. It lives alone in `core/noise.py` so it can be swapped wholesale.

## Flags

| flag | meaning |
| --- | --- |
| `--debug` | echo raw tunnel output and internal trace to stderr |
| `--noise M` | drift radius in meters (default 3) |
| `--noise-interval S` | seconds between samples (default 1) |
| `--no-noise` | disable drift entirely — a perfectly static point |
| `--sudo-tunnel` | macOS only: force the classic root tunnel (run under `sudo`) |

## Layout

```
main.py                    entry point: flags, wiring, exit codes
cli/prompt.py              the REPL — presentation only
core/models.py             Coordinate, Device, TunnelInfo
core/tunnel_manager.py     start-tunnel subprocess lifecycle
core/device_manager.py     RSD + DVT session
core/location_service.py   set/clear, noise ticker, routes (later)
core/noise.py              the jitter arithmetic
```