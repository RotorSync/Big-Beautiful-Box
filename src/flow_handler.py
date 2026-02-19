#!/usr/bin/env python3
"""
IO-Link flow meter handler.

Reads flow data from the Picomag flow meter via IO-Link HAT.
Provides thread-safe access to flow measurements.
"""

import struct
import time
import threading
from typing import Optional, Tuple
from dataclasses import dataclass
import logging

import config

# Try to import iolhat
try:
    import sys
    sys.path.insert(0, config.IOL_HAT_PATH)
    import iolhat
    IOLHAT_AVAILABLE = True
except ImportError:
    IOLHAT_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class FlowReading:
    """A flow meter reading."""
    totalizer_liters: float
    flow_rate_l_per_s: float
    timestamp: float
    is_valid: bool
    error: str = ""
    
    @property
    def totalizer_gallons(self) -> float:
        return self.totalizer_liters * config.LITERS_TO_GALLONS
    
    @property
    def flow_rate_gpm(self) -> float:
        return self.flow_rate_l_per_s * config.LITERS_PER_SEC_TO_GPM
    
    @property
    def is_flowing(self) -> bool:
        return self.flow_rate_l_per_s >= config.FLOW_STOPPED_THRESHOLD


class FlowHandler:
    """
    IO-Link flow meter handler.
    
    Manages communication with the Picomag flow meter via IO-Link HAT.
    Provides thread-safe readings with caching.
    """
    
    def __init__(
        self,
        port: int = config.IOL_PORT,
        data_length: int = config.DATA_LENGTH,
        timeout: float = config.FLOW_METER_TIMEOUT
    ):
        """
        Initialize flow handler.
        
        Args:
            port: IO-Link port number
            data_length: Expected data length from device
            timeout: Timeout before considering disconnected
        """
        self.port = port
        self.data_length = data_length
        self.timeout = timeout
        
        self._lock = threading.Lock()
        self._last_reading: Optional[FlowReading] = None
        self._initialized = False
    
    def initialize(self) -> bool:
        """
        Initialize IO-Link HAT.
        
        Returns:
            True if initialization successful
        """
        if not IOLHAT_AVAILABLE:
            logger.error("iolhat module not available")
            return False
        
        try:
            iolhat.power(self.port, 1)
            time.sleep(0.5)
            self._initialized = True
            logger.info(f"IO-Link port {self.port} initialized")
            return True
            
        except Exception as e:
            logger.error(f"IO-Link initialization failed: {e}")
            return False
    
    def read(self) -> FlowReading:
        """
        Read current flow data from the meter.
        
        Returns:
            FlowReading with current values or last known if error
        """
        now = time.time()
        
        if not self._initialized:
            return FlowReading(
                totalizer_liters=0.0,
                flow_rate_l_per_s=0.0,
                timestamp=now,
                is_valid=False,
                error="Not initialized"
            )
        
        try:
            # Read process data from IO-Link device
            raw_data = iolhat.pd(self.port, 0, self.data_length, None)
            
            if len(raw_data) < 15:
                raise ValueError(f"Invalid data length: {len(raw_data)}")
            
            # Check for all-zero data (device not responding)
            if raw_data == b'\x00' * len(raw_data):
                raise ValueError("Device not responding (all-zero data)")
            
            # Decode Picomag format
            totalizer_liters = abs(struct.unpack('>f', raw_data[4:8])[0])
            flow_rate_l_per_s = struct.unpack('>f', raw_data[8:12])[0]
            
            reading = FlowReading(
                totalizer_liters=totalizer_liters,
                flow_rate_l_per_s=flow_rate_l_per_s,
                timestamp=now,
                is_valid=True
            )
            
            with self._lock:
                self._last_reading = reading
            
            return reading
            
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Flow read error: {error_msg}")
            
            # Return last known reading if available
            with self._lock:
                if self._last_reading:
                    return FlowReading(
                        totalizer_liters=self._last_reading.totalizer_liters,
                        flow_rate_l_per_s=self._last_reading.flow_rate_l_per_s,
                        timestamp=now,
                        is_valid=False,
                        error=error_msg
                    )
            
            return FlowReading(
                totalizer_liters=0.0,
                flow_rate_l_per_s=0.0,
                timestamp=now,
                is_valid=False,
                error=error_msg
            )
    
    @property
    def last_reading(self) -> Optional[FlowReading]:
        """Get the last successful reading."""
        with self._lock:
            return self._last_reading
    
    @property
    def is_disconnected(self) -> bool:
        """Check if flow meter appears disconnected."""
        with self._lock:
            if not self._last_reading:
                return True
            return (time.time() - self._last_reading.timestamp) > self.timeout


def calculate_trigger_threshold(flow_rate_l_per_s: float) -> float:
    """
    Calculate shutoff trigger threshold based on flow rate.
    
    Uses calibration curve to predict coast distance after relay activation.
    
    Args:
        flow_rate_l_per_s: Current flow rate in liters per second
        
    Returns:
        Gallons before target to trigger shutoff
    """
    flow_rate_gpm = flow_rate_l_per_s * config.LITERS_PER_SEC_TO_GPM
    predicted_coast = config.FLOW_CURVE_SLOPE * flow_rate_gpm + config.FLOW_CURVE_INTERCEPT
    return max(predicted_coast, 0.1)
