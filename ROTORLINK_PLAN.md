# RotorLink — local iPad↔Pi link framework (plan)

> Status: planning / not started. This file is mirrored in three repos
> (RotorSync iOS app, Big-Beautiful-Box / TrailerSync, HeliSync) as the shared
> reference. Keep the three copies in sync when the design changes.

## Goal
A reusable local connection between the RotorSync iPad app and any RotorSync Pi
(TrailerSync, HeliSync EGT/CHT, future devices): auto-discovered, multi-client,
extensible, with stable remote/SSH-like access. BLE/USB stay as automatic
fallback. Primary near-term use: **HeliSync over WiFi for pilots**; pilots reach
TrailerSync over **Bluetooth**; ground crew reach TrailerSync over **WiFi**.

## Why it's low-risk
The Pi message protocols are already JSON; the iOS BLE protocol is ~90%
transport-agnostic (sends `[String:Any]` dicts, decodes `Codable` structs by
message type); and the Pi data/handlers are already decoupled from transport
(HeliSync `sensor_reader.py`, the trailer dashboard↔bumble socket). This is a
**transport + discovery** job, not a protocol rewrite.

## Topology (locked)
Per-Pi, **no routers**. Given "all iPads have cellular, no field WiFi":
- **Default = self-host AP** (`hostapd`+`dnsmasq`), WPA2. Field/flight. iPad joins
  it (keeps internet over cellular), local link over WiFi.
- **When a known network is present AND the Pi is idle → drop AP, join as STA**
  (e.g. "Headings" at the hangar) → **advertise over mDNS** → app connects over
  the LAN. iPad stays on its own network with full internet; multi-device,
  multi-Pi, stable `.local` SSH; Pi gets internet/NTP/maintenance.
- **Switch gating (critical):** only AP→STA when **no client is connected and no
  operation is active**; STA→AP after the known network is lost (debounced +
  signal hysteresis). This is what prevents a switch dropping the iPad mid-op.
- **USB WiFi dongle: optional** — only needed to be on the internet *while* an
  iPad is on the AP. With idle-only switching, not required.
- **Optional layer:** add the iPad's Personal Hotspot SSID to the Pi's known
  networks so in the field the Pi joins the iPad (Pi gets internet too). Optional.

## Security (current decision)
- **WPA2 on the AP is the security for now.** Per-device auth (approved-iPad
  check) is **deferred** — see "Auth (deferred)" below; revisit later.

## Architecture

