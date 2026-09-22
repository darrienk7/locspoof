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
import time
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
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
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Be told whenever the reported location or noise state changes.

        Fires on every device write - typed coordinates and each noise tick -
        plus clears and noise toggles. A UI subscribes instead of polling.

        Callbacks are synchronous and run on the event loop, inside the noise
        ticker. Do not block in one: schedule the work. Exceptions are logged
        and swallowed so a broken observer cannot stop the drift.
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self) -> None:
        """Called once state is consistent - never mid-update.

        `_write` deliberately does not notify: during `set_location` it runs
        before the anchor is committed, so observers would briefly see a new
        `current` against the old `anchor`.
        """
        for callback in list(self._listeners):
            try:
                callback()
            except Exception as exc:
                self._debug(f"listener failed: {exc}")

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
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

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

    def attach_simulated(self) -> None:
        """DEBUG MODE: send writes to an in-process stand-in instead of a phone."""
        from .simulated import SimulatedLocation

        if self._sim is None:
            self._sim = SimulatedLocation(debug=self._debug)

    async def detach(self) -> None:
        await self._stop_noise()
        stack, self._stack = self._stack, None
        self._sim = None
        self._anchor = None
        self._current = None
        self.noise.reset()
        self._tick_count = 0
        if stack is not None:
            await stack.aclose()

    # ------------------------------------------------------------------
    # Location
    # ------------------------------------------------------------------

    async def set_location(self, coord: Coordinate) -> None:
        """Move to `coord`, then start drifting around it if noise is on."""
        # Finish any in-flight noise write before changing the anchor or state.
        await self._stop_noise()
        try:
            await self._write(coord)
        except Exception:
            if self._noise_enabled and self._anchor is not None:
                self._start_noise()
            raise
        self._anchor = coord
        self.noise.reset()
        self._tick_count = 0
        if self._noise_enabled:
            self._start_noise()
        self._notify()

    async def clear_location(self) -> None:
        await self._stop_noise()
        await self._require().clear()
        self._anchor = None
        self._current = None
        self._tick_count = 0
        self.noise.reset()
        self._notify()

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
            self.noise.reset()
            self._tick_count = 0
        self._notify()

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
        connection; the web app shows drift as stopped.
        """
        # perf_counter is monotonic with sufficient resolution for short Windows
        # intervals; some Python event-loop clocks advance only every ~15 ms.
        previous_tick = time.perf_counter()
        while True:
            await asyncio.sleep(self.noise.interval_s)
            anchor = self._anchor
            if anchor is None:
                return
            try:
                now = time.perf_counter()
                dt = now - previous_tick
                previous_tick = now
                await self._write(self.noise.jitter(anchor, dt_s=dt))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._debug(f"noise write failed, stopping drift: {exc}")
                self._notify()
                return
            self._tick_count += 1
            self._notify()
