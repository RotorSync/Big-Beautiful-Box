import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../constants/colors.dart';
import '../../controllers/user_controller.dart';
import '../../widgets/app_bar.dart';
import '../../widgets/user_list.dart';
import 'user_form_screen.dart';

class UsersScreen extends StatefulWidget {
  const UsersScreen({super.key});

  @override
  State<UsersScreen> createState() => _UsersScreenState();
}

class _UsersScreenState extends State<UsersScreen> {
  bool _isLoading = true;
  List<Map<String, dynamic>> _users = [];
  final UserController _userController = UserController();

  @override
  void initState() {
    super.initState();
    _fetchUsers();
  }

  Future<void> _fetchUsers() async {
    setState(() {
      _isLoading = true;
    });

    try {
      final users = await _userController.fetchUsers();
      if (mounted) {
        setState(() {
          _users = users;
          _isLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to fetch users: $e'),
            backgroundColor: AppColors.red,
          ),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: const CustomAppBar(title: 'Users'),
      body: _isLoading
          ? const Center(
              child: CircularProgressIndicator(color: AppColors.primary),
            )
          : _users.isEmpty
              ? const Center(
                  child: Text(
                    'No users added yet.',
                    style: TextStyle(color: AppColors.text),
                  ),
                )
              : ListView.builder(
                  itemCount: _users.length,
                  itemBuilder: (context, index) {
                    final user = _users[index];
                    return UserList(
                      userId: user['id'] ?? '',
                      fullName: user['fullName'] ?? '',
                      email: user['email'] ?? '',
                      onEdit: () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (context) => UserFormScreen(user: user),
                          ),
                        ).then((_) => _fetchUsers());
                      },
                    );
                  },
                ),
      floatingActionButton: FloatingActionButton(
        backgroundColor: AppColors.primary,
        onPressed: () {
          Navigator.push(
            context,
            MaterialPageRoute(
              builder: (context) => const UserFormScreen(),
            ),
          ).then((_) => _fetchUsers());
        },
        child: const Icon(LucideIcons.plus, color: AppColors.white),
      ),
    );
  }
}
