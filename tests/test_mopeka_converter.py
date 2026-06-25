"""Tests for mopeka_converter module."""

import os
import sys
import tempfile
import pytest

# Add parent to path so we can import
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from src.mopeka_converter import (
    load_calibration, load_sensor_offsets, mm_to_gallons, 
    _interpolate_gallons, _calibration_table, _sensor_offsets,
    MAX_TANK_HEIGHT_IN
)
import src.mopeka_converter as converter


@pytest.fixture(autouse=True)
def reset_module():
    """Reset module state before each test."""
    converter._calibration_table = []
    converter._calibration_profiles = {}
    converter._sensor_calibration_profiles = {}
    converter._sensor_offsets = {}
    converter._ble_mac_to_sensor_id = {}
    converter._data_dir = None
    converter.MAX_TANK_HEIGHT_IN = 56.73228346456693
    yield


@pytest.fixture
def cal_csv(tmp_path):
    """Create a test calibration CSV."""
    csv_content = """Tank Level (in),Gallons,Tank Size (gal)
56.73,0.0,1070.0
50.0,100.0,1070.0
40.0,300.0,1070.0
30.0,500.0,1070.0
20.0,700.0,1070.0
10.0,900.0,1070.0
1.6,1070.0,1070.0"""
    f = tmp_path / "cal.csv"
    f.write_text(csv_content)
    return str(f)


@pytest.fixture
def sensor_csv(tmp_path):
    """Create a test sensor details CSV."""
    csv_content = """Man,Trailer,Tank,Center Sump?,Height Offset,Mopeka Name in app,Mopeka ID,MQTT Topic for app,Added to app
Milan,1,Front,,-0.38,TR1-Front,0F:37:A5,trailer/1/front,Yes
,1,Back,,0.5,TR1-Back,F7:D0:22,trailer/1/back,Yes
Eugene,8,Front,,0,TR8-Front,FB:30:71,trailer/8/front,Yes
,8,Back,,,TR8-Back,A9:B8:9A,trailer/8/back,Yes"""
    f = tmp_path / "sensors.csv"
    f.write_text(csv_content)
    return str(f)


class TestLoadCalibration:
    def test_loads_points(self, cal_csv):
        load_calibration(cal_csv)
        assert len(converter._calibration_table) == 7
    
    def test_sorted_descending(self, cal_csv):
        load_calibration(cal_csv)
        tops = [p[0] for p in converter._calibration_table]
        assert tops == sorted(tops, reverse=True)
    
    def test_max_height_set(self, cal_csv):
        load_calibration(cal_csv)
        assert converter.MAX_TANK_HEIGHT_IN == 56.73


class TestLoadSensorOffsets:
    def test_loads_offsets(self, sensor_csv):
        load_sensor_offsets(sensor_csv)
        assert len(converter._sensor_offsets) == 3  # 3 with valid offsets
    
    def test_offset_values(self, sensor_csv):
        load_sensor_offsets(sensor_csv)
        assert converter._sensor_offsets['0F:37:A5'] == -0.38
        assert converter._sensor_offsets['F7:D0:22'] == 0.5
        assert converter._sensor_offsets['FB:30:71'] == 0.0
    
    def test_missing_offset_skipped(self, sensor_csv):
        load_sensor_offsets(sensor_csv)
        # A9:B8:9A has no offset value, should be skipped
        assert 'A9:B8:9A' not in converter._sensor_offsets


