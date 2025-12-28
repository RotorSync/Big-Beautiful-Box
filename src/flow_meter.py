#!/usr/bin/env python3
"""
Flow meter communication via IO-Link.

Provides:
- IO-Link communication with the IFM Picomag flow meter
- Automatic retry logic with exponential backoff
- Data parsing and validation
- Connection status monitoring
"""

import struct
import time
from dataclasses import dataclass
from typing import Optional, Tuple
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class FlowMeterStatus(Enum):
    """Flow meter connection status."""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    NO_RESPONSE = "no_response"
    INVALID_DATA = "invalid_data"
    ERROR = "error"


@dataclass
class FlowMeterReading:
    """Flow meter data reading."""
    totalizer_liters: float = 0.0
    flow_rate_l_per_s: float = 0.0
    raw_data: bytes = b''
    status: FlowMeterStatus = FlowMeterStatus.DISCONNECTED
    error_message: str = ""
    timestamp: float = 0.0

    @property
    def totalizer_gallons(self) -> float:
        """Get totalizer value in gallons."""
        return abs(self.totalizer_liters) * 0.264172

    @property
    def flow_rate_gpm(self) -> float:
        """Get flow rate in gallons per minute."""
        return self.flow_rate_l_per_s * 15.850323

    @property
    def is_valid(self) -> bool:
        """Check if reading is valid."""
        return self.status == FlowMeterStatus.CONNECTED


class FlowMeter:
    """
    IO-Link flow meter communication handler.

    Handles communication with the IFM Picomag flow meter via IOL-HAT,
    with automatic retry logic and connection monitoring.
    """

    def __init__(
        self,
        iol_port: int = 2,
        data_length: int = 15,
        max_retries: int = 3,
        retry_delay: float = 0.1,
        timeout: float = 5.0
    ):
        """
        Initialize flow meter handler.

        Args:
            iol_port: IO-Link port number (0-3)
            data_length: Expected data length from flow meter
            max_retries: Maximum number of read retries
            retry_delay: Initial delay between retries (doubles each retry)
            timeout: Seconds without valid read before considered disconnected
        """
        self.iol_port = iol_port
        self.data_length = data_length
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout

        # State tracking
        self._last_valid_reading: Optional[FlowMeterReading] = None
        self._last_successful_time: float = time.time()
        self._consecutive_failures: int = 0

        # IOL-HAT module (loaded lazily)
        self._iolhat = None

    def _get_iolhat(self):
        """Lazily load the iolhat module."""
        if self._iolhat is None:
            try:
                import iolhat
                self._iolhat = iolhat
            except ImportError as e:
                logger.error(f"Failed to import iolhat: {e}")
                raise
        return self._iolhat

    def _read_raw(self) -> bytes:
        """
        Read raw data from flow meter (single attempt).

        Returns:
            Raw bytes from flow meter

        Raises:
            Exception on communication error
        """
        iolhat = self._get_iolhat()
        return iolhat.pd(self.iol_port, 0, self.data_length, None)

    def _parse_data(self, raw_data: bytes) -> Tuple[float, float]:
        """
        Parse raw flow meter data.

        Args:
            raw_data: Raw bytes from flow meter

        Returns:
            Tuple of (totalizer_liters, flow_rate_l_per_s)

        Raises:
            ValueError if data is invalid
        """
        if len(raw_data) < 15:
            raise ValueError(f"Data too short: {len(raw_data)} bytes (expected >= 15)")

        # Check for all-zero response (device not responding)
        if raw_data == b'\x00' * len(raw_data):
            raise ValueError("Device not responding (all-zero data)")

        # Parse totalizer (bytes 4-7, big-endian float)
        # Note: The flow meter returns negative totalizer values, so we use abs()
        totalizer_liters = abs(struct.unpack('>f', raw_data[4:8])[0])

        # Parse flow rate (bytes 8-11, big-endian float)
        flow_rate_l_per_s = struct.unpack('>f', raw_data[8:12])[0]

        return totalizer_liters, flow_rate_l_per_s

    def read(self) -> FlowMeterReading:
        """
        Read flow meter with automatic retry.

        Attempts to read from the flow meter up to max_retries times,
        with exponential backoff between attempts.

        Returns:
            FlowMeterReading with data or error status
        """
        reading = FlowMeterReading(timestamp=time.time())
        last_error = ""
        delay = self.retry_delay

        for attempt in range(self.max_retries):
            try:
                # Attempt to read raw data
                raw_data = self._read_raw()
                reading.raw_data = raw_data

                # Parse the data
                totalizer, flow_rate = self._parse_data(raw_data)

                # Success!
                reading.totalizer_liters = totalizer
                reading.flow_rate_l_per_s = flow_rate
                reading.status = FlowMeterStatus.CONNECTED
                reading.error_message = ""

                # Update state
                self._last_valid_reading = reading
                self._last_successful_time = time.time()
                self._consecutive_failures = 0

                if attempt > 0:
                    logger.info(f"Flow meter read succeeded on attempt {attempt + 1}")

                return reading

            except ValueError as e:
                # Data parsing error
                last_error = str(e)
                logger.debug(f"Flow meter read attempt {attempt + 1} failed: {e}")

            except Exception as e:
                # Communication error
                last_error = str(e)
                logger.warning(f"Flow meter communication error on attempt {attempt + 1}: {e}")

            # Wait before retry (exponential backoff)
            if attempt < self.max_retries - 1:
                time.sleep(delay)
                delay *= 2

        # All retries failed
        self._consecutive_failures += 1
        reading.status = FlowMeterStatus.NO_RESPONSE
        reading.error_message = last_error

        # Return last valid reading values if available (for graceful degradation)
        if self._last_valid_reading:
            reading.totalizer_liters = self._last_valid_reading.totalizer_liters
            reading.flow_rate_l_per_s = self._last_valid_reading.flow_rate_l_per_s
            logger.warning(f"Using cached values after {self.max_retries} failed attempts: {last_error}")
        else:
            logger.error(f"Flow meter read failed after {self.max_retries} attempts: {last_error}")

        return reading

    @property
    def is_connected(self) -> bool:
        """Check if flow meter is currently connected."""
        return (time.time() - self._last_successful_time) < self.timeout

    @property
    def time_since_last_read(self) -> float:
        """Get seconds since last successful read."""
        return time.time() - self._last_successful_time

    @property
    def consecutive_failures(self) -> int:
        """Get count of consecutive failed reads."""
        return self._consecutive_failures

    def reset_stats(self) -> None:
        """Reset connection statistics."""
        self._consecutive_failures = 0
        self._last_successful_time = time.time()


def calculate_coast_distance(flow_rate_l_per_s: float, slope: float, intercept: float) -> float:
    """
    Calculate predicted coast distance after pump shutoff.

    Based on calibration data, predicts how many gallons will continue
    to flow after the shutoff relay is triggered.

    Args:
        flow_rate_l_per_s: Current flow rate in liters per second
        slope: Calibration curve slope
        intercept: Calibration curve intercept

    Returns:
        Predicted coast distance in gallons (minimum 0.1)
    """
    # Convert L/s to GPM
    flow_rate_gpm = flow_rate_l_per_s * 15.850323

    # Calculate predicted coast using calibration curve
    predicted_coast = slope * flow_rate_gpm + intercept

    # Ensure minimum threshold
    return max(predicted_coast, 0.1)
