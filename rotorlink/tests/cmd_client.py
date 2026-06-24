#!/usr/bin/env python3
"""Sends a batch of app {"cmd":...} commands over RotorLink and prints each
command_result. Intended to run against a server pointed at the FAKE dashboard
(see fake_dashboard.py) so nothing real is actuated.
    python3 cmd_client.py [ws://host:8765]
"""
import asyncio
import json
import sys

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765"

COMMANDS = [
    {"id": "1", "cmd": "set_target", "gallons": 12.5},
    {"id": "2", "cmd": "set_mode", "mode": "mix"},
    {"id": "3", "cmd": "pump_stop"},
    {"id": "4", "cmd": "adjust", "delta": 10},
    {"id": "5", "cmd": "set_override", "enabled": False},
    {"id": "6", "cmd": "cursor_move", "dx": 5, "dy": -3},
    {"id": "7", "cmd": "confirm_fill"},
    {"id": "8", "command": "STATE_JSON"},   # raw read path still works
    {"id": "9", "cmd": "frobnicate"},        # unknown -> ok:false, nothing forwarded
]


async def main() -> int:
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"type": "client_hello", "role": "controller", "user": "cmdtest"}))
        for c in COMMANDS:
            msg = {"type": "command"}
            msg.update(c)
            await ws.send(json.dumps(msg))

        results = {}
        try:
            while len(results) < len(COMMANDS):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                if msg.get("type") == "command_result":
                    results[msg.get("id")] = msg
        except Exception as e:
            print("recv ended:", e)

    for c in COMMANDS:
        r = results.get(c["id"])
        print(f"  cmd {c['id']:>2} {c.get('cmd', c.get('command')):<16} -> ok={r.get('ok') if r else None} resp={(r or {}).get('response')!r}")
    # id 9 (unknown) must be ok:false; the rest ok:true
    bad = [cid for cid, r in results.items() if cid != "9" and not r.get("ok")]
    if results.get("9", {}).get("ok") is not False:
        bad.append("9-should-be-false")
    print("CMD CLIENT:", "PASS" if not bad else f"FAIL {bad}")
    return 1 if bad else 0


sys.exit(asyncio.run(main()))
