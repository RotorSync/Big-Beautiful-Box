"""HAT v2 pin map and serial removal (static checks; no hardware)."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

DASH = ast.parse((ROOT / "dashboard.py").read_text(encoding="utf-8"))


def _func(name):
    return next(n for n in DASH.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_hat_v2_pins():
    assert config.FLOW_RESET_PIN == 23      # K3
    assert config.PUMP_STOP_RELAY_PIN == 27  # K2
    assert config.AUX_RELAY_PIN == 16        # K4
    used = {config.FLOW_RESET_PIN, config.PUMP_STOP_RELAY_PIN, config.AUX_RELAY_PIN,
            config.GREEN_BUTTON_PIN}
    assert len(used) == 4
    # Not GPIO14/15 (no UART), 18-21 (SPI1 CAN), 26 (CAN INT), 7-11/24/25 (IOL HAT)
    assert not used & {14, 15, 18, 19, 20, 21, 26, 7, 8, 9, 10, 11, 24, 25}


def test_no_uart_and_can_settings():
    assert config.SERIAL_PORT is None
    assert config.CAN_INTERFACE == "can0" and config.CAN_BITRATE == 250000
    svc = (ROOT / "deploy" / "bbb-can0.service").read_text()
    assert "bitrate 250000" in svc
    boot = (ROOT / "deploy" / "boot-firmware-bbb.conf").read_text().splitlines()
    assert "dtoverlay=mcp251xfd,spi1-0,oscillator=40000000,interrupt=26" in boot
    assert "dtoverlay=spi1-1cs,cs0_spidev=off" in boot
    assert not any(l.startswith(("enable_uart", "dtparam=uart0")) for l in boot)


def test_serial_listener_returns_early_without_port():
    first = _func("serial_listener").body
    guard = next(n for n in first if isinstance(n, ast.If))
    assert ast.unparse(guard.test) == "not config.SERIAL_PORT"
    assert isinstance(guard.body[-1], ast.Return)


def test_aux_relay_initialized_low():
    src = ast.unparse(DASH)
    assert "GPIO.setup(config.AUX_RELAY_PIN, GPIO.OUT)" in src
    assert "GPIO.output(config.AUX_RELAY_PIN, GPIO.LOW)" in src
    _func("set_aux_relay"); _func("pulse_aux_relay")
