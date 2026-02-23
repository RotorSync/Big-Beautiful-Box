import 'dart:async';
import 'dart:convert';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:location/location.dart' as loc;
import 'package:mqtt_client/mqtt_client.dart';
import '../services/mqtt_service.dart';
import '../utils/logger.dart';

class RaspiController {
  BluetoothDevice? _device;
  List<BluetoothCharacteristic?> _chtCharacteristics = List.filled(6, null);
  List<BluetoothCharacteristic?> _egtCharacteristics = List.filled(6, null);
  List<StreamSubscription?> _chtSubscriptions = List.filled(6, null);
  List<StreamSubscription?> _egtSubscriptions = List.filled(6, null);
  final Map<String, Map<String, dynamic>> _devicesData = {};

  bool _isConnected = false;
  bool _isConnecting = false;
  bool _hasShownMqttError = false;
  StreamSubscription<BluetoothConnectionState>? _connectionStateSubscription;

  String raspiStatus = 'Disconnected';
  bool isLoadingRaspi = false;

  final TextEditingController nameController = TextEditingController();
  final TextEditingController deviceNameController = TextEditingController();
  final TextEditingController mqttTopicController = TextEditingController();

  MQTTService? _mqttService;
  String? _mqttTopic;

  VoidCallback? onStateChanged;
  Function(String)? onValidationError;
  Function(String)? onConnectionStatusChanged;

  // UUIDs from the GATT server
  static const String chtServiceUuid = "00001820-0000-1000-8000-00805f9b34fb";
  static const String egtServiceUuid = "00001821-0000-1000-8000-00805f9b34fb";
  static const List<String> chtCharacteristicUuids = [
    "00002a70-0000-1000-8000-00805f9b34fb",
    "00002a71-0000-1000-8000-00805f9b34fb",
    "00002a72-0000-1000-8000-00805f9b34fb",
    "00002a73-0000-1000-8000-00805f9b34fb",
    "00002a74-0000-1000-8000-00805f9b34fb",
    "00002a75-0000-1000-8000-00805f9b34fb",
  ];
  static const List<String> egtCharacteristicUuids = [
    "00002a76-0000-1000-8000-00805f9b34fb",
    "00002a77-0000-1000-8000-00805f9b34fb",
    "00002a78-0000-1000-8000-00805f9b34fb",
    "00002a79-0000-1000-8000-00805f9b34fb",
    "00002a7a-0000-1000-8000-00805f9b34fb",
    "00002a7b-0000-1000-8000-00805f9b34fb",
  ];

  RaspiController({MQTTService? mqttService}) {
    _mqttService = mqttService ?? MQTTService();
    _initializeDefaultData();
    _mqttService?.setConnectionStatusCallback((status) {
      if (status == 'Connected' && _isConnected) {
        _hasShownMqttError = false;
        AppLogger.info("MQTT connected, starting publishing for RasPi");
        for (var identifier in _devicesData.keys) {
          _publishToMQTT(identifier);
        }
      }
    });
  }

  void _initializeDefaultData() {
    _devicesData.clear();
    AppLogger.info("Initialized default data for RasPi devices");
  }

  Map<String, dynamic> getDeviceData(String identifier) {
    final normalizedIdentifier = identifier.toLowerCase();
    final data = _devicesData[normalizedIdentifier] ??
        {
          "cht_1": 0.0,
          "cht_2": 0.0,
          "cht_3": 0.0,
          "cht_4": 0.0,
          "cht_5": 0.0,
          "cht_6": 0.0,
          "egt_1": 0.0,
          "egt_2": 0.0,
          "egt_3": 0.0,
          "egt_4": 0.0,
          "egt_5": 0.0,
          "egt_6": 0.0,
        };
    AppLogger.debug("getDeviceData called for $normalizedIdentifier: $data");
    return data;
  }

  bool get isConnected => _isConnected;

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

