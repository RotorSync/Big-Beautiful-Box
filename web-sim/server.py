#!/usr/bin/env python3
"""Accurate browser workbench backend for the BBB dashboard GUI."""

from __future__ import annotations

import json
import mimetypes
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src.calculations import calculate_trigger_threshold, gpm_to_l_per_s  # noqa: E402


FLOW_STOPPED_GPM = config.FLOW_STOPPED_THRESHOLD * config.LITERS_PER_SEC_TO_GPM
NEW_FILL_GPM = config.NEW_FILL_CYCLE_THRESHOLD * config.LITERS_PER_SEC_TO_GPM


@dataclass
class GlideStop:
    reason: str
    start_time: float
    start_flow_gpm: float
    duration: float = 3.0


@dataclass
class PendingFill:
    actual_gallons: float
    requested_gallons: float
    shutoff_type: str
    flow_gpm: float
    trigger_threshold: float


@dataclass
class BBBWorkbenchModel:
    requested_gallons: float = config.REQUESTED_GALLONS
    fill_requested_gallons: float = config.REQUESTED_GALLONS
    mix_requested_gallons: float = 40.0
    current_mode: str = "fill"
    actual_gallons: float = 0.0
    flow_gpm: float = 0.0
    daily_total: float = 0.0
    last_loads_gallons: list[float] = field(default_factory=list)
    iol_connected: bool = True
    serial_connected: bool = True
    tanks_ok: bool = False
    bms_soc: float | None = None
    bms_voltage: float | None = None
    override_mode: bool = False
    colors_are_green: bool = False
    thumbs_visible: bool = False
    thumbs_width: float = 38.0
    glide: GlideStop | None = None
    pending_fill: PendingFill | None = None
    last_alert_triggered: bool = False
    auto_shutoff_latched: bool = False
    was_flowing: bool = False
    new_fill_flow_started_at: float | None = None
    new_fill_cycle_cleared: bool = False
    last_trigger_flow_gpm: float = 0.0
    last_trigger_threshold: float = 0.0
    last_trigger_actual: float = 0.0
    recent_flow_rates_gpm: deque[float] = field(default_factory=lambda: deque(maxlen=config.FLOW_AVERAGING_SAMPLES))
    last_tick: float = field(default_factory=time.monotonic)
    last_message: str = ""

    def _threshold_for_gpm(self, gpm: float) -> float:
        return calculate_trigger_threshold(gpm_to_l_per_s(gpm))

    def _smoothed_flow_gpm(self) -> float:
        if not self.recent_flow_rates_gpm:
            return self.flow_gpm
        return sum(self.recent_flow_rates_gpm) / len(self.recent_flow_rates_gpm)

    def tick(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self.last_tick)
        self.last_tick = now

        if self.glide:
            progress = min((now - self.glide.start_time) / self.glide.duration, 1.0)
            self.flow_gpm = self.glide.start_flow_gpm * (1.0 - progress)
            if progress >= 1.0:
                self.flow_gpm = 0.0
                self.glide = None

        if self.iol_connected:
            self.actual_gallons += (self.flow_gpm / 60.0) * elapsed

        is_flowing = self.flow_gpm >= FLOW_STOPPED_GPM
        is_new_fill_flowing = self.iol_connected and self.flow_gpm >= NEW_FILL_GPM

        if is_new_fill_flowing:
            if self.new_fill_flow_started_at is None:
                self.new_fill_flow_started_at = now
        else:
            self.new_fill_flow_started_at = None
            self.new_fill_cycle_cleared = False

        if is_flowing:
            self.recent_flow_rates_gpm.append(self.flow_gpm)

        if self.was_flowing and not is_flowing:
            shutoff_type = "Auto" if self.auto_shutoff_latched else "Manual"
            if shutoff_type == "Auto" and self.last_trigger_flow_gpm > 0:
                flow_gpm = self.last_trigger_flow_gpm
                threshold = self.last_trigger_threshold
            else:
                flow_gpm = self._smoothed_flow_gpm()
                threshold = self._threshold_for_gpm(flow_gpm)
            self.pending_fill = PendingFill(
                actual_gallons=self.actual_gallons,
                requested_gallons=self.requested_gallons,
                shutoff_type=shutoff_type,
                flow_gpm=flow_gpm,
                trigger_threshold=threshold,
            )
            self.last_message = (
                f"Fill complete: requested {self.requested_gallons:.1f}, "
                f"actual {self.actual_gallons:.1f}, {shutoff_type}"
            )

        if not self.was_flowing and is_flowing:
            self.colors_are_green = False
            self.last_alert_triggered = False
            self.auto_shutoff_latched = False
            self.new_fill_cycle_cleared = False
            self.last_trigger_flow_gpm = 0.0
            self.last_trigger_threshold = 0.0
            self.last_trigger_actual = 0.0
            self.recent_flow_rates_gpm.clear()
            self.last_message = "New fill cycle started"

        if (
            self.new_fill_flow_started_at is not None
            and not self.new_fill_cycle_cleared
            and now - self.new_fill_flow_started_at >= config.NEW_FILL_CYCLE_HOLD_SECONDS
        ):
            self.thumbs_visible = False
            self.pending_fill = None
            self.new_fill_cycle_cleared = True
            self.last_message = "Sustained high-flow fill cycle: cleared old thumbs/pending fill"

        self.was_flowing = is_flowing

        threshold = self._threshold_for_gpm(self._smoothed_flow_gpm())
        if (
            is_flowing
            and not self.override_mode
            and self.iol_connected
            and not self.last_alert_triggered
            and self.actual_gallons >= self.requested_gallons - threshold
        ):
            self.last_alert_triggered = True
            self.auto_shutoff_latched = True
            self.last_trigger_flow_gpm = self._smoothed_flow_gpm()
            self.last_trigger_threshold = threshold
            self.last_trigger_actual = self.actual_gallons
            self.start_glide_stop("auto-stop")
            self.last_message = (
                f"Auto-stop: flow {self.last_trigger_flow_gpm:.1f} GPM, "
                f"threshold {threshold:.2f} gal"
            )

    def set_flow(self, gpm: float) -> None:
        self.tick()
        gpm = max(0.0, float(gpm))
        if gpm <= 0 and self.flow_gpm > 0:
            self.start_glide_stop("flow stop")
            return
        self.glide = None
        self.flow_gpm = gpm
        self.colors_are_green = False
        self.last_message = f"Flow set to {gpm:.0f} GPM"

    def start_glide_stop(self, reason: str) -> None:
        if self.flow_gpm <= 0:
            return
        self.glide = GlideStop(reason=reason, start_time=time.monotonic(), start_flow_gpm=self.flow_gpm)
        self.last_message = f"Coasting to stop: {reason}"

    def command(self, command: str) -> None:
        self.tick()
        if command in {"+1", "-1", "+10", "-10"}:
            self.requested_gallons = max(0.0, self.requested_gallons + int(command))
            self.colors_are_green = False
            self.thumbs_visible = False
            if self.current_mode == "fill":
                self.fill_requested_gallons = self.requested_gallons
            else:
                self.mix_requested_gallons = self.requested_gallons
            self.last_message = f"Requested gallons now {self.requested_gallons:.0f}"
        elif command == "OV":
            self.override_mode = not self.override_mode
            self.last_message = f"Override {'enabled' if self.override_mode else 'disabled'}"
        elif command == "PS":
            self.start_glide_stop("pump stop")
        elif command == "TU":
            self.handle_thumbs_up()

    def handle_thumbs_up(self) -> None:
        is_flowing = self.flow_gpm >= FLOW_STOPPED_GPM
        if is_flowing:
            self.last_message = f"Thumbs up ignored: flow still active ({self.flow_gpm:.1f} GPM)"
            return

        actual = self.actual_gallons
        within_threshold = abs(actual - self.requested_gallons) <= 2.0
        self.colors_are_green = within_threshold
        self.thumbs_visible = True
        if self.pending_fill:
            self.daily_total += self.pending_fill.actual_gallons
            self.last_loads_gallons = [self.pending_fill.actual_gallons] + self.last_loads_gallons[:2]
            self.last_message = (
                f"Recorded {self.pending_fill.actual_gallons:.1f} gal "
                f"({self.pending_fill.shutoff_type})"
            )
            self.pending_fill = None
        else:
            self.last_message = "Thumbs up shown: no pending fill to record"

    def set_mode(self, mode: str) -> None:
        self.tick()
        if mode not in {"fill", "mix"}:
            return
        self.current_mode = mode
        self.requested_gallons = self.fill_requested_gallons if mode == "fill" else self.mix_requested_gallons
        self.colors_are_green = False
        self.thumbs_visible = False
        self.last_message = f"Mode switched to {mode}"

    def set_sensor(self, key: str, value: Any) -> None:
        self.tick()
        if key == "iol":
            self.iol_connected = bool(value)
        elif key == "serial":
            self.serial_connected = bool(value)
        elif key == "tanks":
            self.tanks_ok = bool(value)
        elif key == "manual":
            self.override_mode = bool(value)
        elif key == "thumbsWidth":
            self.thumbs_width = max(20.0, min(55.0, float(value)))
        self.last_message = f"{key} set to {value}"

    def reset_totalizer(self) -> None:
        self.tick()
        self.actual_gallons = 0.0
        self.pending_fill = None
        self.colors_are_green = False
        self.thumbs_visible = False
        self.last_message = "Flow totalizer reset"

    def state(self) -> dict[str, Any]:
        self.tick()
        smoothed_gpm = self._smoothed_flow_gpm()
        threshold = self._threshold_for_gpm(smoothed_gpm)
        warnings = []
        if not self.serial_connected:
            warnings.append("SWITCH BOX\nDISCONNECTED")
        if not self.iol_connected:
            warnings.append("FLOW METER\nDISCONNECTED")
        if self.actual_gallons > self.requested_gallons + config.WARNING_THRESHOLD:
            warnings.append("OVER TARGET!")
        return {
            "requestedGallons": self.requested_gallons,
            "actualGallons": self.actual_gallons,
            "dailyTotal": self.daily_total,
            "flowGpm": self.flow_gpm,
            "thresholdGallons": threshold,
            "threeSecondCoastGallons": self.flow_gpm * 3.0 / 120.0,
            "iolConnected": self.iol_connected,
            "serialConnected": self.serial_connected,
            "tanksOk": self.tanks_ok,
            "bmsSoc": self.bms_soc,
            "bmsVoltage": self.bms_voltage,
            "overrideMode": self.override_mode,
            "mode": self.current_mode,
            "lastLoadsGallons": self.last_loads_gallons,
            "colorsAreGreen": self.colors_are_green,
            "thumbsVisible": self.thumbs_visible,
            "thumbsWidth": self.thumbs_width,
            "glideReason": self.glide.reason if self.glide else None,
            "pendingFill": self.pending_fill.__dict__ if self.pending_fill else None,
            "warnings": warnings,
            "lastMessage": self.last_message,
        }


