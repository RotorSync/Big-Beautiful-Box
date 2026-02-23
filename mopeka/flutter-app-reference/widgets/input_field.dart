import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../constants/colors.dart';
import 'label.dart';

class CustomInputField extends StatefulWidget {
  final String label;
  final String hintText;
  final TextEditingController controller;
  final FocusNode? focusNode;
  final bool obscureText;
  final String? Function(String?)? validator;
  final TextInputType keyboardType;

  const CustomInputField({
    super.key,
    required this.label,
    required this.hintText,
    required this.controller,
    this.focusNode,
    this.obscureText = false,
    this.validator,
    this.keyboardType = TextInputType.text,
  });

  @override
  State<CustomInputField> createState() => _CustomInputFieldState();
}

class _CustomInputFieldState extends State<CustomInputField> {
  late bool _isObscured;

  @override
  void initState() {
    super.initState();
    _isObscured = widget.obscureText;
  }

  void _toggleVisibility() {
    setState(() {
      _isObscured = !_isObscured;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        CustomLabel(text: widget.label),
        const SizedBox(height: 8),
        TextFormField(
          controller: widget.controller,
          focusNode: widget.focusNode,
          obscureText: _isObscured,
          keyboardType: widget.keyboardType,
          style: const TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w500,
            color: AppColors.secondary,
          ),
          decoration: InputDecoration(
            hintText: widget.hintText,
            hintStyle: const TextStyle(
              fontSize: 14,
              color: AppColors.coolGrey,
            ),
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
            suffixIcon: widget.obscureText
                ? IconButton(
                    icon: Icon(
                        _isObscured ? LucideIcons.eye : LucideIcons.eyeOff,
                        color: AppColors.coolGrey,
                        size: 20),
                    onPressed: _toggleVisibility,
                  )
                : null,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: AppColors.offWhite),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: AppColors.offWhite),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(
                color: AppColors.primary,
                width: 2,
              ),
            ),
            errorStyle: const TextStyle(
              color: AppColors.red,
              fontWeight: FontWeight.w500,
            ),
            errorBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: AppColors.red),
            ),
            focusedErrorBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(
                color: AppColors.red,
                width: 2,
              ),
            ),
          ),
          validator: widget.validator,
          autovalidateMode: AutovalidateMode.onUserInteraction,
        ),
      ],
    );
  }
}
