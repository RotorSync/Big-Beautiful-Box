class Validators {
  // Fullname validator
  static String? validateFullName(String? value) {
    if (value == null || value.isEmpty) {
      return 'Full name is required';
    }

    if (value.length < 3) {
      return 'Full name must be at least 3 characters';
    }

    if (value.length > 50) {
      return 'Full name cannot exceed 50 characters';
    }
    return null;
  }

  // Email validator
  static String? validateEmail(String? value) {
    if (value == null || value.trim().isEmpty) {
      return 'Email is required';
    }

    value = value.trim();

    final emailRegex =
        RegExp(r'^[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$');

    if (!emailRegex.hasMatch(value)) {
      return 'Please enter a valid email address';
    }

    if (value.contains('..') || value.endsWith('.') || value.contains(' ')) {
      return 'Invalid email format';
    }

    return null;
  }

  // Password validator
  static String? validatePassword(String? value) {
    if (value == null || value.isEmpty) {
      return 'Password is required';
    }

    if (value.contains(' ')) {
      return 'Password cannot contain spaces';
    }

    if (value.length < 8) {
      return 'Password must be at least 8 characters';
    }

    return null;
  }

  // Protocol validator
  static String? validateProtocol(String? value) {
    if (value == null) {
      return 'Please select a protocol';
    }
    return null;
  }

  // Host validator
  static String? validateHost(String? value) {
    if (value == null || value.trim().isEmpty) {
      return 'Host is required';
    }

    value = value.trim();

    // Basic hostname validation (allows domains and IPs)
    final hostRegex = RegExp(
        r'^(?:(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}|localhost|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$');

    if (!hostRegex.hasMatch(value)) {
      return 'Please enter a valid host';
    }

    return null;
  }

  // Port validator
  static String? validatePort(String? value) {
    if (value == null || value.trim().isEmpty) {
      return 'Port is required';
    }

    final port = int.tryParse(value.trim());
    if (port == null || port <= 0 || port > 65535) {
      return 'Please enter a valid port number';
    }

    return null;
  }

  static String? validateBasePath(String? value, {required bool isWebSocket}) {
    if (!isWebSocket) {
      return null;
    }

    if (value == null || value.trim().isEmpty) {
      return 'Base Path is required for WebSocket';
    }

    value = value.trim();

    final basePathRegex = RegExp(r'^/[a-zA-Z0-9/_-]*$');

    if (!basePathRegex.hasMatch(value)) {
      return 'Base Path must start with a slash and contain only letters, numbers, underscores, or hyphens';
    }

    return null;
  }

  // Username validator
  static String? validateUsername(String? value) {
    if (value == null || value.trim().isEmpty) {
      return 'Username is required';
    }

    value = value.trim();

    if (value.contains(' ')) {
      return 'Username cannot contain spaces';
    }

    if (value.length < 3) {
      return 'Username must be at least 3 characters';
    }

    return null;
  }

  static String? validateName(String? value) {
    if (value == null || value.trim().isEmpty) {
      return 'Name is required';
    }

    return null;
  }

  static String? validateDeviceName(String? value) {
    if (value == null || value.trim().isEmpty) {
      return 'Device name is required';
    }

    return null;
  }

  static String? validateMAC(String? value) {
    if (value == null || value.trim().isEmpty) {
      return 'MAC address is required';
    }

    final partialMacRegex = RegExp(r'^([0-9A-Fa-f]{2}:){2}([0-9A-Fa-f]{2})$');

    if (!partialMacRegex.hasMatch(value)) {
      return 'Invalid MAC Address';
    }

    return null;
  }

  static String? validateMQTTTopic(String? value) {
    if (value == null || value.trim().isEmpty) {
      return 'MQTT topic is required';
    }
    final topicRegex = RegExp(r'^[^#\s\+]+(/[^#\s\+]+)*$');
    if (!topicRegex.hasMatch(value.trim())) {
      return 'Invalid MQTT topic format';
    }
    return null;
  }

  static String? validateSerialNumber(String? value) {
    if (value == null || value.isEmpty) {
      return 'Serial number is required';
    }

    final RegExp alphanumeric = RegExp(r'^[a-zA-Z0-9]+$');

    if (!alphanumeric.hasMatch(value)) {
      return 'Serial number must contain only letters and numbers';
    }

    if (value.length < 3) {
      return 'Serial number must be at least 3 characters';
    }

    return null;
  }

  static String? validateTankSize(String? value) {
    if (value == null || value.isEmpty) {
      return 'Tank size is required';
    }

    return null;
  }

  static String? validateCalibrationPoints(String? value) {
    if (value == null || value.isEmpty) {
      return 'Calibration points are required';
    }

    final points = int.tryParse(value);
    if (points == null || points <= 0) {
      return 'Please enter a valid positive integer';
    }

    if (points > 100) {
      return 'Calibration points cannot exceed 100';
    }

    return null;
  }

  static String? validateIPAddress(String? value) {
    if (value == null || value.trim().isEmpty) {
      return 'IP Address is required';
    }
    final ipRegex = RegExp(r'^(?:\d{1,3}\.){3}\d{1,3}$');
    if (!ipRegex.hasMatch(value.trim())) {
      return 'Enter a valid IP address (e.g., 192.168.1.100)';
    }
    return null;
  }
}
