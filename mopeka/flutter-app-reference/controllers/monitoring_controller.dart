import 'dart:convert';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter/material.dart';
import 'package:mqtt_client/mqtt_client.dart';
import '../constants/colors.dart';
import '../services/mqtt_service.dart';
import '../utils/logger.dart';

class MonitoringController {
  final MQTTService mqttService;
  Map<String, dynamic>? latestMessage;
  String? subscribedTopic;
  bool isLoading = false;

  VoidCallback? onStateChanged;

  MonitoringController({required this.mqttService}) {
    _listenForMessages();
  }

  void _listenForMessages() {
    if (!mqttService.isInitialized || !mqttService.isConnected) {
      AppLogger.warning(
          'MQTT service is not initialized or connected. Cannot listen for messages.');
      return;
    }

    mqttService.updates?.listen(
      (List<MqttReceivedMessage<MqttMessage>> messages) {
        for (var message in messages) {
          final MqttPublishMessage recMess =
              message.payload as MqttPublishMessage;
          final String payload =
              MqttPublishPayload.bytesToStringAsString(recMess.payload.message);
          final String topic = message.topic;

          try {
            // Attempt to decode the payload as JSON
            final decodedPayload = jsonDecode(payload);
            latestMessage = {
              'topic': topic,
              'message': decodedPayload,
            };
            AppLogger.info(
                'Received JSON message on topic $topic: $decodedPayload');
          } catch (e) {
            // If not JSON, store as plain text
            latestMessage = {
              'topic': topic,
              'message': payload,
            };
            AppLogger.warning(
                'Received non-JSON message on topic $topic: $payload');
          }
          onStateChanged?.call();
        }
      },
      onError: (e) {
        AppLogger.error('Error listening to MQTT updates: $e', e);
      },
      onDone: () {
        AppLogger.info('MQTT updates stream closed.');
      },
    );
  }

  Future<List<Map<String, dynamic>>> fetchSerialNumbers() async {
    try {
      AppLogger.info('Fetching serial numbers from Firestore...');
      final List<Map<String, dynamic>> serialNumbers = [];

      // Step 1: Get all asset types from the serial_numbers collection
      final assetTypesSnapshot =
          await FirebaseFirestore.instance.collection('serial_numbers').get();

      // Step 2: For each asset type, extract the serial_numbers array
      for (var assetDoc in assetTypesSnapshot.docs) {
        final assetType = assetDoc.id; // e.g., "helicopter"
        final serialsData =
            (assetDoc.data()['serial_numbers'] as List<dynamic>?) ?? [];

        for (var serial in serialsData) {
          serialNumbers.add({
            'type': assetType,
            'id': serial['id'] as String? ?? '',
            'name': serial['name'] as String? ?? '',
          });
        }
      }

      AppLogger.info('Fetched serial numbers: $serialNumbers');
      return serialNumbers;
    } catch (e) {
      AppLogger.error('Error fetching serial numbers: $e', e);
      return [];
    }
  }

  Future<void> subscribeToTopic(BuildContext context, String type,
      String serialNumber, String sensor) async {
    if (!mqttService.isInitialized) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('MQTT service is not initialized.'),
            backgroundColor: AppColors.red,
          ),
        );
      }
      return;
    }

    if (!mqttService.isConnected) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('MQTT service is not connected.'),
            backgroundColor: AppColors.red,
          ),
        );
      }
      return;
    }

    final topic = '$type/$serialNumber/$sensor';
    if (topic.isEmpty) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Please select type, serial number, and sensor.'),
            backgroundColor: AppColors.red,
          ),
        );
      }
      return;
    }

    try {
      isLoading = true;
      onStateChanged?.call();
      if (subscribedTopic != null && subscribedTopic != topic) {
        mqttService.unsubscribe(subscribedTopic!);
        AppLogger.info('Unsubscribed from previous topic: $subscribedTopic');
      }
      mqttService.subscribe(topic, MqttQos.atLeastOnce);
      subscribedTopic = topic;
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Subscribed to topic: $topic'),
            backgroundColor: AppColors.green,
          ),
        );
      }
    } catch (e) {
      AppLogger.error('Failed to subscribe to topic $topic: $e', e);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to subscribe: $e'),
            backgroundColor: AppColors.red,
          ),
        );
      }
    } finally {
      isLoading = false;
      onStateChanged?.call();
    }
  }

  void dispose() {
    if (subscribedTopic != null) {
      try {
        mqttService.unsubscribe(subscribedTopic!);
        AppLogger.info('Unsubscribed from topic on dispose: $subscribedTopic');
      } catch (e) {
        AppLogger.error('Failed to unsubscribe on dispose: $e', e);
      }
    }
    AppLogger.info('MonitoringController disposed.');
  }
}
