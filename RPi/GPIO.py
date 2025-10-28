# GPIO compatibility wrapper using lgpio for Raspberry Pi 5
import lgpio

# Constants
BCM = "BCM"
BOARD = "BOARD"
IN = 0
OUT = 1
HIGH = 1
LOW = 0

_mode = None
_chip = None
_pins = {}

def setmode(mode):
    global _mode, _chip
    _mode = mode
    _chip = lgpio.gpiochip_open(4)  # Pi 5 uses gpiochip4

def setwarnings(flag):
    pass  # lgpio doesn't need warnings

def setup(pin, direction):
    global _chip, _pins
    if _chip is None:
        raise RuntimeError("Must call setmode() first")
    if direction == OUT:
        lgpio.gpio_claim_output(_chip, pin, LOW)
    else:
        lgpio.gpio_claim_input(_chip, pin)
    _pins[pin] = direction

def output(pin, value):
    global _chip
    if _chip is None:
        raise RuntimeError("GPIO not initialized")
    lgpio.gpio_write(_chip, pin, value)

def input(pin):
    global _chip
    if _chip is None:
        raise RuntimeError("GPIO not initialized")
    return lgpio.gpio_read(_chip, pin)

def cleanup():
    global _chip, _pins
    if _chip is not None:
        for pin in _pins:
            lgpio.gpio_free(_chip, pin)
        lgpio.gpiochip_close(_chip)
        _chip = None
        _pins = {}
