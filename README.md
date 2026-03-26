# Big Beautiful Box (BBB) — Flow Meter Monitoring & Pump Control

A Raspberry Pi-based spray trailer monitoring system for agricultural helicopter operations.

## ⚠️ Version Compatibility

**This software is designed for BBB HAT v1.1**

A new HAT version (v1.2) is in development with updated GPIO mappings and features.
Check for v1.2 branch or updated documentation before use with newer hardware.

---

## Features

- **Real-time flow monitoring** via IO-Link (Picomag flow meter)
- **Auto-shutoff** with flow-rate-compensated coast prediction
- **BLE integration** with RotorSync iOS app for remote monitoring and control
- **Sensor monitoring** — battery (BMS) and tank levels (Mopeka)
- **BatchMix support** — receive mix formulas from iPad app
- **Serial control** via Switch Box (Pico-based remote)
- **7" screen dashboard** with fullscreen Tkinter GUI

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Spray Trailer                            │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  Flow Meter  │───▶│   IOL HAT    │───▶│   Raspberry Pi   │  │
│  │  (Picomag)   │    │  (IO-Link)   │SPI │    Dashboard     │  │
│  └──────────────┘    └──────────────┘    └────────┬─────────┘  │
│                                                    │            │
│  ┌──────────────┐              ┌──────────────────┼──────────┐ │
│  │  Switch Box  │─────UART────▶│    Serial        │   BLE    │ │
│  │   (Pico)     │    RJ45      │   Listener       │  Server  │ │
│  └──────────────┘              └──────────────────┼──────────┘ │
│                                                    │            │
│  ┌──────────────┐              ┌──────────────────▼──────────┐ │
│  │ Thumbs Up    │─────GPIO────▶│      Pump Relay (K2)        │ │
│  │   Button     │              └─────────────────────────────┘ │
│  └──────────────┘                                              │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    │ Bluetooth LE
                                    ▼
                         ┌──────────────────┐
                         │   RotorSync App  │
                         │  (iPhone/iPad)   │
                         └──────────────────┘
