# Mopeka Sensor Data

Mopeka Pro Check ultrasonic tank level sensors used on the BBB spray trailers.

## Files

### `mopeka-sensor-details.csv`
Sensor assignments for all trailers — includes:
- Trailer number and operator
- Front/back tank position
- **Height offset** (calibration offset per sensor, in inches)
- Mopeka BLE ID
- MQTT topic for the BBB app

### `calibration-points-1070gal-tank.csv`
Lookup table mapping tank level (inches) to gallons for the 1070-gallon spray tanks.
- 67 calibration points from full (1070 gal) to empty (0 gal)
- Tank level measured in inches from bottom
- Used by the BBB Pi to convert raw Mopeka readings to gallons

## Notes
- Height offsets are per-sensor corrections applied before the calibration lookup
- A negative offset means the sensor reads high (subtract from raw reading)
- A positive offset means the sensor reads low (add to raw reading)
- Trailer 1 back tank and Trailer 8 back tank have missing offsets — need calibration
