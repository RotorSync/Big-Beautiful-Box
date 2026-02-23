import 'dart:async';
import 'dart:convert';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:mqtt_client/mqtt_client.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:location/location.dart' as loc;
import '../services/mqtt_service.dart';
import '../utils/logger.dart';

class BmsController {
  BluetoothDevice? _device;
  BluetoothCharacteristic? _notifyCharacteristic;
  BluetoothCharacteristic? _controlCharacteristic;
  StreamSubscription? _notificationSubscription;
  final Map<String, Map<String, dynamic>> _devicesData = {};
  List<int> _frameBuffer = [];
  bool _isConnected = false;
  bool _isConnecting = false;
  bool _hasNotifiedLowSoc = false;

  bool bmsSwitchState = false;
  String bmsStatus = 'Disconnected';
  bool isLoadingBms = false;

  final TextEditingController nameController = TextEditingController();
  final TextEditingController deviceNameController = TextEditingController();
  final TextEditingController thresholdController = TextEditingController();
  final TextEditingController mqttTopicController = TextEditingController();
  bool isEditing = false;
  String? editingDeviceId;

  final MQTTService mqttService;

  VoidCallback? onStateChanged;
  Function(String)? onValidationError;
  Function(String)? onConnectionStatusChanged;

  static const int jbdPktStart = 0xDD;
  static const int jbdPktEnd = 0x77;
  static const int jbdCmdRead = 0xA5;
  static const int jbdCmdWrite = 0x5A;
  static const int jbdCmdHwInfo = 0x03;
  static const int jbdCmdCellInfo = 0x04;
  static const int jbdCmdHwVer = 0x05;
  static const int jbdCmdErrorCounts = 0xAA;
  static const int jbdCmdMos = 0xE1;
  static const int jbdCmdExitFactory = 0x01;
  static const int jbdMosCharge = 0x01;
  static const int jbdMosDischarge = 0x02;

  static const String serviceUuid = "0000ff00-0000-1000-8000-00805f9b34fb";
  static const String notifyUuid = "0000ff01-0000-1000-8000-00805f9b34fb";
  static const String controlUuid = "0000ff02-0000-1000-8000-00805f9b34fb";

  static const List<String> errors = [
    "Cell overvoltage",
    "Cell undervoltage",
    "Pack overvoltage",
    "Pack undervoltage",
    "Charging over temperature",
    "Charging under temperature",
    "Discharging over temperature",
    "Discharging under temperature",
    "Charging overcurrent",
    "Discharging overcurrent",
    "Short circuit",
    "IC front-end error",
    "Mosfet Software Lock",
    "Charge timeout Close",
    "Unknown (0x0E)",
    "Unknown (0x0F)"
  ];

  final FlutterLocalNotificationsPlugin _notificationsPlugin =
      FlutterLocalNotificationsPlugin();

  BmsController({required this.mqttService}) {
    _initializeDefaultData();
    _initializeNotifications();
  }

  void _initializeDefaultData() {
    _devicesData.clear();
    AppLogger.info("Initialized default data for BMS devices");
  }

  void _initializeNotifications() {
    const AndroidInitializationSettings androidSettings =
        AndroidInitializationSettings('notification_icon');
    const DarwinInitializationSettings iosSettings =
        DarwinInitializationSettings();
    const InitializationSettings initSettings =
        InitializationSettings(android: androidSettings, iOS: iosSettings);
    _notificationsPlugin.initialize(initSettings);
    AppLogger.info("Notifications initialized");
  }

  Future<void> _showLowSocNotification(int soc, int threshold) async {
    const AndroidNotificationDetails androidDetails =
        AndroidNotificationDetails(
      'low_soc_channel',
      'Low SOC Alerts',
      importance: Importance.high,
      priority: Priority.high,
      icon: 'notification_icon',
      largeIcon: DrawableResourceAndroidBitmap('notification_icon'),
    );
    const DarwinNotificationDetails iosDetails = DarwinNotificationDetails(
      presentAlert: true,
      presentBadge: true,
      presentSound: true,
    );
    const NotificationDetails platformDetails = NotificationDetails(
      android: androidDetails,
      iOS: iosDetails,
    );

    await _notificationsPlugin.show(
      0,
      'Low Battery Alert ⚠️',
      'State of Charge dropped to $threshold%',
      platformDetails,
    );
    AppLogger.info(
        "Low SOC notification shown: SOC=$soc, Threshold=$threshold");
  }

