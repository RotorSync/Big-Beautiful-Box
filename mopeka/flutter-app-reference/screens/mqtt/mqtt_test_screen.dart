import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../controllers/mqtt_test_controller.dart';
import '../../services/mqtt_service.dart';
import '../../widgets/app_bar.dart';
import '../../constants/colors.dart';
import '../../widgets/button.dart';
import '../../widgets/input_field.dart';

class MQTTTestScreen extends StatefulWidget {
  final MQTTService mqttService;

  const MQTTTestScreen({super.key, required this.mqttService});

  @override
  State<MQTTTestScreen> createState() => _MQTTTestScreenState();
}

class _MQTTTestScreenState extends State<MQTTTestScreen> {
  late MQTTTestController _controller;
  final _formKey = GlobalKey<FormState>();

  @override
  void initState() {
    super.initState();
    _controller = MQTTTestController(mqttService: widget.mqttService);
    _controller.onStateChanged = () {
      if (mounted) {
        setState(() {});
      }
    };
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
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
          final unit = key == 'cht' || key == 'egt'
              ? '°F'
              : key == 'temperatures'
                  ? '°C'
                  : 'V';
          final formattedValue = subValue is double
              ? subValue.toStringAsFixed(key == 'cell_voltages' ? 3 : 1)
              : subValue.toString();
          tableRows.add(_buildTableRow(
            '${key.replaceAll('_', ' ').titleCase} $subKey ($unit)',
            formattedValue,
            key: key,
            rawValue: subValue,
          ));
        });
      } else {
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
          key.replaceAll('_', ' ').titleCase,
          formattedValue,
          key: key,
          rawValue: value,
        ));
      }
    });

    return Table(
      border: TableBorder.all(color: AppColors.offWhite),
      columnWidths: const {
        0: FlexColumnWidth(1),
        1: FlexColumnWidth(1),
      },
      children: tableRows,
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
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar: const CustomAppBar(
          title: "MQTT Connection Test",
          isSubScreen: true,
        ),
        body: SafeArea(
          child: SingleChildScrollView(
            child: Padding(
              padding: const EdgeInsets.all(16.0),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    CustomInputField(
                      label: 'Topic',
                      hintText: 'e.g., bms/telemetry',
                      controller: _controller.topicController,
                    ),
                    const SizedBox(height: 16),
                    CustomInputField(
                      label: 'Message',
                      hintText: 'e.g., Hello World',
                      controller: _controller.messageController,
                    ),
                    const SizedBox(height: 24),
                    Row(
                      children: [
                        Expanded(
                          child: CustomButton(
                            text: 'Publish',
                            icon: LucideIcons.send,
                            variant: ButtonVariant.outline,
                            onPressed: _controller.isLoading
                                ? () {}
                                : () => _controller.publishMessage(context),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: CustomButton(
                            text: 'Subscribe',
                            icon: LucideIcons.bell,
                            onPressed: _controller.isLoading
                                ? () {}
                                : () => _controller.subscribeToTopic(context),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 24),
                    const Text(
                      'Latest Message:',
                      style: TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.bold,
                        color: AppColors.secondary,
                      ),
                    ),
                    const SizedBox(height: 8),
                    _buildMessageDisplay(),
                    const SizedBox(height: 24),
                  ],
                ),
              ),
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
