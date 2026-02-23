import 'package:flutter/material.dart';
import '../constants/colors.dart';
import 'label.dart';

class CustomMultiSelectDropdown<T> extends StatelessWidget {
  final String label;
  final List<T> selectedValues;
  final List<DropdownMenuItem<T>> items;
  final ValueChanged<List<T>>? onChanged;
  final String? Function(List<T>)? validator;
  final int maxSelections;

  const CustomMultiSelectDropdown({
    super.key,
    required this.label,
    required this.selectedValues,
    required this.items,
    this.onChanged,
    this.validator,
    this.maxSelections = 3,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        CustomLabel(text: label),
        const SizedBox(height: 8),
        InkWell(
          onTap: () async {
            final result = await showDialog<List<T>>(
              context: context,
              builder: (context) => _MultiSelectDialog<T>(
                items: items,
                selectedValues: selectedValues,
                maxSelections: maxSelections,
              ),
            );
            if (result != null && onChanged != null) {
              onChanged!(result);
            }
          },
          child: InputDecorator(
            decoration: InputDecoration(
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 16,
                vertical: 14,
              ),
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
              errorText: validator != null ? validator!(selectedValues) : null,
            ),
            child: Text(
              selectedValues.isEmpty
                  ? 'Select up to $maxSelections options'
                  : selectedValues
                      .map((e) => items
                          .firstWhere((item) => item.value == e,
                              orElse: () => DropdownMenuItem<T>(
                                    value: e,
                                    child: Text(e.toString()),
                                  ))
                          .child
                          .toString()
                          .replaceAll('Text("', '')
                          .replaceAll('")', ''))
                      .join(', '),
              style: const TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w500,
                color: AppColors.secondary,
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _MultiSelectDialog<T> extends StatefulWidget {
  final List<DropdownMenuItem<T>> items;
  final List<T> selectedValues;
  final int maxSelections;

  const _MultiSelectDialog({
    required this.items,
    required this.selectedValues,
    required this.maxSelections,
  });

  @override
  _MultiSelectDialogState<T> createState() => _MultiSelectDialogState<T>();
}

class _MultiSelectDialogState<T> extends State<_MultiSelectDialog<T>> {
  late List<T> _tempSelectedValues;

  @override
  void initState() {
    super.initState();
    _tempSelectedValues = List.from(widget.selectedValues);
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Select Options'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: widget.items.map((item) {
            return CheckboxListTile(
              title: item.child,
              value: _tempSelectedValues.contains(item.value),
              onChanged: (bool? selected) {
                setState(() {
                  if (selected == true &&
                      _tempSelectedValues.length < widget.maxSelections) {
                    _tempSelectedValues.add(item.value!);
                  } else if (selected == false) {
                    _tempSelectedValues.remove(item.value);
                  }
                });
              },
              enabled: _tempSelectedValues.length < widget.maxSelections ||
                  _tempSelectedValues.contains(item.value),
            );
          }).toList(),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: () => Navigator.pop(context, _tempSelectedValues),
          child: const Text('OK'),
        ),
      ],
    );
  }
}
