import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:mqtt_client/mqtt_client.dart';
import '../constants/colors.dart';
import '../services/mqtt_service.dart';
import '../utils/logger.dart';

class MQTTTestController {
  final MQTTService mqttService;
  final TextEditingController topicController = TextEditingController();
  final TextEditingController messageController = TextEditingController();

  Map<String, dynamic>? latestMessage; // Store only the latest message
  String? subscribedTopic;
  bool isLoading = false;

  VoidCallback? onStateChanged;

  MQTTTestController({required this.mqttService}) {
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

  Future<void> publishMessage(BuildContext context) async {
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

    final topic = topicController.text.trim();
    final message = messageController.text.trim();

    if (topic.isEmpty || message.isEmpty) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Please enter both topic and message.'),
            backgroundColor: AppColors.red,
          ),
        );
      }
      return;
    }

    try {
      isLoading = true;
      onStateChanged?.call();
      mqttService.publish(topic, message, qos: MqttQos.atLeastOnce);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Message published successfully.'),
            backgroundColor: AppColors.green,
          ),
        );
      }
    } catch (e) {
      AppLogger.error('Failed to publish message: $e', e);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to publish message: $e'),
            backgroundColor: AppColors.red,
          ),
        );
      }
    } finally {
      isLoading = false;
      onStateChanged?.call();
    }
  }

  Future<void> subscribeToTopic(BuildContext context) async {
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

    final topic = topicController.text.trim();

    if (topic.isEmpty) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Please enter a topic to subscribe.'),
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
    topicController.dispose();
    messageController.dispose();
    if (subscribedTopic != null) {
      try {
        mqttService.unsubscribe(subscribedTopic!);
        AppLogger.info('Unsubscribed from topic on dispose: $subscribedTopic');
      } catch (e) {
        AppLogger.error('Failed to unsubscribe on dispose: $e', e);
      }
    }
    AppLogger.info('MQTTTestController disposed.');
  }
}
