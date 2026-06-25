#!/usr/bin/env python3
"""RotorLink smoke test — connects, captures `hello` + a `state` broadcast,
sends a READ-ONLY command (STATE_JSON) and checks the result. Sends NO control
commands; safe against a live trailer. Needs a server running.
    python3 rotorlink/tests/smoke_client.py [ws://host:8765]
"""
import asyncio
import json
import sys

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765"


async def main() -> int:
    got = {"hello": None, "state": None, "result": None}
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"type": "client_hello", "role": "viewer",
                                  "user": "smoketest", "device": "ci"}))
        await ws.send(json.dumps({"type": "command", "id": "t1", "command": "STATE_JSON"}))
        try:
            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "hello" and got["hello"] is None:
                    got["hello"] = msg
                    print("HELLO:", json.dumps(msg))
                elif t == "state" and got["state"] is None:
                    got["state"] = msg
                    print("STATE:", json.dumps(msg)[:300])
                elif t == "command_result":
                    got["result"] = msg
                    print("RESULT:", json.dumps(msg)[:300])
                if got["hello"] and got["state"] and got["result"]:
                    break
        except Exception as e:
            print("recv ended:", e)

    ok = True
    if not got["hello"] or got["hello"].get("device", {}).get("app") != "trailersync":
        print("FAIL: bad/no hello"); ok = False
    if not got["hello"] or not got["hello"].get("capabilities"):
        print("FAIL: no capability manifest"); ok = False
    if not got["result"] or not got["result"].get("ok"):
        print("FAIL: command_result not ok"); ok = False
    if got["state"] is None:
        print("WARN: no state broadcast seen (dashboard idle/unchanging)")
    print("SMOKE TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
