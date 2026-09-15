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
    "  Add 'as <name>' to bookmark them  |  'save <name>' bookmarks where you are now\n"
    "  Or enter an exact saved name  |  'list' shows saved locations\n"
    "  'clear' restores real GPS  |  'noise' shows drift status  |  'q' quits"
)

QUIT_WORDS = ("q", "quit", "exit")

# Commands are matched before saved names, so a bookmark with one of these
# names would be unreachable. `reserved_name` refuses them at save time.
COMMAND_WORDS = (*QUIT_WORDS, "clear", "noise", "list", "save", "delete", "back")
PREFIX_COMMANDS = ("noise", "save", "delete")

# `40.69, -74.04 as home` - the separator between a coordinate and its bookmark.
AS_SEPARATOR = re.compile(r"\s+as\s+", re.IGNORECASE)


def parse_coordinate(text: str) -> tuple[Coordinate | None, str | None]:
    """Parse 'lat lon' or 'lat, lon'.

    Returns (coordinate, error). Both None means the text is not
    coordinate-shaped at all, so it may still be a saved name. A non-None
    error means it clearly *was* meant as coordinates but is unusable.
    """
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 2:
        return None, None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None, None

    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None, "! coordinates must be finite numbers"
    if not -90 <= lat <= 90:
        return None, "! Latitude out of range. Must be in [-90, 90]"
    if not -180 <= lon <= 180:
        return None, "! Longitude out of range. Must be in [-180, 180]"
    return Coordinate(lat, lon), None


def parse_coords(text: str) -> Coordinate | None:
    """Convenience wrapper for callers that only want the coordinate."""
    return parse_coordinate(text)[0]


def split_as(line: str) -> tuple[str, str | None]:
    """Split '<target> as <name>'. Name is None when there is no separator."""
    parts = AS_SEPARATOR.split(line, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return line, None


def reserved_name(name: str) -> bool:
    """Names that could never be typed back, because something else wins."""
    lowered = name.lower()
    if lowered in COMMAND_WORDS:
        return True
    if any(lowered.startswith(word + " ") for word in PREFIX_COMMANDS):
        return True
    if AS_SEPARATOR.search(name):
        return True
    coord, error = parse_coordinate(name)
    return coord is not None or error is not None


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


def save_bookmark(saved: LocationStore, name: str, coord: Coordinate) -> bool:
    """Validate and store one bookmark, reporting the outcome."""
    if not name:
        print("! a bookmark needs a name")
        return False
    if reserved_name(name):
        print(f"! {name!r} is a command or coordinate - pick another name")
        return False
    try:
        saved.save(name, coord)
    except LocationExistsError:
        print(f"! {name!r} is already saved - delete it first or pick another name")
        return False
    except sqlite3.Error as exc:
        print(f"! could not save: {exc}")
        return False
    print(f"  + saved {name}")
    return True


def handle_save_command(location: LocationService, saved: LocationStore, args: str) -> None:
    """`save <name>` bookmarks the current anchor - the exact coordinate typed."""
    name = args.strip()
    if not name:
        print("? usage: save <name>   (bookmarks where you are now)")
        return
    anchor = location.anchor
    if anchor is None:
        print("! nothing to save - set a location first")
        return
    save_bookmark(saved, name, anchor)


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
        lowered = line.lower()
        if lowered == "back":
            return True
        if lowered == "list":
            continue
        if lowered == "delete":
            print("? usage: delete <exact name>")
            continue
        if lowered.startswith("delete "):
            name = line[len("delete "):].strip()
            if saved.delete(name):
                print(f"  - deleted {name}")
            else:
                print(f"? no saved location named {name!r}")
            continue
        print("? not understand")


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

        if lowered == "save" or lowered.startswith("save "):
            handle_save_command(location, saved, line[len("save"):])
            continue

        target, inline_name = split_as(line)
        coord, error = parse_coordinate(target)

        if error is not None:
            print(error)
            continue

        if coord is None:
            # Not coordinate-shaped, so look the whole line up as a bookmark.
            if inline_name is not None:
                print("! 'as <name>' only works with coordinates")
                continue
            entry = saved.get(line)
            if entry is None:
                print(
                    "? not understand. Try 'list' to see saved locations, "
                    "or enter coordinates as 'lat lon'."
                )
                continue
            coord = entry.coordinate

        try:
            await location.set_location(coord)
        except Exception as exc:
            print(f"! failed to set location: {exc}")
            continue

        # Bookmark the exact requested anchor, only after the write succeeds.
        if inline_name is not None:
            save_bookmark(saved, inline_name, coord)

        msg = f"* successful spoof to {coord.latitude:.6f}, {coord.longitude:.6f}"
        if location.noise_enabled:
            msg += f"  (drifting +/-{location.noise.radius_m:g}m)"
        print(msg)
