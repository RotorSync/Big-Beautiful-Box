"""
RotorLink config system — a faithful replication of the BLE server's config
dispatch (rotorsync_bumble.py: `process_config_command` and the `_cmd_*`
functions), reading AND writing the SAME Pi files in the EXACT same formats so
bumble and RotorLink stay consistent.

KEY SIMPLIFICATION vs. BLE: there is no MTU limit over WiFi, so we send the
WHOLE response JSON in one WebSocket message. We do NOT replicate the BLE CHUNK
framing or the zlib+base64 compression envelope. We DO keep SEMANTIC pagination
(page / total_pages / total_items / items / has_more) because the app requests
pages — but every list fits in a single message, and PAGE just re-serves a page
from the cached page set for a prior request (by cursor_request_id, or falling
back to the connection's most recent page set when no cursor is supplied, same
as bumble's _cmd_page falling back to config_response_pages).

Each method returns a plain dict = the same ConfigResponse / PaginatedResponse
JSON shape the app's parseConfigData / parseConfigResponseData already decodes.
The op + request_id are echoed so the app can correlate.

Safety:
  * bumble is the source of truth; we never modify it. We replicate its logic.
  * WRITE ops (ADD/UPDATE/DELETE sensor + calibration, SET_BMS_MAC, SELECT_TRAILER,
    WIFI_SET) modify shared Pi config files. File reads/writes match bumble's
    exact format. We do NOT trigger bumble's BLE-identity restart (that is a
    BLE-server concern); persisting the file is what keeps the two consistent.
  * Unknown ops return the same `{"ok": false, "error": "Unknown op: ..."}` bumble
    returns; one bad command never raises out of `handle`.
"""

import asyncio
import csv
import io
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from . import config

logger = logging.getLogger("rotorlink.config")

# ---------------------------------------------------------------------------
# Constants mirrored from rotorsync_bumble.py
# ---------------------------------------------------------------------------
SENSOR_CSV_HEADER = [
    "Man",
    "Trailer",
    "Tank",
    "Center Sump?",
    "Height Offset",
    "Mopeka Name in app",
    "Mopeka ID",
    "MQTT Topic for app",
    "Added to app",
]

# Default BMS identity bumble ships with. RotorLink reads the persisted MAC from
# mopeka_config.json (bms_mac) and computes the name the same way bumble does.
_DEFAULT_BMS_MAC = "A5:C2:37:31:77:C0"
_DEFAULT_BMS_NAME = "TR2-BMS"
_BMS_ENABLED = True

DEFAULT_CUSTOMER_BLE_NAME = "TrailerSync-Customer"

_PLACEHOLDER_ID = "---------------"


# ===========================================================================
# File access helpers — byte-for-byte compatible with bumble's reader/writer
# ===========================================================================
def _load_config():
    """Load mopeka_config.json (bumble: load_config)."""
    try:
        with open(config.MOPEKA_CONFIG_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"box_mode": "fleet", "assigned_trailer": None, "trailer": None}


