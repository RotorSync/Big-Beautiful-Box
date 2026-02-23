import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../constants/colors.dart';
import '../../controllers/mqtt_controller.dart';
import '../../utils/validators.dart';
import '../../widgets/app_bar.dart';
import '../../widgets/button.dart';
import '../../widgets/dropdown.dart';
import '../../widgets/input_field.dart';

class MQTTConfigScreen extends StatefulWidget {
  const MQTTConfigScreen({super.key});

  @override
  State<MQTTConfigScreen> createState() => _MQTTConfigScreenState();
}

class _MQTTConfigScreenState extends State<MQTTConfigScreen> {
  late MQTTConfigController _controller;

  @override
  void initState() {
    super.initState();
    _controller = MQTTConfigController();
    _controller.onStateChanged = () {
      if (mounted) {
        setState(() {});
      }
    };
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar:
            const CustomAppBar(title: 'MQTT Configuration', isSubScreen: true),
        body: SafeArea(
          child: SingleChildScrollView(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Form(
                key: _controller.formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (_controller.errorMessage != null) ...[
                      Text(
                        _controller.errorMessage!,
                        style: const TextStyle(color: AppColors.red),
                      ),
                      const SizedBox(height: 16),
                    ],
                    CustomDropdown<String>(
                      label: 'Protocol',
                      value: _controller.selectedProtocol,
                      items: const [
                        DropdownMenuItem(
                          value: 'websocket',
                          child: Text('WebSocket'),
                        ),
                        DropdownMenuItem(
                          value: 'tls',
                          child: Text('TLS'),
                        ),
                      ],
                      onChanged: _controller.onProtocolChanged,
                      validator: Validators.validateProtocol,
                    ),
                    const SizedBox(height: 16),
                    CustomInputField(
                      label: 'Host',
                      hintText: 'e.g., broker.hivemq.com',
                      controller: _controller.hostController,
                      validator: Validators.validateHost,
                    ),
                    const SizedBox(height: 16),
                    CustomInputField(
                      label: 'Port',
                      hintText: 'e.g., 1883',
                      controller: _controller.portController,
                      keyboardType: TextInputType.number,
                      validator: Validators.validatePort,
                    ),
                    const SizedBox(height: 16),
                    if (_controller.selectedProtocol == 'websocket') ...[
                      CustomInputField(
                        label: 'Base Path',
                        hintText: 'e.g., /mqtt',
                        controller: _controller.basePathController,
                        validator: (value) => Validators.validateBasePath(
                          value,
                          isWebSocket:
                              _controller.selectedProtocol == 'websocket',
                        ),
                      ),
                      const SizedBox(height: 16),
                    ],
                    CustomInputField(
                      label: 'Username',
                      hintText: 'e.g., john',
                      controller: _controller.usernameController,
                      validator: Validators.validateUsername,
                    ),
                    const SizedBox(height: 16),
                    CustomInputField(
                      label: 'Password',
                      hintText: '********',
                      controller: _controller.passwordController,
                      obscureText: true,
                      validator: Validators.validatePassword,
                    ),
                    const SizedBox(height: 24),
                    CustomButton(
                      text: 'Save',
                      icon: LucideIcons.save,
                      onPressed: _controller.isLoading
                          ? null
                          : () => _controller.saveSettings(context),
                      isLoading: _controller.isLoading,
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
