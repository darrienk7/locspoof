"""Stand-in for the phone's location channel, used in DEBUG MODE.

It has the same two methods LocationService calls on the real channel, so
everything above it - drift, bookmarks, the web app - runs exactly as it would
with an iPhone attached. Nothing leaves this process.
"""

from __future__ import annotations

from typing import Callable, Optional


def _noop(_: str) -> None:
    pass


class SimulatedLocation:
    def __init__(self, debug: Optional[Callable[[str], None]] = None) -> None:
        self._debug = debug or _noop

    async def set(self, latitude: float, longitude: float) -> None:
        self._debug(f"[simulated] set {latitude:.6f}, {longitude:.6f}")

    async def clear(self) -> None:
        self._debug("[simulated] clear")
