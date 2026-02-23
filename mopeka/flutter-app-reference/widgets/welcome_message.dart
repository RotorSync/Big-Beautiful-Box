import 'package:flutter/material.dart';
import '../constants/colors.dart';

class WelcomeMessage extends StatelessWidget {
  final String? fullName;
  final String? initials;

  const WelcomeMessage({
    super.key,
    required this.fullName,
    required this.initials,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Welcome back,',
              style: TextStyle(
                fontSize: 14,
                color: AppColors.text,
              ),
            ),
            Text(
              fullName != null ? '$fullName!' : '',
              style: const TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.bold,
                color: AppColors.secondary,
              ),
            ),
          ],
        ),
        Container(
          width: 48,
          height: 48,
          decoration: BoxDecoration(
            color: AppColors.primary,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Center(
            child: Text(
              initials ?? '',
              style: const TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.bold,
                color: AppColors.white,
              ),
            ),
          ),
        ),
      ],
    );
  }
}
