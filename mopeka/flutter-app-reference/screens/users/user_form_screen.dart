import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import 'package:rotorsync/utils/validators.dart';
import '../../constants/colors.dart';
import '../../controllers/user_controller.dart';
import '../../widgets/app_bar.dart';
import '../../widgets/input_field.dart';
import '../../widgets/dropdown.dart';
import '../../widgets/button.dart';

class UserFormScreen extends StatefulWidget {
  final Map<String, dynamic>? user;

  const UserFormScreen({super.key, this.user});

  @override
  State<UserFormScreen> createState() => _UserFormScreenState();
}

class _UserFormScreenState extends State<UserFormScreen> {
  final TextEditingController _fullNameController = TextEditingController();
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  final _formKey = GlobalKey<FormState>();
  String _selectedRole = 'admin';
  late UserController _userController;
  bool _isPasswordRequired = true;
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    _userController = UserController();
    _isPasswordRequired = widget.user == null;

    if (widget.user != null) {
      _fullNameController.text = widget.user!['fullName'] ?? '';
      _emailController.text = widget.user!['email'] ?? '';
      _selectedRole =
          (widget.user!['role'] ?? 'admin').toString().toLowerCase();
    }
  }

  @override
  void dispose() {
    _fullNameController.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _saveUser() async {
    if (_formKey.currentState!.validate()) {
      setState(() {
        _isLoading = true;
      });

      final fullName = _fullNameController.text.trim();
      final email = _emailController.text.trim();
      final password = _passwordController.text.trim();

      final Map<String, dynamic> userData = {
        'fullName': fullName,
        'email': email,
        'role': _selectedRole,
        if (password.isNotEmpty) 'password': password,
      };

      try {
        Map<String, dynamic> result;
        if (widget.user == null) {
          result = await _userController.createUser(userData);
        } else {
          result =
              await _userController.updateUser(widget.user!['id'], userData);
        }

        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text(result['message']),
              backgroundColor: AppColors.green,
            ),
          );
          Navigator.pop(context);
        }
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text('Failed to save user: $e'),
              backgroundColor: AppColors.red,
            ),
          );
          setState(() {
            _isLoading = false;
          });
        }
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar: CustomAppBar(
          title: widget.user == null ? 'Add User' : 'Edit User',
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
                      keyboardType: TextInputType.emailAddress,
                      validator: Validators.validateEmail,
                    ),
                    const SizedBox(height: 16),
                    CustomInputField(
                      label: 'Password',
                      hintText: '********',
                      controller: _passwordController,
                      obscureText: true,
                      validator: (value) {
                        if (_isPasswordRequired) {
                          return Validators.validatePassword(value);
                        }
                        return null;
                      },
                    ),
                    const SizedBox(height: 16),
                    CustomDropdown<String>(
                      label: 'Role',
                      value: _selectedRole,
                      items: const [
                        DropdownMenuItem(value: 'admin', child: Text('Admin')),
                        DropdownMenuItem(value: 'pilot', child: Text('Pilot')),
                        DropdownMenuItem(value: 'crew', child: Text('Crew')),
                      ],
                      onChanged: (String? newValue) {
                        if (newValue != null) {
                          setState(() {
                            _selectedRole = newValue;
                          });
                        }
                      },
                    ),
                    const SizedBox(height: 24),
                    CustomButton(
                      text: widget.user == null ? 'Add' : 'Update',
                      icon: widget.user == null
                          ? LucideIcons.plus
                          : LucideIcons.refreshCw,
                      onPressed: _isLoading ? null : _saveUser,
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
