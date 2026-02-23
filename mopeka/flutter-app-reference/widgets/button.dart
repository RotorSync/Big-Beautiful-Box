import 'package:flutter/material.dart';
import '../constants/colors.dart';

enum ButtonVariant { filled, destructive, outline }

class CustomButton extends StatelessWidget {
  final String text;
  final IconData icon;
  final VoidCallback? onPressed;
  final ButtonVariant variant;
  final bool isLoading;

  const CustomButton({
    super.key,
    required this.text,
    required this.icon,
    this.onPressed,
    this.variant = ButtonVariant.filled,
    this.isLoading = false,
  });

  ButtonStyle _getButtonStyle(BuildContext context) {
    switch (variant) {
      case ButtonVariant.filled:
        return ElevatedButton.styleFrom(
          backgroundColor: AppColors.primary,
          foregroundColor: AppColors.white,
          disabledBackgroundColor: AppColors.coolGrey,
          elevation: 1, // Soft elevation
          shadowColor: AppColors.primary.withOpacity(0.1),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          minimumSize: const Size.fromHeight(56),

          padding: const EdgeInsets.symmetric(horizontal: 16),
        );

      case ButtonVariant.destructive:
        return ElevatedButton.styleFrom(
          backgroundColor: AppColors.red,
          foregroundColor: AppColors.white,
          disabledBackgroundColor: AppColors.coolGrey,
          elevation: 1,
          shadowColor: AppColors.red.withOpacity(0.1),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          minimumSize: const Size.fromHeight(56),
          padding: const EdgeInsets.symmetric(horizontal: 16),
        );

      case ButtonVariant.outline:
        return ElevatedButton.styleFrom(
          backgroundColor: Colors.transparent,
          foregroundColor: AppColors.primary,
          disabledForegroundColor: AppColors.coolGrey,
          elevation: 0,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
            side: const BorderSide(
              color: AppColors.primary,
              width: 2,
            ),
          ),
          minimumSize: const Size.fromHeight(56),
          padding: const EdgeInsets.symmetric(horizontal: 16),
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    return ElevatedButton(
      onPressed: isLoading ? null : onPressed,
      style: _getButtonStyle(context),
      child: isLoading
          ? const SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(
                color: Colors.white,
                strokeWidth: 2,
              ),
            )
          : Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(icon, size: 20),
                const SizedBox(width: 8),
                Text(
                  text,
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                    color: variant == ButtonVariant.outline
                        ? AppColors.primary
                        : AppColors.white,
                  ),
                ),
              ],
            ),
    );
  }
}
