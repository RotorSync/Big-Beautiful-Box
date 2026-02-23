import 'dart:io';
import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';
import 'package:uuid/uuid.dart';
import '../controllers/mqtt_controller.dart';
import '../utils/logger.dart';

class MQTTService {
  static final MQTTService _instance = MQTTService._internal();
  factory MQTTService() => _instance;

  MqttServerClient? _client;
  final MQTTController _mqttController = MQTTController();
  bool _isConnected = false;
  bool _disconnectedDueToError = false;
  bool _isInitialized = false;

  Function(String)? onConnectionStatusChange;

  MQTTService._internal();

  void setConnectionStatusCallback(Function(String) callback) {
    onConnectionStatusChange = callback;
  }

  bool get isConnected => _isConnected;
  bool get isInitialized => _isInitialized;

  Future<void> initialize() async {
    if (_isInitialized) {
      AppLogger.info('MQTT service is already initialized.');
      return;
    }

    AppLogger.info('Initializing MQTT service...');
    Map<String, dynamic>? settings;
    try {
      settings = await _mqttController.fetchSettings();
    } catch (e) {
      AppLogger.error(
          'Failed to fetch MQTT settings during initialization: $e', e);
      throw Exception('Failed to fetch MQTT settings: $e');
    }

    if (settings == null) {
      AppLogger.warning(
          'No MQTT credentials found. Please configure them first.');
      throw Exception(
          'No MQTT credentials found. Please configure them first.');
    }

    final protocol = settings['protocol'] as String;
    final host = settings['host'] as String;
    final port = int.parse(settings['port'] as String);
    final username = settings['username'] as String;
    final password = settings['password'] as String;
    final basePath = settings['basePath'] as String?;

    AppLogger.debug(
        'MQTT Settings: protocol=$protocol, host=$host, port=$port, username=$username, basePath=$basePath');

    const uuid = Uuid();
    String clientId = uuid.v4();

    if (protocol == 'websocket') {
      String formattedBasePath = basePath != null && basePath.isNotEmpty
          ? (basePath.startsWith('/') ? basePath : '/$basePath')
          : '/mqtt';
      String websocketUrl = "wss://$host:$port$formattedBasePath";
      _client = MqttServerClient.withPort(
        websocketUrl,
        clientId,
        port,
        maxConnectionAttempts: 3,
      );
      _client!.useWebSocket = true;
      _client!.websocketProtocols = ['mqtt'];
    } else if (protocol == 'tls') {
      _client = MqttServerClient.withPort(
        host,
        clientId,
        port,
        maxConnectionAttempts: 3,
      );
      _client!.secure = true;
      _client!.securityContext = SecurityContext.defaultContext;
    } else {
      _client = MqttServerClient.withPort(
        host,
        clientId,
        port,
        maxConnectionAttempts: 3,
      );
    }

    _client!.logging(on: true);
    _client!.setProtocolV311();
    _client!.keepAlivePeriod = 20;
    _client!.connectTimeoutPeriod = 2000;

    _client!.onConnected = _onConnected;
    _client!.onDisconnected = _onDisconnected;
    _client!.onSubscribed = _onSubscribed;
    _client!.onUnsubscribed = _onUnsubscribed;
    _client!.onSubscribeFail = _onSubscribeFail;

    if (username.isNotEmpty && password.isNotEmpty) {
      _client!.connectionMessage = MqttConnectMessage()
          .withClientIdentifier(clientId)
          .authenticateAs(username, password)
          .startClean()
          .withWillQos(MqttQos.atLeastOnce);
    } else {
      _client!.connectionMessage = MqttConnectMessage()
          .withClientIdentifier(clientId)
          .startClean()
          .withWillQos(MqttQos.atLeastOnce);
    }

    _isInitialized = true;
    AppLogger.info('MQTT service initialized successfully.');
  }

