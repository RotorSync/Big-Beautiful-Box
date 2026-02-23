import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import '../utils/logger.dart';
import '../constants/colors.dart';

class SerialNumber {
  final String id;
  final String type;
  final String name;

  SerialNumber({required this.id, required this.type, required this.name});
}

class SerialNumbersController {
  final FirebaseFirestore _firestore = FirebaseFirestore.instance;
  final FirebaseAuth _auth = FirebaseAuth.instance;
  List<SerialNumber> serialNumbers = [];
  bool isEditing = false;
  bool isLoading = false;
  SerialNumber? editingSerialNumber;
  TextEditingController serialController = TextEditingController();
  String selectedType = 'helicopter';
  String? _name;

  Future<void> fetchSerialNumbers(VoidCallback onUpdate) async {
    try {
      AppLogger.info('Fetching serial numbers from Firestore...');
      isLoading = true;
      onUpdate();

      serialNumbers.clear();
      final trailerDoc =
          await _firestore.collection('serial_numbers').doc('trailer').get();
      if (trailerDoc.exists) {
        final trailerSerials =
            (trailerDoc.data()?['serial_numbers'] as List<dynamic>?) ?? [];
        serialNumbers.addAll(trailerSerials.map((serial) => SerialNumber(
              id: serial['id'] as String,
              type: 'trailer',
              name: serial['name'] as String,
            )));
      }

      final helicopterDoc =
          await _firestore.collection('serial_numbers').doc('helicopter').get();
      if (helicopterDoc.exists) {
        final helicopterSerials =
            (helicopterDoc.data()?['serial_numbers'] as List<dynamic>?) ?? [];
        serialNumbers.addAll(helicopterSerials.map((serial) => SerialNumber(
              id: serial['id'] as String,
              type: 'helicopter',
              name: serial['name'] as String,
            )));
      }

      AppLogger.info(
          'Serial numbers fetched successfully from Firestore. Count: ${serialNumbers.length}');
      isLoading = false;
      onUpdate();
    } catch (e) {
      AppLogger.error('Failed to fetch serial numbers from Firestore: $e', e);
      isLoading = false;
      onUpdate();
    }
  }

  Future<Map<String, dynamic>?> fetchUserSerialNumber() async {
    final user = _auth.currentUser;
    if (user == null) {
      AppLogger.warning('No user logged in.');
      return null;
    }

    try {
      AppLogger.info('Fetching selected serial number for user ${user.uid}...');
      final doc = await _firestore.collection('users').doc(user.uid).get();
      final data = doc.data();
      if (data != null && data.containsKey('serial_number')) {
        AppLogger.info(
            'Selected serial number fetched for user ${user.uid}: ${data['serial_number']}');
        return data['serial_number'] as Map<String, dynamic>;
      }
      return null;
    } catch (e) {
      AppLogger.error(
          'Failed to fetch selected serial number for user ${user.uid}: $e', e);
      return null;
    }
  }

  void setName(String name) {
    _name = name;
  }

