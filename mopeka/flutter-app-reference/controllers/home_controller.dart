import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import 'package:hugeicons/hugeicons.dart';
import 'package:provider/provider.dart';
import 'package:rotorsync/controllers/trailer_controller.dart';
import 'package:rotorsync/screens/mopeka_bms.dart';
import '../constants/colors.dart';
import '../controllers/auth_controller.dart';
import '../controllers/mopeka_controller.dart';
import '../controllers/bms_controller.dart';
import '../controllers/raspi_controller.dart';
import '../controllers/tilt_sensor_controller.dart';
import '../screens/users/users_screen.dart';
import '../services/mqtt_service.dart';
import '../screens/mqtt/mqtt_config_screen.dart';
import '../utils/logger.dart';
import '../screens/bms/bms_screen.dart';
import '../screens/raspi/raspi_screen.dart';
import '../screens/mopeka/mopeka_screen.dart';
import '../screens/tilt_sensor_screen.dart';

class HomeController {
  final AuthController _authController = AuthController();
  final MQTTService _mqttService;
  final MopekaController _mopekaController;
  final BmsController _bmsController;
  final RaspiController _raspiController;
  final TiltSensorController _tiltSensorController;

  String? role;
  String? fullName;
  String? email;
  String? initials;

  bool mqttSwitchState = false;
  String mqttStatus = 'Disconnected';
  bool isLoadingMQTT = false;

  bool mopekaSwitchState = false;
  String mopekaStatus = 'Disconnected';
  bool isLoadingMopeka = false;

  bool bmsSwitchState = false;
  String bmsStatus = 'Disconnected';
  bool isLoadingBms = false;

  bool raspiSwitchState = false;
  String raspiStatus = 'Disconnected';
  bool isLoadingRaspi = false;

  bool sensorSwitchState = false;
  String sensorStatus = 'Disconnected';
  bool isLoadingSensor = false;

  int selectedIndex = 0;

  VoidCallback? onStateChanged;
  Function(String, Color)? onShowSnackBar;

  bool _isMounted = true;

  MQTTService get mqttService => _mqttService;
  MopekaController get mopekaController => _mopekaController;
  BmsController get bmsController => _bmsController;
  RaspiController get raspiController => _raspiController;
  TiltSensorController get tiltSensorController => _tiltSensorController;

  HomeController()
      : _mqttService = MQTTService(),
        _mopekaController = MopekaController(),
        _bmsController = BmsController(mqttService: MQTTService()),
        _raspiController = RaspiController(mqttService: MQTTService()),
        _tiltSensorController =
            TiltSensorController(mqttService: MQTTService()) {
    _mqttService.setConnectionStatusCallback((status) {
      mqttStatus = status;
      if (status == 'Connected') {
        mqttSwitchState = true;
        isLoadingMQTT = false;
      } else if (status == 'Disconnected') {
        mqttSwitchState = false;
        isLoadingMQTT = false;
      }
      if (_isMounted) onStateChanged?.call();
    });

    _mopekaController.onConnectionStatusChanged = (status) {
      mopekaStatus = status;
      if (status == 'Monitoring') {
        mopekaSwitchState = true;
        isLoadingMopeka = false;
      } else if (status == 'Disconnected') {
        mopekaSwitchState = false;
        isLoadingMopeka = false;
      } else if (status == 'Scanning...') {
        isLoadingMopeka = true;
        mopekaSwitchState = true;
      }
      if (_isMounted) onStateChanged?.call();
    };

    _mopekaController.onValidationError = (message) {
      if (_isMounted) {
        onShowSnackBar?.call(message, AppColors.red);
      }
    };

    _bmsController.onConnectionStatusChanged = (status) {
      bmsStatus = status;
      if (status == 'Connected') {
        bmsSwitchState = true;
        isLoadingBms = false;
      } else if (status == 'Disconnected') {
        bmsSwitchState = false;
        isLoadingBms = false;
      } else if (status == 'Connecting...') {
        isLoadingBms = true;
      }
      if (_isMounted) onStateChanged?.call();
    };

    _bmsController.onValidationError = (message) {
      if (_isMounted) {
        onShowSnackBar?.call(message, AppColors.red);
      }
    };

    _raspiController.onConnectionStatusChanged = (status) {
      raspiStatus = status;
      if (status == 'Connected') {
        raspiSwitchState = true;
        isLoadingRaspi = false;
      } else if (status == 'Disconnected') {
        raspiSwitchState = false;
        isLoadingRaspi = false;
      } else if (status == 'Connecting...') {
        isLoadingRaspi = true;
      }
      if (_isMounted) onStateChanged?.call();
    };

    _raspiController.onValidationError = (message) {
      if (_isMounted) {
        onShowSnackBar?.call(message, AppColors.red);
      }
    };

    _tiltSensorController.onConnectionStatusChanged = (status) {
      sensorStatus = status;
      if (status == 'Connected') {
        sensorSwitchState = true;
        isLoadingSensor = false;
      } else if (status == 'Disconnected') {
        sensorSwitchState = false;
        isLoadingSensor = false;
      } else if (status == 'Connecting...') {
        isLoadingSensor = true;
      }
      if (_isMounted) onStateChanged?.call();
    };

    _tiltSensorController.onValidationError = (message) {
      if (_isMounted) {
        onShowSnackBar?.call(message, AppColors.red);
      }
    };

    fetchUserDetails();
  }