```

## Hardware

### Main Components

| Component | Description |
|-----------|-------------|
| Raspberry Pi 5 | Main controller (Ubuntu) |
| IOL HAT | IO-Link master for flow meter (SPI) |
| Picomag Flow Meter | IO-Link industrial flow sensor |
| 7" HDMI Display | 1024x600 touchscreen |
| Switch Box | Pico-based pilot remote control |
| BLE Adapters | CSR (GATT server) + Realtek (sensors) |

### GPIO Pin Assignments

| GPIO | Function |
|------|----------|
| GPIO 14/15 | UART RX/TX (Serial to Switch Box) |
| GPIO 22 | Thumbs Up button input |
| GPIO 27 | Pump stop relay |
| GPIO 7-11, 24-25 | SPI + interrupts (IOL HAT) |

### Switch Box Controls

The Pico-based Switch Box connects via RJ45 and sends serial commands:

| Input | Command | Function |
|-------|---------|----------|
| Rotary encoder | `+1` / `-1` | Adjust target gallons |
| Encoder + modifier | `+10` / `-10` | Coarse adjustment |
| Pump Stop button | `PS` | Emergency pump stop |
| Override button | `OV` | Toggle auto-alert |
| Fill/Mix toggle | `FILL` / `MIX` | Switch modes |
| Thumbs Up button | `TU` | Pilot acknowledgment |

## BLE Integration (RotorSync App)

The system runs a BLE GATT server using [Bumble](https://github.com/google/bumble) that exposes:

### Service UUID
`12345678-1234-5678-1234-56789abcdef0`

### Characteristics

| UUID Suffix | Name | Type | Description |
|-------------|------|------|-------------|
| `def1` | BMS | READ | Battery status `{"voltage": x, "soc": y}` |
| `def2` | Mopeka1 | READ | Tank 1 level `{"level_mm": x, "quality": y}` |
| `def3` | Mopeka2 | READ | Tank 2 level `{"level_mm": x, "quality": y}` |
| `def4` | Pump | WRITE | Send `PS` to stop pump |
| `def5` | Gallons | WRITE | Send `+1`, `-1`, `+10`, `-10` |
| `def6` | Requested | READ | Target gallons |
| `def7` | Actual | READ | Current gallons dispensed |
| `def8` | History | READ | Last 5 fill records |
| `def9` | BatchMix | WRITE | JSON batch mix data from iPad |

### BatchMix Format

For large payloads, BatchMix supports chunked writes:
```
CHUNK:1/3:{"product_count":2,"products":[...
CHUNK:2/3:...],"water_needed":45.5,...
CHUNK:3/3:...}
```
### Bluetooth Hardware

For the Dongle that connects to the ipad StaerTech AV53C1-USB-Bluetooth is used.

### Sensor Monitoring

The BLE server also reads nearby sensors via a second Bluetooth adapter:
- **JBD BMS** (A5:C2:37:31:77:C0) — Battery voltage and state of charge
- **Mopeka Pro** sensors — Ultrasonic tank level monitors

### BLE Architecture Notes

- `rotorsync_bumble.py` owns both Bluetooth adapters through Bumble HCI socket transports.
- The GATT/iPad adapter is selected by `GATT_ADAPTER_MAC`.
- The sensor adapter is selected by `SENSOR_ADAPTER_MAC`.
- BlueZ is intentionally stopped for the Rotorsync runtime path. The sensor side no longer uses `bleak`.
- Adapter MAC lookup is only used at startup. After startup, the GATT watchdog tracks the adapter by its resolved sysfs USB device path so it does not touch the live controller.

## Software

### Services

| Service | Description |
|---------|-------------|
| `iol_dashboard.service` | Main dashboard GUI |
| `rotorsync.service` | BLE GATT server |
| `rotorsync_watchdog.service` | BLE server monitor |

### Configuration

Edit `config.py` to adjust:
- GPIO pin assignments
- Serial port settings
- Flow curve calibration
- Display refresh rate
- Log file paths

### Flow Shutoff Curve

The system predicts coast distance based on flow rate:

```
coast_gallons = 0.030625 × GPM - 0.22375
```

Calibration data:
- 22 GPM → 0.45 gal coast
- 70 GPM → 1.92 gal coast

## Installation

### Deployment Assumptions

These steps assume the target Pi starts from a normal Ubuntu Desktop image with the default desktop/login setup.

- User account is `pi`
- GDM is the active display manager
- Network access is available for the initial clone
- BBB hardware is connected after install as normal

The installer then layers the BBB-specific configuration on top of that default Ubuntu desktop install:

- tracked HDMI and boot settings from `deploy/boot-firmware-bbb.conf`
- tracked GDM autologin/X11 settings from `deploy/gdm3-custom.conf`
- vendored `iol-hat` source from this repo
- dashboard and Rotorsync systemd services

### Install Steps

```bash
git clone https://github.com/austins05/Big-Beautiful-Box.git
cd Big-Beautiful-Box
chmod +x install.sh
./install.sh
```

Run the installer as user `pi`, then reboot when it finishes.

The install script configures:
- Python dependencies
- HDMI display settings
- UART on GPIO 14/15
- Auto-login and screen timeout
- Systemd services

### Update Branch

Production devices use the on-screen updater to pull from `origin/master`.
Development can continue on `main`, but anything intended for field updates must also be pushed to `master`.

## Operational Workflow

### Fill Cycle
1. Pilot sets target gallons via Switch Box encoder
2. Ground crew monitors dashboard during fill
3. System auto-stops pump when target reached (with coast compensation)
4. Display turns green when actual is within ±2 gallons
5. Ground crew presses Thumbs Up → pilot sees confirmation
6. Fill history logged to `fill_history.log`

### BatchMix (via RotorSync App)
1. iPad sends mix formula via BLE (BatchMix characteristic)
2. Dashboard displays product list overlay
3. Water target automatically set from formula
4. Ground crew follows mix sequence

## BLE Stability Investigation (March 21, 2026)

### Symptom

The external StarTech GATT dongle would repeatedly fail with:

- `Bluetooth: hciN: command tx timeout`
- `Bluetooth: hciN: Resetting usb device`
- Bumble `BrokenPipeError: [Errno 32] Broken pipe`

When this happened, the same physical adapter kept re-enumerating as new HCI indices (`hci0`, `hci2`, `hci10`, etc.), and `rotorsync.service` would restart repeatedly.

### What Was Ruled Out

- Not a dashboard bug.
- Not a Mopeka sensor issue.
- Not just a stale USB state; reboot did not fix it.
- Not only BlueZ; removing BlueZ helped but did not fully stop the resets.
- Not just Realtek firmware loading; a firmware swap changed the loaded firmware version but did not stop the resets.
- Not just basic Bumble advertising; a minimal Bumble advertiser on the StarTech dongle was stable.

### Root Cause

There were two separate userspace conflicts:

1. BlueZ and Bumble were both touching the same GATT adapter.
2. After BlueZ was removed from the runtime path, the Rotorsync GATT watchdog was still polling the Bumble-owned adapter every 5 seconds with `hciconfig`.

That watchdog polling was enough to reproduce the failure. A minimal Bumble GATT service stayed stable until the same `hciconfig -a` / `hciconfig hciN` polling loop was added. As soon as that loop ran, the HCI socket broke and the controller reset.

### Final Fix

The final stable design is:

- Sensor scanning moved off BlueZ/`bleak` and onto Bumble on the dedicated sensor adapter.
- `rotorsync.service` no longer declares `Wants=bluetooth.target`.
- Rotorsync no longer starts `bluetooth.service`.
- The runtime watchdog no longer polls the live GATT adapter with `hciconfig`.
- Startup still resolves the GATT adapter by MAC.
- After startup, the watchdog tracks the same physical USB interface by its sysfs device path and only exits if that path disappears or rebinds to a different `hciN`.

### Verification

After the watchdog change:

- `rotorsync.service` remained active.
- No new watchdog events were added.
- No new Bumble `BrokenPipeError` entries appeared.
- A 15 minute soak test completed with no new kernel resets:

```text
command tx timeout: 57 -> 57
Resetting usb device: 58 -> 58
```

That was the first stable run after the GATT watchdog stopped touching the Bumble-owned adapter.

### Design Rule Going Forward

Once Bumble has opened the GATT adapter:

- do not poll that adapter with `hciconfig`
- do not have BlueZ manage that same controller
- do not mix `bleak`/BlueZ calls against the Bumble-owned adapter

If adapter presence must be checked at runtime, use sysfs path tracking instead of controller management commands.


## Known Issues

### Flow Meter Disconnect Recovery ([#2](https://github.com/RotorSync/Big-Beautiful-Box/issues/2)) — FIXED

The Picomag flow meter would silently lose its IO-Link connection. The dashboard detected this via stale data (identical raw bytes for 5+ seconds) and triggered power-cycles, but the IOL master daemon could not recover without a full service restart.

**Root cause:** Three bugs in the i-link DL (Data Link) layer in `iol-hat/src-master-application/ilink/iolink_dl.c`:

1. **No watchdog timer in steady-state OPERATE.** The `TInitcyc` software timer is one-shot and only fires once when entering OPERATE. The `timer_tcyc` timer infrastructure existed in skeleton form but was never wired up (never started, no event handler in `dl_main`, `timer_tcyc_elapsed` never set). Once the initial cycle begins, timing is entirely hardware-driven with no software fallback. If the MAX14819 stops generating `RXRDY` interrupts for any reason, the DL thread blocks forever in `os_event_wait()`.

2. **`timer_elapsed` not handled in `AW_REPLY_16`.** Even if a timer did fire, the `AW_REPLY_16` state handler had no check for `timer_elapsed` — it fell through to "unknown event triggered" which did nothing useful.

3. **`get_data` failure caused silent hang.** When `iolink_pl_get_data()` returned `false` (e.g., due to a FIFO level mismatch between `TxRxDataA` and `RxFIFOLvl` register reads), the DL main loop skipped calling `iolink_dl_message_h_sm()` entirely. No next TX was sent, so no response would ever come, causing a permanent hang.

**Fixes applied** (all in `iol-hat/src-master-application/ilink/iolink_dl.c`):

- **100ms watchdog timer** started after every TX message (in `get_od14`, the `AW_REPLY_16` success path, and the retry path). If no `RXRDY` arrives within 100ms, the timer fires and triggers COMLOST recovery, which re-establishes the connection automatically.
- **`timer_elapsed` handler in `AW_REPLY_16`** — when the watchdog fires, the state machine now detects it and calls `iolink_dl_mh_handle_com_lost()` to recover.
- **`get_data` failure signals `rxerror`** — when `get_data` returns false on an `RXRDY` event, the DL main loop now sets `rxerror=true` and calls `iolink_dl_message_h_sm()` so the state machine can handle it (retry or COMLOST) instead of silently hanging.
- **Retries before COMLOST** — transient RX timeouts and errors in `AW_REPLY_16` are retried up to 3 times with `PL_Resend()` before triggering a full COMLOST recovery, reducing unnecessary reconnection cycles.

Also fixed: **Port 2 cross-channel interference** — Port 2 (unused) was set to IOL mode (`-m1 0`) which caused cross-channel interference with Port 1. Changed to OFF (`-m1 3`) in `start_iol_dashboard.sh`.

## Related Repositories

- [Switch-Box-For-BBB](https://github.com/austins05/Switch-Box-For-BBB) — Pico switch box firmware
- [iol-hat](https://github.com/Pinetek-Networks/iol-hat) — IOL HAT library

## License

Proprietary — Headings Helicopters / Rotorsync

## Author

Developed by Rotorsync, 2025-2026