  void _publishToMQTT(String identifier) {
    if (_mqttService == null ||
        !_mqttService!.isInitialized ||
        !_mqttService!.isConnected) {
      if (!_hasShownMqttError) {
        AppLogger.warning(
            "MQTT service is not connected. Readings cannot be published.");
        onValidationError?.call(
            "MQTT service is not connected. Readings cannot be published.");
        _hasShownMqttError = true;
      }
      return;
    }

    if (_mqttTopic == null || _mqttTopic!.isEmpty) {
      if (!_hasShownMqttError) {
        AppLogger.warning(
            "MQTT topic not configured. Readings cannot be published.");
        onValidationError
            ?.call("MQTT topic not configured. Readings cannot be published.");
        _hasShownMqttError = true;
      }
      return;
    }

    _hasShownMqttError = false;
    final normalizedIdentifier = identifier.toLowerCase();
    final data = _devicesData[normalizedIdentifier];
    if (data == null) {
      AppLogger.warning("No data for device $normalizedIdentifier to publish");
      return;
    }

    final payload = {
      "device_id": normalizedIdentifier,
      "timestamp": DateTime.now().toUtc().toIso8601String(),
      "cht": {
        for (int i = 1; i <= 6; i++) "$i": data["cht_$i"] ?? 0.0,
      },
      "egt": {
        for (int i = 1; i <= 6; i++) "$i": data["egt_$i"] ?? 0.0,
      },
    };
    final payloadJson = jsonEncode(payload);

    // Publish to the original topic from raspi_devices
    try {
      _mqttService!.publish(_mqttTopic!, payloadJson, qos: MqttQos.atLeastOnce);
      AppLogger.info(
          "Published to original MQTT topic $_mqttTopic: $payloadJson");
    } catch (e) {
      if (!_hasShownMqttError) {
        AppLogger.error(
            "Failed to publish to original MQTT topic $_mqttTopic: $e", e);
        onValidationError?.call("Failed to publish to MQTT: $e");
        _hasShownMqttError = true;
      }
    }

    // Fetch user's type and id, then publish to new topic
    _fetchUserSerialNumber().then((userSerialData) {
      if (userSerialData != null) {
        final type = userSerialData['type'] as String;
        final id = userSerialData['id'] as String;
        final newTopic = '$type/$id/raspi';
        try {
          _mqttService!
              .publish(newTopic, payloadJson, qos: MqttQos.atLeastOnce);
          AppLogger.info("Published to new MQTT topic $newTopic: $payloadJson");
        } catch (e) {
          if (!_hasShownMqttError) {
            AppLogger.error(
                "Failed to publish to new MQTT topic $newTopic: $e", e);
            onValidationError?.call("Failed to publish to MQTT: $e");
            _hasShownMqttError = true;
          }
        }
      } else {
        AppLogger.warning(
            'Cannot publish to new MQTT topic: No serial number data found for user');
      }
    }).catchError((e) {
      AppLogger.error(
          'Error fetching serial number for MQTT publishing: $e', e);
    });
  }

  void _parseTemperature(
      List<int> data, String identifier, String type, int probeIndex) {
    if (data.isEmpty) {
      return;
    }
    if (data.length != 2) {
      AppLogger.warning(
          "Invalid temperature data length for $type probe ${probeIndex + 1}: ${data.length}");
      return;
    }

    final byteData = ByteData.sublistView(Uint8List.fromList(data));
    double temperature = byteData.getInt16(0, Endian.little).toDouble();
    final normalizedIdentifier = identifier.toLowerCase();

    _devicesData[normalizedIdentifier] ??= {};
    _devicesData[normalizedIdentifier]!["${type}_${probeIndex + 1}"] =
        temperature;

    AppLogger.debug(
        "Parsed $type for probe ${probeIndex + 1}: $temperature°F for $normalizedIdentifier");

    _publishToMQTT(normalizedIdentifier);
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
      AppLogger.debug('Bluetooth permissions: ${bluetoothStatuses.values}');
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
    if (_isConnecting || _isConnected) {
      AppLogger.info("Already connecting or connected, skipping connect");
      return;
    }

    if (!await checkBluetoothAndLocation()) {
      raspiStatus = 'Disconnected';
      isLoadingRaspi = false;
      _isConnected = false;
      _isConnecting = false;
      onConnectionStatusChanged?.call(raspiStatus);
      onStateChanged?.call();
      return;
    }

    _isConnecting = true;
    raspiStatus = 'Connecting...';
    isLoadingRaspi = true;
    _hasShownMqttError = false;
    onConnectionStatusChanged?.call(raspiStatus);
    onStateChanged?.call();

    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user == null) throw Exception("User not authenticated");