  Future<void> fetchUserDetails() async {
    try {
      Map<String, String?> userDetails = await _authController.getUserDetails();
      role = userDetails['role'];
      fullName = userDetails['fullName'];
      email = userDetails['email'];
      if (fullName != null) {
        List<String> nameParts =
            fullName!.split(' ').where((part) => part.isNotEmpty).toList();
        if (nameParts.length >= 2) {
          initials = '${nameParts[0][0]}.${nameParts[1][0]}';
        } else if (nameParts.length == 1) {
          initials = '${nameParts[0][0]}.';
        } else {
          initials = '';
        }
      }
    } catch (e) {
      AppLogger.error('Failed to fetch user details: $e', e);
    } finally {
      if (_isMounted) onStateChanged?.call();
    }
  }

  Future<void> toggleMQTTConnection(bool value) async {
    if (isLoadingMQTT) return;

    mqttSwitchState = value;
    if (value) {
      isLoadingMQTT = true;
      mqttStatus = 'Connecting...';
    } else {
      isLoadingMQTT = false;
      mqttStatus = 'Disconnected';
      await _mqttService.disconnect();
    }
    if (_isMounted) onStateChanged?.call();

    if (!value) return;

    try {
      if (!_mqttService.isInitialized) await _mqttService.initialize();
      await _mqttService.connect();
    } catch (e) {
      mqttSwitchState = false;
      mqttStatus = 'Disconnected';
      isLoadingMQTT = false;
      if (_isMounted) {
        onShowSnackBar?.call('Failed to connect to MQTT: $e', AppColors.red);
        onStateChanged?.call();
      }
    }
  }

