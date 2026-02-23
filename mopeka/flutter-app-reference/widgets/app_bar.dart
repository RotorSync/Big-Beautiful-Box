import 'package:flutter/material.dart';
import '../constants/colors.dart';

class CustomAppBar extends StatelessWidget implements PreferredSizeWidget {
  final String title;
  final bool isSubScreen;

  const CustomAppBar({
    super.key,
    required this.title,
    this.isSubScreen = false,
  });

  @override
  Widget build(BuildContext context) {
    return AppBar(
      backgroundColor: AppColors.primary,
      foregroundColor: AppColors.white,
      automaticallyImplyLeading: true,
      title: Text(title,
          style: TextStyle(
              fontSize: isSubScreen ? 17 : 20, fontWeight: FontWeight.w500)),
    );
  }

  @override
  Size get preferredSize => const Size.fromHeight(kToolbarHeight);
}
