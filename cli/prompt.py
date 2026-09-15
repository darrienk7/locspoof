"""The interactive prompt. Presentation only - no device logic lives here."""

from __future__ import annotations

import asyncio
import math
import re
import sqlite3

from core.location_service import LocationService
from core.location_store import LocationExistsError, LocationStore
from core.models import Coordinate

BANNER = (
    "  Enter coordinates as 'lat lon'   e.g.  40.690008, -74.045843 OR 40.690008 -74.045843\n"
    "  Or enter an exact saved name  |  'list' shows saved locations\n"
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

    if not math.isfinite(radius) or radius <= 0:
        print("! radius must be positive")
        return
    location.noise.radius_m = radius
    print(f"  noise radius set to +/-{radius:g}m")


async def read_line(label: str) -> str | None:
    try:
        return (await asyncio.to_thread(input, label)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


async def show_saved_locations(saved: LocationStore) -> bool:
    """Return to the previous prompt with 'back'; False means quit the app."""
    while True:
        print("  Saved locations:")
        entries = saved.list_all()
        if not entries:
            print("  (none)")
        for entry in entries:
            coord = entry.coordinate
            print(f"  {entry.name}  |  {coord.latitude}, {coord.longitude}")
        print("  'delete <exact name>' deletes  |  'back' returns  |  'q' quits")
        line = await read_line("  saved> ")
        if line is None or line.lower() in QUIT_WORDS:
            return False
        if line.lower() == "back":
            return True
        if line.lower() == "list":
            continue
        if line.lower().startswith("delete "):
            name = line[len("delete "):].strip()
            if saved.delete(name):
                print(f"  - deleted {name}")
            else:
                print("? not understand")
        else:
            print("? not understand")


def reserved_name(name: str) -> bool:
    """Command words/coordinate literals would be unreachable as saved names."""
    lowered = name.lower()
    return (lowered in (*QUIT_WORDS, "clear", "noise", "list", "discard")
            or lowered.startswith("noise ") or parse_coords(name) is not None)


async def choose_save_name(saved: LocationStore) -> tuple[bool, str | None]:
    """(proceed, name): None names are deliberate unsaved moves."""
    while True:
        name = await read_line("  Save a name, or type 'discard' to go without saving ('list' to view): ")
        if name is None or name.lower() in QUIT_WORDS:
            return False, None
        if name.lower() == "discard":
            return True, None
        if name.lower() == "list":
            if not await show_saved_locations(saved):
                return False, None
            continue
        if not name or reserved_name(name):
            print("! choose a nonempty name that is not a command or coordinate")
            continue
        if saved.get(name) is not None:
            print("! name already saved")
            continue
        return True, name


async def run(location: LocationService, saved: LocationStore) -> None:
    print()
    print(BANNER)
    print()

    while True:
        line = await read_line("  loc> ")
        if line is None:
            return

        if not line:
            continue

        lowered = line.lower()

        if lowered in QUIT_WORDS:
            return

        if lowered == "list":
            if not await show_saved_locations(saved):
                return
            continue

        if lowered == "clear":
            await location.clear_location()
            print("  - cleared, real GPS restored")
            continue

        if lowered == "noise" or lowered.startswith("noise "):
            await handle_noise_command(location, line[len("noise"):])
            continue

        coord = parse_coords(line)
        name_to_save = None
        if coord is None:
            # Check if it was a range error vs format error
            parts = [p for p in re.split(r"[,\s]+", line.strip()) if p]
            if len(parts) == 2:
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                    # Format was OK, so check ranges
                    if not -90 <= lat <= 90:
                        print("! Latitude out of range. Must be in [-90, 90]")
                        continue
                    if not -180 <= lon <= 180:
                        print("! Longitude out of range. Must be in [-180, 180]")
                        continue
                except ValueError:
                    pass
            # Format error or unknown saved location name
            entry = saved.get(line)
            if entry is None:
                print("? not understand. Try 'list' to see saved locations, or enter coordinates as 'lat lon'.")
                continue
            coord = entry.coordinate
        else:
            proceed, name_to_save = await choose_save_name(saved)
            if not proceed:
                return

        try:
            await location.set_location(coord)
        except Exception as exc:
            print(f"! failed to set location: {exc}")
            continue

        # Save the exact requested anchor only after the device write succeeds.
        if name_to_save is not None:
            try:
                saved.save(name_to_save, coord)
                print(f"  + saved {name_to_save}")
            except (sqlite3.Error, LocationExistsError) as exc:
                print(f"! location changed but could not save: {exc}")

        msg = f"* successful spoof to {coord.latitude:.6f}, {coord.longitude:.6f}"
        if location.noise_enabled:
            msg += f"  (drifting +/-{location.noise.radius_m:g}m)"
        print(msg)
