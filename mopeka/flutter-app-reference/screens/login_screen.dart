import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../utils/validators.dart';
import '../constants/colors.dart';
import '../widgets/button.dart';
import '../widgets/input_field.dart';
import '../controllers/auth_controller.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  bool _isLoading = false;
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  final AuthController _authController = AuthController();
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _submitForm() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() => _isLoading = true);

    try {
      await _authController.signIn(
        email: _emailController.text.trim(),
        password: _passwordController.text.trim(),
      );
      if (mounted) {
        Navigator.pushReplacementNamed(context, '/home');
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(error.toString()),
            backgroundColor: AppColors.red,
          ),
        );
      }
    } finally {
      if (mounted) {
        setState(() => _isLoading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Form(
              key: _formKey,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Column(
                    children: [
                      Text(
                        'Welcome back!',
                        style: TextStyle(
                          fontSize: 28,
                          fontWeight: FontWeight.bold,
                          color: AppColors.secondary,
                        ),
                      ),
                      SizedBox(height: 8),
                      Text(
                        'Please sign in to your account',
                        style: TextStyle(fontSize: 14, color: AppColors.text),
                      ),
                    ],
                  ),
                  const SizedBox(height: 32),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      CustomInputField(
                        label: 'Email',
                        hintText: 'e.g., john.doe@example.com',
                        controller: _emailController,
                        validator: Validators.validateEmail,
                      ),
                      const SizedBox(height: 16),
                      CustomInputField(
                        label: 'Password',
                        hintText: '********',
                        controller: _passwordController,
                        obscureText: true,
                        validator: Validators.validatePassword,
                      ),
                      const SizedBox(height: 24),
                      CustomButton(
                        text: 'Login',
                        icon: LucideIcons.logIn,
                        onPressed: _isLoading ? null : _submitForm,
                        isLoading: _isLoading,
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
