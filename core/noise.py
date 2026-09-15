"""Stateful synthetic GPS error around a fixed anchor, using only the stdlib.

Each north/east axis combines first-order Gauss-Markov bias, independent white
Gaussian noise, and a reflected random walk. A pure random walk is unbounded;
reflection keeps that component inside a symmetric band around zero. Finally
the combined error is clipped to the existing +/-radius_m per-axis limit.
The Gaussian components are Gaussian *before* that final output limit.

Every output is anchor + error, never previous_coordinate + error. This is a
noise generator, not a filter that estimates a true position from GPS readings.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional

from .models import Coordinate

# Close enough at any latitude for meter-scale offsets.
METERS_PER_DEG_LAT = 111_320.0

# Near the poles a meter is an enormous number of degrees of longitude.
# Clamp the cosine so the conversion can't blow up or divide by zero.
MIN_COS_LAT = 0.01

DEFAULT_RADIUS_M = 3.0
DEFAULT_INTERVAL_S = 1.0


@dataclass(frozen=True)
class NoiseParameters:
    """Scales relative to radius_m; time is measured in seconds.

    The GM scale is its stationary standard deviation; white noise has that
    standard deviation per sample. Walk steps scale with sqrt(elapsed seconds).
    These defaults are tuning choices, not a calibrated iPhone receiver model.
    """

    correlation_time_s: float = 30.0
    gauss_markov_fraction: float = 0.35
    white_fraction: float = 0.15
    walk_step_fraction: float = 0.05
    walk_limit_fraction: float = 0.35

    def __post_init__(self) -> None:
        _positive(self.correlation_time_s, "correlation_time_s")
        for name in ("gauss_markov_fraction", "white_fraction",
                     "walk_step_fraction", "walk_limit_fraction"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.walk_limit_fraction > 1:
            raise ValueError("walk_limit_fraction cannot exceed 1")


@dataclass(frozen=True)
class NoiseComponents:
    """Latest component offsets in (north, east) meters, before output clipping."""

    gauss_markov_m: tuple[float, float] = (0.0, 0.0)
    white_m: tuple[float, float] = (0.0, 0.0)
    random_walk_m: tuple[float, float] = (0.0, 0.0)

    @property
    def total_m(self) -> tuple[float, float]:
        return tuple(sum(axis) for axis in zip(
            self.gauss_markov_m, self.white_m, self.random_walk_m
        ))


class GpsNoise:
    """Generates temporally correlated error around a fixed anchor.

    :param radius_m: maximum offset on each axis, in meters. Consumer GPS
        output is bounded independently on the north and east axes.
    :param interval_s: seconds between samples.
    :param rng: inject a seeded `random.Random` for deterministic tests.
    """

    def __init__(
        self,
        radius_m: float = DEFAULT_RADIUS_M,
        interval_s: float = DEFAULT_INTERVAL_S,
        rng: Optional[random.Random] = None,
        *,
        parameters: Optional[NoiseParameters] = None,
    ) -> None:
        self.parameters = parameters or NoiseParameters()
        self._rng = rng or random.Random()
        self.radius_m = radius_m
        self.interval_s = interval_s
        self.reset()

    @property
    def radius_m(self) -> float:
        return self._radius_m

    @radius_m.setter
    def radius_m(self, value: float) -> None:
        _positive(value, "radius_m")
        self._radius_m = value

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @interval_s.setter
    def interval_s(self, value: float) -> None:
        _positive(value, "interval_s")
        self._interval_s = value

    def reset(self) -> None:
        """Start a new stationary-target session from zero bias, without reseeding."""
        self._anchor: Optional[Coordinate] = None
        # Dimensionless states let a live radius change scale the complete model.
        self._gauss_markov = [0.0, 0.0]
        self._walk = [0.0, 0.0]
        self.last_components = NoiseComponents()
        self.last_offset_m = (0.0, 0.0)

    def jitter(self, anchor: Coordinate, dt_s: Optional[float] = None) -> Coordinate:
        """Return a new Coordinate a few meters from `anchor`.

        `anchor` is never modified, and never read back from the result.
        """
        dt = self.interval_s if dt_s is None else dt_s
        _positive(dt, "dt_s")
        if anchor != self._anchor:
            self.reset()
            self._anchor = anchor

        params = self.parameters
        # Exact discrete transition of a first-order Gauss-Markov process.
        phi = math.exp(-dt / params.correlation_time_s)
        innovation = params.gauss_markov_fraction * math.sqrt(
            -math.expm1(-2.0 * dt / params.correlation_time_s)
        )
        walk_step = params.walk_step_fraction * math.sqrt(dt)
        white = []
        for axis in range(2):
            self._gauss_markov[axis] = (
                phi * self._gauss_markov[axis] + self._rng.gauss(0.0, innovation)
            )
            white.append(self._rng.gauss(0.0, params.white_fraction))
            self._walk[axis] = _reflect(
                self._walk[axis] + self._rng.gauss(0.0, walk_step),
                params.walk_limit_fraction,
            )
        self.last_components = NoiseComponents(
            tuple(value * self.radius_m for value in self._gauss_markov),
            tuple(value * self.radius_m for value in white),
            tuple(value * self.radius_m for value in self._walk),
        )
        north_m, east_m = (
            _clamp(value, -self.radius_m, self.radius_m)
            for value in self.last_components.total_m
        )
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


def _positive(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _reflect(value: float, limit: float) -> float:
    """Reflect at +/-limit, including steps that cross several boundaries."""
    if limit == 0:
        return 0.0
    position = (value + limit) % (4.0 * limit)
    return limit - abs(position - 2.0 * limit)


def _wrap_longitude(lon: float) -> float:
    """Keep longitude in [-180, 180) across the antimeridian."""
    return ((lon + 180.0) % 360.0) - 180.0
