#!/usr/bin/env python3
"""
RS485 serial communication handler.

Handles communication with the switch box via RS485 serial interface.
Supports command parsing, heartbeat monitoring, and bi-directional messaging.
"""

import serial
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SerialCommand(Enum):
    """Known serial commands from switch box."""
    PLUS_1 = "+1"       # Increment requested gallons by 1
    MINUS_1 = "-1"      # Decrement requested gallons by 1
    PLUS_10 = "+10"     # Increment requested gallons by 10
    MINUS_10 = "-10"    # Decrement requested gallons by 10
    OVERRIDE = "OV"     # Toggle override mode
    PUMP_STOP = "PS"    # Activate pump stop relay
    HEARTBEAT = "OK"    # Heartbeat from switch box
    ACK = "ACK"         # Acknowledgment


@dataclass
class SerialStatus:
    """Serial connection status."""
    connected: bool = False
    last_heartbeat: float = 0.0
    last_command: str = ""
    last_command_time: float = 0.0
    error_message: str = ""


@dataclass
class CommandHandler:
    """Command handler registration."""
    command: str
    callback: Callable[[str], None]
    description: str = ""


class SerialHandler:
    """
    RS485 serial communication handler.

    Manages serial port communication with the switch box,
    including command parsing, heartbeat monitoring, and message sending.
    """

    # Heartbeat timeout in seconds
    HEARTBEAT_TIMEOUT = 10.0

    def __init__(
        self,
        port: str = "/dev/ttyAMA0",
        baud_rate: int = 115200,
        timeout: float = 1.0,
        log_file: Optional[str] = None
    ):
        """
        Initialize serial handler.

        Args:
            port: Serial port path
            baud_rate: Baud rate
            timeout: Read timeout in seconds
            log_file: Optional path for debug log
        """
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.log_file = log_file

        self._serial: Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._handlers: Dict[str, CommandHandler] = {}
        self._default_handler: Optional[Callable[[str], None]] = None
        self._status = SerialStatus()
        self._buffer = ""

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

    def connect(self) -> bool:
        """
        Open serial port connection.

        Returns:
            True if connection successful
        """
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.timeout
            )
            self._status.connected = True
            self._status.error_message = ""
            logger.info(f"Serial port opened: {self.port} @ {self.baud_rate}")
            self._log(f"Connected to {self.port}", "INFO")
            return True

        except serial.SerialException as e:
            self._status.connected = False
            self._status.error_message = str(e)
            logger.error(f"Serial connection failed: {e}")
            self._log(f"Connection failed: {e}", "ERROR")
            return False

    def disconnect(self) -> None:
        """Close serial port connection."""
        self.stop()
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
                logger.info("Serial port closed")
            except Exception as e:
                logger.error(f"Serial close error: {e}")
        self._serial = None
        self._status.connected = False

    @property
    def is_connected(self) -> bool:
        """Check if serial port is connected."""
        return self._serial is not None and self._serial.is_open

    @property
    def status(self) -> SerialStatus:
        """Get current serial status."""
        return self._status

    @property
    def heartbeat_ok(self) -> bool:
        """Check if heartbeat is within timeout."""
        if self._status.last_heartbeat == 0:
            return False
        return (time.time() - self._status.last_heartbeat) < self.HEARTBEAT_TIMEOUT

    def register_handler(
        self,
        command: str,
        callback: Callable[[str], None],
        description: str = ""
    ) -> None:
        """
        Register a handler for a specific command.

        Args:
            command: Command string to match
            callback: Function to call when command received
            description: Optional description
        """
        self._handlers[command] = CommandHandler(
            command=command,
            callback=callback,
            description=description
        )
        logger.debug(f"Registered handler for command: {command}")

    def set_default_handler(self, callback: Callable[[str], None]) -> None:
        """
        Set default handler for unrecognized commands.

        Args:
            callback: Function to call for unknown commands
        """
        self._default_handler = callback

    def _process_command(self, command: str) -> None:
        """Process a received command."""
        command = command.strip()
        if not command:
            return

        self._log(f"Received: {command}", "RX")
        self._status.last_command = command
        self._status.last_command_time = time.time()

        # Check for heartbeat
        if command == SerialCommand.HEARTBEAT.value:
            self._status.last_heartbeat = time.time()
            self._log("Heartbeat received", "HEARTBEAT")
            # Still call handler if registered
            if command in self._handlers:
                try:
                    self._handlers[command].callback(command)
                except Exception as e:
                    logger.error(f"Heartbeat handler error: {e}")
            return

        # Find matching handler
        if command in self._handlers:
            try:
                self._handlers[command].callback(command)
            except Exception as e:
                logger.error(f"Command handler error for '{command}': {e}")
                self._log(f"Handler error: {e}", "ERROR")
        elif self._default_handler:
            try:
                self._default_handler(command)
            except Exception as e:
                logger.error(f"Default handler error: {e}")
        else:
            self._log(f"Unknown command: {command}", "WARN")
            logger.warning(f"Unhandled serial command: {command}")

    def _read_loop(self) -> None:
        """Background thread for reading serial data."""
        logger.info("Serial read loop started")
        self._log("Read loop started", "INFO")

        while self._running and self._serial:
            try:
                if self._serial.in_waiting > 0:
                    # Read available data
                    data = self._serial.read(self._serial.in_waiting)
                    try:
                        text = data.decode('utf-8', errors='ignore')
                    except Exception:
                        text = data.decode('latin-1', errors='ignore')

                    # Add to buffer and process complete lines
                    self._buffer += text
                    while '\n' in self._buffer:
                        line, self._buffer = self._buffer.split('\n', 1)
                        self._process_command(line.strip())

                else:
                    time.sleep(0.01)  # Small sleep when no data

            except serial.SerialException as e:
                logger.error(f"Serial read error: {e}")
                self._log(f"Read error: {e}", "ERROR")
                self._status.connected = False
                time.sleep(1)  # Back off on error

            except Exception as e:
                logger.error(f"Serial loop error: {e}")
                time.sleep(0.1)

        logger.info("Serial read loop stopped")

    def start(self) -> bool:
        """
        Start the serial reading thread.

        Returns:
            True if started successfully
        """
        if self._running:
            return True

        if not self.is_connected:
            if not self.connect():
                return False

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop the serial reading thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def send(self, message: str) -> bool:
        """
        Send a message over serial.

        Args:
            message: Message to send (newline added automatically)

        Returns:
            True if sent successfully
        """
        if not self.is_connected:
            logger.warning("Cannot send - serial not connected")
            return False

        try:
            data = (message + '\n').encode('utf-8')
            self._serial.write(data)
            self._serial.flush()
            self._log(f"Sent: {message}", "TX")
            return True

        except Exception as e:
            logger.error(f"Serial send error: {e}")
            self._log(f"Send error: {e}", "ERROR")
            return False

    def send_ack(self) -> bool:
        """Send acknowledgment."""
        return self.send(SerialCommand.ACK.value)
