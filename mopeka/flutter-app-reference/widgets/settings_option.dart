import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../constants/colors.dart';

class SettingsOption extends StatelessWidget {
  final IconData icon;
  final String text;
  final VoidCallback onTap;

  const SettingsOption({
    super.key,
    required this.icon,
    required this.text,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: AppColors.background,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Theme(
        data: Theme.of(context).copyWith(
          splashColor: Colors.transparent,
          highlightColor: Colors.transparent,
        ),
        child: ListTile(
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 16,
            vertical: 8,
          ),
          leading: CircleAvatar(
            backgroundColor: AppColors.accent,
            child: Icon(
              icon,
              color: AppColors.primary,
            ),
          ),
          title: Text(
            text,
            style: const TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w500,
              color: AppColors.secondary,
            ),
          ),
          trailing: const Icon(
            LucideIcons.chevronRight,
            color: AppColors.grey,
            size: 20,
          ),
          onTap: onTap,
        ),
      ),
    );
  }
}
