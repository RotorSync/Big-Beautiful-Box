import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter/material.dart';
import '../utils/logger.dart';
import '../constants/colors.dart';

class MQTTController {
  Future<Map<String, dynamic>?> fetchSettings() async {
    try {
      AppLogger.info('Fetching MQTT settings from Firebase...');
      final doc = await FirebaseFirestore.instance
          .collection('mqtt')
          .doc('credentials')
          .get();
      if (doc.exists) {
        AppLogger.info('MQTT settings fetched successfully from Firebase.');
        return doc.data();
      } else {
        AppLogger.info('No MQTT settings found in Firebase.');
        return null;
      }
    } catch (e) {
      AppLogger.error('Failed to fetch MQTT settings from Firebase: $e', e);
      rethrow;
    }
  }

  Future<void> saveSettings({
    required String protocol,
    required String host,
    required String port,
    required String username,
    required String password,
    String? basePath,
  }) async {
    try {
      AppLogger.info('Saving MQTT settings to Firebase...');
      final settings = {
        'protocol': protocol,
        'host': host,
        'port': port,
        'username': username,
        'password': password,
        'basePath': basePath,
        'updatedAt': FieldValue.serverTimestamp(),
      };

      await FirebaseFirestore.instance
          .collection('mqtt')
          .doc('credentials')
          .set(settings);

      AppLogger.info('MQTT settings saved successfully to Firebase.');
    } catch (e) {
      AppLogger.error('Failed to save MQTT settings to Firebase: $e', e);
      rethrow;
    }
  }
}

class MQTTConfigController {
  final _formKey = GlobalKey<FormState>();

  final TextEditingController hostController = TextEditingController();
  final TextEditingController portController = TextEditingController();
  final TextEditingController basePathController = TextEditingController();
  final TextEditingController usernameController = TextEditingController();
  final TextEditingController passwordController = TextEditingController();

  String? selectedProtocol = 'websocket';
  bool isLoading = false;
  String? errorMessage;

  VoidCallback? onStateChanged;

  final MQTTController _mqttController = MQTTController();

  MQTTConfigController() {
    _fetchAndPopulateSettings();
  }

  GlobalKey<FormState> get formKey => _formKey;

  Future<void> _fetchAndPopulateSettings() async {
    try {
      isLoading = true;
      errorMessage = null;
      onStateChanged?.call();
      AppLogger.info('Fetching MQTT settings for config screen...');
      final settings = await _mqttController.fetchSettings();
      if (settings != null) {
        selectedProtocol = settings['protocol'] as String? ?? 'websocket';
        hostController.text = settings['host'] as String? ?? '';
        portController.text = settings['port'] as String? ?? '';
        basePathController.text = settings['basePath'] as String? ?? '';
        usernameController.text = settings['username'] as String? ?? '';
        passwordController.text = settings['password'] as String? ?? '';
        AppLogger.info('MQTT settings populated in config screen.');
      } else {
        AppLogger.info('No MQTT settings found to populate.');
      }
    } catch (e) {
      AppLogger.error('Failed to fetch MQTT settings for config screen: $e', e);
      errorMessage = 'Failed to load MQTT settings: $e';
    } finally {
      isLoading = false;
      onStateChanged?.call();
    }
  }

  Future<void> saveSettings(BuildContext context) async {
    if (!_formKey.currentState!.validate()) {
      AppLogger.warning('Form validation failed.');
      return;
    }

    try {
      isLoading = true;
      errorMessage = null;
      onStateChanged?.call();
      AppLogger.info('Saving MQTT settings...');

      await _mqttController.saveSettings(
        protocol: selectedProtocol!,
        host: hostController.text.trim(),
        port: portController.text.trim(),
        username: usernameController.text.trim(),
        password: passwordController.text.trim(),
        basePath: selectedProtocol == 'websocket'
            ? basePathController.text.trim()
            : null,
      );

      AppLogger.info('MQTT settings saved successfully.');
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('MQTT settings saved successfully'),
            backgroundColor: AppColors.green,
          ),
        );
        Navigator.pop(context);
      }
    } catch (e) {
      AppLogger.error('Failed to save MQTT settings: $e', e);
      errorMessage = 'Failed to save MQTT settings: $e';
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to save MQTT settings: $e'),
            backgroundColor: AppColors.red,
          ),
        );
      }
    } finally {
      isLoading = false;
      onStateChanged?.call();
    }
  }

  void onProtocolChanged(String? value) {
    selectedProtocol = value;
    basePathController.clear();
    onStateChanged?.call();
  }

  void dispose() {
    hostController.dispose();
    portController.dispose();
    basePathController.dispose();
    usernameController.dispose();
    passwordController.dispose();
    AppLogger.info('MQTTConfigController disposed.');
  }
}
