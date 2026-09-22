"""The web app: a FastAPI server run inside the existing event loop.

uvicorn is an asyncio server, so it runs in the same loop that owns the tunnel
subprocess and the drift ticker. A route handler simply awaits
`location.set_location(coord)` - no worker threads, no loop bridging.

Commands arrive over HTTP. State goes out over a WebSocket, pushed whenever
LocationService reports a change, so every open tab shows the same thing.
"""

from __future__ import annotations

import asyncio
import socket
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.location_service import LocationService
from core.location_store import LocationExistsError, LocationStore
from core.models import Coordinate

if TYPE_CHECKING:
    from core.device_manager import DeviceManager

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# With no page open for this long, treat it as Quit: restore GPS and exit.
# Long enough that a reload, or closing and reopening the tab, keeps the session.
IDLE_SHUTDOWN_S = 30.0

# How long to wait for uvicorn to start listening before opening the browser.
STARTUP_TIMEOUT_S = 10.0


# Request bodies. Pydantic enforces the same ranges Coordinate does, so bad
# input is rejected with a readable message before it reaches the services.

class SetLocationRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    name: Optional[str] = Field(default=None, description="also save it as a bookmark")


class NameRequest(BaseModel):
    name: str = Field(min_length=1)


class NoiseRequest(BaseModel):
    enabled: Optional[bool] = None
    radius_m: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    interval_s: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)


class GuiServer:
    """Holds the services and the set of connected browsers."""

    def __init__(
        self,
        devices: DeviceManager,
        location: LocationService,
        saved: LocationStore,
        debug_reason: Optional[tuple[str, str]] = None,
    ) -> None:
        self.devices = devices
        self.location = location
        self.saved = saved
        # (headline, detail) when running without a real phone, else None.
        self.debug_reason = debug_reason

        self.sockets: set[WebSocket] = set()
        # Set by the listener, drained by one broadcaster task. Coalescing here
        # means a burst of state changes sends one frame, not one per change.
        self.dirty = asyncio.Event()
        self.shutdown = asyncio.Event()

        # Closing the page without pressing Quit must not leave the phone
        # spoofed forever. Once a page has connected, losing the last one
        # starts a countdown; any page reconnecting cancels it.
        self.idle_timeout_s = IDLE_SHUTDOWN_S
        self._ever_connected = False
        self._idle_task: Optional[asyncio.Task] = None

    def client_joined(self, ws: WebSocket) -> None:
        self.sockets.add(ws)
        self._ever_connected = True
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None

    def client_left(self, ws: WebSocket) -> None:
        self.sockets.discard(ws)
        if self.sockets or not self._ever_connected or self.shutdown.is_set():
            return
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_countdown())

    async def _idle_countdown(self) -> None:
        await asyncio.sleep(self.idle_timeout_s)
        print(f"  No page open for {self.idle_timeout_s:g}s - shutting down.")
        self.shutdown.set()

    async def say_goodbye(self) -> None:
        """Tell open pages the app is closing, so they stop trying to reconnect."""
        if self._idle_task is not None:
            self._idle_task.cancel()
        for ws in list(self.sockets):
            try:
                await ws.send_json({"closing": True})
            except Exception:
                pass

    def snapshot(self) -> dict[str, Any]:
        location = self.location
        noise = location.noise
        device = self.devices.device
        tunnel = self.devices.tunnel_info

        def point(coord: Optional[Coordinate]) -> Optional[dict[str, float]]:
            if coord is None:
                return None
            return {"latitude": coord.latitude, "longitude": coord.longitude}

        north, east = noise.last_offset_m
        return {
            "debug": None if self.debug_reason is None else {
                "title": self.debug_reason[0],
                "detail": self.debug_reason[1],
            },
            "device": None if device is None else {
                "udid": device.udid,
                "product_type": device.product_type,
                "ios_version": device.ios_version,
            },
            "tunnel": None if tunnel is None else {
                "address": tunnel.address,
                "port": tunnel.port,
            },
            "anchor": point(location.anchor),
            "current": point(location.current),
            "noise": {
                "enabled": location.noise_enabled,
                "active": location.noise_active,
                "radius_m": noise.radius_m,
                "interval_s": noise.interval_s,
                "tick_count": location.tick_count,
                "offset_north_m": north,
                "offset_east_m": east,
                "offset_magnitude_m": noise.last_offset_magnitude_m,
            },
            "bookmarks": [
                {
                    "name": entry.name,
                    "latitude": entry.coordinate.latitude,
                    "longitude": entry.coordinate.longitude,
                }
                for entry in self.saved.list_all()
            ],
        }

    def mark_dirty(self) -> None:
        """LocationService listener. Must not block - just flag and return."""
        self.dirty.set()

    async def broadcaster(self) -> None:
        while True:
            await self.dirty.wait()
            self.dirty.clear()
            await self.push()

    async def push(self) -> None:
        if not self.sockets:
            return
        payload = self.snapshot()
        for ws in list(self.sockets):
            try:
                await ws.send_json(payload)
            except Exception:
                self.client_left(ws)

    @staticmethod
    def check_name(name: str) -> str:
        name = name.strip()
        if not name:
            raise HTTPException(400, "a bookmark needs a name")
        return name


