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
- **JBD BMS** (A5:C2:37:2B:32:91) — Battery voltage and state of charge
- **Mopeka Pro** sensors — Ultrasonic tank level monitors

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

```bash
git clone https://github.com/austins05/Big-Beautiful-Box.git
cd Big-Beautiful-Box
chmod +x install.sh
./install.sh
```

The install script configures:
- Python dependencies
- HDMI display settings
- UART on GPIO 14/15
- Auto-login and screen timeout
- Systemd services

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