### Pi side — drop-in `rotorlink` module (Python)
1. **Network manager** — the AP/STA auto-switch state machine + idle-only gating
   (via NetworkManager/`nmcli` on Pi OS Bookworm; the trailer's stack TBD).
2. **mDNS advertiser** — avahi `_rotorlink._tcp` + TXT (`app`, `serial`, `name`,
   `sw`, `proto`, `port`), published when on a real network (STA mode).
3. **WebSocket server** (`websockets`, asyncio) — accepts many clients, JSON text
   frames.
4. **Pluggable handler** — each app implements `on_message`/`broadcast`:
   - HeliSync: push `{cht, egt, timestamp, unit}` (~1 Hz) to all clients (reuse
     `sensor_reader`).
   - TrailerSync: bridge the existing dashboard command/state JSON (the same
     payloads the BLE path uses, via the existing local socket).
5. **Handshake** — first message `client_hello`; Pi replies `hello` (device
   descriptor + capability manifest, below).
6. **systemd service** per app (+ watchdog), like the existing units.

### iOS side — transport + discovery
1. **`DeviceTransport` protocol** — `connect/disconnect/send([String:Any])/
   subscribe(type)`. Impls: `WiFiTransport` (WebSocket via Starscream),
   `BLETransport` (refactor of `RaspberryPiBluetoothManager`), `USBTransport`
   (PeerTalk, HeliSync fallback).
2. **`DeviceConnectionManager`** — discovers (`NWBrowser` for `_rotorlink._tcp` +
   BLE scan + USB), auto-selects WiFi when available, falls back to BLE/USB.
   Reused by the trailer manager and `TemperatureService`.
3. **AP auto-join** — `NEHotspotConfiguration` (Hotspot Configuration capability;
   requires App-ID entitlement + profile regen) to join a Pi's AP by SSID.
4. **Interface pinning** — local socket `NWParameters.requiredInterfaceType =
   .wifi` so the Pi link uses WiFi while backend traffic goes over cellular.
5. **Reuse existing models** — no model changes.
6. **Info.plist** — add `_rotorlink._tcp` to `NSBonjourServices`
   (`NSLocalNetworkUsageDescription` already present).

### Message protocol & capability handshake
Reuse existing JSON shapes over WebSocket text frames, in a thin typed envelope.
On connect, the Pi sends:
```json
{ "type": "hello",
  "device": { "app": "helisync", "name": "Trailer 1", "serial": "HS-07",
              "sw": "1.4.2", "proto": 1, "hw": "pi4" },
  "capabilities": [
    { "id": "telemetry.temperature", "v": 1, "push": true,
      "channels": {"egt": 6, "cht": 6}, "unit": "F" }
  ] }
```
The app renders UI from the manifest (show a capability's view only if advertised)
and gates features by `sw`/capability version.

### Forward-compatibility contract (the rule that keeps it future-proof)
- **Extra fields are ignored** (Swift `JSONDecoder` default) — a newer Pi sending
  an extra field never crashes an older app.
- **New fields are declared `Optional`** in models (`decodeIfPresent`).
- **Every message decode is wrapped in `try?`/do-catch** (skip-and-log, never
  crash the app or drop the connection on one bad message).
- **Unknown message `type`s / capability IDs are ignored.**
- **Pi side: additive-only within a capability version** — add fields/types/caps
  freely, but **never rename/retype/remove** in place (a rename silently decodes
  to nil); bump the capability `v` for breaking changes.

### Feature placement (WiFi-first, BLE is the core subset)
The two transports are **not symmetric**, and that's deliberate — you can add
features to WiFi without touching BLE:
- **`rotorlink` (WiFi) is a transparent pass-through.** It forwards command
  frames verbatim and broadcasts the *whole* state/telemetry payload. A new
  dashboard command verb or a new field in the state blob reaches WiFi clients
  with **zero `rotorlink` change** — it's just another key the app already gets.
- **BLE (`bumble`) is an explicit per-characteristic mapping.** Every value is
  hand-wired to a fixed GATT characteristic + encoder, so a new field/command
  needs an explicit BLE change **every time**.
- **Rule — gate features by the capability manifest.** BLE and WiFi may advertise
  *different* capability sets; the app shows/enables a feature only when the
  *currently-connected* transport advertises it. So a WiFi-only feature simply
  doesn't appear on BLE — no BLE code change, nothing breaks.
- **So:** BLE = the stable **core/safety subset** (fill state, requested gallons,
  pump-stop) that pilots need over Bluetooth; **WiFi = where richer features
  land** for ground crew. Keep `rotorlink` a dumb pass-through and iterate there.
- **App-side enabler:** once features are written against `DeviceTransport`
  (P2) and gated on `transport.capabilities`, "add to WiFi only" is fully real
  on the app too — add the UI, gate it, leave `bumble` untouched.

### Multi-client + control arbitration
The Pi broadcasts state to all clients. **Many viewers, one controller** — reuse
`client_hello` role + pilot-priority to grant control to one device; consider
allowing an **emergency pump-stop from any device**. Show "who has control".

### Management / remote access
- Unique mDNS `.local` hostnames per Pi for stable same-network SSH (rename the
  HeliSync Pi off the default `raspberrypi`).
- **Tailscale** (or WireGuard) on each Pi → stable name/IP, SSH from anywhere with
  internet. Recommended for fleet maintenance.
- Optional in-app maintenance/shell channel over the same WebSocket (the trailer
  already does maintenance stdin/stdout over BLE).

### Auth (deferred — WPA2 only for now)
When revisited: layer on top of WPA2 — (a) a shared app secret used as a
**challenge-response** in `client_hello` (proves "our app", offline), and
(b) a **backend-synced device allowlist** (per-iPad approve/revoke, cached for
offline), upgradable to backend-issued **signed device tokens** the Pi verifies
offline. Lives in the handshake before capabilities are exchanged; applies to BLE
and WiFi alike.

## Per-device integration
- **TrailerSync (Big-Beautiful-Box):** add the `rotorlink` WS server bridging the
  existing dashboard/bumble command+state JSON; keep BLE. iOS: route
  `RaspberryPiBluetoothManager` through `DeviceTransport` (WiFi preferred when
  present, BLE fallback).
- **HeliSync:** add the `rotorlink` WS server reusing `sensor_reader`; keep
  PeerTalk/BLE. iOS: `TemperatureService` gains a WiFi transport.

## Phased build
- **P0 — verify (no code):** trailer dashboard↔bumble socket can take a 2nd
  client; each Pi's network stack; the Hotspot Configuration entitlement on the
  App ID; mDNS/multicast allowed on "Headings".
- **P1 — Pi `rotorlink` module + WS server on the trailer, STA/hangar first**
  (no AP yet), mDNS advertise; verify with `wscat`. Add Tailscale.
- **P2 — iOS `DeviceTransport` + `WiFiTransport` + `NWBrowser` discovery**, route
  the trailer through it on the hangar net (BLE fallback). Multi-client + control.
- **P3 — AP mode + gated auto-switch** on the Pi; iOS `NEHotspotConfiguration`
  auto-join + interface pinning. Field test.
- **P4 — bring HeliSync onto `rotorlink`** (easy — `sensor_reader` decoupled).
- **P5 — polish:** reconnection/backoff, transport-status UI, optional maintenance
  channel.

## Risks
- Refactoring the 6,280-line `RaspberryPiBluetoothManager` behind the transport
  protocol — do it behavior-preserving with BLE regression tests.
- Safety commands (pump-stop) over WiFi → ack them; emergency-stop from any device.
- In-flight engine-temp reliability → seamless, visible fallback to USB/BLE.
- AP robustness at boot → watchdog.
- mDNS across guest/VLAN networks → manual-IP fallback.
- `raspberrypi.local` hostname collision → unique hostnames.

## Range (Pi-as-AP, reference)
Pi built-in 2.4 GHz AP, LOS outdoors: ~30–50 m reliable (up to ~100 m ideal);
through metal/enclosure ~10–20 m; USB adapter + antenna 100 m+. (~3–5× BLE.)
HeliSync (Pi on the aircraft with the pilot): range is a non-issue. TrailerSync:
keep the antenna outside the metal enclosure; prefer 2.4 GHz for reach.

## Repos
- iOS app: `RotorSync/RotorSync` — branch `feature/rotorlink`.
- TrailerSync: `RotorSync/Big-Beautiful-Box` — branch `feature/rotorlink`.
- HeliSync: `RotorSync/HeliSync` — branch `feature/rotorlink`.