class TestInterpolation:
    def test_empty_tank(self, cal_csv):
        load_calibration(cal_csv)
        # At top = 56.73 inches from top = empty
        gallons = _interpolate_gallons(56.73)
        assert gallons == 0.0
    
    def test_full_tank(self, cal_csv):
        load_calibration(cal_csv)
        # At 1.6 inches from top = full
        gallons = _interpolate_gallons(1.6)
        assert gallons == 1070.0
    
    def test_midpoint_interpolation(self, cal_csv):
        load_calibration(cal_csv)
        # 45.0 is between 50.0 (100 gal) and 40.0 (300 gal) 
        gallons = _interpolate_gallons(45.0)
        assert gallons == 200.0  # Exact midpoint
    
    def test_above_empty(self, cal_csv):
        load_calibration(cal_csv)
        gallons = _interpolate_gallons(60.0)
        assert gallons == 0.0
    
    def test_below_full(self, cal_csv):
        load_calibration(cal_csv)
        gallons = _interpolate_gallons(0.0)
        assert gallons == 1070.0
    
    def test_no_calibration_data(self):
        gallons = _interpolate_gallons(30.0)
        assert gallons == 0.0


class TestMmToGallons:
    def test_zero_mm_clamps_to_full_end(self, cal_csv, sensor_csv):
        load_calibration(cal_csv)
        load_sensor_offsets(sensor_csv)
        result = mm_to_gallons(0.0)
        assert result['gallons'] == 1070.0
        assert result['level_in'] == 0.0
    
    def test_high_reading_near_empty_end(self, cal_csv, sensor_csv):
        load_calibration(cal_csv)
        load_sensor_offsets(sensor_csv)
        # The converter uses the compensated reading directly on the CSV axis.
        level_mm = 55.0 * 25.4  # 1397 mm
        result = mm_to_gallons(level_mm)
        assert result['gallons'] < 50
    
    def test_with_positive_offset(self, cal_csv, sensor_csv):
        """Positive offset = sensor reads low, add to reading."""
        load_calibration(cal_csv)
        load_sensor_offsets(sensor_csv)
        level_mm = 500.0  # ~19.7 inches
        
        result_no_offset = mm_to_gallons(level_mm)
        result_with_offset = mm_to_gallons(level_mm, 'F7:D0:22')  # +0.5 offset
        
        assert result_with_offset['offset_in'] == 0.5
        assert result_with_offset['level_in'] > result_no_offset['level_in']
        # Larger compensated reading moves toward the low-volume end of the table.
        assert result_with_offset['gallons'] <= result_no_offset['gallons']
    
    def test_with_negative_offset(self, cal_csv, sensor_csv):
        """Negative offset = sensor reads high, subtract from reading."""
        load_calibration(cal_csv)
        load_sensor_offsets(sensor_csv)
        level_mm = 500.0
        
        result_no_offset = mm_to_gallons(level_mm)
        result_with_offset = mm_to_gallons(level_mm, '0F:37:A5')  # -0.38 offset
        
        assert result_with_offset['offset_in'] == -0.38
        assert result_with_offset['level_in'] < result_no_offset['level_in']
        assert result_with_offset['gallons'] >= result_no_offset['gallons']
    
    def test_unknown_sensor_no_offset(self, cal_csv, sensor_csv):
        load_calibration(cal_csv)
        load_sensor_offsets(sensor_csv)
        result = mm_to_gallons(500.0, 'XX:XX:XX')
        assert result['offset_in'] == 0.0
    
    def test_none_sensor_no_offset(self, cal_csv, sensor_csv):
        load_calibration(cal_csv)
        load_sensor_offsets(sensor_csv)
        result = mm_to_gallons(500.0, None)
        assert result['offset_in'] == 0.0
    
    def test_result_keys(self, cal_csv):
        load_calibration(cal_csv)
        result = mm_to_gallons(500.0)
        assert 'gallons' in result
        assert 'level_in' in result
        assert 'level_from_top_in' in result
        assert 'offset_in' in result
    
    def test_gallons_clamped_positive(self, cal_csv):
        load_calibration(cal_csv)
        result = mm_to_gallons(0.0)
        assert result['gallons'] >= 0.0
    
    def test_gallons_clamped_max(self, cal_csv):
        load_calibration(cal_csv)
        # Way more than tank height
        result = mm_to_gallons(2000.0)
        assert result['gallons'] <= 1070.0


