# Mock RPi.GPIO for testing on non-Pi systems
BCM = 11
BOARD = 10
IN = 1
OUT = 0
HIGH = 1
LOW = 0
PUD_UP = 22
PUD_DOWN = 21

_mode = None
_pins = {}
_warnings = True

def setmode(mode):
    global _mode
    _mode = mode

def setwarnings(flag):
    global _warnings
    _warnings = flag

def setup(pin, direction, initial=0, pull_up_down=None):
    _pins[pin] = {'direction': direction, 'value': initial}

def output(pin, value):
    if pin in _pins:
        _pins[pin]['value'] = value

def input(pin):
    return _pins.get(pin, {}).get('value', 0)

def cleanup(pin=None):
    global _pins
    if pin is None:
        _pins = {}
    elif pin in _pins:
        del _pins[pin]
