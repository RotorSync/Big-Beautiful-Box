#!/usr/bin/env python3
"""Run the BBB Tkinter dashboard with local simulator controls."""

import os
import sys
import types


class _FakeSerialException(Exception):
    pass


class _FakeSerial:
    def __init__(self, *args, **kwargs):
        self.is_open = True
        self.in_waiting = 0

    def reset_input_buffer(self):
        pass

    def read(self, size=1):
        return b""

    def write(self, data):
        return len(data)

    def close(self):
        self.is_open = False


def _install_fake_serial_if_needed():
    try:
        import serial  # noqa: F401
        return
    except ImportError:
        module = types.ModuleType("serial")
        module.Serial = _FakeSerial
        module.SerialException = _FakeSerialException
        sys.modules["serial"] = module


os.environ.setdefault("BBB_SIM_MODE", "1")
os.environ.setdefault(
    "BBB_SIM_STATE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sim-data"),
)

_install_fake_serial_if_needed()

import dashboard  # noqa: E402,F401
