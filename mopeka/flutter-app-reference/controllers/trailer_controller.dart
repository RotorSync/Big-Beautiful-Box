import 'dart:async';
import 'dart:convert';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter/material.dart';
import 'package:mqtt_client/mqtt_client.dart';
import 'package:rotorsync/services/mqtt_service.dart';
import 'package:rotorsync/utils/logger.dart';

class TrailerController {
  final MQTTService _mqttService;

  final Map<String, Map<String, dynamic>> _trailerData = {};
  final List<String> _availableTrailers = [];
  List<String> _selectedTrailers = [];
  bool _isLoading = false;
  StreamSubscription? _mqttSubscription;

  VoidCallback? onStateChanged;
  Function(String)? onValidationError;

  TrailerController({
    required MQTTService mqttService,
  }) : _mqttService = mqttService {
    _initialize();
  }

  Future<void> _initialize() async {
    await fetchTrailers();
    _subscribeToMqttTopics();
  }

  Future<void> fetchTrailers() async {
    _isLoading = true;
    onStateChanged?.call();

    try {
      // Fetch the trailer document from serial_numbers/trailer
      final trailerDoc = await FirebaseFirestore.instance
          .collection('serial_numbers')
          .doc('trailer')
          .get();

      _availableTrailers.clear();
      _trailerData.clear();

      if (trailerDoc.exists) {
        final serialNumbers =
            (trailerDoc.data()?['serial_numbers'] as List<dynamic>?) ?? [];
        AppLogger.info(
            'Fetched ${serialNumbers.length} trailers from Firestore');
        for (var serial in serialNumbers) {
          final id = serial['id'] as String? ?? '';
          final name = serial['name'] as String? ?? 'Unnamed Trailer';
          AppLogger.info('Trailer: $name, ID: $id');

          _availableTrailers.add(name);
          _trailerData[name] = {
            'id': id,
            'name': name,
            'bmsData': {
              'total_voltage': 0.0,
              'state_of_charge': 0,
            },
            'mopekaDataFront': {
              'fuel_level_gallons': 0,
              'lastUpdated': DateTime.now(),
            },
            'mopekaDataBack': {
              'fuel_level_gallons': 0,
              'lastUpdated': DateTime.now(),
            },
          };
        }
      } else {
        AppLogger.warning('Trailer document does not exist in serial_numbers');
        onValidationError?.call('No trailers found in Firestore');
      }

      // Subscribe to MQTT topics for selected trailers only
      _updateSubscriptions();
    } catch (e) {
      AppLogger.error('Failed to fetch trailers: $e');
      onValidationError?.call('Failed to fetch trailers: $e');
    } finally {
      _isLoading = false;
      onStateChanged?.call();
    }
  }

  void _updateSubscriptions() {
    if (!_mqttService.isConnected) {
      AppLogger.warning('MQTT not connected, skipping subscription');
      onValidationError?.call('MQTT service is not connected');
      return;
    }

    // Unsubscribe from all previous topics
    for (var trailerName in _trailerData.keys) {
      final trailerId = _trailerData[trailerName]!['id'];
      _mqttService.unsubscribe('trailer/$trailerId/bms');
      _mqttService.unsubscribe('trailer/$trailerId/mopeka');
      AppLogger.info(
          'Unsubscribed from trailer/$trailerId/bms and trailer/$trailerId/mopeka');
    }

    // Subscribe to MQTT topics for selected trailers only (up to 3)
    for (var trailerName in _selectedTrailers) {
      final trailerId = _trailerData[trailerName]!['id'];
      _mqttService.subscribe('trailer/$trailerId/bms', MqttQos.atLeastOnce);
      _mqttService.subscribe('trailer/$trailerId/mopeka', MqttQos.atLeastOnce);
      AppLogger.info(
          'Subscribed to trailer/$trailerId/bms and trailer/$trailerId/mopeka');
    }
  }

