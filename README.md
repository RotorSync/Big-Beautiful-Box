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
- **7" touchscreen dashboard** with fullscreen Tkinter GUI

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

## Related Repositories

- [Switch-Box-For-BBB](https://github.com/austins05/Switch-Box-For-BBB) — Pico switch box firmware
- [iol-hat](https://github.com/Pinetek-Networks/iol-hat) — IOL HAT library

## License

Proprietary — Headings Helicopters / Rotorsync

## Author

Developed by Rotorsync, 2025-2026