  Map<String, dynamic> getDeviceData(String identifier) {
    final data = _devicesData[identifier] ??
        {
          "total_voltage": 0.0,
          "current": 0.0,
          "power": 0.0,
          "capacity_remaining": 0.0,
          "nominal_capacity": 0.0,
          "charging_cycles": 0,
          "state_of_charge": 0,
          "total_cells": 0,
          "min_cell_voltage": 0.0,
          "max_cell_voltage": 0.0,
          "min_voltage_cell": 0,
          "max_voltage_cell": 0,
          "delta_cell_voltage": 0.0,
          "average_cell_voltage": 0.0,
          "software_version": 0.0,
          "balancing": false,
          "errors": "",
          "charging": false,
          "discharging": false,
          "operation_status_bitmask": 0,
        };
    AppLogger.info("getDeviceData called for $identifier: $data");
    return data;
  }

  bool get isConnected => _isConnected;

  int _calculateCrc(List<int> data) {
    int checksum = 0;
    for (int byte in data) {
      checksum = (checksum - byte) & 0xFFFF;
    }
    return checksum;
  }

  List<int> _buildCommand(int action, int function) {
    List<int> frame = [jbdPktStart, action, function, 0x00];
    int crc = _calculateCrc(frame.sublist(2, 4));
    frame.addAll([crc >> 8, crc & 0xFF, jbdPktEnd]);
    return frame;
  }

  List<int> _buildWriteCommand(int register, int value) {
    List<int> frame = [jbdPktStart, jbdCmdWrite, register, 0x02];
    frame.addAll([(value >> 8) & 0xFF, value & 0xFF]);
    int crc = _calculateCrc(frame.sublist(2, 6));
    frame.addAll([crc >> 8, crc & 0xFF, jbdPktEnd]);
    return frame;
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

  Future<String?> _getMqttTopic(String identifier) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) return null;

    final deviceDoc = await FirebaseFirestore.instance
        .collection('users')
        .doc(user.uid)
        .collection('bms_devices')
        .where('device_name', isEqualTo: identifier)
        .get();

