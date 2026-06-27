"""
mDNS advertiser for RotorLink — publishes `_rotorlink._tcp` so the iPad's
NWBrowser can find this Pi by capability instead of a hard-coded IP.

Best-effort and dependency-light: prefers the `zeroconf` Python lib if present,
otherwise shells out to `avahi-publish-service` (avahi-daemon ships on Pi OS).
If neither is available it logs and continues — discovery is a convenience; the
app can always connect by IP/.local name. The TXT record carries the same
descriptor fields as the `hello` so a browser can show/filter before connecting.
"""

import asyncio
import ctypes
import logging
import shutil
import signal
from typing import Optional

from . import config

logger = logging.getLogger("rotorlink.mdns")


def _die_with_parent() -> None:
    """preexec for the avahi child: ask the kernel to SIGTERM it if this process
    dies, so an `avahi-publish-service` is never orphaned even on SIGKILL.
    (Linux-only PR_SET_PDEATHSIG=1; best-effort.)"""
    try:
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM, 0, 0, 0)
    except Exception:
        pass


def _txt_record() -> dict:
    d = config.device_descriptor()
    return {
        "app": d["app"],
        "name": d["name"],
        "serial": d["serial"],
        "sw": d["sw"],
        "proto": str(d["proto"]),
        "hw": d["hw"],
        "port": str(config.WS_PORT),
    }


class MDNSAdvertiser:
    def __init__(self) -> None:
        self._zc = None
        self._info = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._advertised_name: Optional[str] = None

    async def start(self) -> None:
        if not config.MDNS_ENABLED:
            logger.info("mDNS disabled (ROTORLINK_MDNS=0)")
            return
        # register_service() sends/awaits mDNS packets (blocking) — keep it off
        # the event loop.
        loop = asyncio.get_running_loop()
        if await loop.run_in_executor(None, self._start_zeroconf):
            return
        await self._start_avahi()

    def _start_zeroconf(self) -> bool:
        try:
            import socket as _socket

            from zeroconf import ServiceInfo, Zeroconf
        except Exception:
            return False
        try:
            d = config.device_descriptor()
            # Advertise as the trailer NAME (matches BLE) but resolve via the
            # serial (hostname) .local host, so the box stays reachable.
            instance = "%s.%s.local." % (d["name"], config.MDNS_SERVICE_TYPE)
            self._zc = Zeroconf()
            self._info = ServiceInfo(
                type_=config.MDNS_SERVICE_TYPE + ".local.",
                name=instance,
                port=config.WS_PORT,
                properties={k: v.encode() for k, v in _txt_record().items()},
                server="%s.local." % d["serial"],
            )
            self._zc.register_service(self._info, allow_name_change=True)
            self._advertised_name = d["name"]
            logger.info("mDNS advertised via zeroconf: %s", instance)
            return True
        except Exception as e:
            logger.warning("zeroconf advertise failed, will try avahi: %s", e)
            self._zc = None
            return False

    async def _start_avahi(self) -> None:
        if not shutil.which("avahi-publish-service"):
            logger.warning(
                "no zeroconf lib and no avahi-publish-service; mDNS disabled "
                "(connect by IP/.local instead)"
            )
            return
        d = config.device_descriptor()
        txt = ["%s=%s" % (k, v) for k, v in _txt_record().items()]
        args = [
            "avahi-publish-service",
            d["name"],
            config.MDNS_SERVICE_TYPE,
            str(config.WS_PORT),
            *txt,
        ]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                preexec_fn=_die_with_parent,
            )
            self._advertised_name = d["name"]
            logger.info("mDNS advertised via avahi: %s %s", d["name"], config.MDNS_SERVICE_TYPE)
        except Exception as e:
            logger.warning("avahi-publish-service failed; mDNS disabled: %s", e)

    async def maintain(self, interval: float = 20.0) -> None:
        """Re-advertise if the trailer name changes. On a cold boot bumble writes
        the BLE name file AFTER we start (so we may have come up on the serial),
        and a trailer can be reassigned without a reboot — keep mDNS == BLE name."""
        if not config.MDNS_ENABLED:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                current = config.device_descriptor()["name"]
            except Exception as e:
                logger.warning("mDNS name check failed: %s", e)
                continue
            if current and current != self._advertised_name:
                logger.info(
                    "trailer name changed %r -> %r; re-advertising mDNS",
                    self._advertised_name, current,
                )
                await self.stop()
                await self.start()

    async def stop(self) -> None:
        if self._zc is not None:
            try:
                if self._info is not None:
                    self._zc.unregister_service(self._info)
                self._zc.close()
            except Exception:
                pass
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
