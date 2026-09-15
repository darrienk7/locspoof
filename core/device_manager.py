"""Owns the connection to one iPhone.

The key change from the original single-file version: the RSD and DVT context
managers used to be held open by `async with` on the call stack, with the
prompt loop nested inside. Here they live in an AsyncExitStack held as
instance state, so the session's lifetime belongs to this object rather than
to a stack frame. That is what lets a GUI (whose lifetime is "until the window
closes") drive the same code.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Callable, Optional

from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider

from .models import Device, TunnelInfo
from .tunnel_manager import TunnelManager


def _noop(_: str) -> None:
    pass


class DeviceManager:
    """Brings up a tunnel, then an RSD + DVT session on top of it.

    Connecting is two stages on purpose. Callers want to report "tunnel up"
    before the (slower) device handshake starts — the CLI prints between them
    today, and a GUI will want the same progress split.
    """

    def __init__(
        self,
        tunnel: TunnelManager,
        debug: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._tunnel = tunnel
        self._debug = debug or _noop
        self._stack: Optional[AsyncExitStack] = None
        self._dvt: Optional[DvtProvider] = None
        self._device: Optional[Device] = None

    @property
    def device(self) -> Optional[Device]:
        return self._device

    @property
    def tunnel_info(self) -> Optional[TunnelInfo]:
        return self._tunnel.info

    @property
    def is_connected(self) -> bool:
        return self._stack is not None

    @property
    def dvt(self) -> DvtProvider:
        """The live DVT provider.

        core-internal: services in this package build channels on it. It must
        never be handed to the UI — `Device` is what crosses that boundary.
        """
        if self._dvt is None:
            raise RuntimeError("not connected - call connect() first")
        return self._dvt

    async def open_tunnel(self) -> TunnelInfo:
        """Stage 1: start the tunnel and report where RSD is listening."""
        return await self._tunnel.start()

    async def connect(self) -> Device:
        """Stage 2: RemoteXPC handshake, then open the DVT connection."""
        info = self._tunnel.info
        if info is None:
            raise RuntimeError("no tunnel - call open_tunnel() first")

        stack = AsyncExitStack()
        try:
            rsd = await stack.enter_async_context(
                RemoteServiceDiscoveryService((info.address, info.port))
            )
            dvt = await stack.enter_async_context(DvtProvider(rsd))
        except BaseException:
            await stack.aclose()
            raise

        self._stack = stack
        self._dvt = dvt
        self._device = Device(
            udid=rsd.udid,
            product_type=rsd.product_type,
            ios_version=rsd.product_version,
            connected=True,
        )
        self._debug(f"connected to {self._device.udid} ({self._device.ios_version})")
        return self._device

    async def disconnect(self) -> None:
        """Close the DVT connection, then RSD, then the tunnel.

        Unwinding the stack closes DVT before RSD (LIFO), matching the nested
        `async with` ordering of the original.
        """
        stack, self._stack = self._stack, None
        self._dvt = None
        self._device = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception as exc:
                self._debug(f"error closing device session: {exc}")
        await self._tunnel.stop()
