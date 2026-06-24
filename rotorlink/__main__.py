"""
RotorLink entrypoint:  python3 -m rotorlink

Starts the mDNS advertiser and the WebSocket server, and runs until killed.
Logging level via ROTORLINK_LOG (default INFO).
"""

import asyncio
import logging
import os
import signal

from . import config
from .mdns import MDNSAdvertiser
from .network_manager import NetworkManager
from .server import RotorLinkServer


def _configure_logging() -> None:
    level = getattr(logging, os.environ.get("ROTORLINK_LOG", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


async def _amain() -> None:
    # Turn SIGTERM (systemctl stop / kill) into a clean cancellation so the
    # mdns.stop() finally runs and the avahi child is reaped.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    mdns = MDNSAdvertiser()
    await mdns.start()
    server_task = asyncio.create_task(RotorLinkServer().run())
    # AP/STA field-mode manager (disabled unless ROTORLINK_AP_ENABLED=1 — it only
    # dry-run-logs its decisions otherwise, so it never disrupts the network).
    network_task = asyncio.create_task(NetworkManager().run())
    try:
        done, _ = await asyncio.wait(
            {server_task, asyncio.create_task(stop.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        server_task.cancel()
        network_task.cancel()
        for t in (server_task, network_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await mdns.stop()


def main() -> None:
    _configure_logging()
    d = config.device_descriptor()
    logging.getLogger("rotorlink").info(
        "RotorLink %s on %s (app=%s serial=%s)", d["sw"], d["name"], d["app"], d["serial"]
    )
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
