#!/usr/bin/env python3
"""locspoof - entry point. Parses flags, wires the services, owns exit codes."""

from __future__ import annotations

import asyncio
import sys

from cli import prompt
from core.device_manager import DeviceManager
from core.location_service import LocationService
from core.noise import DEFAULT_INTERVAL_S, DEFAULT_RADIUS_M, GpsNoise
from core.tunnel_manager import (
    DEFAULT_TIMEOUT,
    IS_MAC,
    IS_WINDOWS,
    PrivilegeError,
    TunnelError,
    TunnelManager,
    is_admin,
)

DEBUG = "--debug" in sys.argv
FORCE_SUDO_TUNNEL = "--sudo-tunnel" in sys.argv
NO_NOISE = "--no-noise" in sys.argv

# macOS rides Apple's existing tunnel (no root). Everywhere else we build one.
USE_NATIVE_TUNNEL = IS_MAC and not FORCE_SUDO_TUNNEL


def log(msg: str) -> None:
    if DEBUG:
        print(f"[debug] {msg}", file=sys.stderr)


def flag_value(name: str, default: float) -> float:
    """Read `--name X`, falling back to `default`."""
    try:
        index = sys.argv.index(name)
    except ValueError:
        return default
    if index + 1 >= len(sys.argv):
        print(f"! {name} needs a value, using {default}")
        return default
    try:
        return float(sys.argv[index + 1])
    except ValueError:
        print(f"! {name} needs a number, using {default}")
        return default


def report_tunnel_failure(exc: Exception) -> None:
    details = getattr(exc, "details", "")
    if details:
        print("\n--- start-tunnel output ---", file=sys.stderr)
        print(details, file=sys.stderr)
        print("---------------------------\n", file=sys.stderr)
    print(f"Could not open the tunnel: {exc}")
    print("Is the iPhone plugged in, unlocked, trusted, and in Developer Mode?")
    if USE_NATIVE_TUNNEL:
        print("If the native tunnel keeps failing, try:")
        print("  sudo .venv/bin/python main.py --sudo-tunnel")


async def main() -> int:
    if not USE_NATIVE_TUNNEL and not is_admin():
        print("The classic tunnel creates a network interface, so it needs elevation.")
        print()
        if IS_WINDOWS:
            print("Run run.bat, or reopen your terminal as Administrator.")
        else:
            print("Re-run with sudo, e.g.:  sudo .venv/bin/python main.py")
        return 1

    noise = GpsNoise(
        radius_m=flag_value("--noise", DEFAULT_RADIUS_M),
        interval_s=flag_value("--noise-interval", DEFAULT_INTERVAL_S),
    )

    tunnel = TunnelManager(use_native=USE_NATIVE_TUNNEL, timeout=DEFAULT_TIMEOUT, debug=log)
    devices = DeviceManager(tunnel, debug=log)
    location = LocationService(
        devices, noise=noise, noise_enabled=not NO_NOISE, debug=log
    )

    kind = "native (no root)" if USE_NATIVE_TUNNEL else "classic"
    print(f"Opening {kind} tunnel (this takes a few seconds)...")
    try:
        info = await devices.open_tunnel()
    except (TunnelError, PrivilegeError) as exc:
        report_tunnel_failure(exc)
        return 1
    except Exception as exc:
        report_tunnel_failure(exc)
        return 1

    print(f"Tunnel up  ->  RSD {info.address} port {info.port}")

    exit_code = 0
    try:
        device = await devices.connect()
        print(f"Connected to {device.product_type} (iOS {device.ios_version})")

        await location.attach()
        try:
            await prompt.run(location)
        finally:
            print("Restoring real GPS...")
            try:
                await location.clear_location()
            except Exception as exc:
                print(f"  ! clear failed: {exc}")
            await location.detach()
    except Exception as exc:
        print(f"Error: {exc}")
        if DEBUG:
            import traceback
            traceback.print_exc()
        exit_code = 1
    finally:
        await devices.disconnect()
        print("Tunnel closed.")

    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
