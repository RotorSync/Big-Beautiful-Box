#!/usr/bin/env python3
"""Over-the-wire robustness test for the forward-compat contract. Sends a
malformed frame, an unknown message type, and a command missing its field, then
proves the connection SURVIVES with a valid read-only command. No equipment
touched (only STATE_JSON). Needs a server running.
    python3 rotorlink/tests/robust_client.py [ws://host:8765]
"""
import asyncio
import json
import sys

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765"


async def main() -> int:
    fails = []
    async with websockets.connect(URL) as ws:
        await ws.recv()  # drain hello
        await ws.send("{not valid json")
        await ws.send(json.dumps({"type": "totally_unknown_v99", "x": 1}))
        await ws.send(json.dumps({"type": "command", "id": "bad", "future_field": 7}))
        await ws.send(json.dumps({"type": "command", "id": "good", "command": "STATE_JSON"}))

        results = {}
        try:
            while len(results) < 2:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                if msg.get("type") == "command_result":
                    results[msg.get("id")] = msg
        except Exception as e:
            print("recv ended:", e)

        bad = results.get("bad")
        good = results.get("good")
        if not bad or bad.get("ok") is not False:
            print("FAIL: command missing `command` should return ok=false"); fails.append("bad")
        else:
            print("PASS: malformed command -> ok=false, connection alive")
        if not good or not good.get("ok"):
            print("FAIL: valid command after garbage frames failed"); fails.append("good")
        else:
            print("PASS: connection survived garbage frames; valid read ok")

    print("ROBUST:", "ALL PASS" if not fails else f"FAILED {fails}")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
