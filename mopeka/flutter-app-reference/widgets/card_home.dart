import 'package:flutter/material.dart';
import '../constants/colors.dart';

class HomeCard extends StatelessWidget {
  final String title;
  final IconData icon;
  final bool switchState;
  final String status;
  final ValueChanged<bool>? onSwitchChanged;
  final VoidCallback? onTap;

  const HomeCard({
    super.key,
    required this.title,
    required this.icon,
    required this.switchState,
    required this.status,
    this.onSwitchChanged,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(16),
        margin: const EdgeInsets.only(bottom: 16),
        decoration: BoxDecoration(
          color: AppColors.white,
          borderRadius: BorderRadius.circular(12),
          boxShadow: [
            BoxShadow(
              color: AppColors.grey.withOpacity(0.2),
              blurRadius: 8,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(12),
              decoration: const BoxDecoration(
                color: AppColors.accent,
                shape: BoxShape.circle,
              ),
              child: Icon(
                icon,
                color: AppColors.primary,
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w500,
                      color: AppColors.secondary,
                    ),
                  ),
                  Text(
                    status,
                    style: const TextStyle(
                      fontSize: 14,
                      color: AppColors.text,
                    ),
                  ),
                ],
              ),
            ),
            // Switch
            Switch(
              value: switchState,
              onChanged: onSwitchChanged,
              thumbColor: WidgetStateProperty.resolveWith<Color>(
                (Set<WidgetState> states) {
                  if (states.contains(WidgetState.selected)) {
                    return AppColors.white;
                  }
                  return AppColors.grey;
                },
              ),
              trackColor: WidgetStateProperty.resolveWith<Color>(
                (Set<WidgetState> states) {
                  if (states.contains(WidgetState.selected)) {
                    return AppColors.green;
                  }
                  return const Color(0xFFF0F0F0);
                },
              ),
              trackOutlineColor: WidgetStateProperty.resolveWith<Color>(
                (Set<WidgetState> states) {
                  return Colors.transparent;
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}
