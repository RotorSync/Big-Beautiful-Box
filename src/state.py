#!/usr/bin/env python3
"""
Thread-safe state management for the IOL Dashboard.

Provides a centralized, thread-safe store for all global state.
Uses locks to prevent race conditions between GUI and background threads.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, List
import config


@dataclass
class FlowState:
    """Flow meter state."""
    totalizer_liters: float = 0.0
    flow_rate_l_per_s: float = 0.0
    is_connected: bool = False
    error_message: str = ""
    last_read_time: float = field(default_factory=time.time)
    
    @property
    def totalizer_gallons(self) -> float:
        return self.totalizer_liters * config.LITERS_TO_GALLONS
    
    @property
    def flow_rate_gpm(self) -> float:
        return self.flow_rate_l_per_s * config.LITERS_PER_SEC_TO_GPM
    
    @property
    def is_flowing(self) -> bool:
        return self.flow_rate_l_per_s >= config.FLOW_STOPPED_THRESHOLD
    
    @property
    def is_disconnected(self) -> bool:
        return (time.time() - self.last_read_time) > config.FLOW_METER_TIMEOUT


@dataclass
class SerialState:
    """Serial communication state."""
    is_connected: bool = False
    last_heartbeat: float = 0.0
    last_command: str = ""
    command_received: bool = False
    
    @property
    def heartbeat_ok(self) -> bool:
        if self.last_heartbeat == 0:
            return False
        return (time.time() - self.last_heartbeat) < 11.0


@dataclass
class FillState:
    """Fill operation state."""
    requested_gallons: float = config.REQUESTED_GALLONS
    pending_gallons: float = 0.0
    pending_requested: float = 0.0
    pending_shutoff_type: str = ""
    was_flowing: bool = False
    alert_triggered: bool = False
    colors_green: bool = False


@dataclass
class ModeState:
    """Operating mode state."""
    current_mode: str = "fill"  # "fill" or "mix"
    fill_preset: float = config.REQUESTED_GALLONS
    mix_preset: float = 40.0
    override_enabled: bool = False
    override_enabled_time: float = 0.0


@dataclass
class TotalsState:
    """Daily and season totals."""
    daily: float = 0.0
    season: float = 0.0
    last_reset_date: Optional[str] = None


class DashboardState:
    """
    Thread-safe central state store for the dashboard.
    
    All state access goes through this class with proper locking.
    """
    
    def __init__(self):
        self._lock = threading.RLock()
        
        # State containers
        self.flow = FlowState()
        self.serial = SerialState()
        self.fill = FillState()
        self.mode = ModeState()
        self.totals = TotalsState()
        
        # Callbacks for state changes
        self._callbacks: List[Callable[[], None]] = []
    
    def __enter__(self):
        """Context manager for locked access."""
        self._lock.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()
        return False
    
    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback for state changes."""
        with self._lock:
            self._callbacks.append(callback)
    
    def notify_change(self) -> None:
        """Notify all registered callbacks of a state change."""
        with self._lock:
            callbacks = self._callbacks.copy()
        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass
    
    # Flow state accessors
    def update_flow(
        self,
        totalizer_liters: Optional[float] = None,
        flow_rate: Optional[float] = None,
        connected: Optional[bool] = None,
        error: Optional[str] = None
    ) -> None:
        """Update flow meter state."""
        with self._lock:
            if totalizer_liters is not None:
                self.flow.totalizer_liters = totalizer_liters
            if flow_rate is not None:
                self.flow.flow_rate_l_per_s = flow_rate
            if connected is not None:
                self.flow.is_connected = connected
            if error is not None:
                self.flow.error_message = error
            self.flow.last_read_time = time.time()
    
    # Serial state accessors
    def update_serial(
        self,
        connected: Optional[bool] = None,
        heartbeat: bool = False,
        command: Optional[str] = None
    ) -> None:
        """Update serial state."""
        with self._lock:
            if connected is not None:
                self.serial.is_connected = connected
            if heartbeat:
                self.serial.last_heartbeat = time.time()
            if command is not None:
                self.serial.last_command = command
                self.serial.command_received = True
    
    # Fill state accessors
    def get_requested_gallons(self) -> float:
        """Get current requested gallons based on mode."""
        with self._lock:
            if self.mode.current_mode == "fill":
                return self.mode.fill_preset
            else:
                return self.mode.mix_preset
    
    def set_requested_gallons(self, value: float) -> None:
        """Set requested gallons for current mode."""
        with self._lock:
            if self.mode.current_mode == "fill":
                self.mode.fill_preset = value
            else:
                self.mode.mix_preset = value
            self.fill.requested_gallons = value
    
    def adjust_requested(self, delta: float) -> float:
        """Adjust requested gallons by delta, return new value."""
        with self._lock:
            new_value = max(0, self.fill.requested_gallons + delta)
            self.set_requested_gallons(new_value)
            return new_value
    
    # Mode accessors
    def switch_mode(self, new_mode: str) -> None:
        """Switch between fill and mix modes."""
        with self._lock:
            if new_mode not in ("fill", "mix"):
                return
            if new_mode == self.mode.current_mode:
                return
            
            # Save current to preset
            if self.mode.current_mode == "fill":
                self.mode.fill_preset = self.fill.requested_gallons
            else:
                self.mode.mix_preset = self.fill.requested_gallons
            
            # Switch
            self.mode.current_mode = new_mode
            
            # Load new preset
            if new_mode == "fill":
                self.fill.requested_gallons = self.mode.fill_preset
            else:
                self.fill.requested_gallons = self.mode.mix_preset
            
            # Reset colors
            self.fill.colors_green = False
    
    def set_override(self, enabled: bool) -> None:
        """Set override mode."""
        with self._lock:
            self.mode.override_enabled = enabled
            if enabled:
                self.mode.override_enabled_time = time.time()
    
    # Totals accessors
    def add_to_totals(self, gallons: float) -> None:
        """Add gallons to daily and season totals."""
        with self._lock:
            self.totals.daily += gallons
            self.totals.season += gallons
    
    def reset_daily_total(self) -> None:
        """Reset daily total."""
        with self._lock:
            self.totals.daily = 0.0
            self.totals.last_reset_date = time.strftime('%Y-%m-%d')
    
    def reset_season_total(self) -> None:
        """Reset season total."""
        with self._lock:
            self.totals.season = 0.0


# Global state instance
_state: Optional[DashboardState] = None


def get_state() -> DashboardState:
    """Get the global state instance."""
    global _state
    if _state is None:
        _state = DashboardState()
    return _state
