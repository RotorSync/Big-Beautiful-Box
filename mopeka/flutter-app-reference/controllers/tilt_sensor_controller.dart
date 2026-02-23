import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:location/location.dart' as loc;
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:mqtt_client/mqtt_client.dart';
import '../services/mqtt_service.dart';
import '../utils/logger.dart';

class TiltSensorController {
  BluetoothDevice? _device;
  BluetoothCharacteristic? _notifyCharacteristic;
  BluetoothCharacteristic? _controlCharacteristic;
  StreamSubscription? _notificationSubscription;
  Map<String, dynamic> _sensorData = {};
  bool _isConnected = false;
  bool _isConnecting = false;

  final MQTTService mqttService;

  VoidCallback? onStateChanged;
  Function(String)? onValidationError;
  Function(String)? onConnectionStatusChanged;

  static const String serviceUuid = "0000ffe5-0000-1000-8000-00805f9a34fb";
  static const String notifyUuid = "0000ffe4-0000-1000-8000-00805f9a34fb";
  static const String controlUuid = "0000ffe9-0000-1000-8000-00805f9a34fb";
  static const String deviceNamePrefix = "WT";

  TiltSensorController({required this.mqttService}) {
    _initializeDefaultData();
  }

  void _initializeDefaultData() {
    _sensorData = {
      "acc_x": 0.0,
      "acc_y": 0.0,
      "acc_z": 0.0,
      "gyro_x": 0.0,
      "gyro_y": 0.0,
      "gyro_z": 0.0,
      "angle_x": 0.0,
      "angle_y": 0.0,
      "angle_z": 0.0,
      "mag_x": 0.0,
      "mag_y": 0.0,
      "mag_z": 0.0,
      "quat_0": 0.0,
      "quat_1": 0.0,
      "quat_2": 0.0,
      "quat_3": 0.0,
    };
    AppLogger.info("Initialized default data for tilt sensor");
  }

  Map<String, dynamic> getSensorData() {
    AppLogger.info("getSensorData called: $_sensorData");
    return _sensorData;
  }

  bool get isConnected => _isConnected;

  int _getSignInt16(int value) {
    if (value >= 0x8000) return value - 0x10000;
    return value;
  }