  void _subscribeToMqttTopics() {
    if (!_mqttService.isConnected) {
      AppLogger.warning('MQTT not connected, skipping subscription');
      onValidationError?.call('MQTT service is not connected');
      return;
    }

    _mqttSubscription?.cancel();
    _mqttSubscription = _mqttService.updates?.listen(
        (List<MqttReceivedMessage<MqttMessage>> messages) {
      for (var message in messages) {
        final topic = message.topic;
        final payload = MqttPublishPayload.bytesToStringAsString(
            (message.payload as MqttPublishMessage).payload.message);

        try {
          final decodedPayload = jsonDecode(payload);
          final parts = topic.split('/');
          if (parts.length < 3 || parts[0] != 'trailer') continue;

          final trailerId = parts[1];
          final sensorType = parts[2];
          final trailerName = _trailerData.entries
              .firstWhere((entry) => entry.value['id'] == trailerId,
                  orElse: () => MapEntry('', {'name': trailerId}))
              .value['name'];

          // Only process updates for selected trailers
          if (!_selectedTrailers.contains(trailerName)) continue;

          if (sensorType == 'bms') {
            _trailerData[trailerName]?['bmsData'] = {
              'total_voltage':
                  decodedPayload['total_voltage'] as double? ?? 0.0,
              'state_of_charge': decodedPayload['state_of_charge'] as int? ?? 0,
            };
          } else if (sensorType == 'mopeka') {
            // Since we don't have sensor mappings, we'll use the device_id to distinguish
            // Assume the first device_id seen is "Front", the second is "Back"
            final deviceId = decodedPayload['device_id'] as String? ?? '';
            final currentFrontDeviceId = _trailerData[trailerName]
                ?['mopekaDataFront']['device_id'] as String?;
            final currentBackDeviceId = _trailerData[trailerName]
                ?['mopekaDataBack']['device_id'] as String?;

            final mopekaUpdate = {
              'device_id': deviceId,
              'fuel_level_gallons':
                  (decodedPayload['fuel_level_gallons'] as double?)?.round() ??
                      0,
              'lastUpdated':
                  DateTime.parse(decodedPayload['timestamp'] as String),
            };

            if (currentFrontDeviceId == null ||
                currentFrontDeviceId == deviceId) {
              _trailerData[trailerName]?['mopekaDataFront'] = mopekaUpdate;
            } else if (currentBackDeviceId == null ||
                currentBackDeviceId == deviceId) {
              _trailerData[trailerName]?['mopekaDataBack'] = mopekaUpdate;
            } else {
              // If we already have two device IDs and get a new one, replace the older update
              final frontUpdateTime = _trailerData[trailerName]
                  ?['mopekaDataFront']['lastUpdated'] as DateTime;
              final backUpdateTime = _trailerData[trailerName]
                  ?['mopekaDataBack']['lastUpdated'] as DateTime;
              if (frontUpdateTime.isBefore(backUpdateTime)) {
                _trailerData[trailerName]?['mopekaDataFront'] = mopekaUpdate;
              } else {
                _trailerData[trailerName]?['mopekaDataBack'] = mopekaUpdate;
              }
            }
          }
          onStateChanged?.call();
        } catch (e) {
          AppLogger.error('Failed to decode MQTT payload from $topic: $e');
        }
      }
    }, onError: (e) {
      AppLogger.error('MQTT subscription error: $e');
      onValidationError?.call('MQTT subscription error: $e');
    });
  }

  Map<String, dynamic> getTrailerData(String trailerName) {
    final data = _trailerData[trailerName] ?? {};
    final bmsData = data['bmsData'] ?? {};
    final mopekaDataFront = data['mopekaDataFront'] ?? {};
    final mopekaDataBack = data['mopekaDataBack'] ?? {};

    return {
      'voltage': (bmsData['total_voltage'] as double?) ?? 0.0,
      'percentage': (bmsData['state_of_charge'] as int?) ?? 0,
      'frontGallons': (mopekaDataFront['fuel_level_gallons'] as int?) ?? 0,
      'backGallons': (mopekaDataBack['fuel_level_gallons'] as int?) ?? 0,
      'frontLastUpdate':
          (mopekaDataFront['lastUpdated'] as DateTime?) ?? DateTime.now(),
      'backLastUpdate':
          (mopekaDataBack['lastUpdated'] as DateTime?) ?? DateTime.now(),
    };
  }

  List<String> getAvailableTrailers() => _availableTrailers;

  List<String> getSelectedTrailers() => _selectedTrailers;

  void setSelectedTrailers(List<String> trailers) {
    _selectedTrailers = trailers.take(3).toList(); // Limit to 3 trailers
    _updateSubscriptions();
    onStateChanged?.call();
  }

  bool get isLoading => _isLoading;

  void dispose() {
    _mqttSubscription?.cancel();
    for (var trailerName in _trailerData.keys) {
      final trailerId = _trailerData[trailerName]!['id'];
      if (_mqttService.isConnected) {
        _mqttService.unsubscribe('trailer/$trailerId/bms');
        _mqttService.unsubscribe('trailer/$trailerId/mopeka');
        AppLogger.info(
            'Unsubscribed from trailer/$trailerId/bms and trailer/$trailerId/mopeka');
      }
    }
    onStateChanged = null;
    onValidationError = null;
  }
}
