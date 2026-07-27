import csv
from pathlib import Path

from rotorlink import config, config_handler


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SENSOR_ID = "9A:E3:35"


def _seed_rows():
    path = PROJECT_ROOT / "mopeka" / "mopeka-sensor-details.csv"
    lines = path.read_text().splitlines()
    header_index = next(i for i, line in enumerate(lines) if "Mopeka ID" in line)
    return list(csv.DictReader(lines[header_index:]))


def _duplicate_sensor_rows():
    return [
        {
            "Man": "Jake",
            "Trailer": "8",
            "Tank": "Back",
            "Center Sump?": "",
            "Height Offset": "-.92",
            "Mopeka Name in app": "TR8-Mopeka Back tank",
            "Mopeka ID": SENSOR_ID,
            "MQTT Topic for app": "",
            "Added to app": "No",
        },
        {
            "Man": "Jake",
            "Trailer": "8",
            "Tank": "Back",
            "Center Sump?": "",
            "Height Offset": "0.0",
            "Mopeka Name in app": "TR8-Mopeka Back tank",
            "Mopeka ID": SENSOR_ID,
            "MQTT Topic for app": "",
            "Added to app": "No",
        },
    ]


def test_tr8_back_seed_bridges_the_one_update_lag():
    rows = _seed_rows()
    tr8_back = [
        row
        for row in rows
        if row["Trailer"].strip() == "8" and row["Tank"].strip() == "Back"
    ]

    assert len(tr8_back) == 1
    assert tr8_back[0]["Mopeka ID"] == SENSOR_ID
    assert float(tr8_back[0]["Height Offset"]) == -0.90

    real_ids = [
        row["Mopeka ID"].strip().upper()
        for row in rows
        if row["Mopeka ID"].strip() not in ("", "---------------")
    ]
    assert len(real_ids) == len(set(real_ids))


def test_all_runtime_refresh_paths_preserve_per_box_sensor_state():
    dashboard = (PROJECT_ROOT / "dashboard.py").read_text()
    installer = (PROJECT_ROOT / "install.sh").read_text()
    bumble = (PROJECT_ROOT / "rotorsync_bumble.py").read_text()

    for source in (dashboard, installer, bumble):
        assert "mopeka_config.json" in source
        assert "mopeka-sensor-details.csv" in source

    assert (
        'base_name\\" = \\"mopeka-sensor-details.csv\\"'
        in dashboard
    )
    assert '[ "$base_name" = "mopeka-sensor-details.csv" ]' in installer
    assert (
        "('mopeka_config.json', 'mopeka-sensor-details.csv')"
        in bumble
    )

    assert "chown -R pi:pi /opt/mopeka" in dashboard
    assert 'chown -R "$INSTALL_USER:$INSTALL_USER" "$OPT_DIR/mopeka"' in installer


def test_rotorlink_duplicate_add_is_idempotent(monkeypatch, tmp_path):
    sensor_path = tmp_path / "mopeka-sensor-details.csv"
    monkeypatch.setattr(config, "SENSOR_CSV_PATH", str(sensor_path))
    config_handler._save_sensor_csv(_duplicate_sensor_rows()[:1])
    handler = config_handler.ConfigHandler(None)

    response = handler._add_sensor(
        {
            "data": {
                "trailer": 8,
                "tank": "Back",
                "height_offset": "-0.90",
                "id": SENSOR_ID.lower(),
            }
        },
        request_id="retry",
    )

    assert response == {
        "ok": True,
        "op": "ADD_SENSOR",
        "request_id": "retry",
        "id": SENSOR_ID.lower(),
        "existing": True,
    }
    assert len(config_handler._load_sensor_csv()) == 1


def test_rotorlink_update_repairs_all_legacy_duplicate_offsets(
    monkeypatch,
    tmp_path,
):
    sensor_path = tmp_path / "mopeka-sensor-details.csv"
    monkeypatch.setattr(config, "SENSOR_CSV_PATH", str(sensor_path))
    config_handler._save_sensor_csv(_duplicate_sensor_rows())
    handler = config_handler.ConfigHandler(None)

    response = handler._update_sensor(
        {
            "id": SENSOR_ID.lower(),
            "data": {"height_offset": "-0.90"},
        },
        request_id="offset",
    )

    assert response["ok"] is True
    matches = [
        row
        for row in config_handler._load_sensor_csv()
        if row["Mopeka ID"].strip().upper() == SENSOR_ID
    ]
    assert len(matches) == 2
    assert {float(row["Height Offset"]) for row in matches} == {-0.90}
