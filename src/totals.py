#!/usr/bin/env python3
"""
Fill totals tracking and persistence.

Tracks:
- Daily total gallons pumped (auto-resets at midnight)
- Season total gallons pumped (manual reset)
- Fill history logging
"""

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class FillRecord:
    """Record of a completed fill."""
    timestamp: datetime
    requested_gallons: float
    actual_gallons: float
    shutoff_type: str  # "auto" or "manual"

    @property
    def difference(self) -> float:
        """Get difference between actual and requested."""
        return self.actual_gallons - self.requested_gallons


class TotalsTracker:
    """
    Tracks and persists fill totals.

    Manages daily and season totals with automatic daily reset
    and file-based persistence.
    """

    def __init__(
        self,
        daily_file: str,
        season_file: str,
        history_log: str,
        daily_log: str
    ):
        """
        Initialize totals tracker.

        Args:
            daily_file: Path to daily total file
            season_file: Path to season total file
            history_log: Path to fill history log
            daily_log: Path to daily totals log
        """
        self.daily_file = daily_file
        self.season_file = season_file
        self.history_log = history_log
        self.daily_log = daily_log

        self._daily_total: float = 0.0
        self._season_total: float = 0.0
        self._last_reset_date: Optional[str] = None

        # Load existing totals
        self._load()

    def _load(self) -> None:
        """Load totals from files."""
        # Load daily total
        try:
            with open(self.daily_file, 'r') as f:
                lines = f.readlines()
                if len(lines) >= 2:
                    self._daily_total = float(lines[0].strip())
                    self._last_reset_date = lines[1].strip()
        except FileNotFoundError:
            logger.info("No daily total file found, starting fresh")
        except Exception as e:
            logger.error(f"Error loading daily total: {e}")

        # Load season total
        try:
            with open(self.season_file, 'r') as f:
                self._season_total = float(f.read().strip())
        except FileNotFoundError:
            logger.info("No season total file found, starting fresh")
        except Exception as e:
            logger.error(f"Error loading season total: {e}")

        # Check if daily reset is needed
        self._check_daily_reset()

    def _save(self) -> None:
        """Save totals to files."""
        # Save daily total
        try:
            with open(self.daily_file, 'w') as f:
                f.write(f"{self._daily_total}\n")
                f.write(f"{self._last_reset_date}\n")
        except Exception as e:
            logger.error(f"Error saving daily total: {e}")

        # Save season total
        try:
            with open(self.season_file, 'w') as f:
                f.write(f"{self._season_total}\n")
        except Exception as e:
            logger.error(f"Error saving season total: {e}")

    def _check_daily_reset(self) -> None:
        """Check if daily total should be reset (new day)."""
        today = datetime.now().strftime('%Y-%m-%d')

        if self._last_reset_date != today:
            # Log yesterday's total if it was non-zero
            if self._daily_total > 0 and self._last_reset_date:
                try:
                    with open(self.daily_log, 'a') as f:
                        f.write(f"{self._last_reset_date}: {self._daily_total:.2f} gallons\n")
                except Exception as e:
                    logger.error(f"Error logging daily total: {e}")

            # Reset daily total
            logger.info(f"Daily reset: {self._daily_total:.2f} gal -> 0")
            self._daily_total = 0.0
            self._last_reset_date = today
            self._save()

    @property
    def daily_total(self) -> float:
        """Get current daily total in gallons."""
        self._check_daily_reset()
        return self._daily_total

    @property
    def season_total(self) -> float:
        """Get current season total in gallons."""
        return self._season_total

    def add_fill(self, record: FillRecord) -> None:
        """
        Record a completed fill.

        Args:
            record: Fill record to add
        """
        # Add to totals
        self._daily_total += record.actual_gallons
        self._season_total += record.actual_gallons

        # Log to history
        try:
            with open(self.history_log, 'a') as f:
                f.write(
                    f"{record.timestamp.strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"Requested: {record.requested_gallons:.3f} gal | "
                    f"Actual: {record.actual_gallons:.3f} gal | "
                    f"Diff: {record.difference:+.3f} gal | "
                    f"{record.shutoff_type}\n"
                )
        except Exception as e:
            logger.error(f"Error logging fill: {e}")

        # Save totals
        self._save()

        logger.info(
            f"Fill recorded: {record.actual_gallons:.2f} gal "
            f"(Daily: {self._daily_total:.2f}, Season: {self._season_total:.2f})"
        )

    def add_gallons(self, gallons: float) -> None:
        """
        Add gallons to both totals (simple addition).

        Args:
            gallons: Gallons to add
        """
        self._daily_total += gallons
        self._season_total += gallons
        self._save()

    def reset_season(self) -> float:
        """
        Reset season total.

        Returns:
            Previous season total
        """
        previous = self._season_total
        self._season_total = 0.0
        self._save()
        logger.info(f"Season total reset: {previous:.2f} -> 0")
        return previous

    def get_history(self, lines: int = 100) -> str:
        """
        Get recent fill history.

        Args:
            lines: Number of lines to return

        Returns:
            History text
        """
        try:
            with open(self.history_log, 'r') as f:
                all_lines = f.readlines()
                return ''.join(all_lines[-lines:])
        except FileNotFoundError:
            return "(No fill history found)\n"
        except Exception as e:
            return f"Error reading history: {e}\n"
