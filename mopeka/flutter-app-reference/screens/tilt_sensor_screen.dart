import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../widgets/app_bar.dart';
import '../../constants/colors.dart';
import '../../controllers/tilt_sensor_controller.dart';
import '../../utils/logger.dart';

class TiltSensorScreen extends StatefulWidget {
  final TiltSensorController tiltSensorController;

  const TiltSensorScreen({super.key, required this.tiltSensorController});

  @override
  State<TiltSensorScreen> createState() => _TiltSensorScreenState();
}

class _TiltSensorScreenState extends State<TiltSensorScreen> {
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    widget.tiltSensorController.onStateChanged = () {
      AppLogger.info(
          "Tilt sensor state changed on ${Platform.isIOS ? 'iOS' : 'Android'} - Connected: ${widget.tiltSensorController.isConnected}");
      if (mounted) setState(() {});
    };
    widget.tiltSensorController.onConnectionStatusChanged = (status) {
      AppLogger.info(
          "Connection status changed to $status on ${Platform.isIOS ? 'iOS' : 'Android'}");
      if (mounted) setState(() {});
    };
    widget.tiltSensorController.onValidationError = (error) {
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
    final screenWidth = MediaQuery.of(context).size.width;
    final crossAxisCount = screenWidth > 600 ? 3 : 2;
    final data = widget.tiltSensorController.getSensorData();

    AppLogger.info(
        "Building UI: Connected=${widget.tiltSensorController.isConnected}");

    return Scaffold(
      appBar: const CustomAppBar(title: 'Tilt Sensor', isSubScreen: true),
      body: SafeArea(
        child: SingleChildScrollView(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                const Text(
                  'WT901BLECL Sensor',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                    color: AppColors.secondary,
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  widget.tiltSensorController.isConnected
                      ? 'Connected'
                      : 'Disconnected',
                  style: TextStyle(
                    fontSize: 14,
                    color: widget.tiltSensorController.isConnected
                        ? AppColors.green
                        : AppColors.red,
                  ),
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
                      icon: LucideIcons.activity,
                      label: 'Acc X',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["acc_x"] as double? ?? 0.0).toStringAsFixed(3)} g'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.activity,
                      label: 'Acc Y',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["acc_y"] as double? ?? 0.0).toStringAsFixed(3)} g'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.activity,
                      label: 'Acc Z',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["acc_z"] as double? ?? 0.0).toStringAsFixed(3)} g'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.rotateCw,
                      label: 'Gyro X',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["gyro_x"] as double? ?? 0.0).toStringAsFixed(3)} °/s'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.rotateCw,
                      label: 'Gyro Y',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["gyro_y"] as double? ?? 0.0).toStringAsFixed(3)} °/s'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.rotateCw,
                      label: 'Gyro Z',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["gyro_z"] as double? ?? 0.0).toStringAsFixed(3)} °/s'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.compass,
                      label: 'Angle X',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["angle_x"] as double? ?? 0.0).toStringAsFixed(3)} °'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.compass,
                      label: 'Angle Y',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["angle_y"] as double? ?? 0.0).toStringAsFixed(3)} °'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.compass,
                      label: 'Angle Z',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["angle_z"] as double? ?? 0.0).toStringAsFixed(3)} °'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.magnet,
                      label: 'Mag X',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["mag_x"] as double? ?? 0.0).toStringAsFixed(3)} µT'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.magnet,
                      label: 'Mag Y',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["mag_y"] as double? ?? 0.0).toStringAsFixed(3)} µT'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.magnet,
                      label: 'Mag Z',
                      reading: widget.tiltSensorController.isConnected
                          ? '${(data["mag_z"] as double? ?? 0.0).toStringAsFixed(3)} µT'
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.rotate3d,
                      label: 'Quat 0',
                      reading: widget.tiltSensorController.isConnected
                          ? (data["quat_0"] as double? ?? 0.0)
                              .toStringAsFixed(3)
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.rotate3d,
                      label: 'Quat 1',
                      reading: widget.tiltSensorController.isConnected
                          ? (data["quat_1"] as double? ?? 0.0)
                              .toStringAsFixed(3)
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.rotate3d,
                      label: 'Quat 2',
                      reading: widget.tiltSensorController.isConnected
                          ? (data["quat_2"] as double? ?? 0.0)
                              .toStringAsFixed(3)
                          : 'N/A',
                    ),
                    _buildDataCard(
                      icon: LucideIcons.rotate3d,
                      label: 'Quat 3',
                      reading: widget.tiltSensorController.isConnected
                          ? (data["quat_3"] as double? ?? 0.0)
                              .toStringAsFixed(3)
                          : 'N/A',
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
