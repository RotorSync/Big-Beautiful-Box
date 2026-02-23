import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../widgets/app_bar.dart';
import '../constants/colors.dart';
import '../controllers/serial_numbers_controller.dart';
import './serial_number_form_screen.dart';
import '../widgets/dropdown.dart';
import '../widgets/button.dart';

class SerialNumbersScreen extends StatefulWidget {
  final String? role;

  const SerialNumbersScreen({super.key, this.role});

  @override
  State<SerialNumbersScreen> createState() => _SerialNumbersScreenState();
}

class _SerialNumbersScreenState extends State<SerialNumbersScreen> {
  final SerialNumbersController _controller = SerialNumbersController();
  String? _selectedType;
  String? _selectedSerialId;
  List<SerialNumber> _filteredSerials = [];
  Map<String, dynamic>? _initialSelection;

  @override
  void initState() {
    super.initState();
    _initializeScreen();
  }

  Future<void> _initializeScreen() async {
    // Fetch user's selected serial number
    _initialSelection = await _controller.fetchUserSerialNumber();
    // Fetch all serial numbers
    await _controller.fetchSerialNumbers(() {
      if (mounted) {
        setState(() {
          // Pre-fill dropdowns if user has a selection
          if (_initialSelection != null) {
            _selectedType = _initialSelection!['type'];
            _selectedSerialId = _initialSelection!['id'];
            _filteredSerials = _controller.serialNumbers
                .where((serial) => serial.type == _selectedType)
                .toList();
          }
        });
      }
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _onTypeChanged(String? newType) {
    if (newType != null) {
      setState(() {
        _selectedType = newType;
        _selectedSerialId = null;
        _filteredSerials = _controller.serialNumbers
            .where((serial) => serial.type == newType)
            .toList();
      });
    }
  }

  void _onSerialChanged(String? newSerialId) {
    if (newSerialId != null) {
      setState(() {
        _selectedSerialId = newSerialId;
      });
    }
  }

  Future<void> _saveSelection() async {
    if (_selectedType == null || _selectedSerialId == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please select both asset type and serial number.'),
          backgroundColor: AppColors.red,
        ),
      );
      return;
    }

    final selectedSerial = _controller.serialNumbers.firstWhere((serial) =>
        serial.id == _selectedSerialId && serial.type == _selectedType);

    await _controller.saveUserSerialNumber(
      selectedSerial,
      () {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('Serial number saved successfully.'),
              backgroundColor: AppColors.primary,
            ),
          );
          Navigator.pop(context);
        }
      },
      context,
    );
  }

  @override
  Widget build(BuildContext context) {
    final isAdmin = widget.role == 'admin';

    return GestureDetector(
      onTap: () => FocusScope.of(context).unfocus(),
      child: Scaffold(
        appBar: const CustomAppBar(
          title: "Serial Numbers",
          isSubScreen: true,
        ),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: _controller.isLoading
                      ? const Center(
                          child: CircularProgressIndicator(
                            color: AppColors.primary,
                          ),
                        )
                      : isAdmin
                          ? _buildAdminView()
                          : _buildUserView(),
                ),
              ],
            ),
          ),
        ),
        floatingActionButton: isAdmin
            ? FloatingActionButton(
                backgroundColor: AppColors.primary,
                child: const Icon(
                  LucideIcons.plus,
                  color: AppColors.white,
                ),
                onPressed: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (context) => SerialNumberFormScreen(
                        controller: _controller,
                        role: widget.role,
                      ),
                    ),
                  ).then((_) => setState(() {}));
                },
              )
            : null,
      ),
    );
  }

  Widget _buildAdminView() {
    return _controller.serialNumbers.isEmpty
        ? const Center(
            child: Text(
              'No serial numbers added yet.',
              style: TextStyle(color: AppColors.text),
            ),
          )
        : ListView.builder(
            itemCount: _controller.serialNumbers.length,
            itemBuilder: (context, index) {
              final serialNumber = _controller.serialNumbers[index];
              return Container(
                padding: const EdgeInsets.all(16),
                margin: const EdgeInsets.only(bottom: 8),
                decoration: BoxDecoration(
                  color: AppColors.background,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            serialNumber.id,
                            style: const TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.bold,
                              color: AppColors.secondary,
                            ),
                          ),
                          Text(
                            serialNumber.type,
                            style: const TextStyle(
                              fontSize: 14,
                              color: AppColors.text,
                            ),
                          ),
                          Text(
                            serialNumber.name,
                            style: const TextStyle(
                              fontSize: 14,
                              color: AppColors.grey,
                            ),
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      icon: const Icon(
                        LucideIcons.pencil,
                        color: AppColors.grey,
                        size: 20,
                      ),
                      onPressed: () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (context) => SerialNumberFormScreen(
                              controller: _controller,
                              serialNumber: serialNumber,
                              role: widget.role,
                            ),
                          ),
                        ).then((_) => setState(() {}));
                      },
                    ),
                    IconButton(
                      icon: const Icon(
                        LucideIcons.trash2,
                        color: AppColors.red,
                        size: 20,
                      ),
                      onPressed: () async {
                        await _controller.deleteSerialNumber(serialNumber, () {
                          if (mounted) setState(() {});
                        }, context);
                      },
                    ),
                  ],
                ),
              );
            },
          );
  }

  Widget _buildUserView() {
    return Column(
      children: [
        CustomDropdown<String>(
          label: 'Asset Type',
          value: _selectedType,
          items: const [
            DropdownMenuItem(value: 'helicopter', child: Text('Helicopter')),
            DropdownMenuItem(value: 'trailer', child: Text('Trailer')),
          ],
          onChanged: _onTypeChanged,
        ),
        const SizedBox(height: 16),
        CustomDropdown<String>(
          label: 'Serial Number',
          value: _selectedSerialId,
          items: _filteredSerials
              .map((serial) => DropdownMenuItem(
                    value: serial.id,
                    child: Text('${serial.id} (${serial.name})'),
                  ))
              .toList(),
          onChanged: _selectedType != null ? _onSerialChanged : null,
        ),
        const SizedBox(height: 24),
        CustomButton(
          text: 'Save',
          icon: LucideIcons.save,
          onPressed: _saveSelection,
        ),
      ],
    );
  }
}
