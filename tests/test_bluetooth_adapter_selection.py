from src import bluetooth_adapter_selection
from src.bluetooth_adapter_selection import select_adapters


def adapter(hci, mac, vendor_id, product_id, manufacturer='', product=''):
    return {
        'hci': hci,
        'mac': mac,
        'vendor_id': vendor_id,
        'product_id': product_id,
        'manufacturer': manufacturer,
        'product': product,
    }


def test_known_usb_roles_override_stale_saved_macs():
    adapters = [
        adapter('hci0', 'AA:AA:AA:AA:AA:AA', '2c0a', '8761'),
        adapter('hci1', 'BB:BB:BB:BB:BB:BB', '0b05', '1bf6'),
    ]

    gatt, sensor, used_fallback = select_adapters(
        adapters,
        saved_gatt_mac='BB:BB:BB:BB:BB:BB',
        saved_sensor_mac='AA:AA:AA:AA:AA:AA',
    )

    assert gatt['hci'] == 'hci0'
    assert sensor['hci'] == 'hci1'
    assert used_fallback is True


def test_saved_macs_are_used_when_known_usb_roles_are_absent():
    adapters = [
        adapter('hci0', 'AA:AA:AA:AA:AA:AA', '1234', '5678', 'Realtek', 'Bluetooth Radio'),
        adapter('hci1', 'BB:BB:BB:BB:BB:BB', '8765', '4321', 'ASUSTek', 'Bluetooth Controller'),
    ]

    gatt, sensor, used_fallback = select_adapters(
        adapters,
        saved_gatt_mac='AA:AA:AA:AA:AA:AA',
        saved_sensor_mac='BB:BB:BB:BB:BB:BB',
    )

    assert gatt['hci'] == 'hci0'
    assert sensor['hci'] == 'hci1'
    assert used_fallback is False


def test_asus_dongle_is_selected_for_sensors_by_usb_id():
    adapters = [
        adapter('hci0', 'E8:EA:6A:BD:EC:37', '2c0a', '8761', 'Realtek', 'Bluetooth Radio'),
        adapter('hci1', 'A0:AD:9F:71:2F:76', '0b05', '1bf6', 'Realtek', 'Bluetooth Controller'),
    ]

    gatt, sensor, used_fallback = select_adapters(
        adapters,
        saved_gatt_mac='E8:EA:6A:BD:E7:4F',
        saved_sensor_mac='BC:FC:E7:2D:86:7B',
    )

    assert gatt['mac'] == 'E8:EA:6A:BD:EC:37'
    assert sensor['mac'] == 'A0:AD:9F:71:2F:76'
    assert used_fallback is True


def test_ambiguous_same_role_candidates_are_not_guessed():
    adapters = [
        adapter('hci0', 'AA:AA:AA:AA:AA:AA', '2c0a', '8761'),
        adapter('hci1', 'BB:BB:BB:BB:BB:BB', '2c0a', '8761'),
    ]

    gatt, sensor, used_fallback = select_adapters(adapters)

    assert gatt is None
    assert sensor is None
    assert used_fallback is False


def test_hciconfig_parser_extracts_hci_macs(monkeypatch):
    class Result:
        stdout = '''
hci1:   Type: Primary  Bus: USB
        BD Address: A0:AD:9F:71:2F:76  ACL MTU: 1021:6  SCO MTU: 255:12
hci0:   Type: Primary  Bus: USB
        BD Address: E8:EA:6A:BD:EC:37  ACL MTU: 1021:6  SCO MTU: 255:12
'''

    monkeypatch.setattr(
        bluetooth_adapter_selection.subprocess,
        'run',
        lambda *args, **kwargs: Result(),
    )

    assert bluetooth_adapter_selection.hci_macs_from_hciconfig() == {
        'hci0': 'E8:EA:6A:BD:EC:37',
        'hci1': 'A0:AD:9F:71:2F:76',
    }
