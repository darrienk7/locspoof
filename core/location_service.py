"""The one interface for changing where the device thinks it is.

`_write` is deliberately the single path to the device. Both the user typing a
coordinate and the noise ticker go through it, so route playback can later do
the same without special-casing.

Two pieces of state, and the distinction matters:

    anchor   the coordinate the user asked for. Fixed until they change it.
    current  what was last actually written = anchor + this tick's offset.

Noise is always computed from the anchor. Jittering the previous output would
be a random walk, and the device would drift away over time.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Callable, Optional

from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

from .device_manager import DeviceManager
from .models import Coordinate
from .noise import GpsNoise


def _noop(_: str) -> None:
    pass


class LocationService:
    def __init__(
        self,
        devices: DeviceManager,
        noise: Optional[GpsNoise] = None,
        noise_enabled: bool = True,
        debug: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._devices = devices
        self._debug = debug or _noop
        self._stack: Optional[AsyncExitStack] = None
        self._sim: Optional[LocationSimulation] = None

        self._anchor: Optional[Coordinate] = None
        self._current: Optional[Coordinate] = None

        self.noise = noise or GpsNoise()
        self._noise_enabled = noise_enabled
        self._noise_task: Optional[asyncio.Task] = None
        self._tick_count = 0

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def anchor(self) -> Optional[Coordinate]:
        """The coordinate the user asked for, before noise."""
        return self._anchor

    @property
    def current(self) -> Optional[Coordinate]:
        """The coordinate last actually written to the device."""
        return self._current

    @property
    def is_attached(self) -> bool:
        return self._sim is not None

    @property
    def noise_enabled(self) -> bool:
        return self._noise_enabled

    @property
    def noise_active(self) -> bool:
        """True when the ticker is actually running right now."""
        return self._noise_task is not None and not self._noise_task.done()

    @property
    def tick_count(self) -> int:
        return self._tick_count

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def attach(self) -> None:
        """Open the LocationSimulation channel on the live DVT connection."""
        if self._sim is not None:
            return
        stack = AsyncExitStack()
        try:
            sim = await stack.enter_async_context(LocationSimulation(self._devices.dvt))
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._sim = sim

    async def detach(self) -> None:
        await self._stop_noise()
        stack, self._stack = self._stack, None
        self._sim = None
        self._anchor = None
        self._current = None
        if stack is not None:
            await stack.aclose()

    # ------------------------------------------------------------------
    # Location
    # ------------------------------------------------------------------

    async def set_location(self, coord: Coordinate) -> None:
        """Move to `coord`, then start drifting around it if noise is on."""
        self._anchor = coord
        await self._write(coord)
        self._tick_count = 0
        if self._noise_enabled:
            self._start_noise()

    async def clear_location(self) -> None:
        await self._stop_noise()
        self._anchor = None
        await self._require().clear()
        self._current = None
        self._tick_count = 0

    async def set_noise_enabled(self, enabled: bool) -> None:
        """Turn drift on or off mid-session.

        Turning it off rewrites the exact anchor, so the device lands back on
        the coordinate the user actually typed.
        """
        self._noise_enabled = enabled
        if enabled:
            if self._anchor is not None:
                self._start_noise()
        else:
            await self._stop_noise()
            if self._anchor is not None:
                await self._write(self._anchor)

    # ------------------------------------------------------------------
    # Routes - not implemented yet, see the phase-1 plan
    # ------------------------------------------------------------------

    async def play_route(self, route) -> None:
        raise NotImplementedError("route playback is not implemented yet")

    async def stop_route(self) -> None:
        raise NotImplementedError("route playback is not implemented yet")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require(self) -> LocationSimulation:
        if self._sim is None:
            raise RuntimeError("location channel not open - call attach() first")
        return self._sim

    async def _write(self, coord: Coordinate) -> None:
        """The only place a coordinate reaches the device."""
        await self._require().set(coord.latitude, coord.longitude)
        self._current = coord

    def _start_noise(self) -> None:
        if self.noise_active:
            return
        self._noise_task = asyncio.create_task(self._noise_loop())
        self._debug(
            f"noise on: +/-{self.noise.radius_m}m every {self.noise.interval_s}s"
        )

    async def _stop_noise(self) -> None:
        task, self._noise_task = self._noise_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._debug(f"noise task ended with: {exc}")

    async def _noise_loop(self) -> None:
        """Rewrite the location every interval, offset from the anchor.

        Stops on the first write failure rather than spinning on a dead
        connection; the CLI surfaces that through `noise` status.
        """
        while True:
            await asyncio.sleep(self.noise.interval_s)
            anchor = self._anchor
            if anchor is None:
                return
            try:
                await self._write(self.noise.jitter(anchor))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._debug(f"noise write failed, stopping drift: {exc}")
                return
            self._tick_count += 1