def _save_config(cfg):
    """Write mopeka_config.json (bumble: save_config — json.dump indent=2)."""
    os.makedirs(os.path.dirname(config.MOPEKA_CONFIG_PATH), exist_ok=True)
    with open(config.MOPEKA_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def _load_sensor_csv():
    """Parse sensor CSV (bumble: load_sensor_csv).

    4 blank preamble rows, header on row 5, Man column only on Front rows —
    carry forward to Back rows.
    """
    sensors = []
    try:
        with open(config.SENSOR_CSV_PATH, "r", newline="") as f:
            reader = csv.reader(f)
            for _ in range(4):
                next(reader)
            header = next(reader)
            current_man = ""
            for row in reader:
                if not row or len(row) < 2 or not row[1].strip():
                    continue
                d = {}
                for i, h in enumerate(header):
                    d[h.strip()] = row[i].strip() if i < len(row) else ""
                if d.get("Man"):
                    current_man = d["Man"]
                else:
                    d["Man"] = current_man
                sensors.append(d)
    except FileNotFoundError:
        logger.warning("Sensor CSV not found: %s", config.SENSOR_CSV_PATH)
    return sensors


def _save_sensor_csv(sensors):
    """Write sensors back to CSV (bumble: save_sensor_csv).

    Preserves format: 4 blank rows, header, data; Man collapsed when repeated.
    """
    with open(config.SENSOR_CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        for _ in range(4):
            writer.writerow([""] * len(SENSOR_CSV_HEADER))
        writer.writerow(SENSOR_CSV_HEADER)
        last_man = ""
        for s in sensors:
            row = []
            for h in SENSOR_CSV_HEADER:
                val = s.get(h, "")
                if h == "Man":
                    if val == last_man:
                        row.append("")
                    else:
                        row.append(val)
                        last_man = val
                else:
                    row.append(val)
            writer.writerow(row)


def _safe_calibration_profile_key(value):
    key = str(value or "").strip().lower().replace("_", "-")
    return "".join(ch for ch in key if ch.isalnum() or ch == "-")


def _calibration_profile_key_for_box_tank(tank):
    cfg = _load_config()
    mode = _normalize_box_mode(cfg.get("box_mode"))
    trailer = _get_assigned_trailer(cfg)
    tank = str(tank or "").strip().lower()
    tank = "back" if tank.startswith("back") else "front"
    if mode == "fleet" and trailer not in (None, ""):
        return _safe_calibration_profile_key(f"trailer-{trailer}-{tank}")
    return f"customer-{tank}"


def _calibration_csv_path_for_cmd(cmd=None):
    """bumble: _calibration_csv_path_for_cmd — pick the profile CSV or default."""
    cmd = cmd or {}
    profile = cmd.get("profile")
    if not profile and cmd.get("tank"):
        profile = _calibration_profile_key_for_box_tank(cmd.get("tank"))
    profile = _safe_calibration_profile_key(profile)
    if profile:
        os.makedirs(config.CALIBRATION_PROFILE_DIR, exist_ok=True)
        return os.path.join(config.CALIBRATION_PROFILE_DIR, f"{profile}.csv"), profile
    return config.CALIBRATION_CSV_PATH, ""


def _load_calibration_csv(path):
    """Load calibration CSV (bumble: load_calibration_csv)."""
    points = []
    try:
        with open(path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                points.append(
                    {
                        "tank_level_in": float(row["Tank Level (in)"]),
                        "gallons": float(row["Gallons"]),
                        "tank_size": float(row["Tank Size (gal)"]),
                    }
                )
    except FileNotFoundError:
        logger.warning("Calibration CSV not found: %s", path)
    return points


def _save_calibration_csv(points, path):
    """Write calibration points (bumble: save_calibration_csv).

    Sorted descending by tank level.
    """
    points.sort(key=lambda p: p["tank_level_in"], reverse=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Tank Level (in)", "Gallons", "Tank Size (gal)"])
        for p in points:
            writer.writerow([p["tank_level_in"], p["gallons"], p["tank_size"]])


# ===========================================================================
# Config-value helpers (bumble: matching private functions)
# ===========================================================================
def _normalize_ble_mac(value):
    mac = str(value or "").strip().upper().replace("-", ":")
    parts = mac.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        return None
    if any(any(ch not in "0123456789ABCDEF" for ch in part) for part in parts):
        return None
    return ":".join(parts)


def _normalize_box_mode(value):
    mode = str(value or "fleet").strip().lower()
    return "customer" if mode == "customer" else "fleet"


def _get_assigned_trailer(cfg=None):
    cfg = cfg or _load_config()
    return cfg.get("assigned_trailer", cfg.get("trailer"))


def _box_mode_uses_trailer_list(cfg=None):
    cfg = cfg or _load_config()
    return _normalize_box_mode(cfg.get("box_mode")) == "fleet"


def _bms_mac(cfg=None):
    cfg = cfg or _load_config()
    saved = _normalize_ble_mac(cfg.get("bms_mac"))
    return saved or _DEFAULT_BMS_MAC


def _compute_bms_name(cfg=None):
    cfg = cfg or _load_config()
    explicit_name = str(cfg.get("bms_name") or "").strip()
    if explicit_name:
        return explicit_name
    trailer = _get_assigned_trailer(cfg)
    if trailer not in (None, ""):
        return f"TR{trailer}-BMS"
    return _DEFAULT_BMS_NAME


def _current_trailer_info():
    """bumble: _current_trailer_info — the TRAILER characteristic payload."""
    cfg = _load_config()
    mode = _normalize_box_mode(cfg.get("box_mode"))
    trailer_num = _get_assigned_trailer(cfg)
    if mode != "fleet":
        return {"box_mode": mode, "trailer": None, "enabled": False}
    if trailer_num is None:
        return {"box_mode": mode, "trailer": None, "enabled": True}

    sensors = _load_sensor_csv()
    trailer_sensors = [s for s in sensors if str(s.get("Trailer")) == str(trailer_num)]
    front = next((s for s in trailer_sensors if s.get("Tank") == "Front"), None)
    back = next((s for s in trailer_sensors if s.get("Tank") == "Back"), None)
    man = trailer_sensors[0].get("Man", "") if trailer_sensors else ""

    def get_offset(sensor):
        if sensor and sensor.get("Height Offset"):
            try:
                return float(sensor["Height Offset"])
            except ValueError:
                pass
        return 0.0

    return {
        "box_mode": mode,
        "enabled": True,
        "trailer": trailer_num,
        "man": man,
        "front": {
            "id": front["Mopeka ID"] if front else _PLACEHOLDER_ID,
            "offset": get_offset(front),
        },
        "back": {
            "id": back["Mopeka ID"] if back else _PLACEHOLDER_ID,
            "offset": get_offset(back),
        },
    }


def _apply_trailer(trailer_num):
    """bumble: apply_trailer — persist trailer selection to mopeka_config.json.

    Replicates the FILE write bumble does (assigned_trailer/trailer/front_id/
    back_id/display_name). We do NOT touch bumble's scanner globals or trigger a
    BLE-identity restart — bumble re-reads the file and self-restarts on its own
    cadence; what matters for consistency is the persisted file.
    """
    sensors = _load_sensor_csv()
    trailer_sensors = [s for s in sensors if str(s.get("Trailer")) == str(trailer_num)]
    if not trailer_sensors:
        return None

    front = next((s for s in trailer_sensors if s.get("Tank") == "Front"), None)
    back = next((s for s in trailer_sensors if s.get("Tank") == "Back"), None)
    man = trailer_sensors[0].get("Man", "")

    front_id = front["Mopeka ID"] if front else _PLACEHOLDER_ID
    back_id = back["Mopeka ID"] if back else _PLACEHOLDER_ID

    def parse_offset(sensor):
        if sensor and sensor.get("Height Offset"):
            try:
                return float(sensor["Height Offset"])
            except ValueError:
                pass
        return 0.0

    front_offset = parse_offset(front)
    back_offset = parse_offset(back)

    cfg = _load_config()
    cfg.update(
        {
            "box_mode": _normalize_box_mode(cfg.get("box_mode")),
            "assigned_trailer": trailer_num,
            "trailer": trailer_num,
            "front_id": front_id,
            "back_id": back_id,
            "display_name": f"TrailerSync-TR{trailer_num}",
        }
    )
    _save_config(cfg)

    return {
        "trailer": trailer_num,
        "man": man,
        "front": {"id": front_id, "offset": front_offset},
        "back": {"id": back_id, "offset": back_offset},
    }


# ===========================================================================
# Pagination (bumble: paginate_response + _set_paginated_config_response)
# ===========================================================================
def _paginate_response(items, page_size_bytes=450):
    """bumble: paginate_response — split items into page envelopes."""
    if not items:
        return [
            {"page": 1, "total_pages": 1, "total_items": 0, "items": []}
        ]
    pages_items = []
    current_page = []
    overhead = 60
    current_size = overhead
    for item in items:
        item_json = json.dumps(item, separators=(",", ":"))
        item_size = len(item_json) + 1
        if current_size + item_size > page_size_bytes and current_page:
            pages_items.append(current_page)
            current_page = []
            current_size = overhead
        current_page.append(item)
        current_size += item_size
    if current_page:
        pages_items.append(current_page)
    total_pages = len(pages_items)
    total_items = len(items)
    return [
        {
            "page": i + 1,
            "total_pages": total_pages,
            "total_items": total_items,
            "items": page_items,
        }
        for i, page_items in enumerate(pages_items)
    ]


# ===========================================================================
# History helpers (bumble: mopeka + fill history readers)
# ===========================================================================
def _history_float(value):
    try:
        if value in (None, ""):
            return None
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _history_int(value):
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _history_timestamp_epoch(value):
    try:
        return int(time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S")))
    except (TypeError, ValueError):
        return None


def _clamped_history_window(cmd, *, default_hours=12.0):
    now = time.time()
    min_since = now - config.HISTORY_RETENTION_SECONDS
    try:
        until = float(cmd.get("until", now))
    except (TypeError, ValueError):
        until = now
    until = max(min(until, now + 60.0), min_since)

    if "since" in cmd:
        try:
            since = float(cmd.get("since"))
        except (TypeError, ValueError):
            since = until - (default_hours * 3600.0)
    else:
        try:
            hours = float(cmd.get("hours", default_hours))
        except (TypeError, ValueError):
            hours = default_hours
        hours = max(1.0, min(float(config.HISTORY_RETENTION_SECONDS) / 3600.0, hours))
        since = until - (hours * 3600.0)

    since = max(min(since, until), min_since)
    return since, until


def _history_named_field(parts, name):
    prefix = f"{name}:"
    for part in parts:
        text = part.strip()
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return ""


def _parse_float_token(value):
    try:
        cleaned = (
            str(value)
            .replace("gal", "")
            .replace("GPM", "")
            .replace("F", "")
            .replace("s", "")
            .strip()
        )
        if not cleaned:
            return None
        return round(float(cleaned), 3)
    except (TypeError, ValueError):
        return None


def _fill_history_item_from_line(line):
    parts = line.strip().split("|")
    if len(parts) < 3:
        return None
    timestamp = _history_timestamp_epoch(parts[0].strip())
    if timestamp is None:
        return None
    requested = _parse_float_token(_history_named_field(parts, "Requested"))
    actual = _parse_float_token(_history_named_field(parts, "Actual"))
    if requested is None or actual is None:
        return None
    shutoff_type = ""
    for part in parts[3:]:
        text = part.strip()
        if text.lower().startswith(("auto", "manual")):
            shutoff_type = text
            break
    return {
        "t": timestamp,
        "rq": requested,
        "ag": actual,
        "df": round(actual - requested, 3),
        "st": shutoff_type,
        "tf": _parse_float_token(_history_named_field(parts, "Temp")),
        "s2t": _parse_float_token(_history_named_field(parts, "StopToThumb")),
        # Flow window epochs (None when the box didn't record them — the app
        # flags such records loudly). Same fields as rotorsync_bumble's parser:
        # loads fetched over WiFi must not lose pilot/flow attribution.
        "fs": _history_timestamp_epoch(_history_named_field(parts, "FlowStart")),
        "fe": _history_timestamp_epoch(_history_named_field(parts, "FlowEnd")),
        "pl": _history_named_field(parts, "Pilot").strip() or None,
    }


def _history_newest_first_requested(cmd):
    value = cmd.get("newest_first")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "desc",
        "newest",
        "newest_first",
    ):
        return True
    order = str(cmd.get("order") or "").strip().lower()
    return order in ("desc", "newest", "newest_first")


def _mopeka_history_paths():
    path = Path(config.MOPEKA_HISTORY_LOG_PATH)
    paths = []
    if path.exists():
        paths.append(path)
    for rotated_path in sorted(
        path.parent.glob(f"{path.name}.*"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    ):
        if rotated_path == path:
            continue
        if (
            rotated_path.name.endswith(".zst")
            or rotated_path.suffix in ("", ".1")
            or rotated_path.name.endswith(".csv.1")
        ):
            paths.append(rotated_path)
    return paths[:120]


def _read_mopeka_history_rows(path):
    try:
        if path.name.endswith(".zst"):
            result = subprocess.run(
                ["zstdcat", str(path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                logger.warning("Mopeka history read skipped %s: %s", path, detail)
                return []
            return list(csv.DictReader(io.StringIO(result.stdout)))
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        logger.warning("Mopeka history read skipped %s: %s", path, e)
        return []


def _load_mopeka_history_items(cmd):
    since, until = _clamped_history_window(cmd)
    items = []
    seen = set()
    for path in _mopeka_history_paths():
        for row in _read_mopeka_history_rows(path):
            timestamp = _history_timestamp_epoch(row.get("timestamp"))
            if timestamp is None or timestamp < since or timestamp > until:
                continue
            item = {
                "t": timestamp,
                "r": row.get("reason") or "",
                "fg": _history_float(row.get("front_gal")),
                "bg": _history_float(row.get("back_gal")),
                "fmm": _history_float(row.get("front_mm")),
                "bmm": _history_float(row.get("back_mm")),
                "fin": _history_float(row.get("front_in")),
                "bin": _history_float(row.get("back_in")),
                "fq": _history_int(row.get("front_quality")),
                "bq": _history_int(row.get("back_quality")),
            }
            key = (item["t"], item["fg"], item["bg"], item["fmm"], item["bmm"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return sorted(
        items, key=lambda item: item["t"], reverse=_history_newest_first_requested(cmd)
    )


def _load_fill_history_items(cmd):
    if not os.path.exists(config.FILL_HISTORY_LOG_PATH):
        return []
    since, until = _clamped_history_window(cmd)
    items = []
    with open(config.FILL_HISTORY_LOG_PATH) as f:
        for line in f:
            item = _fill_history_item_from_line(line)
            if not item:
                continue
            if item["t"] < since or item["t"] > until:
                continue
            items.append(item)
    return sorted(items, key=lambda item: item["t"])


# ===========================================================================
# ConfigHandler — the dispatcher
# ===========================================================================
class ConfigHandler:
    """Replicates bumble's config dispatch over WiFi (whole-JSON responses).

    `dashboard` is the async DashboardClient used for WIFI_SET / WIFI_STATUS
    (the only ops that talk to the :9999 socket, exactly as bumble does).
    """

    def __init__(self, dashboard):
        self.dashboard = dashboard
        # Cached page sets keyed by the originating request_id, so a PAGE op can
        # re-serve a page from a prior list request (mirrors bumble's
        # config_response_pages_by_request). Bounded to avoid unbounded growth.
        self._pages_by_request = {}
        # Most recent page set produced by ANY paginated command on this
        # connection — the fallback for cursor-less PAGE requests (app builds
        # <=81 never send cursor_request_id). Mirrors bumble's
        # config_response_pages, which is swapped to a per-connection buffer in
        # process_config_command_for_connection; rotorlink has one handler per
        # connection, so a per-handler attribute is the same scope. Like
        # bumble, every non-PAGE command clears it and every paginated list
        # command sets it.
        self._last_pages = []

    def _store_pages(self, request_id, pages):
        if request_id:
            self._pages_by_request[request_id] = pages
            if len(self._pages_by_request) > 24:
                for stale in list(self._pages_by_request.keys())[:-24]:
                    self._pages_by_request.pop(stale, None)

    def _paginated(self, items, *, op, request_id, page_size_bytes=450, page=1):
        """Build the page-1 (or requested-page) envelope, cache the full set,
        and add op/request_id/has_more — the whole JSON in one message."""
        pages = _paginate_response(items, page_size_bytes=page_size_bytes)
        for p in pages:
            p["op"] = op
            if request_id is not None:
                p["request_id"] = request_id
        self._store_pages(request_id, pages)
        self._last_pages = pages
        idx = max(1, min(page, len(pages))) - 1
        envelope = dict(pages[idx])
        envelope["has_more"] = (idx + 1) < len(pages)
        return envelope

    async def handle(self, cmd):
        """Dispatch one config command dict, return the response dict.

        Never raises: a bad command yields an `{"ok": false, "error": ...}` body
        with the op/request_id echoed, exactly like bumble.
        """
        if not isinstance(cmd, dict):
            return {"ok": False, "error": "command must be a JSON object"}
        op = cmd.get("op", "")
        request_id = cmd.get("request_id")
        # bumble parity: every command except PAGE resets the connection's
        # last page set (paginated list handlers then repopulate it via
        # _paginated). PAGE only reads it.
        if op != "PAGE":
            self._last_pages = []
        logger.info("config command: %s", op)
        try:
            return await self._dispatch(op, cmd, request_id)
        except Exception as e:
            logger.warning("config command error (%s): %s", op, e)
            return {"ok": False, "error": str(e), "request_id": request_id, "op": op}

    async def _dispatch(self, op, cmd, request_id):
        if op == "WIFI_SET":
            return await self._wifi_set(cmd, request_id)
        if op == "WIFI_STATUS":
            return await self._wifi_status(cmd, request_id)
        if op == "GET_BMS":
            return self._get_bms(request_id)
        if op == "SET_BMS_MAC":
            return self._set_bms_mac(cmd, request_id)
        if op == "GET_TRAILER":
            return self._get_trailer(request_id)
        if op == "SELECT_TRAILER":
            return self._select_trailer(cmd, request_id)
        if op == "LIST_TRAILERS":
            return self._list_trailers(cmd, request_id)
        if op == "LIST_SENSORS":
            return self._list_sensors(cmd, request_id)
        if op == "ADD_SENSOR":
            return self._add_sensor(cmd, request_id)
        if op == "UPDATE_SENSOR":
            return self._update_sensor(cmd, request_id)
        if op == "DELETE_SENSOR":
            return self._delete_sensor(cmd, request_id)
        if op == "LIST_CALIBRATION":
            return self._list_calibration(cmd, request_id)
        if op == "ADD_CALIBRATION":
            return self._add_calibration(cmd, request_id)
        if op == "UPDATE_CALIBRATION":
            return self._update_calibration(cmd, request_id)
        if op == "DELETE_CALIBRATION":
            return self._delete_calibration(cmd, request_id)
        if op == "GET_MOPEKA_HISTORY":
            return self._get_mopeka_history(cmd, request_id)
        if op == "GET_FILL_HISTORY":
            return self._get_fill_history(cmd, request_id)
        if op == "PAGE":
            return self._page(cmd, request_id)
        # Unknown op — same body bumble returns. (Note: CONFIG_META is NOT
        # implemented by bumble either, so it lands here; the app treats the
        # unknown-op reply as "CONFIG_META unsupported" and carries on.)
        return {"ok": False, "error": f"Unknown op: {op}", "request_id": request_id, "op": op}

    # --- trailer config ----------------------------------------------------
    def _get_trailer(self, request_id):
        payload = {"ok": True, "op": "GET_TRAILER", "current": _current_trailer_info()}
        if request_id is not None:
            payload["request_id"] = request_id
        return payload

    def _select_trailer(self, cmd, request_id):
        if not _box_mode_uses_trailer_list():
            return {
                "ok": False,
                "op": "SELECT_TRAILER",
                "request_id": request_id,
                "error": "Trailer selection disabled in customer mode",
            }
        try:
            trailer_num = int(cmd.get("trailer"))
        except Exception:
            return {
                "ok": False,
                "op": "SELECT_TRAILER",
                "request_id": request_id,
                "error": "Invalid trailer",
            }
        result = _apply_trailer(trailer_num)
        if result is None:
            return {
                "ok": False,
                "op": "SELECT_TRAILER",
                "request_id": request_id,
                "error": f"Trailer {trailer_num} not found",
            }
        return {
            "ok": True,
            "op": "SELECT_TRAILER",
            "request_id": request_id,
            "current": result,
        }

    def _list_trailers(self, cmd, request_id):
        if not _box_mode_uses_trailer_list():
            return {
                "ok": False,
                "op": "LIST_TRAILERS",
                "request_id": request_id,
                "error": "Trailer list disabled in customer mode",
            }
        sensors = _load_sensor_csv()
        trailers = {}
        for s in sensors:
            t = s.get("Trailer", "")
            if t not in trailers:
                trailers[t] = {
                    "trailer": int(t) if t.isdigit() else t,
                    "man": s.get("Man", ""),
                }
            tank = s.get("Tank", "")
            mid = s.get("Mopeka ID", "")
            if tank == "Front":
                trailers[t]["front"] = mid
            elif tank == "Back":
                trailers[t]["back"] = mid
        items = sorted(trailers.values(), key=lambda x: x.get("trailer", 0))
        return self._paginated(
            items, op="LIST_TRAILERS", request_id=request_id, page=cmd.get("page", 1)
        )

    # --- sensors -----------------------------------------------------------
    def _list_sensors(self, cmd, request_id):
        sensors = _load_sensor_csv()
        trailer_filter = cmd.get("trailer")
        if trailer_filter is not None:
            sensors = [
                s for s in sensors if str(s.get("Trailer")) == str(trailer_filter)
            ]
        items = []
        for s in sensors:
            items.append(
                {
                    "man": s.get("Man", ""),
                    "trailer": int(s["Trailer"])
                    if s.get("Trailer", "").isdigit()
                    else s.get("Trailer", ""),
                    "tank": s.get("Tank", ""),
                    "id": s.get("Mopeka ID", ""),
                    "offset": s.get("Height Offset", ""),
                    "name": s.get("Mopeka Name in app", ""),
                }
            )
        return self._paginated(
            items, op="LIST_SENSORS", request_id=request_id, page=cmd.get("page", 1)
        )

    _SENSOR_FIELD_MAP = {
        "man": "Man",
        "trailer": "Trailer",
        "tank": "Tank",
        "center_sump": "Center Sump?",
        "height_offset": "Height Offset",
        "name": "Mopeka Name in app",
        "id": "Mopeka ID",
        "mqtt_topic": "MQTT Topic for app",
        "added_to_app": "Added to app",
    }

    def _add_sensor(self, cmd, request_id):
        data = cmd.get("data")
        if not data:
            return {"ok": False, "op": "ADD_SENSOR", "request_id": request_id, "error": "Missing data field"}
        sensors = _load_sensor_csv()
        new_sensor = {}
        for json_key, csv_key in self._SENSOR_FIELD_MAP.items():
            if json_key in data:
                new_sensor[csv_key] = str(data[json_key])
        if "Mopeka ID" not in new_sensor or "Trailer" not in new_sensor or "Tank" not in new_sensor:
            return {"ok": False, "op": "ADD_SENSOR", "request_id": request_id, "error": "Required: id, trailer, tank"}
        sensors.append(new_sensor)
        tank_order = {"Front": 0, "Back": 1}
        sensors.sort(
            key=lambda s: (
                int(s["Trailer"]) if s.get("Trailer", "").isdigit() else 999,
                tank_order.get(s.get("Tank", ""), 2),
            )
        )
        _save_sensor_csv(sensors)
        return {"ok": True, "op": "ADD_SENSOR", "request_id": request_id, "id": new_sensor.get("Mopeka ID", "")}

    def _update_sensor(self, cmd, request_id):
        sensor_id = cmd.get("id")
        data = cmd.get("data")
        if not sensor_id or not data:
            return {"ok": False, "op": "UPDATE_SENSOR", "request_id": request_id, "error": "Required: id, data"}
        sensors = _load_sensor_csv()
        found = False
        for s in sensors:
            if s.get("Mopeka ID") == sensor_id:
                for json_key, csv_key in self._SENSOR_FIELD_MAP.items():
                    if json_key in data:
                        s[csv_key] = str(data[json_key])
                found = True
                break
        if not found:
            return {"ok": False, "op": "UPDATE_SENSOR", "request_id": request_id, "error": f"Sensor {sensor_id} not found"}
        _save_sensor_csv(sensors)
        return {"ok": True, "op": "UPDATE_SENSOR", "request_id": request_id, "id": sensor_id}

    def _delete_sensor(self, cmd, request_id):
        sensor_id = cmd.get("id")
        if not sensor_id:
            return {"ok": False, "op": "DELETE_SENSOR", "request_id": request_id, "error": "Required: id"}
        sensors = _load_sensor_csv()
        original_len = len(sensors)
        sensors = [s for s in sensors if s.get("Mopeka ID") != sensor_id]
        if len(sensors) == original_len:
            return {"ok": False, "op": "DELETE_SENSOR", "request_id": request_id, "error": f"Sensor {sensor_id} not found"}
        _save_sensor_csv(sensors)
        return {"ok": True, "op": "DELETE_SENSOR", "request_id": request_id, "id": sensor_id}

    # --- calibration -------------------------------------------------------
    def _list_calibration(self, cmd, request_id):
        path, _profile = _calibration_csv_path_for_cmd(cmd or {})
        points = _load_calibration_csv(path)
        items = [
            {
                "index": i,
                "tank_level_in": p["tank_level_in"],
                "gallons": p["gallons"],
                "tank_size": p["tank_size"],
            }
            for i, p in enumerate(points)
        ]
        return self._paginated(
            items, op="LIST_CALIBRATION", request_id=request_id, page=cmd.get("page", 1)
        )

    def _add_calibration(self, cmd, request_id):
        data = cmd.get("data")
        if not data:
            return {"ok": False, "op": "ADD_CALIBRATION", "request_id": request_id, "error": "Missing data field"}
        if "tank_level_in" not in data or "gallons" not in data:
            return {"ok": False, "op": "ADD_CALIBRATION", "request_id": request_id, "error": "Required: tank_level_in, gallons"}
        path, profile = _calibration_csv_path_for_cmd(cmd)
        points = _load_calibration_csv(path)
        points.append(
            {
                "tank_level_in": float(data["tank_level_in"]),
                "gallons": float(data["gallons"]),
                "tank_size": float(data.get("tank_size", 1070.0)),
            }
        )
        _save_calibration_csv(points, path)
        return {"ok": True, "op": "ADD_CALIBRATION", "request_id": request_id, "profile": profile or None}

    def _update_calibration(self, cmd, request_id):
        index = cmd.get("index")
        data = cmd.get("data")
        if index is None or not data:
            return {"ok": False, "op": "UPDATE_CALIBRATION", "request_id": request_id, "error": "Required: index, data"}
        path, profile = _calibration_csv_path_for_cmd(cmd)
        points = _load_calibration_csv(path)
        if index < 0 or index >= len(points):
            return {"ok": False, "op": "UPDATE_CALIBRATION", "request_id": request_id, "error": f"Index {index} out of range (0-{len(points) - 1})"}
        for key in ("tank_level_in", "gallons", "tank_size"):
            if key in data:
                points[index][key] = float(data[key])
        _save_calibration_csv(points, path)
        return {"ok": True, "op": "UPDATE_CALIBRATION", "request_id": request_id, "index": index, "profile": profile or None}

    def _delete_calibration(self, cmd, request_id):
        index = cmd.get("index")
        if index is None:
            return {"ok": False, "op": "DELETE_CALIBRATION", "request_id": request_id, "error": "Required: index"}
        path, profile = _calibration_csv_path_for_cmd(cmd)
        points = _load_calibration_csv(path)
        if index < 0 or index >= len(points):
            return {"ok": False, "op": "DELETE_CALIBRATION", "request_id": request_id, "error": f"Index {index} out of range (0-{len(points) - 1})"}
        points.pop(index)
        _save_calibration_csv(points, path)
        return {"ok": True, "op": "DELETE_CALIBRATION", "request_id": request_id, "index": index, "profile": profile or None}

    # --- BMS ---------------------------------------------------------------
    def _get_bms(self, request_id):
        cfg = _load_config()
        payload = {
            "ok": True,
            "op": "GET_BMS",
            "bms": {
                "mac": _bms_mac(cfg),
                "name": _compute_bms_name(cfg),
                "enabled": bool(_BMS_ENABLED),
            },
        }
        if request_id is not None:
            payload["request_id"] = request_id
        return payload

    def _set_bms_mac(self, cmd, request_id):
        mac = _normalize_ble_mac(cmd.get("mac"))
        if not mac:
            return {"ok": False, "op": "SET_BMS_MAC", "request_id": request_id, "error": "Invalid MAC address"}
        cfg = _load_config()
        cfg["bms_mac"] = mac
        _save_config(cfg)
        return {
            "ok": True,
            "op": "SET_BMS_MAC",
            "request_id": request_id,
            "bms": {"mac": mac, "name": _compute_bms_name(cfg), "enabled": bool(_BMS_ENABLED)},
        }

    # --- history -----------------------------------------------------------
    def _get_mopeka_history(self, cmd, request_id):
        items = _load_mopeka_history_items(cmd)
        return self._paginated(
            items,
            op="GET_MOPEKA_HISTORY",
            request_id=request_id,
            page_size_bytes=8192,
            page=cmd.get("page", 1),
        )

    def _get_fill_history(self, cmd, request_id):
        items = _load_fill_history_items(cmd)
        return self._paginated(
            items,
            op="GET_FILL_HISTORY",
            request_id=request_id,
            page_size_bytes=450,
            page=cmd.get("page", 1),
        )

    # --- pagination cursor -------------------------------------------------
    def _page(self, cmd, request_id):
        page = cmd.get("page", 1)
        cursor_request_id = cmd.get("cursor_request_id")
        # bumble parity (_cmd_page): with a cursor, serve from that request's
        # cached set; cursor-less (app builds <=81), fall back to the last
        # page set produced on this connection.
        pages = (
            self._pages_by_request.get(cursor_request_id)
            if cursor_request_id
            else self._last_pages
        )
        if not pages:
            return {"ok": False, "op": "PAGE", "request_id": request_id, "error": "No paginated data available"}
        if page < 1 or page > len(pages):
            return {"ok": False, "op": "PAGE", "request_id": request_id, "error": f"Page {page} out of range (1-{len(pages)})"}
        envelope = dict(pages[page - 1])
        if cursor_request_id:
            envelope["cursor_request_id"] = cursor_request_id
        if request_id is not None:
            envelope["request_id"] = request_id
        envelope["has_more"] = page < len(pages)
        return envelope

    # --- WiFi (forwarded to the dashboard, exactly like bumble) ------------
    async def _wifi_set(self, cmd, request_id):
        ssid = str(cmd.get("ssid", "")).strip()
        password = str(cmd.get("password", ""))
        hidden = bool(cmd.get("hidden", False))
        if not ssid:
            return {"ok": False, "op": "WIFI_SET", "request_id": request_id, "error": "Missing ssid"}
        if len(ssid) > 64:
            return {"ok": False, "op": "WIFI_SET", "request_id": request_id, "error": "SSID too long"}
        if len(password) > 128:
            return {"ok": False, "op": "WIFI_SET", "request_id": request_id, "error": "Password too long"}
        payload = {"ssid": ssid, "password": password, "hidden": hidden}
        resp = await self.dashboard.send_command(
            f"WIFI_SET:{json.dumps(payload, separators=(',', ':'))}"
        )
        if resp and resp.startswith("WIFI_OK:"):
            try:
                data = json.loads(resp.split(":", 1)[1])
            except Exception:
                data = {"ssid": ssid}
            data["ok"] = True
            data["op"] = "WIFI_SET"
            if request_id is not None:
                data["request_id"] = request_id
            return data
        return {"ok": False, "op": "WIFI_SET", "request_id": request_id, "error": _wifi_code_from_response(resp)}

    async def _wifi_status(self, cmd, request_id):
        resp = await self.dashboard.send_command("WIFI_STATUS")
        if resp and resp.startswith("WIFI_STATUS:"):
            try:
                data = json.loads(resp.split(":", 1)[1])
            except Exception:
                data = {"ok": False, "error": "PARSE_ERROR"}
            data["op"] = "WIFI_STATUS"
            if request_id is not None:
                data["request_id"] = request_id
            return data
        return {"ok": False, "op": "WIFI_STATUS", "request_id": request_id, "error": "NO_RESPONSE"}


def _wifi_code_from_response(resp):
    """bumble: _wifi_code_from_response."""
    if not resp:
        return "NO_RESPONSE"
    if resp.startswith("WIFI_OK:"):
        return "OK"
    if resp.startswith("WIFI_ERR:"):
        try:
            data = json.loads(resp.split(":", 1)[1])
            return data.get("code", "UNKNOWN")
        except Exception:
            return "UNKNOWN"
    return "BAD_RESPONSE"
