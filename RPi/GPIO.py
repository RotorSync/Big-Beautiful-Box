# GPIO compatibility wrapper using lgpio for Raspberry Pi 5
import lgpio

# Constants
BCM = "BCM"
BOARD = "BOARD"
IN = 0
OUT = 1
HIGH = 1
LOW = 0
PUD_OFF = 0
PUD_DOWN = 1
PUD_UP = 2

_mode = None
_chip = None
_pins = {}

def setmode(mode):
    global _mode, _chip
    _mode = mode
    # Only open chip once - don't re-open if already initialized
    if _chip is None:
        _chip = lgpio.gpiochip_open(0)  # Pi 5 uses gpiochip4

def setwarnings(flag):
    pass  # lgpio doesn't need warnings

def setup(pin, direction, pull_up_down=PUD_OFF):
    global _chip, _pins
    if _chip is None:
        raise RuntimeError("Must call setmode() first")

    # If pin already setup, free it first
    if pin in _pins:
        try:
            lgpio.gpio_free(_chip, pin)
        except:
            pass  # Ignore errors if already freed

    if direction == OUT:
        lgpio.gpio_claim_output(_chip, pin, LOW)
    else:
        lgpio.gpio_claim_input(_chip, pin)
        # Set pull up/down resistor
        if pull_up_down == PUD_UP:
            lgpio.gpio_set_pull_up_down(_chip, pin, lgpio.SET_PULL_UP)
        elif pull_up_down == PUD_DOWN:
            lgpio.gpio_set_pull_up_down(_chip, pin, lgpio.SET_PULL_DOWN)
        else:
            lgpio.gpio_set_pull_up_down(_chip, pin, lgpio.SET_PULL_NONE)
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
