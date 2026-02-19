#!/usr/bin/env python3
"""
Enhanced logging module with rotation support.

Provides:
- Rotating file handler (max 10MB, 5 backups)
- Consistent log format
- Thread-safe logging
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

# Default log settings
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB
DEFAULT_BACKUP_COUNT = 5
DEFAULT_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
DEFAULT_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


def setup_logger(
    name: str,
    log_file: Optional[str] = None,
    level: int = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    console: bool = True
) -> logging.Logger:
    """
    Set up a logger with rotation support.
    
    Args:
        name: Logger name
        log_file: Path to log file (None for console only)
        level: Logging level
        max_bytes: Max file size before rotation
        backup_count: Number of backup files to keep
        console: Whether to also log to console
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    formatter = logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT)
    
    # Console handler
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # File handler with rotation
    if log_file:
        # Ensure directory exists
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get an existing logger by name."""
    return logging.getLogger(name)


# Pre-configured loggers for common use cases
def get_main_logger() -> logging.Logger:
    """Get the main dashboard logger with rotation."""
    return setup_logger(
        'dashboard',
        log_file='/home/pi/iol_dashboard.log',
        level=logging.INFO,
        max_bytes=10 * 1024 * 1024,  # 10MB
        backup_count=3
    )


def get_serial_logger() -> logging.Logger:
    """Get serial debug logger with rotation."""
    return setup_logger(
        'serial',
        log_file='/home/pi/serial_debug.log',
        level=logging.DEBUG,
        max_bytes=5 * 1024 * 1024,  # 5MB
        backup_count=2
    )


def get_relay_logger() -> logging.Logger:
    """Get relay test logger with rotation."""
    return setup_logger(
        'relay',
        log_file='/home/pi/relay_test.log',
        level=logging.DEBUG,
        max_bytes=5 * 1024 * 1024,  # 5MB
        backup_count=2
    )


def get_button_logger() -> logging.Logger:
    """Get button debug logger with rotation."""
    return setup_logger(
        'button',
        log_file='/home/pi/button_debug.log',
        level=logging.DEBUG,
        max_bytes=5 * 1024 * 1024,  # 5MB
        backup_count=2
    )