MODEL = BBBWorkbenchModel()


def reset_model() -> None:
    global MODEL
    MODEL = BBBWorkbenchModel()


class WorkbenchHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._send_json(MODEL.state())
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"
        file_path = (WEB_ROOT / path.lstrip("/")).resolve()
        if WEB_ROOT not in file_path.parents and file_path != WEB_ROOT:
            self.send_error(403)
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            action = payload.get("action")
            if action == "setFlow":
                MODEL.set_flow(payload.get("gpm", 0))
            elif action == "command":
                MODEL.command(str(payload.get("command", "")))
            elif action == "mode":
                MODEL.set_mode(str(payload.get("mode", "")))
            elif action == "sensor":
                MODEL.set_sensor(str(payload.get("key", "")), payload.get("value"))
            elif action == "resetTotalizer":
                MODEL.reset_totalizer()
            elif action == "battery":
                MODEL.bms_soc = 84.0
                MODEL.bms_voltage = 13.1
                MODEL.last_message = "Battery set to 84% / 13.1V"
            elif action == "resetScenario":
                reset_model()
            else:
                self._send_json({"error": "unknown action"}, 400)
                return
            self._send_json(MODEL.state())
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), WorkbenchHandler)
    print("BBB GUI workbench serving on http://127.0.0.1:8765/")
    server.serve_forever()


if __name__ == "__main__":
    main()
