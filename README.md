# locspoof

Terminal location spoofer for iOS 17+, wrapping [pymobiledevice3](https://github.com/doronz88/pymobiledevice3).

Replaces this two-step manual dance:

```
python -m pymobiledevice3 lockdown start-tunnel
python -m pymobiledevice3 developer dvt simulate-location set --rsd ADDR PORT -- LAT LON
```

...with a single prompt that opens the tunnel once, holds the DVT
LocationSimulation channel open, and lets you type coordinates repeatedly.

## Requirements

- iPhone in **Developer Mode**, plugged in, unlocked, and trusted
- A `.venv` in this folder with `pymobiledevice3` installed

## Run

**macOS** — no sudo. pymobiledevice3 piggybacks Apple's native tunnel via
`remotepairingd`, which needs no privileges.

```
./run.sh
./run.sh --debug
```

First time only: `chmod +x run.sh`

**Windows** — the classic tunnel builds a TUN interface, so it needs Administrator.

```
run.bat            # self-elevates via UAC, then starts the prompt
run.bat --debug
```
# OR

Either platform, from an already-privileged shell:

```
.venv/bin/python spoof.py          # macOS / Linux
.venv\Scripts\python.exe spoof.py  # Windows
```

## At the prompt

```
loc> 40.690008, -74.045843     set any location (Ex: Statue of Liberty)
loc> 35.6762 139.6503          commas optional
loc> clear                     restore real GPS, stay connected
loc> q.                        clear, close tunnel, exit
```

Quitting always restores your real GPS.

## Flags

| flag | meaning |
| --- | --- |
| `--debug` | echo raw tunnel output to stderr |
| `--sudo-tunnel` | macOS only: force the classic root tunnel instead of the native one (run under `sudo`) |
