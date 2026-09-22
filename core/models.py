"""Plain data the UI is allowed to see.

Nothing in here imports pymobiledevice3. These types are the boundary: they
cross from core/ to the web app, while raw pymobiledevice3
objects stay inside core/.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Coordinate:
    """A WGS-84 point. Construction validates range, so every consumer of a
    Coordinate can assume it is in-bounds."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.latitude <= 90.0):
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not (-180.0 <= self.longitude <= 180.0):
            raise ValueError(f"longitude out of range: {self.longitude}")


@dataclass(frozen=True)
class SavedLocation:
    """A case-sensitive bookmark for an exact requested coordinate (no noise)."""

    name: str
    coordinate: Coordinate


@dataclass(frozen=True)
class TunnelInfo:
    """Where the RSD endpoint lives, once a tunnel is up."""

    address: str
    port: int


@dataclass(frozen=True)
class Device:
    """The connected iPhone, as much of it as we can honestly read today.

    `name` and `developer_mode` are deliberately absent: a friendly name needs
    a lockdown DeviceName read we don't currently perform, and developer_mode
    would be hardcoded True (if the tunnel opened, Developer Mode is on). Both
    arrive with real device discovery.
    """

    udid: str
    product_type: str
    ios_version: str
