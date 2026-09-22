#!/usr/bin/env python3
"""locspoof - connects to your iPhone and opens the web app.

With no usable iPhone it still opens, in DEBUG MODE: every control works, but
writes go to an in-process stand-in instead of a phone, and the page says so.
"""

from __future__ import annotations

import asyncio
import re
import sys
from typing import Optional

from core.device_manager import DeviceManager
from core.location_service import LocationService
from core.location_store import LocationStore
from core.tunnel_manager import IS_MAC, IS_WINDOWS, TunnelManager, is_admin
from gui import server

USAGE = f"""locspoof - set your iPhone's GPS location from a web page

usage: run.sh [options]      (run.bat on Windows)

options:
  -h, --help       show this message and exit
  --port N         serve the page on port N (default {server.DEFAULT_PORT})
  --no-browser     don't open the page automatically
  --debug          start in DEBUG MODE even if an iPhone is connected
  --verbose        print connection details and every location write
  --sudo-tunnel    macOS only: use the root tunnel if the normal one fails
"""

BOOL_FLAGS = frozenset({"-h", "--help", "--no-browser", "--debug", "--verbose", "--sudo-tunnel"})
VALUE_FLAGS = frozenset({"--port"})

ARGS = sys.argv[1:]
HELP = "-h" in ARGS or "--help" in ARGS
FORCE_DEBUG = "--debug" in ARGS
VERBOSE = "--verbose" in ARGS
NO_BROWSER = "--no-browser" in ARGS
FORCE_SUDO_TUNNEL = "--sudo-tunnel" in ARGS

# macOS borrows Apple's existing tunnel (no admin). Elsewhere we build one.
USE_NATIVE_TUNNEL = IS_MAC and not FORCE_SUDO_TUNNEL

# The headline shown in the page's DEBUG MODE banner.
NO_PHONE = "No Phone Connected"
PHONE_FAILED = "Phone found but couldn't connect"
FORCED = "Started with --debug"


# pymobiledevice3 colours its log output; strip that before showing it.
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def failure_summary(exc: Exception) -> str:
    """The most useful single line explaining a failed connection.

    The tunnel reports failures on stderr, and the last line there is usually
    the actual cause ("...Developer Mode is not enabled"). That beats the
    generic "exited without printing an address" the exception itself carries.
    """
    details = getattr(exc, "details", "") or ""
    lines = [ANSI_ESCAPE.sub("", line).strip() for line in details.splitlines()]
    lines = [line for line in lines if line]
    if lines:
        return lines[-1][:200]
    return str(exc) or type(exc).__name__


def log(message: str) -> None:
    if VERBOSE:
        print(f"  [verbose] {message}", file=sys.stderr)


def parse_args() -> Optional[int]:
    """Return the port, or None if the arguments are unusable (already reported)."""
    index = 0
    while index < len(ARGS):
        arg = ARGS[index]
        if arg in BOOL_FLAGS:
            index += 1
        elif arg in VALUE_FLAGS:
            index += 2
        else:
            print(f"! unknown option: {arg}  (see --help)")
            return None

    if "--port" not in ARGS:
        return server.DEFAULT_PORT
    position = ARGS.index("--port") + 1
    try:
        port = int(ARGS[position])
    except (IndexError, ValueError):
        print("! --port needs a number, e.g. --port 8766")
        return None
    if not 1 <= port <= 65535:
        print("! --port must be between 1 and 65535")
        return None
    return port


async def iphone_plugged_in() -> bool:
    """Ask the OS's USB device service whether an iPhone is attached.

    Takes about a tenth of a second, so "no phone" is detected immediately
    instead of waiting out the tunnel's timeout. If the service itself isn't
    running (on Windows: Apple Mobile Device Support missing), no phone can
    connect either way.
    """
    from pymobiledevice3.usbmux import list_devices

    try:
        devices = await list_devices()
    except Exception as exc:
        log(f"usbmux unavailable: {exc}")
        return False
    return any(device.is_usb for device in devices)


async def connect(devices: DeviceManager, location: LocationService) -> Optional[tuple[str, str]]:
    """Bring up the real session.

    Returns None on success, or (headline, detail) explaining why the app is
    falling back to DEBUG MODE.
    """
    if not await iphone_plugged_in():
        return (
            NO_PHONE,
            "Plug in your iPhone with a cable, unlock it, tap Trust if asked, "
            "then restart locspoof.",
        )

    if not USE_NATIVE_TUNNEL and not is_admin():
        where = "run.bat" if IS_WINDOWS else "sudo ./run.sh"
        return (
            PHONE_FAILED,
            f"Connecting to an iPhone here needs administrator rights. Start locspoof with {where}.",
        )

    print("  iPhone found - connecting (a few seconds)...")
    try:
        await devices.open_tunnel()
        device = await devices.connect()
        await location.attach()
    except Exception as exc:
        # Tear down whatever half-opened before falling back.
        await devices.disconnect()
        log(f"connection failed: {exc}\n{getattr(exc, 'details', '')}")
        return (
            PHONE_FAILED,
            f"{failure_summary(exc)} — check the iPhone is unlocked and trusted, "
            "Developer Mode is on (Settings › Privacy & Security), and this "
            "computer is online the first time you connect.",
        )

    print(f"  Connected to {device.product_type} (iOS {device.ios_version})")
    return None


async def main() -> int:
    if HELP:
        print(USAGE, end="")
        return 0

    port = parse_args()
    if port is None:
        return 2

    # Before touching the phone: a second copy of locspoof would otherwise
    # open a second tunnel to it and only then discover it can't serve.
    if not server.port_is_free(port):
        print(f"! port {port} is already in use - is locspoof already running?")
        print(f"  Close it, or start this one with --port {port + 1}")
        return 1

    tunnel = TunnelManager(use_native=USE_NATIVE_TUNNEL, debug=log)
    devices = DeviceManager(tunnel, debug=log, status=lambda message: print(f"  {message}"))
    location = LocationService(devices, debug=log)

    print("locspoof")
    if FORCE_DEBUG:
        debug_reason = (FORCED, "Nothing you do here will move a real phone.")
    else:
        debug_reason = await connect(devices, location)

    if debug_reason is not None:
        location.attach_simulated()
        print(f"  DEBUG MODE - {debug_reason[0]}")
        print(f"  {debug_reason[1]}")

    started = False
    try:
        with LocationStore() as saved:
            started = await server.serve(
                devices,
                location,
                saved,
                port=port,
                open_browser=not NO_BROWSER,
                debug_reason=debug_reason,
            )
    finally:
        if debug_reason is None:
            print("  Restoring your real GPS...")
        try:
            await location.clear_location()
        except Exception as exc:
            print(f"  ! could not restore GPS: {exc}")
        await location.detach()
        await devices.disconnect()
        print("  Closed.")
    return 0 if started else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
