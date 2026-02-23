import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../constants/colors.dart';
import '../../controllers/raspi_controller.dart';
import '../../utils/logger.dart';
import '../../utils/validators.dart';
import '../../widgets/app_bar.dart';
import '../../widgets/button.dart';
import '../../widgets/input_field.dart';

class RaspiConfigScreen extends StatefulWidget {
  final RaspiController controller;

  const RaspiConfigScreen({super.key, required this.controller});

  @override
  State<RaspiConfigScreen> createState() => _RaspiConfigScreenState();
}

class _RaspiConfigScreenState extends State<RaspiConfigScreen> {
  final FocusNode _nameFocus = FocusNode();
  final FocusNode _deviceNameFocus = FocusNode();
  final FocusNode _mqttTopicFocus = FocusNode();
  final _formKey = GlobalKey<FormState>();
  String? _editingDeviceId;

  @override
  void initState() {
    super.initState();
    widget.controller.onStateChanged = () {
      AppLogger.info("RaspiConfigScreen state changed");
      if (mounted) setState(() {});
    };
    widget.controller.onValidationError = (error) {
      AppLogger.error("Validation error in RaspiConfigScreen: $error");
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(error), backgroundColor: AppColors.red),
        );
      }
    };
  }

  @override
  void dispose() {
    _nameFocus.dispose();
    _deviceNameFocus.dispose();
    _mqttTopicFocus.dispose();
    super.dispose();
  }

  void _clearForm() {
    widget.controller.nameController.clear();
    widget.controller.deviceNameController.clear();
    widget.controller.mqttTopicController.clear();
    _editingDeviceId = null;
  }

  @override
  Widget build(BuildContext context) {
    final user = FirebaseAuth.instance.currentUser;

    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar:
            const CustomAppBar(title: 'RasPi Configuration', isSubScreen: true),
        body: Padding(
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
                      hintText: 'e.g., Engine RasPi',
                      controller: widget.controller.nameController,
                      focusNode: _nameFocus,
                      validator: Validators.validateName,
                    ),
                    const SizedBox(height: 16),
                    CustomInputField(
                      label: 'Device Name',
                      hintText: 'e.g., RasPi1',
                      controller: widget.controller.deviceNameController,
                      focusNode: _deviceNameFocus,
                      validator: Validators.validateDeviceName,
                    ),
                    const SizedBox(height: 16),
                    CustomInputField(
                      label: 'MQTT Topic',
                      hintText: 'e.g., raspi/engine/temperatures',
                      controller: widget.controller.mqttTopicController,
                      focusNode: _mqttTopicFocus,
                      validator: Validators.validateMQTTTopic,
                    ),
                    const SizedBox(height: 24),
                    CustomButton(
                      text: _editingDeviceId == null ? 'Add' : 'Update',
                      icon: _editingDeviceId == null
                          ? LucideIcons.plus
                          : LucideIcons.save,
                      onPressed: () async {
                        if (_formKey.currentState!.validate()) {
                          final name =
                              widget.controller.nameController.text.trim();
                          final deviceName = widget
                              .controller.deviceNameController.text
                              .trim();
                          final mqttTopic =
                              widget.controller.mqttTopicController.text.trim();
                          if (_editingDeviceId == null) {
                            await widget.controller
                                .addDevice(name, deviceName, mqttTopic);
                          } else {
                            await widget.controller.updateDevice(
                                _editingDeviceId!, name, deviceName, mqttTopic);
                          }
                          _clearForm();
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
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                  color: AppColors.secondary,
                ),
              ),
              const SizedBox(height: 8),
              Expanded(
                child: user == null
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
                            .collection('raspi_devices')
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
                                child: CircularProgressIndicator());
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
                                        ],
                                      ),
                                    ),
                                    IconButton(
                                      icon: const Icon(
                                        LucideIcons.edit,
                                        color: AppColors.primary,
                                        size: 20,
                                      ),
                                      onPressed: () {
                                        widget.controller.nameController.text =
                                            deviceData['name'] ?? '';
                                        widget.controller.deviceNameController
                                                .text =
                                            deviceData['device_name'] ?? '';
                                        widget.controller.mqttTopicController
                                                .text =
                                            deviceData['mqtt_topic'] ?? '';
                                        setState(() {
                                          _editingDeviceId = deviceId;
                                        });
                                        AppLogger.info(
                                            "Editing RasPi device: $deviceId");
                                      },
                                    ),
                                    IconButton(
                                      icon: const Icon(
                                        LucideIcons.trash2,
                                        color: AppColors.red,
                                        size: 20,
                                      ),
                                      onPressed: () async {
                                        await widget.controller
                                            .deleteDevice(deviceId);
                                        if (_editingDeviceId == deviceId) {
                                          _clearForm();
                                        }
                                        AppLogger.info(
                                            "Deleted RasPi device: $deviceId");
                                      },
                                    ),
                                  ],
                                ),
                              );
                            },
                          );
                        },
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
