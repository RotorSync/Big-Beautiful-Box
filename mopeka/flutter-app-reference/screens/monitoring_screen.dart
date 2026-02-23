import 'package:flutter/material.dart';
import '../controllers/monitoring_controller.dart';
import '../services/mqtt_service.dart';
import '../widgets/app_bar.dart';
import '../constants/colors.dart';
import '../widgets/dropdown.dart';
import '../widgets/button.dart';

class MonitoringScreen extends StatefulWidget {
  final MQTTService mqttService;

  const MonitoringScreen({super.key, required this.mqttService});

  @override
  State<MonitoringScreen> createState() => _MonitoringScreenState();
}

class _MonitoringScreenState extends State<MonitoringScreen> {
  late MonitoringController _controller;
  String? _selectedType;
  String? _selectedSerialNumber;
  String? _selectedSensor;
  List<Map<String, dynamic>> _serialNumbers = [];

  @override
  void initState() {
    super.initState();
    _controller = MonitoringController(mqttService: widget.mqttService);
    _controller.onStateChanged = () {
      if (mounted) {
        setState(() {});
      }
    };
    _fetchSerialNumbers();
  }

  Future<void> _fetchSerialNumbers() async {
    final serialNumbers = await _controller.fetchSerialNumbers();
    setState(() {
      _serialNumbers = serialNumbers;
    });
  }

  void _onTypeChanged(String? newType) {
    if (newType != null) {
      setState(() {
        _selectedType = newType;
        _selectedSerialNumber = null; // Reset serial number on type change
      });
    }
  }

  void _onSerialNumberChanged(String? newSerialNumber) {
    if (newSerialNumber != null) {
      setState(() {
        _selectedSerialNumber = newSerialNumber;
      });
    }
  }

  void _onSensorChanged(String? newSensor) {
    if (newSensor != null) {
      setState(() {
        _selectedSensor = newSensor;
      });
    }
  }

