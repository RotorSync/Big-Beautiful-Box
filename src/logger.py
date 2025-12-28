#!/usr/bin/env python3
"""
Centralized logging for IOL Dashboard.

Provides structured logging with:
- Console output (INFO and above)
- File logging with rotation
- Component-specific loggers
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from typing import Optional


class DashboardFormatter(logging.Formatter):
    """Custom formatter with timestamp and level."""

    def format(self, record):
        # Add timestamp in consistent format
        record.timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return super().format(record)


def setup_logging(
    log_file: str,
    level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,  # 5MB
    backup_count: int = 3,
    console_output: bool = True
) -> logging.Logger:
    """
    Set up the root logger with file and console handlers.

    Args:
        log_file: Path to the main log file
        level: Logging level (default INFO)
        max_bytes: Maximum log file size before rotation
        backup_count: Number of backup files to keep
        console_output: Whether to also log to console

    Returns:
        The configured root logger
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear any existing handlers
    root_logger.handlers.clear()

    # Create formatter
    formatter = DashboardFormatter(
        '%(timestamp)s [%(levelname)s] %(name)s: %(message)s'
    )

    # File handler with rotation
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Could not set up file logging: {e}", file=sys.stderr)

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific component.

    Args:
        name: Component name (e.g., 'flow_meter', 'serial', 'gpio')

    Returns:
        Logger instance for the component
    """
    return logging.getLogger(f"iol_dashboard.{name}")


class FileLogger:
    """
    Simple file logger for component-specific logs (e.g., relay, button).

    Provides append-only logging to a dedicated file.
    """

    def __init__(self, log_file: str):
        """
        Initialize file logger.

        Args:
            log_file: Path to the log file
        """
        self.log_file = log_file

    def log(self, message: str, prefix: str = "") -> None:
        """
        Write a log message to the file.

        Args:
            message: Message to log
            prefix: Optional prefix (e.g., 'SUCCESS', 'ERROR')
        """
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(self.log_file, 'a') as f:
                if prefix:
                    f.write(f"{timestamp} [{prefix}] {message}\n")
                else:
                    f.write(f"{timestamp} {message}\n")
        except Exception:
            pass  # Silently ignore logging errors

    def separator(self) -> None:
        """Write a separator line to the log."""
        try:
            with open(self.log_file, 'a') as f:
                f.write(f"\n{'='*60}\n")
        except Exception:
            pass

    def write_raw(self, text: str) -> None:
        """Write raw text to the log file."""
        try:
            with open(self.log_file, 'a') as f:
                f.write(text)
        except Exception:
            pass
