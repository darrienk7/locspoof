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

# The DVT service location simulation runs on. iOS only offers it once Apple's
# Developer Disk Image is mounted - and unmounts that image on every reboot.
DVT_SERVICE = DvtProvider.RSD_SERVICE_NAME


def _noop(_: str) -> None:
    pass


class DeviceManager:
    """Brings up a tunnel, then an RSD + DVT session on top of it.

    Connecting is two stages on purpose. Callers want to report "tunnel up"
    before the (slower) device handshake starts.
    """

    def __init__(
        self,
        tunnel: TunnelManager,
        debug: Optional[Callable[[str], None]] = None,
        status: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._tunnel = tunnel
        self._debug = debug or _noop
        # User-facing progress, for the slow first-time step below.
        self._status = status or _noop
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

        address = (info.address, info.port)
        stack = AsyncExitStack()
        try:
            rsd = await stack.enter_async_context(RemoteServiceDiscoveryService(address))
            if not self._offers(rsd, DVT_SERVICE):
                await self._mount_developer_image(rsd)
                # The service list is a snapshot taken at the handshake, so the
                # newly available service only shows up on a fresh connection.
                await stack.aclose()
                stack = AsyncExitStack()
                rsd = await stack.enter_async_context(RemoteServiceDiscoveryService(address))
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
        )
        self._debug(f"connected to {self._device.udid} ({self._device.ios_version})")
        return self._device

    @staticmethod
    def _offers(rsd: RemoteServiceDiscoveryService, service: str) -> bool:
        from pymobiledevice3.exceptions import InvalidServiceError

        try:
            rsd.get_service_port(service)
        except InvalidServiceError:
            return False
        return True

    async def _mount_developer_image(self, rsd: RemoteServiceDiscoveryService) -> None:
        """Mount Apple's Developer Disk Image so developer services appear.

        The first time on a computer this downloads the image (cached after,
        under ~/.pymobiledevice3) and has Apple sign it for this phone, so it
        needs an internet connection and can take a minute.
        """
        from pymobiledevice3.exceptions import AlreadyMountedError
        from pymobiledevice3.services.mobile_image_mounter import auto_mount

        self._status("Preparing the iPhone for location control (first time can take a minute)...")
        try:
            await auto_mount(rsd)
        except AlreadyMountedError:
            self._debug("developer disk image already mounted")

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
