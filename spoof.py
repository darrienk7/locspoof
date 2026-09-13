#!/usr/bin/env python3


import asyncio
import ctypes
import os
import re
import sys

from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

# How long to wait for `start-tunnel` to print its address/port line.
TUNNEL_TIMEOUT = 60.0

# --script-mode prints exactly one line: "<address> <port>"
RSD_LINE = re.compile(r"^(\S+)\s+(\d+)\s*$")

DEBUG = "--debug" in sys.argv


def log(msg: str) -> None:
    if DEBUG:
        print(f"[debug] {msg}", file=sys.stderr)


def is_admin() -> bool:
    """True if we have the privileges start-tunnel needs."""
    if os.name == "nt":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


async def open_tunnel() -> tuple[asyncio.subprocess.Process, str, int]:
    """
    Spawn `lockdown start-tunnel --script-mode` and read the RSD address/port
    it prints. MUST KEEP IT ALIVE OR THE TUNNEL WILL CLOSE EVEN AFTER
    EXTRACTING THE ADDRESS AND PORT NUM
    """
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    argv = [
        sys.executable, "-m", "pymobiledevice3",
        "lockdown", "start-tunnel", "--script-mode",
    ]
    log(f"spawning: {' '.join(argv)}")

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    async def read_rsd_line() -> tuple[str, int]:
        # Skip anything that isn't the "<address> <port>" line.
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                raise RuntimeError("start-tunnel exited without printing an RSD address")
            line = raw.decode(errors="replace").strip()
            log(f"tunnel stdout: {line!r}")
            match = RSD_LINE.match(line)
            if match:
                return match.group(1), int(match.group(2))

    try:
        address, port = await asyncio.wait_for(read_rsd_line(), timeout=TUNNEL_TIMEOUT)
    except BaseException:
        stderr = b""
        if proc.returncode is None:
            proc.terminate()
        try:
            stderr = (await asyncio.wait_for(proc.stderr.read(), timeout=5.0)) or b""
        except (asyncio.TimeoutError, Exception):
            pass
        text = stderr.decode(errors="replace").strip()
        if text:
            print("\n--- start-tunnel output ---", file=sys.stderr)
            print(text, file=sys.stderr)
            print("---------------------------\n", file=sys.stderr)
        raise

    return proc, address, port


def parse_coords(text: str) -> tuple[float, float] | None:
    """Accept the coords. Returns None if unparseable."""
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        print("! Coordinates out of range. Must be in LAT: [-90, 90], LON: [-180,180]")
        return None
    return lat, lon


async def prompt_loop(loc: LocationSimulation) -> None:
    print()
    print("  Enter coordinates as 'lat, lon'   e.g.  40.690008, -74.045843")
    print("  'clear' restores real GPS  |  'q' OR 'exit' OR 'quit' quits")
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
        if lowered in ("q", "quit", "exit"):
            return
        if lowered == "clear":
            await loc.clear()
            print("  - cleared, real GPS restored")
            continue

        coords = parse_coords(line)
        if coords is None:
            print("! Not correct format. try:  40.690008, -74.045843 OR 40.690008 -74.045843\n")
            continue

        lat, lon = coords
        try:
            await loc.set(lat, lon)
        except Exception as exc:
            print(f"  ! failed to set location: {exc}")
            continue
        print(f"+ Successfully changed location to {lat:.6f}, {lon:.6f}\n")


async def main() -> int:
    if not is_admin():
        print("This needs an elevated shell - 'lockdown start-tunnel' creates a")
        print("network interface and is marked @sudo_required.")
        print()
        if os.name == "nt":
            print("Run run.bat, or reopen your terminal as Administrator.")
        else:
            print("Re-run with sudo.")
        return 1

    print("Opening tunnel (this takes a few seconds)...")
    try:
        tunnel, address, port = await open_tunnel()
    except Exception as exc:
        print(f"Could not open the tunnel: {exc}")
        print("Is the iPhone plugged in, unlocked, trusted, and in Developer Mode?")
        return 1

    print(f"Tunnel up  ->  RSD {address} port {port}")

    rsd = RemoteServiceDiscoveryService((address, port))
    exit_code = 0
    try:
        await rsd.connect()
        log(f"connected to {rsd.udid} ({rsd.product_version})")
        print(f"Connected to {rsd.product_type} (iOS {rsd.product_version})")

        async with DvtProvider(rsd) as dvt, LocationSimulation(dvt) as loc:
            try:
                await prompt_loop(loc)
            finally:
                print("Restoring real GPS...")
                try:
                    await loc.clear()
                except Exception as exc:
                    print(f"  ! clear failed: {exc}")
    except Exception as exc:
        print(f"Error: {exc}")
        if DEBUG:
            import traceback
            traceback.print_exc()
        exit_code = 1
    finally:
        try:
            await rsd.close()
        except Exception:
            pass
        if tunnel.returncode is None:
            log("terminating tunnel")
            tunnel.terminate()
            try:
                await asyncio.wait_for(tunnel.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                tunnel.kill()
        print("Tunnel closed.")

    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
