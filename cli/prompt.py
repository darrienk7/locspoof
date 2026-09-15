"""The interactive prompt. Presentation only - no device logic lives here."""

from __future__ import annotations

import asyncio
import re

from core.location_service import LocationService
from core.models import Coordinate

BANNER = (
    "  Enter coordinates as 'lat lon'   e.g.  40.690008, -74.045843 OR 40.690008 -74.045843\n"
    "  'clear' restores real GPS  |  'noise' shows drift status  |  'q' quits"
)

QUIT_WORDS = ("q", "quit", "exit")


def parse_coords(text: str) -> Coordinate | None:
    """Accept '40.69, -74.04' or '40.69 -74.04'. Returns None if unparseable."""
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    try:
        return Coordinate(lat, lon)
    except ValueError:
        print("! out of range LAT: [-90, 90] LON: [-180, 180]")
        return None


def describe_noise(location: LocationService) -> str:
    """One line of drift status, for the `noise` command."""
    noise = location.noise
    if not location.noise_enabled:
        return "  noise: OFF"

    state = "drifting" if location.noise_active else "armed (set a location to start)"
    line = (
        f"  noise: ON - {state}, +/-{noise.radius_m:g}m "
        f"every {noise.interval_s:g}s, {location.tick_count} ticks"
    )
    if location.tick_count:
        north, east = noise.last_offset_m
        current = location.current
        line += (
            f"\n  last offset: {north:+.2f}m N, {east:+.2f}m E "
            f"({noise.last_offset_magnitude_m:.2f}m)"
        )
        if current is not None:
            line += f"\n  now at: {current.latitude:.6f}, {current.longitude:.6f}"
    return line


async def handle_noise_command(location: LocationService, args: str) -> None:
    """`noise` | `noise on` | `noise off` | `noise <meters>`"""
    args = args.strip().lower()

    if not args:
        print(describe_noise(location))
        return

    if args in ("on", "off"):
        await location.set_noise_enabled(args == "on")
        print(f"  noise {args}")
        if args == "off" and location.anchor is not None:
            anchor = location.anchor
            print(f"  back to exact {anchor.latitude:.6f}, {anchor.longitude:.6f}")
        return

    try:
        radius = float(args)
    except ValueError:
        print("? usage: noise | noise on | noise off | noise <meters>")
        return

    if radius <= 0:
        print("! radius must be positive")
        return
    location.noise.radius_m = radius
    print(f"  noise radius set to +/-{radius:g}m")


async def run(location: LocationService) -> None:
    print()
    print(BANNER)
    print()

    while True:
        try:
            line = (await asyncio.to_thread(input, "  loc> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not line:
            continue

        lowered = line.lower()

        if lowered in QUIT_WORDS:
            return

        if lowered == "clear":
            await location.clear_location()
            print("  - cleared, real GPS restored")
            continue

        if lowered == "noise" or lowered.startswith("noise "):
            await handle_noise_command(location, line[len("noise"):])
            continue

        coord = parse_coords(line)
        if coord is None:
            print("? Not understand - try:  40.690008, -74.045843")
            continue

        try:
            await location.set_location(coord)
        except Exception as exc:
            print(f"! failed to set location: {exc}")
            continue

        msg = f"* successful spoof to {coord.latitude:.6f}, {coord.longitude:.6f}"
        if location.noise_enabled:
            msg += f"  (drifting +/-{location.noise.radius_m:g}m)"
        print(msg)