  Future<void> connect() async {
    if (!_isInitialized || _client == null) {
      AppLogger.error(
          'MQTT client is not initialized. Call initialize() first.');
      throw Exception(
          'MQTT client is not initialized. Call initialize() first.');
    }

    if (_isConnected) {
      AppLogger.info('MQTT client is already connected');
      onConnectionStatusChange?.call('Connected');
      return;
    }

    try {
      onConnectionStatusChange?.call('Connecting...');
      AppLogger.info('Attempting to connect to MQTT broker...');
      await _client!.connect();
      AppLogger.info('Connection attempt completed.');
    } catch (e) {
      AppLogger.error('MQTT connection failed: $e', e);
      _client!.disconnect();
      onConnectionStatusChange?.call('Disconnected');
      throw Exception('Failed to connect to MQTT broker: $e');
    }
  }

  Future<void> disconnect() async {
    if (!_isInitialized || _client == null) {
      AppLogger.error(
          'MQTT client is not initialized. Call initialize() first.');
      throw Exception(
          'MQTT client is not initialized. Call initialize() first.');
    }
    _client!.disconnect();
    _isConnected = false;
    onConnectionStatusChange?.call('Disconnected');
    AppLogger.info('MQTT client disconnected manually.');
  }

  void publish(String topic, String message,
      {MqttQos qos = MqttQos.atLeastOnce}) {
    if (!_isInitialized || _client == null) {
      AppLogger.error(
          'MQTT client is not initialized. Call initialize() first.');
      throw Exception(
          'MQTT client is not initialized. Call initialize() first.');
    }
    if (!_isConnected) {
      AppLogger.error('MQTT client is not connected.');
      throw Exception('MQTT client is not connected');
    }

    AppLogger.debug('Publishing to topic: $topic, message: $message');
    final builder = MqttClientPayloadBuilder();
    builder.addString(message);
    _client!.publishMessage(topic, qos, builder.payload!);
  }

  void subscribe(String topic, MqttQos qos) {
    if (!_isInitialized || _client == null) {
      AppLogger.error(
          'MQTT client is not initialized. Call initialize() first.');
      throw Exception(
          'MQTT client is not initialized. Call initialize() first.');
    }
    if (!_isConnected) {
      AppLogger.error('MQTT client is not connected.');
      throw Exception('MQTT client is not connected');
    }

    _client!.subscribe(topic, qos);
    AppLogger.info('Subscribing to topic: $topic with QoS: $qos');
  }

  void unsubscribe(String topic) {
    if (!_isInitialized || _client == null) {
      AppLogger.error(
          'MQTT client is not initialized. Call initialize() first.');
      throw Exception(
          'MQTT client is not initialized. Call initialize() first.');
    }
    if (!_isConnected) {
      AppLogger.error('MQTT client is not connected.');
      throw Exception('MQTT client is not connected');
    }

    _client!.unsubscribe(topic);
    AppLogger.info('Unsubscribing from topic: $topic');
  }

  Stream<List<MqttReceivedMessage<MqttMessage>>>? get updates {
    if (!_isInitialized || _client == null) {
      AppLogger.error(
          'MQTT client is not initialized. Call initialize() first.');
      throw Exception(
          'MQTT client is not initialized. Call initialize() first.');
    }
    return _client!.updates;
  }

  void _onConnected() {
    AppLogger.info('MQTT client connected');
    _isConnected = true;
    onConnectionStatusChange?.call('Connected');
  }

  void _onDisconnected() {
    AppLogger.info('MQTT client disconnected');
    _isConnected = false;
    if (!_disconnectedDueToError) {
      onConnectionStatusChange?.call('Disconnected');
    }
    _disconnectedDueToError = false;
  }

  void _onSubscribed(String topic) {
    AppLogger.info('Subscribed to topic: $topic');
  }

  void _onUnsubscribed(String? topic) {
    AppLogger.info('Unsubscribed from topic: $topic');
  }

  void _onSubscribeFail(String topic) {
    AppLogger.warning('Failed to subscribe to topic: $topic');
  }

  void dispose() {
    if (_isConnected) {
      _client?.disconnect();
    }
    _client = null;
    _isConnected = false;
    _isInitialized = false;
    AppLogger.info('MQTT service disposed.');
  }
}
