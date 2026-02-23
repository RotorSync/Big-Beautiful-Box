import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'constants/colors.dart';
import 'constants/firebase.dart';
import 'screens/login_screen.dart';
import 'screens/home_screen.dart';
import 'controllers/home_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp(options: kIsWeb ? FirebaseConfig.options : null);
  await dotenv.load(fileName: ".env");

  // Set default system UI overlay style
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.dark,
    statusBarBrightness: Brightness.light,
  ));

  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    // Create RouteObserver instance
    final RouteObserver<ModalRoute<void>> routeObserver =
        RouteObserver<ModalRoute<void>>();

    return MultiProvider(
      providers: [
        Provider(create: (_) => HomeController()),
      ],
      child: MaterialApp(
        title: 'Rotorsync',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          scaffoldBackgroundColor: AppColors.white,
          colorScheme: ColorScheme.fromSeed(seedColor: AppColors.primary),
          useMaterial3: true,
          textSelectionTheme: const TextSelectionThemeData(
            cursorColor: AppColors.primary,
            selectionColor: AppColors.accent,
            selectionHandleColor: AppColors.primary,
          ),
        ),
        home: AuthWrapper(routeObserver: routeObserver),
        routes: {
          '/home': (context) => HomeScreen(routeObserver: routeObserver),
          '/login': (context) => const LoginScreen(),
        },
        navigatorObservers: [routeObserver],
      ),
    );
  }
}

class AuthWrapper extends StatelessWidget {
  final RouteObserver<ModalRoute<void>> routeObserver;

  const AuthWrapper({super.key, required this.routeObserver});

  @override
  Widget build(BuildContext context) {
    final homeController = Provider.of<HomeController>(context, listen: false);

    return StreamBuilder<User?>(
      stream: FirebaseAuth.instance.authStateChanges(),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Scaffold(
            body: Center(
              child: CircularProgressIndicator(color: AppColors.primary),
            ),
          );
        }
        if (snapshot.hasData) {
          return HomeScreen(routeObserver: routeObserver);
        } else {
          homeController.mopekaController.disconnect();
          return const LoginScreen();
        }
      },
    );
  }
}