      final devicesSnapshot = await FirebaseFirestore.instance
          .collection('users')
          .doc(user.uid)
          .collection('raspi_devices')
          .get();

      if (devicesSnapshot.docs.isEmpty) {
        throw Exception("No RasPi devices configured");
      }

      final deviceData = devicesSnapshot.docs.first.data();
      String identifier = deviceData['device_name'];
      _mqttTopic = deviceData['mqtt_topic'] as String?;
      AppLogger.info(
          "Attempting to connect to RasPi: $identifier, MQTT Topic: $_mqttTopic");

      AppLogger.info("Starting scan for RasPi: $identifier");
      await FlutterBluePlus.startScan(
        withServices: [Guid(chtServiceUuid), Guid(egtServiceUuid)],
        timeout: const Duration(seconds: 10),
      );

      BluetoothDevice? targetDevice;
      await for (List<ScanResult> scanResults in FlutterBluePlus.scanResults) {
        for (ScanResult result in scanResults) {
          String scannedIdentifier = result.device.platformName.toLowerCase();
          AppLogger.debug("Found device: $scannedIdentifier");
          if (scannedIdentifier == identifier.toLowerCase()) {
            targetDevice = result.device;
            break;
          }
        }
        if (targetDevice != null) break;
      }

      await FlutterBluePlus.stopScan();

      if (targetDevice == null) {
        throw Exception("RasPi device with identifier $identifier not found");
      }

      _device = targetDevice;
      AppLogger.info("Connecting to RasPi ${targetDevice.remoteId}");
      await _device!.connect(timeout: const Duration(seconds: 15));

      // Verify connection state
      if (await _device!.connectionState.first !=
          BluetoothConnectionState.connected) {
        throw Exception("Failed to establish connection with $identifier");
      }

      // Listen for unexpected disconnections
      _connectionStateSubscription = _device!.connectionState.listen((state) {
        if (state == BluetoothConnectionState.disconnected && _isConnected) {
          AppLogger.warning("Unexpected RasPi disconnection detected");
          _isConnected = false;
          _isConnecting = false;
          raspiStatus = 'Disconnected';
          isLoadingRaspi = false;
          _mqttTopic = null;
          _hasShownMqttError = false;
          onConnectionStatusChanged?.call(raspiStatus);
          onStateChanged?.call();
        }
      });

      AppLogger.info("Discovering services for RasPi...");
      List<BluetoothService> services = await _device!.discoverServices();

      for (var service in services) {
        AppLogger.debug("Discovered service: ${service.uuid.toString()}");
        for (var characteristic in service.characteristics) {
          AppLogger.debug(
              "  Characteristic: ${characteristic.uuid.toString()}");
        }
      }

      BluetoothService? chtService = services.firstWhere(
        (s) {
          String serviceUuid = s.uuid.toString().toLowerCase();
          bool isMatch = serviceUuid == chtServiceUuid.toLowerCase() ||
              serviceUuid == "1820";
          if (isMatch) {
            AppLogger.info("CHT Service found: $serviceUuid");
          }
          return isMatch;
        },
        orElse: () {
          AppLogger.error("CHT Service $chtServiceUuid not found");
          throw Exception("CHT Service $chtServiceUuid not found");
        },
      );

