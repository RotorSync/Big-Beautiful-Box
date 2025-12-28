#!/usr/bin/env python3
"""
GPIO handler for relay control and button monitoring.

Provides:
- Relay activation/deactivation
- Green button monitoring with debounce
- GPIO initialization and cleanup
"""

import threading
import time
from typing import Callable, Optional
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class GPIOState(Enum):
    """GPIO availability state."""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass
class ButtonEvent:
    """Button press event data."""
    timestamp: float
    pin: int


class GPIOHandler:
    """
    GPIO handler for relay control and button input.

    Manages GPIO initialization, relay control, and button monitoring
    with proper cleanup and error handling.
    """

    def __init__(
        self,
        relay_pin: int = 27,
        button_pin: int = 22,
        log_file: Optional[str] = None
    ):
        """
        Initialize GPIO handler.

        Args:
            relay_pin: BCM GPIO pin for pump stop relay
            button_pin: BCM GPIO pin for green button (active low)
            log_file: Optional path for relay/button debug log
        """
        self.relay_pin = relay_pin
        self.button_pin = button_pin
        self.log_file = log_file

        self._gpio = None
        self._state = GPIOState.UNAVAILABLE
        self._button_thread: Optional[threading.Thread] = None
        self._button_callback: Optional[Callable[[ButtonEvent], None]] = None
        self._running = False

    def initialize(self) -> bool:
        """
        Initialize GPIO subsystem.

        Returns:
            True if GPIO is available and initialized
        """
        try:
            import RPi.GPIO as GPIO
            self._gpio = GPIO

            # Set BCM mode
            try:
                GPIO.setmode(GPIO.BCM)
            except RuntimeError:
                # Already set - that's OK
                pass

            # Suppress warnings
            GPIO.setwarnings(False)

            # Set up relay pin as output (initially LOW/off)
            GPIO.setup(self.relay_pin, GPIO.OUT, initial=GPIO.LOW)

            # Set up button pin as input with pull-up
            GPIO.setup(self.button_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

            self._state = GPIOState.AVAILABLE
            logger.info(f"GPIO initialized: relay={self.relay_pin}, button={self.button_pin}")
            return True

        except ImportError:
            logger.warning("RPi.GPIO not available - GPIO functions disabled")
            self._state = GPIOState.UNAVAILABLE
            return False

        except Exception as e:
            logger.error(f"GPIO initialization failed: {e}")
            self._state = GPIOState.ERROR
            return False

    def cleanup(self) -> None:
        """Clean up GPIO resources."""
        self.stop_button_monitor()

        if self._gpio and self._state == GPIOState.AVAILABLE:
            try:
                self._gpio.cleanup()
                logger.info("GPIO cleaned up")
            except Exception as e:
                logger.error(f"GPIO cleanup error: {e}")

    @property
    def is_available(self) -> bool:
        """Check if GPIO is available."""
        return self._state == GPIOState.AVAILABLE

    def _log(self, message: str, prefix: str = "") -> None:
        """Write to debug log file if configured."""
        if self.log_file:
            try:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                with open(self.log_file, 'a') as f:
                    if prefix:
                        f.write(f"{timestamp} [{prefix}] {message}\n")
                    else:
                        f.write(f"{timestamp} {message}\n")
            except Exception:
                pass

    def activate_relay(self, duration: float = 5.0) -> bool:
        """
        Activate pump stop relay for specified duration.

        Args:
            duration: Seconds to hold relay active

        Returns:
            True if relay was activated successfully
        """
        if not self.is_available:
            logger.warning("Cannot activate relay - GPIO not available")
            return False

        self._log(f"Activating relay on GPIO {self.relay_pin} for {duration}s", "INFO")

        try:
            # Activate relay (HIGH)
            self._gpio.output(self.relay_pin, self._gpio.HIGH)
            self._log(f"Relay GPIO {self.relay_pin} set HIGH", "SUCCESS")
            logger.info(f"Relay activated for {duration}s")

            # Hold for duration
            time.sleep(duration)

            # Deactivate relay (LOW)
            self._gpio.output(self.relay_pin, self._gpio.LOW)
            self._log(f"Relay GPIO {self.relay_pin} set LOW", "SUCCESS")
            logger.info("Relay deactivated")

            return True

        except Exception as e:
            self._log(f"Relay error: {e}", "ERROR")
            logger.error(f"Relay activation failed: {e}")
            return False

    def set_relay(self, state: bool) -> bool:
        """
        Set relay to specific state.

        Args:
            state: True for HIGH (on), False for LOW (off)

        Returns:
            True if successful
        """
        if not self.is_available:
            return False

        try:
            self._gpio.output(self.relay_pin, self._gpio.HIGH if state else self._gpio.LOW)
            return True
        except Exception as e:
            logger.error(f"Relay set failed: {e}")
            return False

    def read_button(self) -> bool:
        """
        Read current button state.

        Returns:
            True if button is pressed (active low)
        """
        if not self.is_available:
            return False

        try:
            # Button is active low (pressed = LOW = 0)
            return self._gpio.input(self.button_pin) == self._gpio.LOW
        except Exception:
            return False

    def start_button_monitor(
        self,
        callback: Callable[[ButtonEvent], None],
        debounce_ms: int = 300
    ) -> bool:
        """
        Start background thread to monitor button presses.

        Args:
            callback: Function to call on button press
            debounce_ms: Debounce time in milliseconds

        Returns:
            True if monitor started successfully
        """
        if not self.is_available:
            logger.warning("Cannot start button monitor - GPIO not available")
            return False

        if self._running:
            logger.warning("Button monitor already running")
            return True

        self._button_callback = callback
        self._running = True

        def monitor_loop():
            last_state = self._gpio.HIGH
            debounce_sec = debounce_ms / 1000.0

            logger.info(f"Button monitor started on GPIO {self.button_pin}")
            self._log("Button monitor started", "INFO")

            while self._running:
                try:
                    current_state = self._gpio.input(self.button_pin)

                    # Detect button press (HIGH -> LOW transition)
                    if last_state == self._gpio.HIGH and current_state == self._gpio.LOW:
                        event = ButtonEvent(
                            timestamp=time.time(),
                            pin=self.button_pin
                        )
                        self._log("Button pressed!", "EVENT")
                        logger.info("Green button pressed")

                        if self._button_callback:
                            try:
                                self._button_callback(event)
                            except Exception as e:
                                logger.error(f"Button callback error: {e}")

                        # Debounce delay
                        time.sleep(debounce_sec)

                    last_state = current_state
                    time.sleep(0.05)  # Check every 50ms

                except Exception as e:
                    logger.error(f"Button monitor error: {e}")
                    time.sleep(1)  # Back off on error

            logger.info("Button monitor stopped")

        self._button_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._button_thread.start()
        return True

    def stop_button_monitor(self) -> None:
        """Stop the button monitor thread."""
        self._running = False
        if self._button_thread and self._button_thread.is_alive():
            self._button_thread.join(timeout=1.0)
        self._button_thread = None
