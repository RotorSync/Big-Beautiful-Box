import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:hugeicons/hugeicons.dart';
import '../constants/colors.dart';
import '../controllers/home_controller.dart';
import '../widgets/card_home.dart';
import '../widgets/welcome_message.dart';
import 'settings_screen.dart';
import 'users/users_screen.dart';

class HomeScreen extends StatefulWidget {
  final RouteObserver<ModalRoute<void>> routeObserver;

  const HomeScreen({super.key, required this.routeObserver});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with RouteAware {
  late HomeController _controller;

  @override
  void initState() {
    super.initState();
    _controller = HomeController();
    _controller.onStateChanged = () {
      if (mounted) setState(() {});
    };
    _controller.onShowSnackBar = (message, color) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(message), backgroundColor: color),
        );
      }
    };
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    widget.routeObserver.subscribe(this, ModalRoute.of(context)!);
  }

  @override
  void didPopNext() {
    SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.dark,
      statusBarBrightness: Brightness.light,
    ));
    super.didPopNext();
  }

  @override
  void dispose() {
    widget.routeObserver.unsubscribe(this);
    _controller.dispose();
    super.dispose();
  }

  List<Widget> _buildScreens() {
    return [
      SafeArea(
        top: true,
        child: SingleChildScrollView(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                WelcomeMessage(
                    fullName: _controller.fullName ?? '',
                    initials: _controller.initials ?? ''),
                const SizedBox(height: 24),
                HomeCard(
                  title: 'Mopeka',
                  icon: HugeIcons.strokeRoundedDroplet,
                  switchState: _controller.mopekaSwitchState,
                  status: _controller.mopekaStatus,
                  onSwitchChanged: (value) =>
                      _controller.toggleMopekaConnection(value),
                  onTap: () => _controller.navigateToMopekaScreen(context),
                ),
                HomeCard(
                  title: 'BMS',
                  icon: HugeIcons.strokeRoundedBatteryEmpty,
                  switchState: _controller.bmsSwitchState,
                  status: _controller.bmsStatus,
                  onSwitchChanged: (value) =>
                      _controller.toggleBmsConnection(value),
                  onTap: () => _controller.navigateToBmsScreen(context),
                ),
                HomeCard(
                  title: 'RasPi',
                  icon: HugeIcons.strokeRoundedCpu,
                  switchState: _controller.raspiSwitchState,
                  status: _controller.raspiStatus,
                  onSwitchChanged: (value) =>
                      _controller.toggleRaspiConnection(value),
                  onTap: () => _controller.navigateToRaspiScreen(context),
                ),
                HomeCard(
                  title: 'MQTT',
                  icon: HugeIcons.strokeRoundedServerStack02,
                  switchState: _controller.mqttSwitchState,
                  status: _controller.mqttStatus,
                  onSwitchChanged: (value) =>
                      _controller.toggleMQTTConnection(value),
                  onTap: _controller.role == 'admin'
                      ? () => _controller.navigateToMQTTConfig(context)
                      : null,
                ),
                HomeCard(
                  title: 'WT Tilt Sensor',
                  icon: HugeIcons.strokeRoundedNavigation04,
                  switchState: _controller.sensorSwitchState,
                  status: _controller.sensorStatus,
                  onSwitchChanged: (value) =>
                      _controller.toggleSensorConnection(value),
                  onTap: () => _controller.navigateToSensorScreen(context),
                ),
                HomeCard(
                  title: 'Mopeka BMS',
                  icon: HugeIcons.strokeRoundedNavigation04,
                  switchState: _controller.sensorSwitchState,
                  status: _controller.sensorStatus,
                  onSwitchChanged: (value) =>
                      _controller.toggleSensorConnection(value),
                  onTap: () => _controller.navigateToMopekaBMS(context),
                ),
              ],
            ),
          ),
        ),
      ),
      if (_controller.role == 'admin') const UsersScreen(),
      Scaffold(
        appBar: AppBar(
          title: const Text('Map'),
          backgroundColor: AppColors.white,
          elevation: 1,
          centerTitle: true,
        ),
        body: const SafeArea(top: false, child: Center(child: Text('Map'))),
      ),
      SettingsScreen(
        mqttService: _controller.mqttService,
        fullName: _controller.fullName,
        email: _controller.email,
        initials: _controller.initials,
        role: _controller.role,
        onProfileUpdated: (fullName, email, initials) {
          _controller.fullName = fullName;
          _controller.email = email;
          _controller.initials = initials;
          if (mounted) setState(() {});
        },
      ),
    ];
  }

  Widget _buildBody() {
    if (_controller.role == null || _controller.fullName == null) {
      return const Center(
        child: CircularProgressIndicator(color: AppColors.primary),
      );
    }

    return IndexedStack(
      index: _controller.selectedIndex,
      children: _buildScreens(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final navItems = _controller.buildNavItems();

    return Scaffold(
      body: Column(
        children: [
          Expanded(child: _buildBody()),
        ],
      ),
      bottomNavigationBar: Container(
        decoration: const BoxDecoration(
          border: Border(
            top: BorderSide(color: AppColors.offWhite, width: 1.0),
          ),
        ),
        child: Container(
          color: AppColors.white,
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: navItems.asMap().entries.map((entry) {
              int index = entry.key;
              Map<String, dynamic> item = entry.value;
              bool isSelected = _controller.selectedIndex == index;

              return Expanded(
                child: InkWell(
                  onTap: () {
                    _controller.onItemTapped(index);
                    setState(() {});
                  },
                  splashFactory: NoSplash.splashFactory,
                  highlightColor: Colors.transparent,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 18, vertical: 6),
                        decoration: BoxDecoration(
                          color: isSelected
                              ? AppColors.accent
                              : Colors.transparent,
                          borderRadius: BorderRadius.circular(50),
                        ),
                        child: Icon(
                          item['icon'],
                          color:
                              isSelected ? AppColors.primary : AppColors.grey,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        item['label'],
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight:
                              isSelected ? FontWeight.bold : FontWeight.w500,
                          color:
                              isSelected ? AppColors.primary : AppColors.text,
                        ),
                      ),
                    ],
                  ),
                ),
              );
            }).toList(),
          ),
        ),
      ),
    );
  }
}
