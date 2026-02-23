import 'dart:convert';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:http/http.dart' as http;
import 'package:firebase_auth/firebase_auth.dart';
import '../utils/logger.dart';

class ProfileController {
  String get backendUrl => dotenv.env['BACKEND_URL'] ?? 'http://localhost:5000';

  Future<Map<String, dynamic>> updateProfile({
    required String fullName,
    required String email,
    String? password,
    required String currentEmail,
  }) async {
    try {
      AppLogger.info('Updating profile for email: $currentEmail...');
      final user = FirebaseAuth.instance.currentUser;
      if (user == null) {
        AppLogger.warning('No user is currently signed in for profile update.');
        return {
          'success': false,
          'message': 'No user is currently signed in.',
        };
      }

      final String uid = user.uid;

      final Map<String, dynamic> userData = {
        'fullName': fullName.trim(),
        'email': email.trim(),
        if (password != null && password.isNotEmpty)
          'password': password.trim(),
      };

      final response = await http.put(
        Uri.parse('$backendUrl/api/users/update/$uid'),
        headers: {
          'Content-Type': 'application/json',
        },
        body: jsonEncode(userData),
      );

      if (response.statusCode == 200) {
        final responseData = jsonDecode(response.body);
        AppLogger.info('Profile updated successfully. New email: $email');
        return {
          'success': true,
          'message': responseData['message'] ?? 'Profile updated successfully.',
        };
      } else {
        final responseData = jsonDecode(response.body);
        AppLogger.error('Failed to update profile: ${responseData['message']}');
        return {
          'success': false,
          'message': responseData['message'] ?? 'Failed to update profile.',
        };
      }
    } catch (e) {
      AppLogger.error('Failed to update profile: $e', e);
      return {
        'success': false,
        'message': 'Failed to update profile: $e',
      };
    }
  }
}
