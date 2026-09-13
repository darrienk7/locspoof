# locspoof

Terminal location spoofer for iOS 17+, wrapping [pymobiledevice3](https://github.com/doronz88/pymobiledevice3).

Replaces this simple two step command prompt method:

```
python -m pymobiledevice3 lockdown start-tunnel
python -m pymobiledevice3 developer dvt simulate-location set --rsd ADDR PORT -- LAT LON
```

...with a single prompt that opens the tunnel once, holds the DVT
LocationSimulation channel open, and lets you type coordinates repeatedly.

## Requirements

- iPhone in **Developer Mode**, plugged in, unlocked, and trusted
- An **elevated shell** — `lockdown start-tunnel` is `@sudo_required`
- The `.venv` in this folder with `pymobiledevice3` installed

## Run

```
run.bat --debug    # self-elevates via UAC, then starts the prompt
```

Or from an already-Administrator terminal:

```
.venv\Scripts\python.exe spoof.py
```

## At the prompt

```
loc> 40.690008, -74.045843     set location (Statue of Liberty)
loc> 35.6762 139.6503          commas optional
loc> clear                     restore real GPS, stay connected
loc> q                         clear, close tunnel, exit
```

Quitting always restores your real GPS.
