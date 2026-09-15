"""Barebones GPS jitter.

Real receivers never report a perfectly still point: a stationary phone drifts
a few meters as satellite geometry and multipath change. A location that is
pinned to six identical decimal places forever looks nothing like GPS.

This is the simplest thing that captures that: a uniform random offset, in
meters, converted to degrees. No filtering, no correlation between samples, no
velocity. Deliberately so - it is meant to be replaced.

The one rule that matters: jitter is always applied to the ANCHOR (the point
the user asked for), never to the previously jittered point. Feeding output
back in would turn this into a random walk that wanders away without bound.
"""

from __future__ import annotations

import math
import random
from typing import Optional

from .models import Coordinate

# Close enough at any latitude for meter-scale offsets.
METERS_PER_DEG_LAT = 111_320.0

# Near the poles a meter is an enormous number of degrees of longitude.
# Clamp the cosine so the conversion can't blow up or divide by zero.
MIN_COS_LAT = 0.01

DEFAULT_RADIUS_M = 3.0
DEFAULT_INTERVAL_S = 1.0


class GpsNoise:
    """Generates a small random offset around a fixed anchor.

    :param radius_m: maximum offset on each axis, in meters. Consumer GPS
        sits around 3-5 m in the open.
    :param interval_s: seconds between samples.
    :param rng: inject a seeded `random.Random` for deterministic tests.
    """

    def __init__(
        self,
        radius_m: float = DEFAULT_RADIUS_M,
        interval_s: float = DEFAULT_INTERVAL_S,
        rng: Optional[random.Random] = None,
    ) -> None:
        if radius_m <= 0:
            raise ValueError("radius_m must be positive")
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.radius_m = radius_m
        self.interval_s = interval_s
        self._rng = rng or random.Random()
        # (north, east) meters of the most recent sample - for status display.
        self.last_offset_m: tuple[float, float] = (0.0, 0.0)

    def jitter(self, anchor: Coordinate) -> Coordinate:
        """Return a new Coordinate a few meters from `anchor`.

        `anchor` is never modified, and never read back from the result.
        """
        north_m = self._rng.uniform(-self.radius_m, self.radius_m)
        east_m = self._rng.uniform(-self.radius_m, self.radius_m)
        self.last_offset_m = (north_m, east_m)

        d_lat = north_m / METERS_PER_DEG_LAT

        cos_lat = math.cos(math.radians(anchor.latitude))
        if abs(cos_lat) < MIN_COS_LAT:
            cos_lat = math.copysign(MIN_COS_LAT, cos_lat or 1.0)
        d_lon = east_m / (METERS_PER_DEG_LAT * cos_lat)

        lat = _clamp(anchor.latitude + d_lat, -90.0, 90.0)
        lon = _wrap_longitude(anchor.longitude + d_lon)
        return Coordinate(lat, lon)

    @property
    def last_offset_magnitude_m(self) -> float:
        north, east = self.last_offset_m
        return math.hypot(north, east)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _wrap_longitude(lon: float) -> float:
    """Keep longitude in [-180, 180) across the antimeridian."""
    return ((lon + 180.0) % 360.0) - 180.0
