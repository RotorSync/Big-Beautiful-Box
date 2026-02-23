import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import 'package:rotorsync/screens/serial_numbers_screen.dart';
import 'package:rotorsync/widgets/button.dart';
import '../services/mqtt_service.dart';
import '../widgets/app_bar.dart';
import '../constants/colors.dart';
import '../controllers/auth_controller.dart';
import '../widgets/settings_option.dart';
import '../widgets/user_card.dart';
import 'profile_screen.dart';
import 'mqtt/mqtt_test_screen.dart';
import '../screens/login_screen.dart';
import 'monitoring_screen.dart';

class SettingsScreen extends StatefulWidget {
  final MQTTService mqttService;
  final String? fullName;
  final String? email;
  final String? initials;
  final String? role;
  final void Function(String, String, String)? onProfileUpdated;

  const SettingsScreen({
    super.key,
    required this.mqttService,
    this.fullName,
    this.email,
    this.initials,
    this.role,
    this.onProfileUpdated,
  });

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final AuthController _authController = AuthController();
  String? _fullName;
  String? _email;
  String? _initials;

  @override
  void initState() {
    super.initState();
    _fullName = widget.fullName;
    _email = widget.email;
    _initials = widget.initials;
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar: const CustomAppBar(title: "Settings"),
        body: SafeArea(
          child: Column(
            children: [
              Expanded(
                child: SingleChildScrollView(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        UserCard(
                          initials: _initials,
                          fullName: _fullName,
                          email: _email,
                        ),
                        const SizedBox(height: 24),
                        const Text(
                          'General',
                          style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.bold,
                            color: AppColors.secondary,
                          ),
                        ),
                        const SizedBox(height: 8),
                        SettingsOption(
                          icon: LucideIcons.user,
                          text: 'Profile',
                          onTap: () async {
                            final result = await Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (context) => ProfileScreen(
                                  fullName: _fullName,
                                  email: _email,
                                  initials: _initials,
                                ),
                              ),
                            );
                            if (result != null && mounted) {
                              setState(() {
                                _fullName = result['fullName'];
                                _email = result['email'];
                                _initials = result['initials'];
                              });
                              widget.onProfileUpdated?.call(
                                _fullName!,
                                _email!,
                                _initials!,
                              );
                            }
                          },
                        ),
                        SettingsOption(
                          icon: LucideIcons.messageCircle,
                          text: 'Connection Test',
                          onTap: () {
                            Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (context) => MQTTTestScreen(
                                    mqttService: widget.mqttService),
                              ),
                            );
                          },
                        ),
                        SettingsOption(
                          icon: LucideIcons.hash,
                          text: 'Serial Numbers',
                          onTap: () {
                            Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (context) => SerialNumbersScreen(
                                  role: widget.role,
                                ),
                              ),
                            );
                          },
                        ),
                        SettingsOption(
                          icon: LucideIcons.activity,
                          text: 'Monitoring',
                          onTap: () {
                            Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (context) => MonitoringScreen(
                                  mqttService: widget.mqttService,
                                ),
                              ),
                            );
                          },
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              Padding(
                padding: const EdgeInsets.all(16),
                child: CustomButton(
                  text: 'Logout',
                  icon: LucideIcons.logOut,
                  variant: ButtonVariant.destructive,
                  onPressed: () async {
                    try {
                      await _authController.signOut();
                      if (!context.mounted) return;
                      Navigator.pushAndRemoveUntil(
                        context,
                        MaterialPageRoute(
                            builder: (context) => const LoginScreen()),
                        (Route<dynamic> route) => false,
                      );
                    } catch (e) {
                      if (!context.mounted) return;
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                          content: Text('Failed to sign out: $e'),
                          backgroundColor: AppColors.red,
                        ),
                      );
                    }
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