  Future<void> toggleMopekaConnection(bool value) async {
    if (value && isLoadingMopeka) return;

    AppLogger.info('Toggling Mopeka to: $value');
    mopekaSwitchState = value;
    if (value) {
      isLoadingMopeka = true;
      mopekaStatus = 'Scanning...';
    } else {
      isLoadingMopeka = false;
      mopekaStatus = 'Disconnected';
      await _mopekaController.disconnect();
    }
    if (_isMounted) onStateChanged?.call();

    if (!value) return;

    try {
      final devicesSnapshot = await FirebaseFirestore.instance
          .collection('users')
          .doc(FirebaseAuth.instance.currentUser?.uid)
          .collection('mopeka_sensors')
          .get();
      if (devicesSnapshot.docs.isEmpty) {
        mopekaSwitchState = false;
        isLoadingMopeka = false;
        mopekaStatus = 'Disconnected';
        if (_isMounted) {
          onStateChanged?.call();
          onShowSnackBar?.call('No Mopeka sensors saved', AppColors.red);
        }
        return;
      }

      final isBluetoothEnabled =
          await _mopekaController.checkBluetoothAndLocation();
      if (!isBluetoothEnabled) {
        mopekaSwitchState = false;
        isLoadingMopeka = false;
        mopekaStatus = 'Disconnected';
        if (_isMounted) {
          onStateChanged?.call();
        }
        return;
      }

      await _mopekaController.connect(null);
    } catch (e) {
      mopekaSwitchState = false;
      isLoadingMopeka = false;
      mopekaStatus = 'Disconnected';
      if (_isMounted) {
        onShowSnackBar?.call('Failed to connect to Mopeka: $e', AppColors.red);
        onStateChanged?.call();
      }
      AppLogger.error('Connection error: $e');
    }
  }

  Future<void> toggleBmsConnection(bool value) async {
    if (value && isLoadingBms) return;

    AppLogger.info('Toggling BMS to: $value');
    bmsSwitchState = value;
    if (value) {
      isLoadingBms = true;
      bmsStatus = 'Connecting...';
    } else {
      isLoadingBms = false;
      bmsStatus = 'Disconnected';
      await _bmsController.disconnect();
    }
    if (_isMounted) onStateChanged?.call();

    if (!value) return;

    try {
      final devicesSnapshot = await FirebaseFirestore.instance
          .collection('users')
          .doc(FirebaseAuth.instance.currentUser?.uid)
          .collection('bms_devices')
          .get();
      if (devicesSnapshot.docs.isEmpty) {
        bmsSwitchState = false;
        isLoadingBms = false;
        bmsStatus = 'Disconnected';
        if (_isMounted) {
          onStateChanged?.call();
          onShowSnackBar?.call('No BMS devices saved', AppColors.red);
        }
        return;
      }

      final isBluetoothEnabled =
          await _bmsController.checkBluetoothAndLocation();
      if (!isBluetoothEnabled) {
        bmsSwitchState = false;
        isLoadingBms = false;
        bmsStatus = 'Disconnected';
        if (_isMounted) {
          onStateChanged?.call();
        }
        return;
      }

      await _bmsController.connect();
    } catch (e) {
      bmsSwitchState = false;
      isLoadingBms = false;
      bmsStatus = 'Disconnected';
      if (_isMounted) {
        onShowSnackBar?.call('Failed to connect to BMS: $e', AppColors.red);
        onStateChanged?.call();
      }
    }
  }

  Future<void> toggleRaspiConnection(bool value) async {
    if (value && isLoadingRaspi) return;

    AppLogger.info('Toggling RasPi to: $value');
    raspiSwitchState = value;
    if (value) {
      isLoadingRaspi = true;
      raspiStatus = 'Connecting...';
    } else {
      isLoadingRaspi = false;
      raspiStatus = 'Disconnected';
      await _raspiController.disconnect();
    }
    if (_isMounted) onStateChanged?.call();

    if (!value) return;

    try {
      final devicesSnapshot = await FirebaseFirestore.instance
          .collection('users')
          .doc(FirebaseAuth.instance.currentUser?.uid)
          .collection('raspi_devices')
          .get();
      if (devicesSnapshot.docs.isEmpty) {
        raspiSwitchState = false;
        isLoadingRaspi = false;
        raspiStatus = 'Disconnected';
        if (_isMounted) {
          onShowSnackBar?.call('No RasPi devices saved', AppColors.red);
          onStateChanged?.call();
        }
        return;
      }

      final isBluetoothEnabled =
          await _raspiController.checkBluetoothAndLocation();
      if (!isBluetoothEnabled) {
        raspiSwitchState = false;
        isLoadingRaspi = false;
        raspiStatus = 'Disconnected';
        if (_isMounted) {
          onStateChanged?.call();
          onShowSnackBar?.call(
              'Bluetooth and Location permissions required', AppColors.red);
        }
        return;
      }

      await _raspiController.connect();
    } catch (e) {
      raspiSwitchState = false;
      isLoadingRaspi = false;
      raspiStatus = 'Disconnected';
      if (_isMounted) {
        onShowSnackBar?.call('Failed to connect to RasPi: $e', AppColors.red);
        onStateChanged?.call();
      }
    }
  }

