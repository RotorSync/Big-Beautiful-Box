import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../widgets/app_bar.dart';
import '../widgets/dropdown.dart';
import '../widgets/input_field.dart';
import '../widgets/button.dart';
import '../controllers/serial_numbers_controller.dart';
import '../utils/validators.dart';

class SerialNumberFormScreen extends StatefulWidget {
  final SerialNumbersController controller;
  final SerialNumber? serialNumber;
  final String? role;

  const SerialNumberFormScreen({
    super.key,
    required this.controller,
    this.serialNumber,
    this.role,
  });

  @override
  State<SerialNumberFormScreen> createState() => _SerialNumberFormScreenState();
}

class _SerialNumberFormScreenState extends State<SerialNumberFormScreen> {
  final _formKey = GlobalKey<FormState>();
  bool _isLoading = false;
  final TextEditingController _nameController = TextEditingController();

  @override
  void initState() {
    super.initState();
    if (widget.serialNumber != null) {
      widget.controller.startEditing(widget.serialNumber!, () {});
      _nameController.text = widget.serialNumber!.name;
    }
  }

  @override
  void dispose() {
    _nameController.dispose();
    super.dispose();
  }

  Future<void> _handleSave() async {
    if (_formKey.currentState!.validate()) {
      if (!mounted) return;
      setState(() => _isLoading = true);
      try {
        widget.controller.setName(_nameController.text.trim());
        if (widget.controller.isEditing) {
          await widget.controller.updateSerialNumber(() {
            if (mounted) Navigator.pop(context);
          }, context);
        } else {
          await widget.controller.addSerialNumber(() {
            if (mounted) Navigator.pop(context);
          }, context);
        }
      } finally {
        if (mounted) setState(() => _isLoading = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar: const CustomAppBar(
          title: "Add Serial Number",
          isSubScreen: true,
        ),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Form(
              key: _formKey,
              child: Column(
                children: [
                  CustomInputField(
                    label: 'Serial Number',
                    hintText: 'e.g., K978192',
                    controller: widget.controller.serialController,
                    validator: Validators.validateSerialNumber,
                  ),
                  const SizedBox(height: 16),
                  CustomInputField(
                    label: 'Friendly Name',
                    hintText: 'e.g., Sky Hawk',
                    controller: _nameController,
                    validator: (value) {
                      if (value == null || value.trim().isEmpty) {
                        return 'Please enter a friendly name.';
                      }
                      return null;
                    },
                  ),
                  const SizedBox(height: 16),
                  CustomDropdown<String>(
                    label: 'Asset Type',
                    value: widget.controller.selectedType,
                    items: const [
                      DropdownMenuItem(
                          value: 'helicopter', child: Text('Helicopter')),
                      DropdownMenuItem(
                          value: 'trailer', child: Text('Trailer')),
                    ],
                    onChanged: (String? newValue) {
                      if (newValue != null) {
                        setState(() {
                          widget.controller.selectedType = newValue;
                        });
                      }
                    },
                  ),
                  const SizedBox(height: 24),
                  CustomButton(
                    text: widget.controller.isEditing ? 'Update' : 'Add',
                    icon: widget.controller.isEditing
                        ? LucideIcons.refreshCw
                        : LucideIcons.plus,
                    onPressed: _isLoading ? null : _handleSave,
                    isLoading: _isLoading,
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
