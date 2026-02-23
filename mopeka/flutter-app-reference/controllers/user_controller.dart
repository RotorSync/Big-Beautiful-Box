import 'dart:convert';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:http/http.dart' as http;
import '../utils/logger.dart';

class UserController {
  final FirebaseFirestore _firestore = FirebaseFirestore.instance;
  String get backendUrl => dotenv.env['BACKEND_URL'] ?? 'http://localhost:5000';

  Future<List<Map<String, dynamic>>> fetchUsers() async {
    try {
      AppLogger.info('Fetching users from Firestore...');
      final snapshot = await _firestore.collection('users').get();
      final users = snapshot.docs.map((doc) {
        final data = doc.data();
        data['id'] = doc.id;
        return data;
      }).toList();
      AppLogger.info(
          'Users fetched successfully from Firestore. Count: ${users.length}');
      return users;
    } catch (e) {
      AppLogger.error('Failed to fetch users from Firestore: $e', e);
      throw Exception('Failed to fetch users: ${e.toString()}');
    }
  }

  Future<Map<String, dynamic>> createUser(Map<String, dynamic> userData) async {
    try {
      AppLogger.info('Creating user');
      final url = '$backendUrl/api/users/create';
      final response = await http.post(
        Uri.parse(url),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(userData),
      );

      if (response.statusCode == 201) {
        final responseData = jsonDecode(response.body);
        AppLogger.info(
            'User created successfully. Email: ${userData['email']}');
        return {
          'success': true,
          'message': responseData['message'] ?? 'User created successfully'
        };
      }
      AppLogger.error('Failed to create user: ${response.body}');
      throw Exception('Failed to create user: ${response.body}');
    } catch (e) {
      AppLogger.error('Failed to create user: $e', e);
      throw Exception('Failed to create user: ${e.toString()}');
    }
  }

  Future<Map<String, dynamic>> updateUser(
      String userId, Map<String, dynamic> userData) async {
    try {
      AppLogger.info('Updating user for userId: $userId...');
      final url = '$backendUrl/api/users/update/$userId';
      final response = await http.put(
        Uri.parse(url),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(userData),
      );

      if (response.statusCode == 200) {
        final responseData = jsonDecode(response.body);
        AppLogger.info(
            'User updated successfully. Email: ${userData['email']}');
        return {
          'success': true,
          'message': responseData['message'] ?? 'User updated successfully'
        };
      }
      AppLogger.error('Failed to update user: ${response.body}');
      throw Exception('Failed to update user: ${response.body}');
    } catch (e) {
      AppLogger.error('Failed to update user: $e', e);
      throw Exception('Failed to update user: ${e.toString()}');
    }
  }
}