  Future<void> toggleSensorConnection(bool value) async {
    if (value && isLoadingSensor) return;

    AppLogger.info('Toggling Sensor to: $value');
    sensorSwitchState = value;
    if (value) {
      isLoadingSensor = true;
      sensorStatus = 'Connecting...';
    } else {
      isLoadingSensor = false;
      sensorStatus = 'Disconnected';
      await _tiltSensorController.disconnect();
    }
    if (_isMounted) onStateChanged?.call();

    if (!value) return;

    try {
      final isBluetoothEnabled =
          await _tiltSensorController.checkBluetoothAndLocation();
      if (!isBluetoothEnabled) {
        sensorSwitchState = false;
        isLoadingSensor = false;
        sensorStatus = 'Disconnected';
        if (_isMounted) {
          onStateChanged?.call();
          onShowSnackBar?.call(
              'Bluetooth and Location permissions required', AppColors.red);
        }
        return;
      }

      await _tiltSensorController.connect();
    } catch (e) {
      sensorSwitchState = false;
      isLoadingSensor = false;
      sensorStatus = 'Disconnected';
      if (_isMounted) {
        onShowSnackBar?.call('Failed to connect to Sensor: $e', AppColors.red);
        onStateChanged?.call();
      }
      AppLogger.error('Connection error: $e');
    }
  }

  Future<void> navigateToMQTTConfig(BuildContext context) async {
    await Navigator.push(context,
        MaterialPageRoute(builder: (context) => const MQTTConfigScreen()));
  }

  Future<void> navigateToMopekaScreen(BuildContext context) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => ChangeNotifierProvider.value(
          value: _mopekaController,
          child: const MopekaScreen(),
        ),
      ),
    );
  }

  Future<void> navigateToBmsScreen(BuildContext context) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
          builder: (context) => BmsScreen(bmsController: _bmsController)),
    );
  }

  Future<void> navigateToRaspiScreen(BuildContext context) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
          builder: (context) => RaspiScreen(raspiController: _raspiController)),
    );
  }

  Future<void> navigateToSensorScreen(BuildContext context) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
          builder: (context) =>
              TiltSensorScreen(tiltSensorController: _tiltSensorController)),
    );
  }

  Future<void> navigateToMopekaBMS(BuildContext context) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => MopekaBmsScreen(
          trailerController: TrailerController(
            mqttService: _mqttService,
          ),
        ),
      ),
    );
  }

  void navigateToUsersScreen(BuildContext context) {
    Navigator.push(
        context, MaterialPageRoute(builder: (context) => const UsersScreen()));
  }

  List<Map<String, dynamic>> buildNavItems() {
    List<Map<String, dynamic>> items = [
      {'icon': HugeIcons.strokeRoundedHome02, 'label': 'Home'}
    ];
    if (role == 'admin') {
      items.add(
          {'icon': HugeIcons.strokeRoundedUserMultiple02, 'label': 'Users'});
    }
    items.add({'icon': HugeIcons.strokeRoundedNavigation05, 'label': 'Map'});
    items.add({'icon': HugeIcons.strokeRoundedSetting07, 'label': 'Settings'});
    return items;
  }

  void onItemTapped(int index) {
    selectedIndex = index;
    if (_isMounted) onStateChanged?.call();
  }

  void dispose() {
    _isMounted = false;
    _mqttService.dispose();
    _mopekaController.dispose();
    _bmsController.dispose();
    _raspiController.dispose();
    _tiltSensorController.dispose();
    onStateChanged = null;
    onShowSnackBar = null;
  }
}
