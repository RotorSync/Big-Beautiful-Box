# Tank-test logger (MFP tank-fill accuracy test)

Records Picomag flow (from the existing flow-control thread) and MFP CAN
telemetry against ONE clock (`time.monotonic()`, wall time alongside), so the
MFP's reported level can be compared with metered gallons offline.

**Off by default.** With `TANK_TEST_LOGGER_ENABLED = False` (shipped) the only
code on the flow path is `if tank_test_logger is not None:` (about 10 ns).
Branch `feature/tank-test-logger` only. Do not merge to master without review:
field boxes auto-update from master.

## How it works
- `dashboard.flow_control_loop` (50 Hz, owns the IO-Link reads) calls
  `tank_test_logger.submit_flow(...)` **after** `_flow_control_process_sample`
  (the shutoff decision). It appends to a bounded deque in O(1). If the deque is
  full, the sample is dropped and counted (`flow_dropped`). It never blocks.
  **The logger never opens IO-Link.**
- A `tanktest-can` thread reads the CANable with python-can, writes every frame to
  `can_raw.log` and decodes MFP frames (`src/mfp_can_decode.py`, beta firmware layout).
- A `tanktest-writer` thread writes the CSV: one row per *fresh* Picomag sample
  (about 12.5 Hz), plus a 1 s heartbeat when the data is unchanged. Each row carries
  the latest decoded MFP fields and their ages.

## Hardware: CANable on the BBB USB, 250 kbit/s, 29-bit
- candleLight / gs_usb firmware (appears as `can0`):
  `sudo ip link set can0 up type can bitrate 250000`
  and then `TANK_TEST_CAN_INTERFACE="socketcan"`, `TANK_TEST_CAN_CHANNEL="can0"`.
- slcan (stock) firmware: `TANK_TEST_CAN_INTERFACE="slcan"`,
  `TANK_TEST_CAN_CHANNEL="/dev/ttyACM0"` (bitrate is passed by the logger).
- Needs `pip install python-can`. If CAN cannot open, flow is still logged and
  the CSV and `markers.txt` get a `CAN_OPEN_FAILED:` event.

## Running on a box (dashboard mode, real flow)
1. Set `TANK_TEST_LOGGER_ENABLED = True` in `config.py`, or start the dashboard
   with `BBB_TANK_TEST_LOGGER=1` (and optionally `BBB_TANK_TEST_LABEL=fill1`).
2. A run directory appears in `TANK_TEST_LOG_DIR` (`/home/pi/tank_test_logs`):
   `tanktest_YYYYmmdd_HHMMSS_<label>/`.
3. Markers: `python3 -m src.tank_test_logger mark start-fill` (or `echo txt > /home/pi/tank_test_logs/MARK`).
4. Stop: `python3 -m src.tank_test_logger stop`, or stop the dashboard. A stopped
   logger stays stopped until the dashboard restarts.

## CAN-only / PC mode (CLI)
    python3 -m src.tank_test_logger run --out DIR --interface socketcan --channel can0 --label x
    python3 -m src.tank_test_logger run --out DIR --interface virtual --channel t --sim-flow --duration 60
SIGUSR1 adds a marker. Ctrl-C or SIGTERM stops the run. `mark` and `stop` work in both modes.
The CLI never reads the Picomag. `--sim-flow` feeds a fake 6 L/s.

## Outputs (per run)
| file | content |
|---|---|
| `flow_mfp.csv` | `mono_s, wall_epoch, wall_iso, totalizer_l/gal, flow_lps/gpm, picomag_temp_f`, MFP LEVEL/FAST/ATTITUDE/HEALTH/HALL fields, `*_age_s`, `flow_dropped, can_frames, can_errors`; event rows `START/MARK:x/STOP` |
| `can_raw.log` | candump `-l` format `(epoch) can0 18FF1010#....`, replayable with `canplayer` or `tools/mfp_can_sim.py replay` |
| `markers.txt` | START / MARK / STOP with mono and wall time |
| `run_info.json` | counters, settings, last error |

## Offline analysis
    python3 tools/tank_test_align.py RUN/flow_mfp.csv --table tank.csv [--initial-gal 0] [--ullage-height H]
`tank.csv` holds `level_in,gallons` (header optional). Use `--ullage-height` for
distance-from-top tables such as the Mopeka file. Each LEVEL frame (by `level_seq`)
is matched to the totalizer interpolated at the frame's receive time. The script
writes `align_error.csv`, `align_summary.txt` and `align_error.png` (if matplotlib).

### Building the tank table (first test: no table exists yet)
    python3 tools/tank_test_align.py RUN/flow_mfp.csv --build-table tank.csv --initial-gal 0 --bin-in 0.5
Start from a known volume (empty = 0), fill slowly, and log the whole fill. Each LEVEL frame's
`level_in` is paired with the metered gallons at the same moment, then binned by level (median per
bin). Gallons are forced to be non-decreasing. Frames without the status OK bit are skipped
(override with `--allow-not-ok`). The output `level_in,gallons,n` can be passed as `--table` on later runs.

## PC acceptance / latency bench
    python3 tools/tank_test_bench.py --phase-s 30 --pairs 2 --interface virtual --channel bench
The bench runs the real `flow_control_loop` / `read_flow_meter` source against a mock Picomag,
alternating logger OFF and ON phases, while `tools/mfp_can_sim.py` emits beta frames.
It reports loop-period jitter percentiles.

## MFP CAN decode (authority: mfp-beta-fw mfp_can.h + mfp_core/mfp_wire.c)
All frames are DLC 8, little-endian. SA is the low byte (bench 0x10).
| frame | ID | rate | bytes |
|---|---|---|---|
| LEVEL | 0x18FF10SA | 1 Hz | [0-1] u16 settled volume 0.1 gal; [2-3] u16 level 0.001 in (FULLY corrected); [4-5] i16 flow 0.1 gpm; [6] status low byte; [7] seq |
| FAST | 0x18FF11SA | 5 Hz | [0-1] u16 fast volume 0.1 gal; [2-3] u16 z_null 0.001 in (RAW, uncorrected); [4-5] u16 slosh amplitude 0.001 in; [6-7] 0 |
| ATTITUDE | 0x18FF12SA | 1 Hz | i16 pitch 0.01 deg; i16 roll 0.01 deg; i16 temp 0.1 C; u16 vib 0.01 (telemetry only, never re-apply) |
| HEALTH_A | 0x18FF13SA | 0.2 Hz | [0] sensor_fault_map; [7] seq |
| HEALTH_B | 0x18FF14SA | 0.2 Hz | amplitude %; fit residual %; u16 uptime min; reset count; fw_rev; TEC; REC |
| HALL_FAULT | 0x18FF15SA | on raise/clear + 1 Hz round-robin | strip; sensor (0xFF = strip); code; flags b0 active, b1 test, b2 excluded; u16 count; n_active; detail |
Sentinels: u16 0xFFFF and i16 0x8000 decode as blank (no value).
Status bits: 0 OK, 1 FLOAT_STUCK, 2 FIELD_ANOMALY, 3 SENSOR_FAULT, 4 TILT_EXCESS,
5 UNCALIBRATED, 6 SG_DEFAULT, 7 NO_ATTITUDE.

## Known note
`config.py`: `IOL_PORT = 2  # Flow meter on X1 (port 0)`. The comment and the value disagree.
The logger does not touch either one.