  void _subscribeToTopic() {
    if (_selectedType == null ||
        _selectedSerialNumber == null ||
        _selectedSensor == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please select type, serial number, and sensor.'),
          backgroundColor: AppColors.red,
        ),
      );
      return;
    }
    _controller.subscribeToTopic(
      context,
      _selectedType!,
      _selectedSerialNumber!,
      _selectedSensor!,
    );
  }

  Widget _buildMessageDisplay() {
    if (_controller.latestMessage == null) {
      return Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: AppColors.background,
          borderRadius: BorderRadius.circular(12),
        ),
        child: const Text(
          'No messages received yet.',
          style: TextStyle(color: AppColors.secondary),
        ),
      );
    }

    final topic = _controller.latestMessage!['topic'] as String;
    final message = _controller.latestMessage!['message'];

    if (message is String) {
      return Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: AppColors.background,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text(
          '[$topic]: $message',
          style: const TextStyle(
            fontSize: 14,
            color: AppColors.secondary,
          ),
        ),
      );
    }

    final jsonMessage = message as Map<String, dynamic>;
    final tableRows = <TableRow>[
      const TableRow(
        decoration: BoxDecoration(color: AppColors.background),
        children: [
          Padding(
            padding: EdgeInsets.all(8),
            child: Text(
              'Field',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.bold,
                color: AppColors.secondary,
              ),
            ),
          ),
          Padding(
            padding: EdgeInsets.all(8),
            child: Text(
              'Value',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.bold,
                color: AppColors.secondary,
              ),
            ),
          ),
        ],
      ),
    ];

    tableRows
        .add(_buildTableRow('Topic', topic, key: 'topic', rawValue: topic));

    jsonMessage.forEach((key, value) {
      if (value is Map<String, dynamic>) {
        value.forEach((subKey, subValue) {
          String unit = '';
          if (_selectedSensor == 'raspi') {
            unit = key == 'cht' || key == 'egt' ? '°F' : '';
          } else if (_selectedSensor == 'bms') {
            unit = key == 'temperatures'
                ? '°C'
                : key == 'cell_voltages' || key == 'total_voltage'
                    ? 'V'
                    : key == 'current'
                        ? 'A'
                        : key == 'power'
                            ? 'W'
                            : key == 'capacity_remaining' ||
                                    key == 'nominal_capacity'
                                ? 'Ah'
                                : '%';
          } else if (_selectedSensor == 'mopeka') {
            unit = key == 'temperature' ? '°C' : 'mm';
          } else if (_selectedSensor == 'tilt') {
            unit = key == 'acc'
                ? 'g'
                : key == 'gyro'
                    ? '°/s'
                    : key == 'angle'
                        ? '°'
                        : key == 'mag'
                            ? 'µT'
                            : '';
          }

          final formattedValue = subValue is double
              ? subValue.toStringAsFixed(
                  key == 'cell_voltages' || key == 'quaternions' ? 3 : 1)
              : subValue.toString();
          tableRows.add(_buildTableRow(
            '${key.replaceAll('_', ' ').titleCase} $subKey${unit.isNotEmpty ? ' ($unit)' : ''}',
            formattedValue,
            key: key,
            rawValue: subValue,
          ));
        });
      } else {
        String unit = '';
        if (_selectedSensor == 'bms') {
          unit = key == 'total_voltage'
              ? 'V'
              : key == 'current'
                  ? 'A'
                  : key == 'power'
                      ? 'W'
                      : key == 'capacity_remaining' || key == 'nominal_capacity'
                          ? 'Ah'
                          : '%';
        } else if (_selectedSensor == 'mopeka') {
          unit = key == 'temperature' ? '°C' : 'mm';
        }

        final formattedValue = value is double
            ? value.toStringAsFixed(key == 'total_voltage' ||
                    key == 'current' ||
                    key == 'power' ||
                    key == 'capacity_remaining' ||
                    key == 'nominal_capacity'
                ? 2
                : 1)
            : value.toString();
        tableRows.add(_buildTableRow(
          key.replaceAll('_', ' ').titleCase +
              (unit.isNotEmpty ? ' ($unit)' : ''),
          formattedValue,
          key: key,
          rawValue: value,
        ));
      }
    });

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Table(
        border: TableBorder.all(color: AppColors.offWhite),
        columnWidths: const {
          0: IntrinsicColumnWidth(),
          1: IntrinsicColumnWidth(),
        },
        children: tableRows,
      ),
    );
  }

  TableRow _buildTableRow(String field, String value,
      {required String key, required dynamic rawValue}) {
    Color valueColor = key == 'errors' ? AppColors.red : AppColors.green;

    return TableRow(
      children: [
        Padding(
          padding: const EdgeInsets.all(8),
          child: Text(
            field,
            style: const TextStyle(fontSize: 14, color: AppColors.text),
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(8),
          child: Text(
            value,
            style: TextStyle(fontSize: 14, color: valueColor),
          ),
        ),
      ],
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar: const CustomAppBar(
          title: "Monitoring",
          isSubScreen: true,
        ),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                CustomDropdown<String>(
                  label: 'Asset Type',
                  value: _selectedType,
                  items: _serialNumbers
                      .map((serial) => serial['type'] as String)
                      .toSet()
                      .map<DropdownMenuItem<String>>(
                          (type) => DropdownMenuItem<String>(
                                value: type,
                                child: Text(type.titleCase),
                              ))
                      .toList(),
                  onChanged: _onTypeChanged,
                ),
                const SizedBox(height: 16),
                CustomDropdown<String>(
                  label: 'Serial Number',
                  value: _selectedSerialNumber,
                  items: _selectedType == null
                      ? []
                      : _serialNumbers
                          .where((serial) => serial['type'] == _selectedType)
                          .map<DropdownMenuItem<String>>((serial) {
                          final id = serial['id'] as String;
                          final name = serial['name'] as String;
                          return DropdownMenuItem<String>(
                            value: id,
                            child:
                                Text('$id${name.isNotEmpty ? ' ($name)' : ''}'),
                          );
                        }).toList(),
                  onChanged: _onSerialNumberChanged,
                ),
                const SizedBox(height: 16),
                CustomDropdown<String>(
                  label: 'Sensor',
                  value: _selectedSensor,
                  items: const [
                    DropdownMenuItem(value: 'mopeka', child: Text('Mopeka')),
                    DropdownMenuItem(value: 'bms', child: Text('BMS')),
                    DropdownMenuItem(value: 'raspi', child: Text('Raspi')),
                    DropdownMenuItem(value: 'tilt', child: Text('Tilt')),
                  ],
                  onChanged: _onSensorChanged,
                ),
                const SizedBox(height: 24),
                CustomButton(
                  text: 'Monitor',
                  icon: Icons.monitor,
                  onPressed: _controller.isLoading ? () {} : _subscribeToTopic,
                ),
                const SizedBox(height: 24),
                const Text(
                  'Latest Data:',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.bold,
                    color: AppColors.secondary,
                  ),
                ),
                const SizedBox(height: 8),
                Expanded(
                  child: SingleChildScrollView(
                    child: _buildMessageDisplay(),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

extension StringExtension on String {
  String get titleCase {
    return split(' ').map((word) {
      if (word.isEmpty) return word;
      return word[0].toUpperCase() + word.substring(1).toLowerCase();
    }).join(' ');
  }
}
