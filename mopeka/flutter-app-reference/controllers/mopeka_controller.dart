import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:rotorsync/utils/logger.dart';
import '../utils/mopeka_utils.dart';

class MopekaController with ChangeNotifier {
  static const int mopekaManufacturer = 89;
  static const String mopekaProServiceUuid =
      "0000fee5-0000-1000-8000-00805f9b34fb";
  static const MediumType mediumType = MediumType.air;

  final MopekaSensorData _sensorData = MopekaSensorData();
  bool _isScanning = false;
  bool _isMonitoring = false;
  String? _deviceAddress;
  double? _tankSize;
  StreamSubscription? _scanSubscription;
  StreamSubscription? _monitorSubscription;

  MopekaSensorData get sensorData => _sensorData;
  bool get isScanning => _isScanning;
  bool get isMonitoring => _isMonitoring;
  String get connectionStatus => _isScanning
      ? 'Connecting...'
      : _isMonitoring
          ? 'Connected'
          : 'Disconnected';
  double? get tankSize => _tankSize;

  Function(String)? onValidationError;
  Function(String)? onConnectionStatusChanged;

  MopekaController() {
    FlutterBluePlus.setLogLevel(LogLevel.verbose, color: false);
  }

  Future<bool> checkBluetoothAndLocation() async {
    try {
      if (!(await FlutterBluePlus.isSupported)) {
        onValidationError?.call('Bluetooth not supported on this device');
        return false;
      }

      final state = await FlutterBluePlus.adapterState
          .firstWhere((state) => state != BluetoothAdapterState.unknown)
          .timeout(const Duration(seconds: 5), onTimeout: () {
        onValidationError?.call('Bluetooth state unknown for too long');
        return BluetoothAdapterState.unknown;
      });

      if (state != BluetoothAdapterState.on) {
        onValidationError?.call('Bluetooth is not enabled');
        return false;
      }
      return true;
    } catch (e) {
      AppLogger.error('Error checking Bluetooth: $e');
      onValidationError?.call('Error checking Bluetooth: $e');
      return false;
    }
  }

  Future<void> connect(double? tankSize) async {
    if (_isScanning || _isMonitoring) return;

    _tankSize = tankSize;
    _isScanning = true;
    notifyListeners();
    onConnectionStatusChanged?.call('Connecting...');

    try {
      final isBluetoothEnabled = await checkBluetoothAndLocation();
      if (!isBluetoothEnabled) {
        _isScanning = false;
        notifyListeners();
        onConnectionStatusChanged?.call('Disconnected');
        return;
      }

      await FlutterBluePlus.startScan(timeout: const Duration(seconds: 120));
      AppLogger.info('Started BLE scan for Mopeka sensors');

      _scanSubscription = FlutterBluePlus.onScanResults.listen((results) {
        for (var result in results) {
          var manufacturerData = result.advertisementData.manufacturerData;
          var serviceUuids = result.advertisementData.serviceUuids;

          if (!manufacturerData.containsKey(mopekaManufacturer) ||
              !serviceUuids.contains(Guid.fromString(mopekaProServiceUuid))) {
            continue;
          }

          var data = manufacturerData[mopekaManufacturer]!;
          int modelNum = data[0];
          var deviceType = deviceTypes[modelNum];
          if (deviceType == null) {
            AppLogger.info('Unsupported Mopeka device type: $modelNum');
            continue;
          }

          if (data.length != deviceType.advLength) {
            AppLogger.info('Invalid advertisement length: ${data.length}');
            continue;
          }

          String deviceName =
              "${deviceType.name} ${result.device.remoteId.toString().replaceAll(":", "").substring(8)}";
          _deviceAddress = result.device.remoteId.toString();
          _updateSensorData(data, result.rssi, deviceName);
          AppLogger.info(
              'Found Mopeka TD40 sensor: ${result.device.remoteId}, starting monitoring');

          _stopScanning();
          _isScanning = false;
          _isMonitoring = true;
          onConnectionStatusChanged?.call('Connected');
          notifyListeners();

          _startMonitoring(result.device.remoteId.toString());
          break;
        }
      }, onError: (e) {
        AppLogger.error('Error during scan: $e');
        _isScanning = false;
        notifyListeners();
        onConnectionStatusChanged?.call('Disconnected');
        onValidationError?.call('Error during scan: $e');
      });
    } catch (e) {
      AppLogger.error('Error starting scan: $e');
      _isScanning = false;
      notifyListeners();
      onConnectionStatusChanged?.call('Disconnected');
      onValidationError?.call('Error starting scan: $e');
    }
  }

  Future<void> _startMonitoring(String deviceId) async {
    try {
      await FlutterBluePlus.startScan(
        withRemoteIds: [deviceId],
        timeout: const Duration(seconds: 3600),
      );
      AppLogger.info('Started monitoring for Mopeka TD40 sensor: $deviceId');

      _monitorSubscription = FlutterBluePlus.onScanResults.listen((results) {
        for (var result in results) {
          if (result.device.remoteId.toString() != deviceId) continue;

          var manufacturerData = result.advertisementData.manufacturerData;
          if (!manufacturerData.containsKey(mopekaManufacturer)) continue;

          var data = manufacturerData[mopekaManufacturer]!;
          String deviceName =
              "${deviceTypes[data[0]]!.name} ${result.device.remoteId.toString().replaceAll(":", "").substring(8)}";
          _updateSensorData(data, result.rssi, deviceName);
          AppLogger.info(
              'Monitoring update for $deviceId: RSSI ${result.rssi}');
        }
      }, onError: (e) {
        AppLogger.error('Error during monitoring: $e');
        _isMonitoring = false;
        _deviceAddress = null;
        notifyListeners();
        onConnectionStatusChanged?.call('Disconnected');
        onValidationError?.call('Error during monitoring: $e');
      });
    } catch (e) {
      AppLogger.error('Error starting monitoring: $e');
      _isMonitoring = false;
      _deviceAddress = null;
      notifyListeners();
      onConnectionStatusChanged?.call('Disconnected');
      onValidationError?.call('Error starting monitoring: $e');
    }
  }

  Future<void> disconnect() async {
    await _stopScanning();
    await _stopMonitoring();
    _deviceAddress = null;
    _isScanning = false;
    _isMonitoring = false;
    notifyListeners();
    onConnectionStatusChanged?.call('Disconnected');
  }

  Future<void> _stopScanning() async {
    await _scanSubscription?.cancel();
    _scanSubscription = null;
    await FlutterBluePlus.stopScan();
    AppLogger.info('Stopped scanning');
  }

  Future<void> _stopMonitoring() async {
    await _monitorSubscription?.cancel();
    _monitorSubscription = null;
    await FlutterBluePlus.stopScan();
    AppLogger.info('Stopped monitoring');
  }

  void _updateSensorData(List<int> data, int rssi, String deviceName) {
    _sensorData.updateFromAdvertisement(data, rssi, mediumType, deviceName);
    notifyListeners();
  }

  void setTankSize(double size) {
    _tankSize = size;
    notifyListeners();
  }

  double calculateFuelLevel() {
    if (_tankSize == null || _tankSize! <= 0) return 0.0;
    if (_sensorData.readingQualityRaw < 1) return 0.0;
    double fuelHeight = _tankSize! - _sensorData.tankLevelIn;
    return ((fuelHeight / _tankSize!) * 100).clamp(0.0, 100.0);
  }

  @override
  void dispose() {
    _stopScanning();
    _stopMonitoring();
    super.dispose();
  }
}