class TestCalibrationProfiles:
    def test_customer_front_profile_overrides_shared_curve(self, tmp_path):
        data_dir = tmp_path
        (data_dir / "calibration-points-1070gal-tank.csv").write_text(
            """Tank Level (in),Gallons,Tank Size (gal)
50,0,1000
0,1000,1000
"""
        )
        (data_dir / "mopeka-sensor-details.csv").write_text(
            """Man,Trailer,Tank,Center Sump?,Height Offset,Mopeka Name in app,Mopeka ID,MQTT Topic for app,Added to app
Customer,1,Front,,0,Front,AA:BB:CC,,Yes
"""
        )
        (data_dir / "mopeka_config.json").write_text(
            '{"box_mode":"customer","front_id":"AA:BB:CC"}'
        )
        profile_dir = data_dir / "calibrations"
        profile_dir.mkdir()
        (profile_dir / "customer-front.csv").write_text(
            """Tank Level (in),Gallons,Tank Size (gal)
50,0,500
0,500,500
"""
        )
        (profile_dir / "trailer-1-front.csv").write_text(
            """Tank Level (in),Gallons,Tank Size (gal)
50,0,200
0,200,200
"""
        )

        converter.init(str(data_dir))
        profiled = mm_to_gallons(25.0 * 25.4, "AA:BB:CC")
        fallback = mm_to_gallons(25.0 * 25.4, "DD:EE:FF")

        assert profiled["gallons"] == 250.0
        assert fallback["gallons"] == 500.0

    def test_fleet_profile_uses_assigned_trailer_and_tank(self, tmp_path):
        data_dir = tmp_path
        (data_dir / "calibration-points-1070gal-tank.csv").write_text(
            """Tank Level (in),Gallons,Tank Size (gal)
50,0,1000
0,1000,1000
"""
        )
        (data_dir / "mopeka-sensor-details.csv").write_text(
            """Man,Trailer,Tank,Center Sump?,Height Offset,Mopeka Name in app,Mopeka ID,MQTT Topic for app,Added to app
Vern,2,Front,,0,Front,29:54:06,,Yes
,2,Back,,0,Back,96:BB:5D,,Yes
"""
        )
        (data_dir / "mopeka_config.json").write_text(
            '{"box_mode":"fleet","trailer":2,"front_id":"29:54:06","back_id":"96:BB:5D"}'
        )
        profile_dir = data_dir / "calibrations"
        profile_dir.mkdir()
        (profile_dir / "trailer-2-back.csv").write_text(
            """Tank Level (in),Gallons,Tank Size (gal)
50,0,700
0,700,700
"""
        )

        converter.init(str(data_dir))
        front = mm_to_gallons(25.0 * 25.4, "29:54:06")
        back = mm_to_gallons(25.0 * 25.4, "96:BB:5D")

        assert front["gallons"] == 500.0
        assert back["gallons"] == 350.0

    def test_customer_manual_sensor_does_not_use_fleet_profile(self, tmp_path):
        data_dir = tmp_path
        (data_dir / "calibration-points-1070gal-tank.csv").write_text(
            """Tank Level (in),Gallons,Tank Size (gal)
50,0,1000
0,1000,1000
"""
        )
        (data_dir / "mopeka-sensor-details.csv").write_text(
            """Man,Trailer,Tank,Center Sump?,Height Offset,Mopeka Name in app,Mopeka ID,MQTT Topic for app,Added to app
Vern,2,Front,,0,Front,29:54:06,,Yes
"""
        )
        (data_dir / "mopeka_config.json").write_text(
            '{"box_mode":"customer","front_id":"29:54:06"}'
        )
        profile_dir = data_dir / "calibrations"
        profile_dir.mkdir()
        (profile_dir / "trailer-2-front.csv").write_text(
            """Tank Level (in),Gallons,Tank Size (gal)
50,0,200
0,200,200
"""
        )

        converter.init(str(data_dir))
        front = mm_to_gallons(25.0 * 25.4, "29:54:06")

        assert front["gallons"] == 500.0