      BluetoothService? egtService = services.firstWhere(
        (s) {
          String serviceUuid = s.uuid.toString().toLowerCase();
          bool isMatch = serviceUuid == egtServiceUuid.toLowerCase() ||
              serviceUuid == "1821";
          if (isMatch) {
            AppLogger.info("EGT Service found: $serviceUuid");
          }
          return isMatch;
        },
        orElse: () {
          AppLogger.error("EGT Service $egtServiceUuid not found");
          throw Exception("EGT Service $egtServiceUuid not found");
        },
      );

      for (int i = 0; i < 6; i++) {
        _chtCharacteristics[i] = chtService.characteristics.firstWhere(
          (c) {
            String charUuid = c.uuid.toString().toLowerCase();
            String targetUuid = chtCharacteristicUuids[i].toLowerCase();
            bool isMatch = charUuid == targetUuid ||
                charUuid == targetUuid.substring(4, 8);
            return isMatch;
          },
          orElse: () {
            AppLogger.error(
                "CHT Characteristic ${chtCharacteristicUuids[i]} not found");
            throw Exception(
                "CHT Characteristic ${chtCharacteristicUuids[i]} not found");
          },
        );

        await _chtCharacteristics[i]!.setNotifyValue(true);
        _chtSubscriptions[i] = _chtCharacteristics[i]!.lastValueStream.listen(
              (data) => _parseTemperature(data, identifier, "cht", i),
            );
      }

      for (int i = 0; i < 6; i++) {
        _egtCharacteristics[i] = egtService.characteristics.firstWhere(
          (c) {
            String charUuid = c.uuid.toString().toLowerCase();
            String targetUuid = egtCharacteristicUuids[i].toLowerCase();
            bool isMatch = charUuid == targetUuid ||
                charUuid == targetUuid.substring(4, 8);
            return isMatch;
          },
          orElse: () {
            AppLogger.error(
                "EGT Characteristic ${egtCharacteristicUuids[i]} not found");
            throw Exception(
                "EGT Characteristic ${egtCharacteristicUuids[i]} not found");
          },
        );

        await _egtCharacteristics[i]!.setNotifyValue(true);
        _egtSubscriptions[i] = _egtCharacteristics[i]!.lastValueStream.listen(
              (data) => _parseTemperature(data, identifier, "egt", i),
            );
      }

