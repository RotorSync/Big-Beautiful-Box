import 'package:flutter/material.dart';
import '../constants/colors.dart';

class CustomLabel extends StatelessWidget {
  final String text;

  const CustomLabel({
    super.key,
    required this.text,
  });

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: const TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w500,
        color: AppColors.slateGrey,
      ),
    );
  }
}
