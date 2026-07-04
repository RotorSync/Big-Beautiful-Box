import struct

import config
from src.flow_handler import FlowHandler
from src.flow_meter import FlowMeter, FlowMeterReading


def _picomag_frame(totalizer_liters, flow_l_per_s):
    return (
        b"\x00" * 4
        + struct.pack(">f", totalizer_liters)
        + struct.pack(">f", flow_l_per_s)
        + b"\x00" * 3
    )


def test_flow_meter_parse_preserves_signed_negative_totalizer():
    meter = FlowMeter()

    totalizer_liters, flow_l_per_s = meter._parse_data(
        _picomag_frame(-94.937035, 0.25)
    )

    assert totalizer_liters < 0
    assert round(totalizer_liters * config.LITERS_TO_GALLONS, 3) == -25.08
    assert flow_l_per_s == 0.25


def test_flow_meter_reading_gallons_preserves_sign():
    reading = FlowMeterReading(totalizer_liters=-10.0)

    assert reading.totalizer_gallons == -10.0 * 0.264172


def test_flow_handler_read_preserves_signed_negative_totalizer(monkeypatch):
    class FakeIOLHat:
        @staticmethod
        def pd(_port, _len_out, _len_in, _pd_out):
            return _picomag_frame(-94.937035, 0.0)

    import src.flow_handler as flow_handler

    monkeypatch.setattr(flow_handler, "iolhat", FakeIOLHat)
    handler = FlowHandler()
    handler._initialized = True

    reading = handler.read()

    assert reading.is_valid is True
    assert round(reading.totalizer_gallons, 3) == -25.08
    assert reading.flow_rate_gpm == 0.0
