"""Owns the `start-tunnel` subprocess.

The tunnel only exists while that child process is alive, so this class holds
it and hands back the RSD address/port it printed. Nothing here prints: the
caller decides how to present progress and failures.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import re
import sys
from typing import Callable, Optional

from .models import TunnelInfo

# `--script-mode` prints exactly one line: "<address> <port>"
RSD_LINE = re.compile(r"^(\S+)\s+(\d+)\s*$")

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

DEFAULT_TIMEOUT = 60.0


class TunnelError(RuntimeError):
    """The tunnel could not be started.

    `details` carries whatever start-tunnel wrote to stderr, so the caller can
    show it without this module doing any printing of its own.
    """

    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(message)
        self.details = details


class PrivilegeError(TunnelError):
    """The classic tunnel needs elevation and we don't have it."""


def is_admin() -> bool:
    """True if we have the privileges the classic tunnel needs."""
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def _noop(_: str) -> None:
    pass


class TunnelManager:
    """Starts and stops one tunnel.

    :param use_native: True to ride Apple's existing tunnel (macOS, no root).
        False to build the classic TUN interface (needs elevation).
    :param timeout: seconds to wait for the address/port line.
    :param debug: optional sink for trace lines; core never writes to stdout.
    """

    def __init__(
        self,
        use_native: bool,
        timeout: float = DEFAULT_TIMEOUT,
        debug: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._use_native = use_native
        self._timeout = timeout
        self._debug = debug or _noop
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._info: Optional[TunnelInfo] = None

    @property
    def use_native(self) -> bool:
        return self._use_native

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def info(self) -> Optional[TunnelInfo]:
        return self._info

    def argv(self) -> list[str]:
        base = [sys.executable, "-m", "pymobiledevice3"]
        if self._use_native:
            return base + ["remote", "start-tunnel", "--script-mode"]
        return base + ["lockdown", "start-tunnel", "--script-mode"]

    async def start(self) -> TunnelInfo:
        """Spawn start-tunnel and read the RSD address/port it prints.

        The child stays alive until `stop()`; that is what keeps the tunnel up.
        """
        if not self._use_native and not is_admin():
            raise PrivilegeError("the classic tunnel requires elevation")

        env = dict(os.environ, PYTHONUNBUFFERED="1")
        argv = self.argv()
        self._debug(f"spawning: {' '.join(argv)}")

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._proc = proc

        try:
            address, port = await asyncio.wait_for(
                self._read_rsd_line(proc), timeout=self._timeout
            )
        except BaseException as exc:
            details = await self._drain_stderr(proc)
            await self.stop()
            raise TunnelError(str(exc), details=details) from exc

        self._info = TunnelInfo(address, port)
        return self._info

    async def stop(self) -> None:
        """Terminate the tunnel child, escalating to kill if it lingers."""
        proc, self._proc = self._proc, None
        self._info = None
        if proc is None or proc.returncode is not None:
            return
        self._debug("terminating tunnel")
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            # Already gone (e.g. _drain_stderr terminated it); nothing to do.
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass

    async def _read_rsd_line(self, proc: asyncio.subprocess.Process) -> tuple[str, int]:
        # Skip anything that isn't the "<address> <port>" line.
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                raise RuntimeError("start-tunnel exited without printing an RSD address")
            line = raw.decode(errors="replace").strip()
            self._debug(f"tunnel stdout: {line!r}")
            match = RSD_LINE.match(line)
            if match:
                return match.group(1), int(match.group(2))

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process) -> str:
        if proc.stderr is None:
            return ""
        if proc.returncode is None:
            proc.terminate()
        try:
            data = await asyncio.wait_for(proc.stderr.read(), timeout=5.0)
        except Exception:
            return ""
        return (data or b"").decode(errors="replace").strip()
