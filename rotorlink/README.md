# rotorlink (TrailerSync)

Local WiFi link between the RotorSync iPad app and this trailer Pi. A thin,
standalone `asyncio` + `websockets` service that bridges the **existing**
dashboard command/state protocol (the line socket on `127.0.0.1:9999`, the same
one the BLE server `rotorsync_bumble.py` uses) onto a WebSocket.

It touches neither the dashboard nor the BLE server — it is just another
short-lived client of `:9999`, run as its own systemd service. BLE stays as-is
and as the automatic fallback. See `../ROTORLINK_PLAN.md` for the full design.

## Run
```bash
cd /home/pi/Big-Beautiful-Box
python3 -m rotorlink            # ws://0.0.0.0:8765, mDNS _rotorlink._tcp
```
Requires `websockets` (already installed on the trailer, v16). mDNS uses the
`zeroconf` lib if present, else `avahi-publish-service` (best-effort).

## Service
```bash
sudo cp systemd/rotorlink.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rotorlink
```

## Protocol (text JSON frames, `type`-tagged)
Pi → app: `hello` (device descriptor + capability manifest), `state`
(dashboard snapshot, on change), `history`, `command_result`, `error`, `pong`.
App → Pi: `client_hello` `{role,user,device}`, `command` `{id?,command,args?}`,
`ping`.

The `command` field is a raw dashboard command line (e.g. `STATE_JSON`,
`STATUS`, `HISTORY`, `SET_REQUESTED_GALLONS:12.5`, `BATCHMIX:{...}`), forwarded
verbatim and answered with the dashboard's response in `command_result`.

### Forward-compatibility contract
Extra fields ignored; unknown `type`s ignored (connection kept); new fields are
added, never renamed/retyped in place. This is what lets a newer Pi talk to an
older app and vice versa.

### Control arbitration
Off by default (clean transport for P1). Set `ROTORLINK_ARBITRATION=1` to enforce
"many viewers, one controller": read commands (`STATE_JSON`/`STATUS`/`HISTORY`)
and an emergency `STOP` are always allowed; other control commands require being
the controller.

## Config (env)
`ROTORLINK_WS_PORT` (8765), `ROTORLINK_DASHBOARD_PORT` (9999),
`ROTORLINK_STATE_POLL_INTERVAL` (0.5s), `ROTORLINK_NAME`, `ROTORLINK_SERIAL`,
`ROTORLINK_MDNS` (1), `ROTORLINK_ARBITRATION` (0), `ROTORLINK_LOG` (INFO).
