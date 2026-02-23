import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import 'package:provider/provider.dart';
import '../../constants/colors.dart';
import '../../controllers/mopeka_controller.dart';
import '../../widgets/app_bar.dart';

class MopekaScreen extends StatefulWidget {
  const MopekaScreen({super.key});

  @override
  State<MopekaScreen> createState() => _MopekaScreenState();
}

class _MopekaScreenState extends State<MopekaScreen> {
  final TextEditingController _tankSizeController = TextEditingController();
  bool _tankSizeConfirmed = false;

  @override
  void initState() {
    super.initState();
  }

  @override
  void dispose() {
    _tankSizeController.dispose();
    super.dispose();
  }

  Future<void> _showTankSizeDialog(
      BuildContext context, MopekaController controller) async {
    await showDialog(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Enter Tank Size'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('Please enter the tank size in inches:'),
            const SizedBox(height: 10),
            TextField(
              controller: _tankSizeController,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'Tank Size (in)',
                border: OutlineInputBorder(),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () {
              double? size = double.tryParse(_tankSizeController.text);
              if (size != null && size > 0) {
                controller.setTankSize(size);
                setState(() {
                  _tankSizeConfirmed = true;
                });
                Navigator.pop(dialogContext);
              } else {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text('Please enter a valid tank size'),
                    backgroundColor: AppColors.red,
                  ),
                );
              }
            },
            child: const Text('Confirm'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel'),
          ),
        ],
      ),
    );
  }

  Widget _buildInfoRow(String label, String value) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          label,
          style: const TextStyle(fontSize: 14, color: AppColors.text),
        ),
        Text(
          value,
          style: const TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w500,
            color: AppColors.secondary,
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<MopekaController>(
      builder: (context, controller, child) {
        // Show dialog after first frame, using Consumer's context
        if (!_tankSizeConfirmed) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            _showTankSizeDialog(context, controller);
          });
        }

        final tankLevel = controller.sensorData.readingQualityRaw >= 1
            ? controller.calculateFuelLevel()
            : null;
        final tankSize = controller.tankSize ?? 0.0;

        return Scaffold(
          backgroundColor: Colors.white,
          appBar: const CustomAppBar(
            title: 'Mopeka TD40 Sensor',
            isSubScreen: true,
          ),
          body: SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: controller.isScanning
                  ? const Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          CircularProgressIndicator(
                            color: AppColors.primary,
                          ),
                          SizedBox(height: 20),
                          Text(
                            'Scanning for Mopeka sensors...',
                            style: TextStyle(color: AppColors.text),
                          ),
                        ],
                      ),
                    )
                  : controller.isMonitoring
                      ? ListView(
                          children: [
                            Container(
                              padding: const EdgeInsets.all(16),
                              margin: const EdgeInsets.only(bottom: 8),
                              decoration: BoxDecoration(
                                color: AppColors.background,
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Column(
                                children: [
                                  Stack(
                                    alignment: Alignment.center,
                                    children: [
                                      SizedBox(
                                        width: 120,
                                        height: 120,
                                        child: CircularProgressIndicator(
                                          value: tankLevel != null
                                              ? tankLevel / 100.0
                                              : 0.0,
                                          strokeWidth: 10,
                                          backgroundColor:
                                              AppColors.grey.withOpacity(0.2),
                                          valueColor:
                                              AlwaysStoppedAnimation<Color>(
                                            tankLevel != null
                                                ? tankLevel > 20
                                                    ? AppColors.green
                                                    : AppColors.red
                                                : AppColors.grey,
                                          ),
                                        ),
                                      ),
                                      Icon(
                                        LucideIcons.fuel,
                                        size: 40,
                                        color: tankLevel != null
                                            ? AppColors.primary
                                            : AppColors.grey,
                                      ),
                                    ],
                                  ),
                                  const SizedBox(height: 16),
                                  Row(
                                    mainAxisAlignment: MainAxisAlignment.center,
                                    children: [
                                      Text(
                                        tankLevel != null
                                            ? 'Fuel Level (${tankLevel.toStringAsFixed(1)}%)'
                                            : 'Fuel Level: Not Connected',
                                        style: const TextStyle(
                                          fontSize: 16,
                                          fontWeight: FontWeight.bold,
                                          color: AppColors.secondary,
                                        ),
                                      ),
                                    ],
                                  ),
                                  const SizedBox(height: 24),
                                  Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      const Text(
                                        'Sensor Info',
                                        style: TextStyle(
                                          fontSize: 14,
                                          fontWeight: FontWeight.bold,
                                          color: AppColors.secondary,
                                        ),
                                      ),
                                      const SizedBox(height: 8),
                                      _buildInfoRow(
                                        'Name',
                                        controller.sensorData.deviceName,
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'RSSI',
                                        '${controller.sensorData.rssi} dBm',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Temperature',
                                        '${controller.sensorData.temperature.toStringAsFixed(1)} °C',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Battery',
                                        '${controller.sensorData.batteryPercentage.toStringAsFixed(1)} %',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Battery Voltage',
                                        '${controller.sensorData.batteryVoltage.toStringAsFixed(2)} V',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Tank Level Raw',
                                        '${controller.sensorData.tankLevelRaw}',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Tank Level MM',
                                        controller.sensorData
                                                    .readingQualityRaw >=
                                                1
                                            ? '${controller.sensorData.tankLevelMm.toStringAsFixed(0)} mm'
                                            : 'Unreliable',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Tank Level In',
                                        controller.sensorData
                                                    .readingQualityRaw >=
                                                1
                                            ? '${controller.sensorData.tankLevelIn.toStringAsFixed(2)} in'
                                            : 'Unreliable',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Fuel Level',
                                        controller.sensorData
                                                    .readingQualityRaw >=
                                                1
                                            ? '${controller.calculateFuelLevel().toStringAsFixed(1)} %'
                                            : 'Unreliable',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Accelerometer X',
                                        '${controller.sensorData.accelerometerX}',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Accelerometer Y',
                                        '${controller.sensorData.accelerometerY}',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Reading Quality Raw',
                                        '${controller.sensorData.readingQualityRaw}',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Reading Quality',
                                        '${controller.sensorData.readingQualityPercent} %',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Button Pressed',
                                        '${controller.sensorData.buttonPressed}',
                                      ),
                                      const SizedBox(height: 4),
                                      _buildInfoRow(
                                        'Tank Size',
                                        '${tankSize.toStringAsFixed(1)} in',
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          ],
                        )
                      : const Center(
                          child: Text(
                            'No Mopeka sensor connected',
                            style: TextStyle(color: AppColors.text),
                          ),
                        ),
            ),
          ),
        );
      },
    );
  }
}
