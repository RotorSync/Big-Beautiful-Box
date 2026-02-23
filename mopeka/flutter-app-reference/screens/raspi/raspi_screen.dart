import 'dart:async';
import 'dart:io';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../constants/colors.dart';
import '../../controllers/raspi_controller.dart';
import '../../utils/logger.dart';
import '../../widgets/app_bar.dart';
import 'raspi_config_screen.dart';

class RaspiScreen extends StatefulWidget {
  final RaspiController raspiController;

  const RaspiScreen({super.key, required this.raspiController});

  @override
  State<RaspiScreen> createState() => _RaspiScreenState();
}

class _RaspiScreenState extends State<RaspiScreen> {
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    AppLogger.info(
        "RaspiScreen initialized on ${Platform.isIOS ? 'iOS' : 'Android'}");
    widget.raspiController.onStateChanged = () {
      AppLogger.info(
          "RasPi state changed - Connected: ${widget.raspiController.isConnected}, Status: ${widget.raspiController.raspiStatus}");
      if (mounted) setState(() {});
    };
    widget.raspiController.onConnectionStatusChanged = (status) {
      AppLogger.info("Connection status changed to $status");
      if (mounted) setState(() {});
    };
    widget.raspiController.onValidationError = (error) {
      AppLogger.error("Validation error: $error");
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(error), backgroundColor: AppColors.red),
        );
      }
    };
    _refreshTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (mounted) {
        AppLogger.debug(
            "Refresh timer triggered, status: ${widget.raspiController.raspiStatus}");
        setState(() {});
      }
    });
  }

  @override
  void dispose() {
    AppLogger.info("RaspiScreen disposed");
    _refreshTimer?.cancel();
    super.dispose();
  }

  double _calculateAverage(List<double> temperatures) {
    if (temperatures.isEmpty) return 0.0;
    double sum = temperatures.reduce((a, b) => a + b);
    return sum / temperatures.length;
  }

  Widget _buildTemperatureBarPair({
    required double chtTemp,
    required double egtTemp,
    required double chtMaxValue,
    required double egtMaxValue,
    required double chtWarningThreshold,
    required double egtWarningThreshold,
    required int probeIndex,
  }) {
    double chtHeightFactor = chtTemp / chtMaxValue;
    double egtHeightFactor = egtTemp / egtMaxValue;
    bool chtIsWarning = chtTemp >= chtWarningThreshold;
    bool egtIsWarning = egtTemp >= egtWarningThreshold;

    return Column(
      children: [
        Row(
          children: [
            Container(
              width: 20,
              height: 100,
              decoration: BoxDecoration(
                border: Border.all(color: AppColors.offWhite),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Stack(
                alignment: Alignment.bottomCenter,
                children: [
                  Container(
                    width: 20,
                    height: 100 * chtHeightFactor.clamp(0.0, 1.0),
                    decoration: BoxDecoration(
                      color: chtIsWarning ? AppColors.red : AppColors.green,
                      borderRadius: BorderRadius.circular(4),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 4),
            Container(
              width: 20,
              height: 100,
              decoration: BoxDecoration(
                border: Border.all(color: AppColors.offWhite),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Stack(
                alignment: Alignment.bottomCenter,
                children: [
                  Container(
                    width: 20,
                    height: 100 * egtHeightFactor.clamp(0.0, 1.0),
                    decoration: BoxDecoration(
                      color: egtIsWarning ? AppColors.red : AppColors.primary,
                      borderRadius: BorderRadius.circular(4),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          '${probeIndex + 1}',
          style: const TextStyle(fontSize: 12, color: AppColors.text),
        ),
      ],
    );
  }

  Widget _buildTemperatureTable(Map<String, double> data) {
    return Container(
      margin: const EdgeInsets.only(top: 24),
      child: Table(
        border: TableBorder.all(color: AppColors.offWhite),
        columnWidths: const {
          0: FlexColumnWidth(1),
          1: FlexColumnWidth(1),
        },
        children: [
          const TableRow(
            decoration: BoxDecoration(color: AppColors.background),
            children: [
              Padding(
                padding: EdgeInsets.all(8),
                child: Text(
                  'Probe',
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
                  'Temp (°F)',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.bold,
                    color: AppColors.secondary,
                  ),
                ),
              ),
            ],
          ),
          ...List.generate(
              6,
              (index) => TableRow(
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(8),
                        child: Text(
                          'CHT ${index + 1}',
                          style: const TextStyle(
                              fontSize: 14, color: AppColors.text),
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.all(8),
                        child: Text(
                          (data["cht_${index + 1}"] ?? 0.0).toStringAsFixed(1),
                          style: TextStyle(
                            fontSize: 14,
                            color: (data["cht_${index + 1}"] ?? 0.0) >= 400.0
                                ? AppColors.red
                                : AppColors.green,
                          ),
                        ),
                      ),
                    ],
                  )),
          ...List.generate(
            6,
            (index) => TableRow(
              children: [
                Padding(
                  padding: const EdgeInsets.all(8),
                  child: Text(
                    'EGT ${index + 1}',
                    style: const TextStyle(fontSize: 14, color: AppColors.text),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.all(8),
                  child: Text(
                    (data["egt_${index + 1}"] ?? 0.0).toStringAsFixed(1),
                    style: TextStyle(
                      fontSize: 14,
                      color: (data["egt_${index + 1}"] ?? 0.0) >= 1450.0
                          ? AppColors.red
                          : AppColors.primary,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final user = FirebaseAuth.instance.currentUser;

    return Scaffold(
      appBar: const CustomAppBar(title: 'RasPi', isSubScreen: true),
      body: SafeArea(
        child: user == null
            ? const Center(
                child: Text(
                  'Please sign in to view RasPi devices.',
                  style: TextStyle(fontSize: 16, color: AppColors.text),
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
                        'No RasPi devices added yet.',
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
                              widget.raspiController.getDeviceData(identifier);

                          AppLogger.info(
                              "Building UI for $identifier: Connected=${widget.raspiController.isConnected}, Status=${widget.raspiController.raspiStatus}");

                          List<double> chtTemps = [
                            data["cht_1"] as double? ?? 0.0,
                            data["cht_2"] as double? ?? 0.0,
                            data["cht_3"] as double? ?? 0.0,
                            data["cht_4"] as double? ?? 0.0,
                            data["cht_5"] as double? ?? 0.0,
                            data["cht_6"] as double? ?? 0.0,
                          ];

                          List<double> egtTemps = [
                            data["egt_1"] as double? ?? 0.0,
                            data["egt_2"] as double? ?? 0.0,
                            data["egt_3"] as double? ?? 0.0,
                            data["egt_4"] as double? ?? 0.0,
                            data["egt_5"] as double? ?? 0.0,
                            data["egt_6"] as double? ?? 0.0,
                          ];

                          double chtAverage = _calculateAverage(chtTemps);
                          double egtAverage = _calculateAverage(egtTemps);

                          return Column(
                            crossAxisAlignment: CrossAxisAlignment.center,
                            children: [
                              Row(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Column(
                                    children: [
                                      const Text(
                                        'CHT °F',
                                        style: TextStyle(
                                          fontSize: 16,
                                          fontWeight: FontWeight.bold,
                                          color: AppColors.secondary,
                                        ),
                                      ),
                                      Text(
                                        widget.raspiController.isConnected
                                            ? chtAverage.toStringAsFixed(0)
                                            : 'N/A',
                                        style: TextStyle(
                                          fontSize: 24,
                                          fontWeight: FontWeight.bold,
                                          color: chtAverage >= 400.0
                                              ? AppColors.red
                                              : AppColors.green,
                                        ),
                                      ),
                                    ],
                                  ),
                                  const SizedBox(width: 32),
                                  Column(
                                    children: [
                                      const Text(
                                        'EGT °F',
                                        style: TextStyle(
                                          fontSize: 16,
                                          fontWeight: FontWeight.bold,
                                          color: AppColors.secondary,
                                        ),
                                      ),
                                      Text(
                                        widget.raspiController.isConnected
                                            ? egtAverage.toStringAsFixed(0)
                                            : 'N/A',
                                        style: TextStyle(
                                          fontSize: 24,
                                          fontWeight: FontWeight.bold,
                                          color: egtAverage >= 1450.0
                                              ? AppColors.red
                                              : AppColors.primary,
                                        ),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                              const SizedBox(height: 24),
                              Row(
                                mainAxisAlignment:
                                    MainAxisAlignment.spaceEvenly,
                                children: List.generate(6, (index) {
                                  return _buildTemperatureBarPair(
                                    chtTemp: chtTemps[index],
                                    egtTemp: egtTemps[index],
                                    chtMaxValue: 500.0,
                                    egtMaxValue: 1600.0,
                                    chtWarningThreshold: 400.0,
                                    egtWarningThreshold: 1450.0,
                                    probeIndex: index,
                                  );
                                }),
                              ),
                              _buildTemperatureTable(
                                  data.cast<String, double>()),
                              if (index < devices.length - 1)
                                const Divider(color: AppColors.grey),
                              const SizedBox(height: 24),
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
          AppLogger.info("Navigating to RaspiConfigScreen");
          Navigator.push(
            context,
            MaterialPageRoute(
              builder: (context) =>
                  RaspiConfigScreen(controller: widget.raspiController),
            ),
          );
        },
      ),
    );
  }
}
