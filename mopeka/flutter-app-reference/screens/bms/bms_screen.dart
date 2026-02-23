import 'dart:async';
import 'dart:io';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import 'package:rotorsync/widgets/app_bar.dart';
import '../../constants/colors.dart';
import '../../controllers/bms_controller.dart';
import '../../utils/logger.dart';
import 'bms_config_screen.dart';

class BmsScreen extends StatefulWidget {
  final BmsController bmsController;

  const BmsScreen({super.key, required this.bmsController});

  @override
  State<BmsScreen> createState() => _BmsScreenState();
}

class _BmsScreenState extends State<BmsScreen> {
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    widget.bmsController.onStateChanged = () {
      AppLogger.info(
          "BMS state changed on ${Platform.isIOS ? 'iOS' : 'Android'} - Connected: ${widget.bmsController.isConnected}");
      if (mounted) setState(() {});
    };
    widget.bmsController.onConnectionStatusChanged = (status) {
      AppLogger.info(
          "Connection status changed to $status on ${Platform.isIOS ? 'iOS' : 'Android'}");
      if (mounted) setState(() {});
    };
    widget.bmsController.onValidationError = (error) {
      AppLogger.error("Validation error: $error");
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(error), backgroundColor: AppColors.red),
        );
      }
    };
    _refreshTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  Widget _buildDataCard({
    required IconData icon,
    required String label,
    required String reading,
  }) {
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: AppColors.background,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.offWhite),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            padding: const EdgeInsets.all(8),
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              color: AppColors.accent,
            ),
            child: Icon(icon, color: AppColors.primary, size: 20),
          ),
          const SizedBox(height: 6),
          Text(
            label,
            style: const TextStyle(fontSize: 12, color: AppColors.text),
            textAlign: TextAlign.center,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 4),
          Text(
            reading,
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.bold,
              color: AppColors.secondary,
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final user = FirebaseAuth.instance.currentUser;
    final screenWidth = MediaQuery.of(context).size.width;
    final crossAxisCount = screenWidth > 600 ? 3 : 2;

    return Scaffold(
      appBar: const CustomAppBar(title: 'BMS', isSubScreen: true),
      body: SafeArea(
        child: user == null
            ? const Center(
                child: Text(
                  'Please sign in to view BMS devices.',
                  style: TextStyle(fontSize: 16, color: AppColors.text),
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
                        style: TextStyle(fontSize: 16, color: AppColors.red),
                      ),
                    );
                  }
                  if (!snapshot.hasData) {
                    return const Center(child: CircularProgressIndicator());
                  }

                  final devices = snapshot.data!.docs;
                  if (devices.isEmpty) {
                    return const Center(
                      child: Text(
                        'No BMS devices added yet.',
                        style: TextStyle(fontSize: 16, color: AppColors.text),
                      ),
                    );
                  }

                  return SingleChildScrollView(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: devices.asMap().entries.map((entry) {
                          final index = entry.key;
                          final deviceDoc = entry.value;
                          final deviceData =
                              deviceDoc.data() as Map<String, dynamic>;
                          final identifier =
                              deviceData['device_name'] as String? ?? '';
                          final data =
                              widget.bmsController.getDeviceData(identifier);

                          final soc = data["state_of_charge"] as int? ?? 0;

                          AppLogger.info(
                              "Building UI for $identifier: SOC=$soc, Connected=${widget.bmsController.isConnected}");

                          return Column(
                            crossAxisAlignment: CrossAxisAlignment.center,
                            children: [
                              Text(
                                deviceData['name'] as String? ??
                                    'Unnamed Device',
                                style: const TextStyle(
                                  fontSize: 18,
                                  fontWeight: FontWeight.bold,
                                  color: AppColors.secondary,
                                ),
                              ),
                              const SizedBox(height: 8),
                              Text(
                                'Device: $identifier',
                                style: const TextStyle(
                                    fontSize: 14, color: AppColors.text),
                              ),
                              const SizedBox(height: 24),
                              Stack(
                                alignment: Alignment.center,
                                children: [
                                  SizedBox(
                                    width: 160,
                                    height: 160,
                                    child: CircularProgressIndicator(
                                      value: widget.bmsController.isConnected
                                          ? soc / 100.0
                                          : 0.0,
                                      strokeWidth: 12,
                                      backgroundColor:
                                          AppColors.grey.withOpacity(0.2),
                                      valueColor: AlwaysStoppedAnimation<Color>(
                                        widget.bmsController.isConnected
                                            ? AppColors.green
                                            : AppColors.grey,
                                      ),
                                    ),
                                  ),
                                  Column(
                                    mainAxisAlignment: MainAxisAlignment.center,
                                    children: [
                                      const Text(
                                        'SOC',
                                        style: TextStyle(
                                            fontSize: 16,
                                            color: AppColors.text),
                                      ),
                                      Text(
                                        widget.bmsController.isConnected
                                            ? '$soc%'
                                            : 'N/A',
                                        style: const TextStyle(
                                          fontSize: 24,
                                          fontWeight: FontWeight.bold,
                                          color: AppColors.secondary,
                                        ),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                              const SizedBox(height: 24),
                              GridView.count(
                                crossAxisCount: crossAxisCount,
                                shrinkWrap: true,
                                physics: const NeverScrollableScrollPhysics(),
                                crossAxisSpacing: 12,
                                mainAxisSpacing: 12,
                                childAspectRatio: 1.2,
                                children: [
                                  _buildDataCard(
                                    icon: LucideIcons.zap,
                                    label: 'Total Voltage',
                                    reading: widget.bmsController.isConnected
                                        ? '${(data["total_voltage"] as double? ?? 0.0).toStringAsFixed(2)}V'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.zap,
                                    label: 'Current',
                                    reading: widget.bmsController.isConnected
                                        ? '${(data["current"] as double? ?? 0.0).toStringAsFixed(2)}A'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.plug2,
                                    label: 'Power',
                                    reading: widget.bmsController.isConnected
                                        ? '${(data["power"] as double? ?? 0.0).toStringAsFixed(2)}W'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.batteryCharging,
                                    label: 'Remaining Capacity',
                                    reading: widget.bmsController.isConnected
                                        ? '${(data["capacity_remaining"] as double? ?? 0.0).toStringAsFixed(2)}Ah'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.batteryFull,
                                    label: 'Nominal Capacity',
                                    reading: widget.bmsController.isConnected
                                        ? '${(data["nominal_capacity"] as double? ?? 0.0).toStringAsFixed(2)}Ah'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.repeat,
                                    label: 'Charging Cycles',
                                    reading: widget.bmsController.isConnected
                                        ? '${data["charging_cycles"] as int? ?? 0}'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.activity,
                                    label: 'Delta Cell Voltage',
                                    reading: widget.bmsController.isConnected
                                        ? '${(data["delta_cell_voltage"] as double? ?? 0.0).toStringAsFixed(3)}V'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.cpu,
                                    label: 'Software Version',
                                    reading: widget.bmsController.isConnected
                                        ? (data["software_version"]
                                                    as double? ??
                                                0.0)
                                            .toStringAsFixed(1)
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.alertTriangle,
                                    label: 'Errors',
                                    reading: widget.bmsController.isConnected
                                        ? data["errors"] as String? ?? 'None'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.scale,
                                    label: 'Balancing',
                                    reading: widget.bmsController.isConnected
                                        ? data["balancing"] as bool? ?? false
                                            ? 'Active'
                                            : 'Inactive'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.batteryCharging,
                                    label: 'Charging',
                                    reading: widget.bmsController.isConnected
                                        ? data["charging"] as bool? ?? false
                                            ? 'Active'
                                            : 'Inactive'
                                        : 'N/A',
                                  ),
                                  _buildDataCard(
                                    icon: LucideIcons.battery,
                                    label: 'Discharging',
                                    reading: widget.bmsController.isConnected
                                        ? data["discharging"] as bool? ?? false
                                            ? 'Active'
                                            : 'Inactive'
                                        : 'N/A',
                                  ),
                                  for (int i = 1;
                                      i <= (data["total_cells"] as int? ?? 0);
                                      i++)
                                    _buildDataCard(
                                      icon: LucideIcons.battery,
                                      label: 'Cell $i Voltage',
                                      reading: widget.bmsController.isConnected
                                          ? '${(data["cell_voltage_$i"] as double? ?? 0.0).toStringAsFixed(3)}V'
                                          : 'N/A',
                                    ),
                                  for (int i = 1; i <= 6; i++)
                                    if (data.containsKey('temperature_$i'))
                                      _buildDataCard(
                                        icon: LucideIcons.thermometer,
                                        label: 'Temperature $i',
                                        reading: widget
                                                .bmsController.isConnected
                                            ? '${(data["temperature_$i"] as double? ?? 0.0).toStringAsFixed(1)}°C'
                                            : 'N/A',
                                      ),
                                ],
                              ),
                              const SizedBox(height: 24),
                              if (index < devices.length - 1)
                                const Divider(color: AppColors.grey),
                            ],
                          );
                        }).toList(),
                      ),
                    ),
                  );
                },
              ),
      ),
      floatingActionButton: FloatingActionButton(
        backgroundColor: AppColors.primary,
        child: const Icon(LucideIcons.plus, color: AppColors.white),
        onPressed: () {
          Navigator.push(
            context,
            MaterialPageRoute(
              builder: (context) =>
                  BmsConfigScreen(controller: widget.bmsController),
            ),
          );
        },
      ),
    );
  }
}