  Future<Map<String, dynamic>?> _fetchUserSerialNumber() async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) {
      AppLogger.warning('No user logged in.');
      return null;
    }

    try {
      AppLogger.info('Fetching serial number for user ${user.uid}...');
      final doc = await FirebaseFirestore.instance
          .collection('users')
          .doc(user.uid)
          .get();
      final data = doc.data();
      if (data != null && data.containsKey('serial_number')) {
        AppLogger.info(
            'Serial number fetched for user ${user.uid}: ${data['serial_number']}');
        return data['serial_number'] as Map<String, dynamic>;
      }
      AppLogger.warning('No serial_number found for user ${user.uid}');
      return null;
    } catch (e) {
      AppLogger.error(
          'Failed to fetch serial number for user ${user.uid}: $e', e);
      return null;
    }
  }

  void _publishToMqtt(String identifier) async {
    if (!mqttService.isConnected) {
      AppLogger.warning(
          "MQTT service is not connected. Readings cannot be published.");
      onValidationError?.call(
          "MQTT service is not connected. Readings cannot be published.");
      return;
    }

    final payload = {
      "device_id": identifier,
      "timestamp": DateTime.now().toUtc().toIso8601String(),
      "acc": {
        "x": _sensorData["acc_x"] ?? 0.0,
        "y": _sensorData["acc_y"] ?? 0.0,
        "z": _sensorData["acc_z"] ?? 0.0,
      },
      "gyro": {
        "x": _sensorData["gyro_x"] ?? 0.0,
        "y": _sensorData["gyro_y"] ?? 0.0,
        "z": _sensorData["gyro_z"] ?? 0.0,
      },
      "angle": {
        "x": _sensorData["angle_x"] ?? 0.0,
        "y": _sensorData["angle_y"] ?? 0.0,
        "z": _sensorData["angle_z"] ?? 0.0,
      },
      "mag": {
        "x": _sensorData["mag_x"] ?? 0.0,
        "y": _sensorData["mag_y"] ?? 0.0,
        "z": _sensorData["mag_z"] ?? 0.0,
      },
      "quaternions": {
        "0": _sensorData["quat_0"] ?? 0.0,
        "1": _sensorData["quat_1"] ?? 0.0,
        "2": _sensorData["quat_2"] ?? 0.0,
        "3": _sensorData["quat_3"] ?? 0.0,
      },
    };
    final payloadJson = jsonEncode(payload);

    // Publish to the dynamic topic using user's type and id
    final userSerialData = await _fetchUserSerialNumber();
    if (userSerialData != null) {
      final type = userSerialData['type'] as String;
      final id = userSerialData['id'] as String;
      final newTopic = '$type/$id/tilt';
      AppLogger.debug('Constructed dynamic MQTT topic: $newTopic');
      try {
        mqttService.publish(newTopic, payloadJson, qos: MqttQos.atLeastOnce);
        AppLogger.info(
            "Published to dynamic MQTT topic $newTopic: $payloadJson");
      } catch (e) {
        AppLogger.error(
            "Failed to publish to dynamic MQTT topic $newTopic: $e", e);
        onValidationError?.call("Failed to publish to MQTT: $e");
      }
    } else {
      AppLogger.warning(
          'Cannot publish to dynamic MQTT topic: No serial number data found for user');
    }
  }

  void _parseSensorData(List<int> data) {
    if (data.length != 20 || data[0] != 0x55) {
      AppLogger.warning("Invalid sensor data packet: ${data.length} bytes");
      return;
    }

    if (data[1] == 0x61) {
      // IMU Data
      _sensorData.addAll({
        "acc_x": _getSignInt16((data[3] << 8) | data[2]) / 32768 * 16,
        "acc_y": _getSignInt16((data[5] << 8) | data[4]) / 32768 * 16,
        "acc_z": _getSignInt16((data[7] << 8) | data[6]) / 32768 * 16,
        "gyro_x": _getSignInt16((data[9] << 8) | data[8]) / 32768 * 2000,
        "gyro_y": _getSignInt16((data[11] << 8) | data[10]) / 32768 * 2000,
        "gyro_z": _getSignInt16((data[13] << 8) | data[12]) / 32768 * 2000,
        "angle_x": _getSignInt16((data[15] << 8) | data[14]) / 32768 * 180,
        "angle_y": _getSignInt16((data[17] << 8) | data[16]) / 32768 * 180,
        "angle_z": _getSignInt16((data[19] << 8) | data[18]) / 32768 * 180,
      });
    } else if (data[1] == 0x71) {
      if (data[2] == 0x3A) {
        // Magnetic Field
        _sensorData.addAll({
          "mag_x": _getSignInt16((data[5] << 8) | data[4]) / 120,
          "mag_y": _getSignInt16((data[7] << 8) | data[6]) / 120,
          "mag_z": _getSignInt16((data[9] << 8) | data[8]) / 120,
        });
      } else if (data[2] == 0x51) {
        // Quaternions
        _sensorData.addAll({
          "quat_0": _getSignInt16((data[5] << 8) | data[4]) / 32768,
          "quat_1": _getSignInt16((data[7] << 8) | data[6]) / 32768,
          "quat_2": _getSignInt16((data[9] << 8) | data[8]) / 32768,
          "quat_3": _getSignInt16((data[11] << 8) | data[10]) / 32768,
        });
      }
    }

    AppLogger.info("Parsed sensor data: $_sensorData");
    _publishToMqtt(_device?.platformName ?? 'unknown_device');
    onStateChanged?.call();
  }

  Future<bool> checkBluetoothAndLocation() async {
    try {
      AppLogger.info('Checking Bluetooth status');
      final bluetoothPermissions = [
        Permission.bluetooth,
        if (defaultTargetPlatform == TargetPlatform.android)
          Permission.bluetoothScan,
        if (defaultTargetPlatform == TargetPlatform.android)
          Permission.bluetoothConnect,
      ];
      final bluetoothStatuses = await bluetoothPermissions.request();
      AppLogger.info('Bluetooth permissions: ${bluetoothStatuses.values}');
      if (bluetoothStatuses.values.any((s) => s.isDenied)) {
        AppLogger.warning('Bluetooth permissions denied');
        onValidationError?.call('Bluetooth permissions denied');
        if (bluetoothStatuses.values.any((s) => s.isPermanentlyDenied)) {
          await openAppSettings();
        }
        return false;
      }

      final isBluetoothOn =
          await FlutterBluePlus.adapterState.first == BluetoothAdapterState.on;
      if (!isBluetoothOn) {
        AppLogger.warning('Bluetooth is off');
        onValidationError?.call('Bluetooth is off');
        return false;
      }

      bool locationGranted = false;
      bool locationServicesEnabled = false;

      if (defaultTargetPlatform == TargetPlatform.iOS) {
        loc.Location location = loc.Location();
        AppLogger.info('Checking location services (iOS)');
        locationServicesEnabled = await location.serviceEnabled();
        if (!locationServicesEnabled) {
          AppLogger.info('Requesting location services (iOS)');
          locationServicesEnabled = await location.requestService();
          if (!locationServicesEnabled) {
            AppLogger.warning('Location services are off (iOS)');
            onValidationError?.call('Location services are off');
            return false;
          }
        }

        AppLogger.info('Checking location permission (iOS)');
        loc.PermissionStatus locPermission = await location.hasPermission();
        if (locPermission == loc.PermissionStatus.denied) {
          AppLogger.info('Requesting location permission (iOS)');
          locPermission = await location.requestPermission();
          if (locPermission == loc.PermissionStatus.granted ||
              locPermission == loc.PermissionStatus.grantedLimited) {
            locationGranted = true;
          } else {
            AppLogger.warning(
                'Location permission denied (iOS): $locPermission');
            onValidationError?.call('Location permission denied');
            return false;
          }
        } else if (locPermission == loc.PermissionStatus.granted ||
            locPermission == loc.PermissionStatus.grantedLimited) {
          locationGranted = true;
        } else if (locPermission == loc.PermissionStatus.deniedForever) {
          AppLogger.warning(
              'Location permission permanently denied (iOS). Please enable it in Settings.');
          onValidationError?.call(
              'Location permission denied. Please enable it in Settings.');
          await openAppSettings();
          return false;
        }
      } else {
        AppLogger.info('Checking location services (Android)');
        locationServicesEnabled =
            await Permission.location.serviceStatus.isEnabled;
        if (!locationServicesEnabled) {
          AppLogger.warning('Location services are off (Android)');
          onValidationError?.call('Location services are off');
          return false;
        }

        AppLogger.info('Checking location permission (Android)');
        var locationStatus = await Permission.locationWhenInUse.status;
        if (!locationStatus.isGranted) {
          locationStatus = await Permission.locationWhenInUse.request();
        }

        if (locationStatus.isGranted) {
          locationGranted = true;
        } else if (locationStatus.isPermanentlyDenied) {
          AppLogger.warning(
              'Location permission permanently denied (Android). Please enable it in Settings.');
          onValidationError?.call(
              'Location permission denied. Please enable it in Settings.');
          await openAppSettings();
          return false;
        } else {
          AppLogger.warning('Location permission denied (Android)');
          onValidationError?.call('Location permission denied');
          return false;
        }
      }

      AppLogger.info('All Bluetooth and Location checks passed');
      return locationGranted && locationServicesEnabled;
    } catch (e) {
      AppLogger.error('Error checking Bluetooth/Location: $e', e);
      onValidationError?.call('Error: $e');
      return false;
    }
  }

  Future<void> connect() async {
    if (_isConnecting || _isConnected) return;

    if (!await checkBluetoothAndLocation()) {
      onConnectionStatusChanged?.call('Disconnected');
      onStateChanged?.call();
      return;
    }

    _isConnecting = true;
    onConnectionStatusChanged?.call('Connecting...');
    onStateChanged?.call();

    try {
      AppLogger.info("Starting scan for WT901BLECL");
      await FlutterBluePlus.startScan(
        withServices: [Guid(serviceUuid)],
        timeout: const Duration(seconds: 10),
      );

      BluetoothDevice? targetDevice;
      await for (List<ScanResult> scanResults in FlutterBluePlus.scanResults) {
        for (ScanResult result in scanResults) {
          String scannedName = result.device.platformName;
          AppLogger.info("Found device: $scannedName");
          if (scannedName.contains(deviceNamePrefix)) {
            targetDevice = result.device;
            break;
          }
        }
        if (targetDevice != null) break;
      }

      await FlutterBluePlus.stopScan();

      if (targetDevice == null) {
        throw Exception("No WT901BLECL device found");
      }

      _device = targetDevice;
      AppLogger.info("Connecting to ${targetDevice.remoteId}");
      await _device!.connect(timeout: const Duration(seconds: 15));

      AppLogger.info("Discovering services...");
      List<BluetoothService> services = await _device!.discoverServices();

      BluetoothService? targetService = services.firstWhere(
        (s) => s.uuid.toString().toLowerCase() == serviceUuid.toLowerCase(),
        orElse: () => throw Exception("Service $serviceUuid not found"),
      );

      _notifyCharacteristic = targetService.characteristics.firstWhere(
        (c) => c.uuid.toString().toLowerCase() == notifyUuid.toLowerCase(),
        orElse: () =>
            throw Exception("Notify characteristic $notifyUuid not found"),
      );

      _controlCharacteristic = targetService.characteristics.firstWhere(
        (c) => c.uuid.toString().toLowerCase() == controlUuid.toLowerCase(),
        orElse: () =>
            throw Exception("Control characteristic $controlUuid not found"),
      );

      AppLogger.info("Enabling notifications...");
      await _notifyCharacteristic!.setNotifyValue(true);
      _notificationSubscription = _notifyCharacteristic!.lastValueStream.listen(
        (data) => _parseSensorData(data),
      );

      AppLogger.info("Sending initial commands...");
      await _controlCharacteristic!.write([0xff, 0xaa, 0x27, 0x3A, 0x00],
          withoutResponse: true); // Magnetic field
      await Future.delayed(const Duration(milliseconds: 100));
      await _controlCharacteristic!.write([0xff, 0xaa, 0x27, 0x51, 0x00],
          withoutResponse: true); // Quaternions

      _isConnected = true;
      _isConnecting = false;
      onConnectionStatusChanged?.call('Connected');
      AppLogger.info("Connection successful, polling...");
      _pollSensor();
    } catch (e) {
      _isConnected = false;
      _isConnecting = false;
      onConnectionStatusChanged?.call('Disconnected');
      onValidationError?.call('Failed to connect: $e');
      AppLogger.error("Connection error: $e", e);
    } finally {
      onStateChanged?.call();
    }
  }

  Future<void> _pollSensor() async {
    while (_isConnected && _controlCharacteristic != null) {
      try {
        await _controlCharacteristic!.write([0xff, 0xaa, 0x27, 0x3A, 0x00],
            withoutResponse: true); // Magnetic field
        await Future.delayed(const Duration(milliseconds: 100));
        await _controlCharacteristic!.write([0xff, 0xaa, 0x27, 0x51, 0x00],
            withoutResponse: true); // Quaternions
        await Future.delayed(const Duration(seconds: 1));
      } catch (e) {
        AppLogger.warning("Polling error: $e");
        break;
      }
    }
  }

  Future<void> disconnect() async {
    if (!_isConnected) return;

    try {
      await _notificationSubscription?.cancel();
      _notificationSubscription = null;
      await _device?.disconnect();
      _device = null;
      _notifyCharacteristic = null;
      _controlCharacteristic = null;
      _isConnected = false;
      _sensorData = {};
      AppLogger.info("Disconnected tilt sensor successfully");
    } catch (e) {
      onValidationError?.call('Error disconnecting: $e');
      AppLogger.warning("Disconnect error: $e");
    } finally {
      onConnectionStatusChanged?.call('Disconnected');
      onStateChanged?.call();
    }
  }

  void dispose() {
    disconnect();
    onStateChanged = null;
    onValidationError = null;
    onConnectionStatusChanged = null;
    AppLogger.info("TiltSensorController disposed");
  }
}
