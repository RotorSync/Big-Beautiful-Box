import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';

class AuthController {
  final FirebaseAuth _firebaseAuth;
  final FirebaseFirestore _firestore;

  AuthController({
    FirebaseAuth? firebaseAuth,
    FirebaseFirestore? firestore,
  })  : _firebaseAuth = firebaseAuth ?? FirebaseAuth.instance,
        _firestore = firestore ?? FirebaseFirestore.instance;

  Future<void> signIn({
    required String email,
    required String password,
  }) async {
    try {
      await _firebaseAuth.signInWithEmailAndPassword(
        email: email,
        password: password,
      );
    } on FirebaseAuthException catch (e) {
      switch (e.code) {
        case 'user-not-found':
          throw 'No user found for that email.';
        case 'wrong-password':
          throw 'Incorrect password.';
        case 'invalid-email':
          throw 'Invalid email format.';
        case 'invalid-credential':
          throw 'Invalid email or password.';
        case 'user-disabled':
          throw 'This account has been disabled.';
        case 'too-many-requests':
          throw 'Too many attempts. Please try again later.';
        default:
          throw 'An error occurred: ${e.message ?? "Unknown error"}';
      }
    } catch (e) {
      throw 'Failed to sign in: $e';
    }
  }

  User? get currentUser => _firebaseAuth.currentUser;

  Future<Map<String, String?>> getUserDetails() async {
    User? user = currentUser;
    if (user == null) return {'role': null, 'fullName': null, 'email': null};

    DocumentSnapshot doc =
        await _firestore.collection('users').doc(user.uid).get();
    if (doc.exists) {
      final data = doc.data() as Map<String, dynamic>?;
      return {
        'role': data?['role'] as String?,
        'fullName': data?['fullName'] as String?,
        'email': data?['email'] as String?,
      };
    }
    return {'role': null, 'fullName': null, 'email': null};
  }

  Future<void> signOut() async {
    await _firebaseAuth.signOut();
  }
}
