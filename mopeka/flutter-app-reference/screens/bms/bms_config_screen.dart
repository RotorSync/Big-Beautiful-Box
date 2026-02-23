import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../constants/colors.dart';
import '../../controllers/bms_controller.dart';
import '../../utils/logger.dart';
import '../../utils/validators.dart';
import '../../widgets/app_bar.dart';
import '../../widgets/button.dart';
import '../../widgets/dropdown.dart';
import '../../widgets/input_field.dart';

class BmsConfigScreen extends StatefulWidget {
  final Map<String, dynamic>? device;
  final String? deviceId;
  final BmsController controller;

  const BmsConfigScreen(
      {super.key, this.device, this.deviceId, required this.controller});

  @override
  State<BmsConfigScreen> createState() => _BmsConfigScreenState();
}

class _BmsConfigScreenState extends State<BmsConfigScreen> {
  final FocusNode _nameFocus = FocusNode();
  final FocusNode _deviceNameFocus = FocusNode();
  final FocusNode _mqttTopicFocus = FocusNode();
  final _formKey = GlobalKey<FormState>();
  String? _selectedThreshold;

  @override
  void initState() {
    super.initState();
    widget.controller.onStateChanged = () {
      AppLogger.info("BmsConfigScreen state changed");
      if (mounted) setState(() {});
    };
    widget.controller.onValidationError = (error) {
      AppLogger.error("Validation error in BmsConfigScreen: $error");
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(error), backgroundColor: AppColors.red),
        );
      }
    };
    if (widget.device != null && widget.deviceId != null) {
      widget.controller.editDevice(widget.device!, widget.deviceId!);
      _selectedThreshold = widget.device!['threshold']?.toString() ?? '50';
    } else {
      _selectedThreshold = '50';
    }
  }

  @override
  void dispose() {
    _nameFocus.dispose();
    _deviceNameFocus.dispose();
    _mqttTopicFocus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final user = FirebaseAuth.instance.currentUser;

    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar:
            const CustomAppBar(title: 'BMS Configuration', isSubScreen: true),
        body: SingleChildScrollView(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Form(
                  key: _formKey,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      CustomInputField(
                        label: 'Name',
                        hintText: 'e.g., Trailer 1 BMS',
                        controller: widget.controller.nameController,
                        focusNode: _nameFocus,
                        validator: Validators.validateName,
                      ),
                      const SizedBox(height: 16),
                      CustomInputField(
                        label: 'Device Name',
                        hintText: 'e.g., Batt',
                        controller: widget.controller.deviceNameController,
                        focusNode: _deviceNameFocus,
                        validator: Validators.validateDeviceName,
                      ),
                      const SizedBox(height: 16),
                      CustomInputField(
                        label: 'MQTT Topic',
                        hintText: 'e.g., bms/telemetry/Batt1',
                        controller: widget.controller.mqttTopicController,
                        focusNode: _mqttTopicFocus,
                        validator: Validators.validateMQTTTopic,
                      ),
                      const SizedBox(height: 16),
                      CustomDropdown<String>(
                        label: 'Threshold',
                        value: _selectedThreshold,
                        items: List.generate(10, (index) {
                          final value = (index + 1) * 10;
                          return DropdownMenuItem(
                            value: value.toString(),
                            child: Text('$value%'),
                          );
                        }),
                        onChanged: (String? newValue) {
                          if (newValue != null) {
                            setState(() {
                              _selectedThreshold = newValue;
                              widget.controller.thresholdController.text =
                                  newValue;
                            });
                          }
                        },
                      ),
                      const SizedBox(height: 24),
                      CustomButton(
                        text: widget.controller.isEditing ? 'Update' : 'Add',
                        icon: widget.controller.isEditing
                            ? LucideIcons.refreshCcw
                            : LucideIcons.plus,
                        onPressed: () async {
                          if (_formKey.currentState!.validate()) {
                            if (widget.controller.isEditing) {
                              await widget.controller
                                  .updateDevice(_selectedThreshold!);
                            } else {
                              await widget.controller
                                  .addDevice(_selectedThreshold!);
                            }
                            if (mounted) {
                              Navigator.pop(context);
                            }
                          }
                        },
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 24),
                const Text(
                  'Devices',
                  style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      color: AppColors.secondary),
                ),
                const SizedBox(height: 16),
                user == null
                    ? const Center(
                        child: Text(
                          'Please sign in to view devices.',
                          style: TextStyle(fontSize: 16, color: AppColors.grey),
                        ),
                      )
                    : StreamBuilder<QuerySnapshot>(
                        stream: FirebaseFirestore.instance
                            .collection('users')
                            .doc(user.uid)
                            .collection('bms_devices')
                            .snapshots(),
                        builder: (context, snapshot) {
                          if (snapshot.hasError) {
                            return const Center(
                              child: Text(
                                'Error loading devices',
                                style: TextStyle(
                                    fontSize: 16, color: AppColors.red),
                              ),
                            );
                          }
                          if (!snapshot.hasData) {
                            return const Center(
                                child: CircularProgressIndicator(
                                    color: AppColors.primary));
                          }

                          final devices = snapshot.data!.docs;
                          if (devices.isEmpty) {
                            return const Center(
                              child: Text(
                                'No devices added yet.',
                                style: TextStyle(
                                    fontSize: 16, color: AppColors.text),
                              ),
                            );
                          }

                          return ListView.builder(
                            shrinkWrap: true,
                            physics: const NeverScrollableScrollPhysics(),
                            itemCount: devices.length,
                            itemBuilder: (context, index) {
                              final deviceDoc = devices[index];
                              final deviceData =
                                  deviceDoc.data() as Map<String, dynamic>;
                              final deviceId = deviceDoc.id;

                              return Container(
                                padding: const EdgeInsets.all(16),
                                margin: const EdgeInsets.only(bottom: 8),
                                decoration: BoxDecoration(
                                  color: AppColors.background,
                                  borderRadius: BorderRadius.circular(12),
                                ),
                                child: Row(
                                  children: [
                                    Expanded(
                                      child: Column(
                                        crossAxisAlignment:
                                            CrossAxisAlignment.start,
                                        children: [
                                          Text(
                                            deviceData['name'] as String? ??
                                                'Unnamed Device',
                                            style: const TextStyle(
                                              fontSize: 16,
                                              fontWeight: FontWeight.bold,
                                              color: AppColors.secondary,
                                            ),
                                          ),
                                          const SizedBox(height: 8),
                                          Text(
                                            'Device Name: ${deviceData['device_name'] as String? ?? 'N/A'}',
                                            style: const TextStyle(
                                                fontSize: 14,
                                                color: AppColors.text),
                                          ),
                                          const SizedBox(height: 8),
                                          Text(
                                            'MQTT Topic: ${deviceData['mqtt_topic'] as String? ?? 'N/A'}',
                                            style: const TextStyle(
                                                fontSize: 14,
                                                color: AppColors.text),
                                          ),
                                          const SizedBox(height: 8),
                                          Text(
                                            'Threshold: ${deviceData['threshold'] ?? 'N/A'}%',
                                            style: const TextStyle(
                                                fontSize: 14,
                                                color: AppColors.text),
                                          ),
                                        ],
                                      ),
                                    ),
                                    IconButton(
                                      icon: const Icon(LucideIcons.pencil,
                                          color: AppColors.grey, size: 20),
                                      onPressed: () {
                                        widget.controller
                                            .editDevice(deviceData, deviceId);
                                        setState(() {
                                          _selectedThreshold =
                                              deviceData['threshold']
                                                      ?.toString() ??
                                                  '50';
                                        });
                                      },
                                    ),
                                    IconButton(
                                      icon: const Icon(LucideIcons.trash2,
                                          color: AppColors.red, size: 20),
                                      onPressed: () async {
                                        await widget.controller
                                            .deleteDevice(deviceId);
                                      },
                                    ),
                                  ],
                                ),
                              );
                            },
                          );
                        },
                      ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
