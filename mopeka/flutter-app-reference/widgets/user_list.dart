import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../constants/colors.dart';

class UserList extends StatelessWidget {
  final String userId;
  final String fullName;
  final String email;
  final VoidCallback onEdit;

  const UserList({
    super.key,
    required this.userId,
    required this.fullName,
    required this.email,
    required this.onEdit,
  });

  @override
  Widget build(BuildContext context) {
    final String initials = _getInitials(fullName);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 16.0),
      child: Row(
        children: [
          _buildAvatar(initials),
          const SizedBox(width: 16),
          _buildUserInfo(fullName, email),
          _buildEditButton(),
        ],
      ),
    );
  }

  String _getInitials(String fullName) {
    final List<String> fullNameParts = fullName.split(' ');
    if (fullNameParts.isEmpty) return '';
    if (fullNameParts.length == 1) return fullNameParts[0][0].toUpperCase();
    return "${fullNameParts[0][0]}${fullNameParts[fullNameParts.length - 1][0]}"
        .toUpperCase();
  }

  Widget _buildAvatar(String initials) {
    return CircleAvatar(
      backgroundColor: AppColors.primary,
      radius: 28,
      child: Text(
        initials,
        style: const TextStyle(
          color: AppColors.white,
          fontWeight: FontWeight.bold,
          fontSize: 18,
        ),
      ),
    );
  }

  Widget _buildUserInfo(String fullName, String email) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            fullName,
            style: const TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.bold,
              color: AppColors.secondary,
            ),
          ),
          Text(
            email,
            style: const TextStyle(
              fontSize: 14,
              color: AppColors.text,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildEditButton() {
    return IconButton(
      icon: const Icon(LucideIcons.pencil, color: AppColors.primary),
      onPressed: onEdit,
    );
  }
}