  Future<void> addSerialNumber(
      VoidCallback onUpdate, BuildContext context) async {
    final serial = serialController.text.trim().toUpperCase();
    final name = _name?.trim() ?? '';
    if (serial.isEmpty ||
        name.isEmpty ||
        serialNumbers.any((sn) => sn.id == serial)) {
      AppLogger.warning(
          'Serial number or name is empty or serial already exists: $serial');
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
                'Serial number or name is empty or serial already exists.'),
            backgroundColor: AppColors.red,
          ),
        );
      }
      return;
    }

    try {
      AppLogger.info(
          'Adding serial number to Firestore: $serial (type: $selectedType, name: $name)...');
      final docId = selectedType.toLowerCase();
      final docRef = _firestore.collection('serial_numbers').doc(docId);
      await docRef.set({
        'serial_numbers': FieldValue.arrayUnion([
          {'id': serial, 'name': name}
        ]),
      }, SetOptions(merge: true));

      serialNumbers
          .add(SerialNumber(id: serial, type: selectedType, name: name));
      serialController.clear();
      _name = null;
      AppLogger.info('Serial number added successfully to Firestore: $serial');
      onUpdate();
    } catch (e) {
      AppLogger.error('Failed to add serial number to Firestore: $e', e);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to add serial number: $e'),
            backgroundColor: AppColors.red,
          ),
        );
      }
    }
  }

  Future<void> updateSerialNumber(
      VoidCallback onUpdate, BuildContext context) async {
    if (editingSerialNumber == null) {
      AppLogger.warning('No serial number selected for update.');
      return;
    }

    final newSerial = serialController.text.trim().toUpperCase();
    final newName = _name?.trim() ?? '';
    if (newSerial.isEmpty ||
        newName.isEmpty ||
        (newSerial != editingSerialNumber!.id &&
            serialNumbers.any((sn) => sn.id == newSerial))) {
      AppLogger.warning(
          'Serial number or name is empty or serial already exists: $newSerial');
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
                'Serial number or name is empty or serial already exists.'),
            backgroundColor: AppColors.red,
          ),
        );
      }
      return;
    }

    try {
      AppLogger.info(
          'Updating serial number in Firestore from ${editingSerialNumber!.id} to $newSerial (type: $selectedType, name: $newName)...');
      final oldDocId = editingSerialNumber!.type.toLowerCase();
      final oldDocRef = _firestore.collection('serial_numbers').doc(oldDocId);
      await oldDocRef.update({
        'serial_numbers': FieldValue.arrayRemove([
          {'id': editingSerialNumber!.id, 'name': editingSerialNumber!.name}
        ]),
      });

      final newDocId = selectedType.toLowerCase();
      final newDocRef = _firestore.collection('serial_numbers').doc(newDocId);
      await newDocRef.set({
        'serial_numbers': FieldValue.arrayUnion([
          {'id': newSerial, 'name': newName}
        ]),
      }, SetOptions(merge: true));

      final index = serialNumbers.indexOf(editingSerialNumber!);
      serialNumbers[index] =
          SerialNumber(id: newSerial, type: selectedType, name: newName);

      isEditing = false;
      editingSerialNumber = null;
      serialController.clear();
      _name = null;
      selectedType = 'helicopter';
      AppLogger.info(
          'Serial number updated successfully in Firestore: $newSerial');
      onUpdate();
    } catch (e) {
      AppLogger.error('Failed to update serial number in Firestore: $e', e);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to update serial number: $e'),
            backgroundColor: AppColors.red,
          ),
        );
      }
    }
  }

  Future<void> deleteSerialNumber(SerialNumber serialNumber,
      VoidCallback onUpdate, BuildContext context) async {
    try {
      AppLogger.info(
          'Deleting serial number from Firestore: ${serialNumber.id} (type: ${serialNumber.type})...');
      final docId = serialNumber.type.toLowerCase();
      final docRef = _firestore.collection('serial_numbers').doc(docId);
      await docRef.update({
        'serial_numbers': FieldValue.arrayRemove([
          {'id': serialNumber.id, 'name': serialNumber.name}
        ]),
      });

      serialNumbers.remove(serialNumber);
      if (editingSerialNumber == serialNumber) {
        isEditing = false;
        editingSerialNumber = null;
        serialController.clear();
        _name = null;
        selectedType = 'helicopter';
      }
      AppLogger.info(
          'Serial number deleted successfully from Firestore: ${serialNumber.id}');
      onUpdate();
    } catch (e) {
      AppLogger.error('Failed to delete serial number from Firestore: $e', e);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to delete serial number: $e'),
            backgroundColor: AppColors.red,
          ),
        );
      }
    }
  }

  Future<void> saveUserSerialNumber(SerialNumber serialNumber,
      VoidCallback onUpdate, BuildContext context) async {
    final user = _auth.currentUser;
    if (user == null) {
      AppLogger.warning('No user logged in.');
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('No user logged in.'),
            backgroundColor: AppColors.red,
          ),
        );
      }
      return;
    }

    try {
      AppLogger.info(
          'Saving serial number for user ${user.uid}: ${serialNumber.id}...');
      final docRef = _firestore.collection('users').doc(user.uid);
      await docRef.set({
        'serial_number': {
          'id': serialNumber.id,
          'type': serialNumber.type,
        },
      }, SetOptions(merge: true));

      AppLogger.info(
          'Serial number saved successfully for user ${user.uid}: ${serialNumber.id}');
      onUpdate();
    } catch (e) {
      AppLogger.error('Failed to save serial number for user: $e', e);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to save serial number: $e'),
            backgroundColor: AppColors.red,
          ),
        );
      }
    }
  }

  void startEditing(SerialNumber serialNumber, VoidCallback onUpdate) {
    AppLogger.info('Starting edit mode for serial number: ${serialNumber.id}');
    isEditing = true;
    editingSerialNumber = serialNumber;
    serialController.text = serialNumber.id;
    _name = serialNumber.name;
    selectedType = serialNumber.type;
    onUpdate();
  }

  void dispose() {
    AppLogger.info('Disposing SerialNumbersController...');
    serialController.dispose();
  }
}
