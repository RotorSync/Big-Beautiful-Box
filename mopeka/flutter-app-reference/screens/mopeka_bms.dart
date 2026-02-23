import 'dart:async';
import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import 'package:rotorsync/widgets/multi_select_dropdown.dart';
import '../../widgets/app_bar.dart';
import '../../constants/colors.dart';
import '../../utils/logger.dart';
import '../../controllers/trailer_controller.dart';

class MopekaBmsScreen extends StatefulWidget {
  final TrailerController trailerController;

  const MopekaBmsScreen({super.key, required this.trailerController});

  @override
  State<MopekaBmsScreen> createState() => _MopekaBmsScreenState();
}

class _MopekaBmsScreenState extends State<MopekaBmsScreen> {
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    widget.trailerController.onStateChanged = () {
      AppLogger.info("Trailer state changed");
      if (mounted) setState(() {});
    };
    widget.trailerController.onValidationError = (error) {
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
    widget.trailerController.dispose();
    super.dispose();
  }

  Widget _buildGallonCard({
    required String label,
    required int gallons,
    required DateTime lastUpdate,
  }) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.background,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.offWhite),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Text(
            label,
            style: const TextStyle(
              fontSize: 14,
              color: AppColors.text,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            '$gallons',
            style: const TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.bold,
              color: AppColors.secondary,
            ),
          ),
          const Text(
            'Gallons',
            style: TextStyle(
              fontSize: 12,
              color: AppColors.text,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Last Update: ${DateTime.now().difference(lastUpdate).inSeconds} sec ago',
            style: const TextStyle(
              fontSize: 12,
              color: AppColors.coolGrey,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTrailerSection({
    required String trailerName,
    required double voltage,
    required int percentage,
    required int frontGallons,
    required int backGallons,
    required DateTime frontLastUpdate,
    required DateTime backLastUpdate,
  }) {
    return Container(
      margin: const EdgeInsets.only(bottom: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Trailer Header
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                trailerName,
                style: const TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                  color: AppColors.secondary,
                ),
              ),
              Row(
                children: [
                  const Icon(
                    LucideIcons.battery,
                    size: 20,
                    color: AppColors.primary,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    '${voltage.toStringAsFixed(1)}V  $percentage%',
                    style: const TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w500,
                      color: AppColors.text,
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 16),
          // Front and Back Cards
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: _buildGallonCard(
                  label: 'Front',
                  gallons: frontGallons,
                  lastUpdate: frontLastUpdate,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _buildGallonCard(
                  label: 'Back',
                  gallons: backGallons,
                  lastUpdate: backLastUpdate,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final availableTrailers = widget.trailerController.getAvailableTrailers();

    return Scaffold(
      appBar: const CustomAppBar(title: 'Mopeka BMS', isSubScreen: true),
      body: SafeArea(
        child: SingleChildScrollView(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (widget.trailerController.isLoading)
                  const Center(
                    child: CircularProgressIndicator(color: AppColors.primary),
                  )
                else
                  CustomMultiSelectDropdown<String>(
                    label: 'Select Trailers',
                    selectedValues:
                        widget.trailerController.getSelectedTrailers(),
                    items: availableTrailers
                        .map((trailer) => DropdownMenuItem(
                              value: trailer,
                              child: Text(trailer),
                            ))
                        .toList(),
                    onChanged: (values) {
                      widget.trailerController.setSelectedTrailers(values);
                    },
                    validator: (values) => values.isEmpty
                        ? 'Please select at least one trailer'
                        : null,
                    maxSelections: 3,
                  ),
                const SizedBox(height: 24),
                if (widget.trailerController.getSelectedTrailers().isEmpty)
                  const Center(
                    child: Text(
                      'No trailers selected',
                      style: TextStyle(
                        fontSize: 14,
                        color: AppColors.coolGrey,
                      ),
                    ),
                  )
                else
                  ...widget.trailerController
                      .getSelectedTrailers()
                      .map((trailerName) {
                    final data =
                        widget.trailerController.getTrailerData(trailerName);
                    return _buildTrailerSection(
                      trailerName: trailerName,
                      voltage: (data['voltage'] as double?) ?? 0.0,
                      percentage: (data['percentage'] as int?) ?? 0,
                      frontGallons: (data['frontGallons'] as int?) ?? 0,
                      backGallons: (data['backGallons'] as int?) ?? 0,
                      frontLastUpdate: (data['frontLastUpdate'] as DateTime?) ??
                          DateTime.now(),
                      backLastUpdate: (data['backLastUpdate'] as DateTime?) ??
                          DateTime.now(),
                    );
                  }),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
