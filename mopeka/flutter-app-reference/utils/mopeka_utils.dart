enum MediumType {
  propane('propane'),
  air('air'),
  freshWater('fresh_water'),
  wasteWater('waste_water'),
  liveWell('live_well'),
  blackWater('black_water'),
  rawWater('raw_water'),
  gasoline('gasoline'),
  diesel('diesel'),
  lng('lng'),
  oil('oil'),
  hydraulicOil('hydraulic_oil');

  final String value;
  const MediumType(this.value);
}

final Map<MediumType, List<double>> mopekaTankLevelCoefficients = {
  MediumType.propane: [0.573045, -0.002822, -0.00000535],
  MediumType.air: [0.153096, 0.000327, -0.000000294],
  MediumType.freshWater: [0.600592, 0.003124, -0.00001368],
  MediumType.wasteWater: [0.600592, 0.003124, -0.00001368],
  MediumType.liveWell: [0.600592, 0.003124, -0.00001368],
  MediumType.blackWater: [0.600592, 0.003124, -0.00001368],
  MediumType.rawWater: [0.600592, 0.003124, -0.00001368],
  MediumType.gasoline: [0.7373417462, -0.001978229885, 0.00000202162],
  MediumType.diesel: [0.7373417462, -0.001978229885, 0.00000202162],
  MediumType.lng: [0.7373417462, -0.001978229885, 0.00000202162],
  MediumType.oil: [0.7373417462, -0.001978229885, 0.00000202162],
  MediumType.hydraulicOil: [0.7373417462, -0.001978229885, 0.00000202162],
};

class MopekaDevice {
  final String model;
  final String name;
  final int advLength;

  MopekaDevice(this.model, this.name, this.advLength);
}

final Map<int, MopekaDevice> deviceTypes = {
  0x3: MopekaDevice("M1017", "Pro Check", 10),
  0x4: MopekaDevice("Pro-200", "Pro-200", 10),
  0x5: MopekaDevice("Pro H20", "Pro Check H2O", 10),
  0x6: MopekaDevice("M1017", "Lippert BottleCheck", 10),
  0x8: MopekaDevice("M1015", "Pro Plus", 10),
  0x9: MopekaDevice("M1015", "Pro Plus with Cellular", 10),
  0xA: MopekaDevice("TD40/TD200", "TD40/TD200", 10),
  0xB: MopekaDevice("TD40/TD200", "TD40/TD200 with Cellular", 10),
  0xC: MopekaDevice("M1017", "Pro Check Universal", 10),
};

class MopekaSensorData {
  int rssi = 0;
  String deviceName = '';
  double batteryVoltage = 0.0;
  double batteryPercentage = 0.0;
  bool buttonPressed = false;
  double temperature = 0.0;
  int tankLevelRaw = 0;
  double tankLevelMm = 0.0;
  double tankLevelIn = 0.0;
  int readingQualityRaw = 0;
  int readingQualityPercent = 0;
  int accelerometerX = 0;
  int accelerometerY = 0;

  MopekaSensorData();

  void updateFromAdvertisement(
      List<int> data, int rssi, MediumType mediumType, String deviceName) {
    this.deviceName = deviceName;
    this.rssi = rssi;

    int battery = data[1];
    batteryVoltage = battery / 32.0;
    batteryPercentage = ((batteryVoltage - 2.2) / 0.65) * 100.0;
    batteryPercentage = batteryPercentage.clamp(0.0, 100.0);

    buttonPressed = (data[2] & 0x80) > 0;
    int temp = data[2] & 0x7F;
    temperature = (temp - 40).toDouble();

    tankLevelRaw = ((data[4] << 8) + data[3]) & 0x3FFF;
    var coefs = mopekaTankLevelCoefficients[mediumType]!;
    tankLevelMm = (tankLevelRaw *
            (coefs[0] + (coefs[1] * temp) + (coefs[2] * (temp * temp))))
        .toDouble();
    tankLevelIn = tankLevelMm / 25.4;

    readingQualityRaw = data[4] >> 6;
    readingQualityPercent = ((readingQualityRaw / 3) * 100).round();

    accelerometerX = data[8];
    accelerometerY = data[9];
  }
}