def build_app(gui: GuiServer) -> FastAPI:
    app = FastAPI(title="locspoof", docs_url="/docs", redoc_url=None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc: RequestValidationError) -> JSONResponse:
        """Flatten Pydantic's error list to one string, so the page can just
        show `detail` regardless of which failure it was."""
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"][1:]) or "request"
        return JSONResponse({"detail": f"{field}: {first['msg']}"}, status_code=400)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        return gui.snapshot()

    @app.post("/api/location")
    async def set_location(body: SetLocationRequest) -> dict[str, Any]:
        name = gui.check_name(body.name) if body.name and body.name.strip() else None
        coord = Coordinate(body.latitude, body.longitude)
        try:
            await gui.location.set_location(coord)
        except Exception as exc:
            raise HTTPException(502, f"failed to set location: {exc}") from exc

        # Bookmark only after the device write succeeded.
        warning = None
        if name:
            try:
                gui.saved.save(name, coord)
            except LocationExistsError:
                warning = f"moved, but {name!r} is already saved"
            except Exception as exc:
                warning = f"moved, but could not save: {exc}"
            gui.dirty.set()
        return {"ok": True, "warning": warning}

    @app.post("/api/location/clear")
    async def clear_location() -> dict[str, Any]:
        try:
            await gui.location.clear_location()
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/goto")
    async def goto(body: NameRequest) -> dict[str, Any]:
        entry = gui.saved.get(body.name)
        if entry is None:
            raise HTTPException(404, f"no saved location named {body.name!r}")
        try:
            await gui.location.set_location(entry.coordinate)
        except Exception as exc:
            raise HTTPException(502, f"failed to set location: {exc}") from exc
        return {"ok": True}

    @app.post("/api/bookmarks")
    async def save_current(body: NameRequest) -> dict[str, Any]:
        """Bookmark the current location, exactly as it was typed."""
        name = gui.check_name(body.name)
        anchor = gui.location.anchor
        if anchor is None:
            raise HTTPException(409, "nothing to save - set a location first")
        try:
            gui.saved.save(name, anchor)
        except LocationExistsError as exc:
            raise HTTPException(409, f"{name!r} is already saved") from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
        gui.dirty.set()
        return {"ok": True}

    @app.post("/api/bookmarks/delete")
    async def delete_bookmark(body: NameRequest) -> dict[str, Any]:
        if not gui.saved.delete(body.name):
            raise HTTPException(404, f"no saved location named {body.name!r}")
        gui.dirty.set()
        return {"ok": True}

    @app.post("/api/noise")
    async def set_noise(body: NoiseRequest) -> dict[str, Any]:
        noise = gui.location.noise
        if body.radius_m is not None:
            noise.radius_m = body.radius_m
        if body.interval_s is not None:
            noise.interval_s = body.interval_s
        if body.enabled is not None:
            try:
                await gui.location.set_noise_enabled(body.enabled)
            except Exception as exc:
                raise HTTPException(502, str(exc)) from exc
        else:
            gui.dirty.set()
        return {"ok": True}

    @app.post("/api/shutdown")
    async def shutdown() -> dict[str, Any]:
        """Quit: main.py then restores GPS and closes the tunnel."""
        gui.shutdown.set()
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()
        gui.client_joined(ws)
        try:
            await ws.send_json(gui.snapshot())
            while True:
                # Clients never send commands - those go over HTTP. This read
                # exists only to notice the socket closing.
                await ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            gui.client_left(ws)

    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    return app


async def _wait_until_listening(server: uvicorn.Server, serve_task: asyncio.Task) -> bool:
    """True once uvicorn is accepting connections; False if it died or stalled.

    Opening the browser before this point gives the user a "can't connect"
    page they would have to reload by hand.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STARTUP_TIMEOUT_S
    while not server.started:
        if serve_task.done() or loop.time() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


def port_is_free(port: int, host: str = DEFAULT_HOST) -> bool:
    """True if the web app can bind here. Checked before touching the phone."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


async def serve(
    devices: DeviceManager,
    location: LocationService,
    saved: LocationStore,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    debug_reason: Optional[tuple[str, str]] = None,
) -> bool:
    """Run until the user quits from the page or presses Ctrl-C.

    Returns False if the server could not start.
    """
    if not port_is_free(port, host):
        print(f"! port {port} is already in use - is locspoof already running?")
        print(f"  Close it, or start this one with --port {port + 1}")
        return False

    gui = GuiServer(devices, location, saved, debug_reason=debug_reason)
    location.add_listener(gui.mark_dirty)

    config = uvicorn.Config(
        build_app(gui),
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="off",
        ws="wsproto",
    )
    server = uvicorn.Server(config)
    # We own this loop and its shutdown; uvicorn must not install its own
    # SIGINT handler or Ctrl-C would bypass the session teardown in main.py.
    server.install_signal_handlers = lambda: None

    url = f"http://{host}:{port}"
    serve_task = asyncio.create_task(server.serve())
    broadcast_task = asyncio.create_task(gui.broadcaster())
    shutdown_task = asyncio.create_task(gui.shutdown.wait())

    try:
        if await _wait_until_listening(server, serve_task):
            print(f"  Open {url}")
            print("  Keep this window open. Quit from the page, or press Ctrl-C here.")
            print(f"  Closing the page for {IDLE_SHUTDOWN_S:g}s also quits.")
            if open_browser:
                webbrowser.open(url)
        elif not serve_task.done():
            print(f"  ! the page server is slow to start - try opening {url} yourself")

        done, _ = await asyncio.wait(
            [serve_task, shutdown_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            # Surface a server crash instead of exiting silently.
            if task is serve_task:
                task.result()
    finally:
        location.remove_listener(gui.mark_dirty)
        await gui.say_goodbye()
        server.should_exit = True
        for task in (broadcast_task, shutdown_task):
            task.cancel()
        for task in (serve_task, broadcast_task, shutdown_task):
            try:
                await task
            except BaseException:
                pass
    return True
