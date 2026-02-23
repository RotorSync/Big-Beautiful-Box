import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import 'package:rotorsync/widgets/app_bar.dart';
import '../constants/colors.dart';
import '../controllers/profile_controller.dart';
import '../utils/validators.dart';
import '../widgets/button.dart';
import '../widgets/input_field.dart';

class ProfileScreen extends StatefulWidget {
  final String? fullName;
  final String? email;
  final String? initials;

  const ProfileScreen({super.key, this.fullName, this.email, this.initials});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  final ProfileController _profileController = ProfileController();
  final _formKey = GlobalKey<FormState>();
  bool _isLoading = false;

  final TextEditingController _fullNameController = TextEditingController();
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _fullNameController.text = widget.fullName ?? '';
    _emailController.text = widget.email ?? '';
  }

  Future<void> _handleSave() async {
    if (_formKey.currentState!.validate()) {
      setState(() => _isLoading = true);
      try {
        final result = await _profileController.updateProfile(
          fullName: _fullNameController.text,
          email: _emailController.text,
          password: _passwordController.text,
          currentEmail: widget.email ?? '',
        );
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text(result['message']),
              backgroundColor:
                  result['success'] ? AppColors.green : AppColors.red,
            ),
          );
          if (result['success']) {
            Navigator.pop(context, {
              'fullName': _fullNameController.text,
              'email': _emailController.text,
              'initials': widget.initials,
            });
          }
        }
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text('Failed to update profile: $e'),
              backgroundColor: AppColors.red,
            ),
          );
        }
      } finally {
        if (mounted) setState(() => _isLoading = false);
      }
    }
  }

  @override
  void dispose() {
    _fullNameController.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar: const CustomAppBar(
          title: "Profile",
          isSubScreen: true,
        ),
        body: SafeArea(
          child: SingleChildScrollView(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    CustomInputField(
                      label: 'Full Name',
                      hintText: 'e.g., John Doe',
                      controller: _fullNameController,
                      validator: Validators.validateFullName,
                    ),
                    const SizedBox(height: 16),
                    CustomInputField(
                      label: 'Email',
                      hintText: 'e.g., john.doe@example.com',
                      controller: _emailController,
                      validator: Validators.validateEmail,
                      keyboardType: TextInputType.emailAddress,
                    ),
                    const SizedBox(height: 16),
                    CustomInputField(
                      label: 'Password',
                      hintText: '********',
                      controller: _passwordController,
                      validator: (value) => value != null && value.isNotEmpty
                          ? Validators.validatePassword(value)
                          : null,
                      obscureText: true,
                    ),
                    const SizedBox(height: 24),
                    CustomButton(
                      text: 'Update',
                      icon: LucideIcons.refreshCcw,
                      onPressed: _isLoading ? null : () => _handleSave(),
                      isLoading: _isLoading,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