      // Confirm connection state after setup
      if (await _device!.connectionState.first ==
          BluetoothConnectionState.connected) {
        _isConnected = true;
        _isConnecting = false;
        raspiStatus = 'Connected';
        isLoadingRaspi = false;
        AppLogger.info("RasPi connection successful");
      } else {
        throw Exception("Connection lost during setup for $identifier");
      }
    } catch (e) {
      _isConnected = false;
      _isConnecting = false;
      raspiStatus = 'Disconnected';
      isLoadingRaspi = false;
      AppLogger.error("RasPi connection error: $e", e);
      onValidationError?.call('Failed to connect to RasPi: $e');
      await _cleanupOnFailure();
    } finally {
      onConnectionStatusChanged?.call(raspiStatus);
      onStateChanged?.call();
    }
  }

  Future<void> _cleanupOnFailure() async {
    try {
      for (var sub in _chtSubscriptions) {
        await sub?.cancel();
      }
      for (var sub in _egtSubscriptions) {
        await sub?.cancel();
      }
      _chtSubscriptions = List.filled(6, null);
      _egtSubscriptions = List.filled(6, null);
      await _connectionStateSubscription?.cancel();
      _connectionStateSubscription = null;
      if (_device != null) {
        await _device!.disconnect();
      }
      _device = null;
      _chtCharacteristics = List.filled(6, null);
      _egtCharacteristics = List.filled(6, null);
      _mqttTopic = null;
      _hasShownMqttError = false;
      AppLogger.info("Cleaned up resources after RasPi connection failure");
    } catch (e) {
      AppLogger.warning("Error during cleanup: $e");
    }
  }

  Future<void> disconnect() async {
    try {
      for (var sub in _chtSubscriptions) {
        await sub?.cancel();
      }
      for (var sub in _egtSubscriptions) {
        await sub?.cancel();
      }
      _chtSubscriptions = List.filled(6, null);
      _egtSubscriptions = List.filled(6, null);
      await _connectionStateSubscription?.cancel();
      _connectionStateSubscription = null;
      if (_device != null) {
        await _device!.disconnect();
      }
      _device = null;
      _chtCharacteristics = List.filled(6, null);
      _egtCharacteristics = List.filled(6, null);
      _isConnected = false;
      _isConnecting = false;
      raspiStatus = 'Disconnected';
      isLoadingRaspi = false;
      _mqttTopic = null;
      _hasShownMqttError = false;
      AppLogger.info("Disconnected RasPi successfully");
    } catch (e) {
      AppLogger.warning("RasPi disconnect error: $e");
      onValidationError?.call('Error disconnecting RasPi: $e');
    } finally {
      onConnectionStatusChanged?.call(raspiStatus);
      onStateChanged?.call();
    }
  }

  Future<void> addDevice(
      String name, String deviceName, String mqttTopic) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) {
      AppLogger.warning('User not authenticated');
      onValidationError?.call('User not authenticated');
      return;
    }

    try {
      await FirebaseFirestore.instance
          .collection('users')
          .doc(user.uid)
          .collection('raspi_devices')
          .add({
        'name': name,
        'device_name': deviceName,
        'mqtt_topic': mqttTopic,
        'created_at': FieldValue.serverTimestamp(),
        'updated_at': FieldValue.serverTimestamp(),
      });
      AppLogger.info("Added new RasPi device: $deviceName");
    } catch (e) {
      AppLogger.error("Error adding RasPi device: $e", e);
      onValidationError?.call('Failed to add RasPi device: $e');
    } finally {
      onStateChanged?.call();
    }
  }

  Future<void> updateDevice(
      String deviceId, String name, String deviceName, String mqttTopic) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) {
      AppLogger.warning('User not authenticated');
      onValidationError?.call('User not authenticated');
      return;
    }

    try {
      await FirebaseFirestore.instance
          .collection('users')
          .doc(user.uid)
          .collection('raspi_devices')
          .doc(deviceId)
          .update({
        'name': name,
        'device_name': deviceName,
        'mqtt_topic': mqttTopic,
        'updated_at': FieldValue.serverTimestamp(),
      });
      AppLogger.info("Updated RasPi device: $deviceId");
    } catch (e) {
      AppLogger.error("Error updating RasPi device: $e", e);
      onValidationError?.call('Failed to update RasPi device: $e');
    } finally {
      onStateChanged?.call();
    }
  }

  Future<void> deleteDevice(String deviceId) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) {
      AppLogger.warning('User not authenticated');
      onValidationError?.call('User not authenticated');
      return;
    }

    try {
      await FirebaseFirestore.instance
          .collection('users')
          .doc(user.uid)
          .collection('raspi_devices')
          .doc(deviceId)
          .delete();
      AppLogger.info("Deleted RasPi device: $deviceId");
    } catch (e) {
      AppLogger.error("Error deleting RasPi device: $e", e);
      onValidationError?.call('Failed to delete RasPi device: $e');
    } finally {
      onStateChanged?.call();
    }
  }

  void dispose() {
    disconnect();
    nameController.dispose();
    deviceNameController.dispose();
    mqttTopicController.dispose();
    onStateChanged = null;
    onValidationError = null;
    onConnectionStatusChanged = null;
    AppLogger.info("RaspiController disposed");
  }
}