    if (deviceDoc.docs.isNotEmpty) {
      return deviceDoc.docs.first['mqtt_topic']?.toString() ?? 'bms/telemetry';
    }
    return null;
  }

  void _publishToMqtt(String identifier) async {
    if (!mqttService.isConnected || !_devicesData.containsKey(identifier)) {
      AppLogger.warning(
          'Cannot publish to MQTT: MQTT not connected or no data for $identifier');
      return;
    }

    final topic = await _getMqttTopic(identifier);
    if (topic == null) {
      AppLogger.warning('No MQTT topic found for $identifier');
      return;
    }

    final data = _devicesData[identifier]!;
    final cellVoltages = <String, double>{};
    for (int i = 1; i <= (data['total_cells'] ?? 0); i++) {
      cellVoltages['$i'] = data['cell_voltage_$i'] ?? 0.0;
    }

    final temperatures = <String, double>{};
    for (int i = 1; i <= 6; i++) {
      if (data.containsKey('temperature_$i')) {
        temperatures['$i'] = data['temperature_$i'] ?? 0.0;
      }
    }

    final payload = {
      'device_id': identifier,
      'timestamp': DateTime.now().toUtc().toIso8601String(),
      'total_voltage': data['total_voltage'] ?? 0.0,
      'current': data['current'] ?? 0.0,
      'state_of_charge': data['state_of_charge'] ?? 0,
      'power': data['power'] ?? 0.0,
      'capacity_remaining': data['capacity_remaining'] ?? 0.0,
      'nominal_capacity': data['nominal_capacity'] ?? 0.0,
      'charging_cycles': data['charging_cycles'] ?? 0,
      'total_cells': data['total_cells'] ?? 0,
      'cell_voltages': cellVoltages,
      'errors': data['errors'] ?? 'None',
      'charging': data['charging'] ?? false,
      'discharging': data['discharging'] ?? false,
      'balancing': data['balancing'] ?? false,
      'temperatures': temperatures,
    };
    final payloadString = jsonEncode(payload);

    // Publish to the original topic from bms_devices
    try {
      mqttService.publish(
        topic,
        payloadString,
        qos: MqttQos.atLeastOnce,
      );
      AppLogger.info(
          'Published BMS data to original MQTT topic $topic for $identifier: $payload');
    } catch (e) {
      AppLogger.error('Failed to publish BMS data to $topic: $e', e);
    }

    // Fetch user's type and id, then publish to new topic
    final userSerialData = await _fetchUserSerialNumber();
    if (userSerialData != null) {
      final type = userSerialData['type'] as String;
      final id = userSerialData['id'] as String;
      final newTopic = '$type/$id/bms';
      try {
        mqttService.publish(
          newTopic,
          payloadString,
          qos: MqttQos.atLeastOnce,
        );
        AppLogger.info(
            'Published BMS data to new MQTT topic $newTopic for $identifier: $payload');
      } catch (e) {
        AppLogger.error('Failed to publish BMS data to $newTopic: $e', e);
      }
    } else {
      AppLogger.warning(
          'Cannot publish to new MQTT topic: No serial number data found for user');
    }
  }

  void _parseHardwareInfo(List<int> data, String identifier) {
    if (data.length < 23) {
      AppLogger.warning("Invalid hardware info length: ${data.length}");
      return;
    }

    final byteData = ByteData.sublistView(Uint8List.fromList(data));
    double totalVoltage = byteData.getUint16(0, Endian.big) * 0.01;
    double current = byteData.getInt16(2, Endian.big) * 0.01;
    double power = totalVoltage * current;
    double capacityRemaining = byteData.getUint16(4, Endian.big) * 0.01;
    double nominalCapacity = byteData.getUint16(6, Endian.big) * 0.01;
    int chargingCycles = byteData.getUint16(8, Endian.big);
    int balanceStatusBitmask = byteData.getUint32(12, Endian.big);
    int errorsBitmask = byteData.getUint16(16, Endian.big);
    double softwareVersion = (data[18] >> 4) + (data[18] & 0x0F) * 0.1;
    int stateOfCharge = data[19];
    int mosfetStatus = data[20];
    int batteryStrings = data[21];
    int temperatureSensors = data[22] < 6 ? data[22] : 6;

    String errorString = errors
        .asMap()
        .entries
        .where((entry) => (errorsBitmask & (1 << entry.key)) != 0)
        .map((entry) => entry.value)
        .join(";");

    _devicesData[identifier] ??= {};
    _devicesData[identifier]!.addAll({
      "total_voltage": totalVoltage,
      "current": current,
      "power": power,
      "capacity_remaining": capacityRemaining,
      "nominal_capacity": nominalCapacity,
      "charging_cycles": chargingCycles,
      "state_of_charge": stateOfCharge,
      "software_version": softwareVersion,
      "balancing": balanceStatusBitmask > 0,
      "errors": errorString.isEmpty ? "None" : errorString,
      "charging": (mosfetStatus & jbdMosCharge) != 0,
      "discharging": (mosfetStatus & jbdMosDischarge) != 0,
      "total_cells": batteryStrings,
      "operation_status_bitmask": mosfetStatus,
    });

    for (int i = 0; i < temperatureSensors; i++) {
      double temp = (byteData.getUint16(23 + i * 2, Endian.big) - 2731) * 0.1;
      _devicesData[identifier]!["temperature_${i + 1}"] = temp;
    }

    AppLogger.info(
        "Parsed hardware info for $identifier: ${_devicesData[identifier]}");
    _checkSocThreshold(identifier, stateOfCharge);
    _publishToMqtt(identifier);
    onStateChanged?.call();
  }

  void _checkSocThreshold(String identifier, int soc) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) return;

    final deviceDoc = await FirebaseFirestore.instance
        .collection('users')
        .doc(user.uid)
        .collection('bms_devices')
        .where('device_name', isEqualTo: identifier)
        .get();

    if (deviceDoc.docs.isNotEmpty) {
      final threshold = int.parse(deviceDoc.docs.first['threshold'] ?? '50');
      if (soc <= threshold && !_hasNotifiedLowSoc) {
        await _showLowSocNotification(soc, threshold);
        _hasNotifiedLowSoc = true;
      } else if (soc > threshold) {
        _hasNotifiedLowSoc = false;
      }
    }
  }

  void _parseCellInfo(List<int> data, String identifier) {
    if (data.length < 2 || data.length % 2 != 0) {
      AppLogger.warning("Invalid cell info length: ${data.length}");
      return;
    }

    int cells = (data.length ~/ 2) < 32 ? (data.length ~/ 2) : 32;
    double minCellVoltage = 100.0;
    double maxCellVoltage = -100.0;
    double totalVoltage = 0.0;
    int minVoltageCell = 0;
    int maxVoltageCell = 0;

    final byteData = ByteData.sublistView(Uint8List.fromList(data));
    _devicesData[identifier] ??= {};
    _devicesData[identifier]!["total_cells"] = cells;

    for (int i = 0; i < cells; i++) {
      double voltage = byteData.getUint16(i * 2, Endian.big) * 0.001;
      _devicesData[identifier]!["cell_voltage_${i + 1}"] = voltage;
      totalVoltage += voltage;
      if (voltage < minCellVoltage) {
        minCellVoltage = voltage;
        minVoltageCell = i + 1;
      }
      if (voltage > maxCellVoltage) {
        maxCellVoltage = voltage;
        maxVoltageCell = i + 1;
      }
    }

    double averageCellVoltage = cells > 0 ? totalVoltage / cells : 0;
    _devicesData[identifier]!.addAll({
      "min_cell_voltage": minCellVoltage,
      "max_cell_voltage": maxCellVoltage,
      "min_voltage_cell": minVoltageCell,
      "max_voltage_cell": maxVoltageCell,
      "delta_cell_voltage": maxCellVoltage - minCellVoltage,
      "average_cell_voltage": averageCellVoltage,
    });
    AppLogger.info(
        "Parsed cell info for $identifier: ${_devicesData[identifier]}");
    _publishToMqtt(identifier);
    onStateChanged?.call();
  }

  void _handleNotification(List<int> data, String identifier) {
    AppLogger.info(
        "Raw notification data for $identifier: ${data.map((e) => e.toRadixString(16)).join(' ')}");
    _frameBuffer.addAll(data);

    while (true) {
      int startIdx = _frameBuffer.indexOf(jbdPktStart);
      int endIdx = _frameBuffer.indexOf(jbdPktEnd);

      if (startIdx == -1 || endIdx == -1 || endIdx < startIdx) {
        if (startIdx != -1 && endIdx == -1) {
          _frameBuffer = _frameBuffer.sublist(startIdx);
        } else {
          _frameBuffer.clear();
        }
        break;
      }

      List<int> frame = _frameBuffer.sublist(startIdx, endIdx + 1);
      _frameBuffer = _frameBuffer.sublist(endIdx + 1);

      if (frame.length < 7) {
        AppLogger.warning(
            "Frame too short: ${frame.map((e) => e.toRadixString(16)).join(' ')}");
        continue;
      }

      int function = frame[1];
      int dataLen = frame[3];
      if (frame.length != 4 + dataLen + 3) {
        AppLogger.warning(
            "Invalid frame length: ${frame.map((e) => e.toRadixString(16)).join(' ')}");
        continue;
      }

      List<int> payload = frame.sublist(4, 4 + dataLen);
      final byteData = ByteData.sublistView(Uint8List.fromList(
          frame.sublist(frame.length - 3, frame.length - 1)));
      int crc = byteData.getUint16(0, Endian.big);
      if (_calculateCrc(frame.sublist(2, 4 + dataLen)) != crc) {
        AppLogger.warning(
            "CRC mismatch: ${frame.map((e) => e.toRadixString(16)).join(' ')}");
        continue;
      }

      AppLogger.info(
          "Valid frame for $identifier: ${frame.map((e) => e.toRadixString(16)).join(' ')}");
      if (function == jbdCmdHwInfo) {
        _parseHardwareInfo(payload, identifier);
      } else if (function == jbdCmdCellInfo) {
        _parseCellInfo(payload, identifier);
      }
    }
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
          AppLogger.warning(' institutionalized services are off (Android)');
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
      bmsStatus = 'Disconnected';
      isLoadingBms = false;
      onConnectionStatusChanged?.call(bmsStatus);
      onStateChanged?.call();
      return;
    }

    _isConnecting = true;
    bmsStatus = 'Connecting...';
    isLoadingBms = true;
    onConnectionStatusChanged?.call(bmsStatus);
    onStateChanged?.call();

    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user == null) throw Exception("User not authenticated");

      final devicesSnapshot = await FirebaseFirestore.instance
          .collection('users')
          .doc(user.uid)
          .collection('bms_devices')
          .get();

      if (devicesSnapshot.docs.isEmpty) {
        throw Exception("No BMS devices configured");
      }

      final deviceData = devicesSnapshot.docs.first.data();
      String identifier = deviceData['device_name'];
      AppLogger.info("Attempting to connect to: $identifier");

      AppLogger.info("Starting scan for: $identifier");
      await FlutterBluePlus.startScan(
        withServices: [Guid(serviceUuid)],
        timeout: const Duration(seconds: 10),
      );

      BluetoothDevice? targetDevice;
      await for (List<ScanResult> scanResults in FlutterBluePlus.scanResults) {
        for (ScanResult result in scanResults) {
          String scannedIdentifier = result.device.platformName;
          AppLogger.info("Found device: $scannedIdentifier");
          if (scannedIdentifier == identifier) {
            targetDevice = result.device;
            break;
          }
        }
        if (targetDevice != null) break;
      }

      await FlutterBluePlus.stopScan();

      if (targetDevice == null) {
        throw Exception("Device with identifier $identifier not found");
      }

      _device = targetDevice;
      AppLogger.info("Connecting to ${targetDevice.remoteId}");
      await _device!.connect(timeout: const Duration(seconds: 15));

      AppLogger.info("Discovering services...");
      List<BluetoothService> services = await _device!.discoverServices();

      BluetoothService? targetService = services.firstWhere(
        (s) =>
            s.uuid.toString().toLowerCase() == serviceUuid.toLowerCase() ||
            s.uuid.toString() == "ff00",
        orElse: () => throw Exception("Service $serviceUuid not found"),
      );

      _notifyCharacteristic = targetService.characteristics.firstWhere(
        (c) =>
            c.uuid.toString().toLowerCase() == notifyUuid.toLowerCase() ||
            c.uuid.toString() == "ff01",
        orElse: () =>
            throw Exception("Notify characteristic $notifyUuid not found"),
      );

      _controlCharacteristic = targetService.characteristics.firstWhere(
        (c) =>
            c.uuid.toString().toLowerCase() == controlUuid.toLowerCase() ||
            c.uuid.toString() == "ff02",
        orElse: () =>
            throw Exception("Control characteristic $controlUuid not found"),
      );

      AppLogger.info("Enabling notifications...");
      await _notifyCharacteristic!.setNotifyValue(true);
      _notificationSubscription = _notifyCharacteristic!.lastValueStream.listen(
        (data) => _handleNotification(data, identifier),
      );

      AppLogger.info("Sending initial commands...");
      await _controlCharacteristic!.write(
          _buildCommand(jbdCmdRead, jbdCmdHwInfo),
          withoutResponse: true);
      await Future.delayed(const Duration(seconds: 1));
      await _controlCharacteristic!.write(
          _buildCommand(jbdCmdRead, jbdCmdCellInfo),
          withoutResponse: true);

      _isConnected = true;
      _isConnecting = false;
      bmsSwitchState = true;
      bmsStatus = 'Connected';
      isLoadingBms = false;

      AppLogger.info("Connection successful, polling...");
      _pollBms(identifier);
    } catch (e) {
      _isConnected = false;
      _isConnecting = false;
      bmsSwitchState = false;
      bmsStatus = 'Disconnected';
      isLoadingBms = false;
      onValidationError?.call('Failed to connect: $e');
      AppLogger.error("Connection error: $e", e);
    } finally {
      onConnectionStatusChanged?.call(bmsStatus);
      onStateChanged?.call();
    }
  }

  Future<void> _pollBms(String identifier) async {
    while (_isConnected && _controlCharacteristic != null) {
      try {
        await _controlCharacteristic!.write(
            _buildCommand(jbdCmdRead, jbdCmdHwInfo),
            withoutResponse: true);
        await Future.delayed(const Duration(seconds: 1));
        await _controlCharacteristic!.write(
            _buildCommand(jbdCmdRead, jbdCmdCellInfo),
            withoutResponse: true);
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
      bmsSwitchState = false;
      bmsStatus = 'Disconnected';
      isLoadingBms = false;
      _frameBuffer.clear();
      _devicesData.clear();
      _hasNotifiedLowSoc = false;
      AppLogger.info("Disconnected BMS successfully");
    } catch (e) {
      onValidationError?.call('Error disconnecting: $e');
      AppLogger.warning("Disconnect error: $e");
    } finally {
      onConnectionStatusChanged?.call(bmsStatus);
      onStateChanged?.call();
    }
  }

  Future<void> toggleSwitch(String feature, bool state) async {
    if (!_isConnected || _controlCharacteristic == null) return;

    int bitmask = feature == "charging" ? jbdMosCharge : jbdMosDischarge;
    int currentStatus = _devicesData[_device!.remoteId.toString()]
            ?["operation_status_bitmask"] ??
        0;
    int value = state ? (currentStatus | bitmask) : (currentStatus & ~bitmask);

    try {
      await _controlCharacteristic!
          .write(_buildWriteCommand(jbdCmdMos, value), withoutResponse: true);
      await Future.delayed(const Duration(seconds: 1));
      await _controlCharacteristic!.write(
          _buildCommand(jbdCmdRead, jbdCmdHwInfo),
          withoutResponse: true);
      AppLogger.info("Toggled $feature to $state");
    } catch (e) {
      onValidationError?.call('Error toggling switch: $e');
      AppLogger.warning("Toggle switch error: $e");
    }
  }

  Future<void> addDevice(String threshold) async {
    final user = FirebaseAuth.instance.currentUser;

    if (user == null) {
      onValidationError?.call('User not authenticated');
      return;
    }

    String name = nameController.text.trim();
    String deviceName = deviceNameController.text.trim();
    String mqttTopic = mqttTopicController.text.trim();

    try {
      await FirebaseFirestore.instance
          .collection('users')
          .doc(user.uid)
          .collection('bms_devices')
          .add({
        'name': name,
        'device_name': deviceName,
        'mqtt_topic': mqttTopic,
        'threshold': threshold,
        'created_at': FieldValue.serverTimestamp(),
        'updated_at': FieldValue.serverTimestamp(),
      });
      AppLogger.info("Added new device: $deviceName");

      nameController.clear();
      deviceNameController.clear();
      mqttTopicController.clear();
      thresholdController.clear();
    } catch (e) {
      onValidationError?.call('Failed to add device: $e');
      AppLogger.error("Error adding device: $e", e);
    } finally {
      onStateChanged?.call();
    }
  }

  Future<void> updateDevice(String threshold) async {
    final user = FirebaseAuth.instance.currentUser;

    if (user == null || editingDeviceId == null) {
      onValidationError?.call('User not authenticated or no device selected');
      return;
    }

    String name = nameController.text.trim();
    String deviceName = deviceNameController.text.trim();
    String mqttTopic = mqttTopicController.text.trim();

    try {
      await FirebaseFirestore.instance
          .collection('users')
          .doc(user.uid)
          .collection('bms_devices')
          .doc(editingDeviceId)
          .update({
        'name': name,
        'device_name': deviceName,
        'mqtt_topic': mqttTopic,
        'threshold': threshold,
        'updated_at': FieldValue.serverTimestamp(),
      });
      AppLogger.info("Updated device: $editingDeviceId");

      nameController.clear();
      deviceNameController.clear();
      mqttTopicController.clear();
      thresholdController.clear();
      isEditing = false;
      editingDeviceId = null;
    } catch (e) {
      onValidationError?.call('Failed to update device: $e');
      AppLogger.error("Error updating device: $e", e);
    } finally {
      onStateChanged?.call();
    }
  }

  Future<void> deleteDevice(String deviceId) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) return;

    try {
      await FirebaseFirestore.instance
          .collection('users')
          .doc(user.uid)
          .collection('bms_devices')
          .doc(deviceId)
          .delete();
      AppLogger.info("Deleted device: $deviceId");
    } catch (e) {
      onValidationError?.call('Failed to delete device: $e');
      AppLogger.error("Error deleting device: $e", e);
    } finally {
      onStateChanged?.call();
    }
  }

  void editDevice(Map<String, dynamic> device, String deviceId) {
    nameController.text = device['name'] ?? '';
    deviceNameController.text = device['device_name'] ?? '';
    mqttTopicController.text = device['mqtt_topic'] ?? '';
    thresholdController.text = device['threshold']?.toString() ?? '50';
    isEditing = true;
    editingDeviceId = deviceId;
    onStateChanged?.call();
    AppLogger.info("Editing device: $deviceId");
  }

  void dispose() {
    disconnect();
    nameController.dispose();
    deviceNameController.dispose();
    mqttTopicController.dispose();
    thresholdController.dispose();
    onStateChanged = null;
    onValidationError = null;
    onConnectionStatusChanged = null;
    AppLogger.info("BmsController disposed");
  }
}
