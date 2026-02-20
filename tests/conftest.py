#!/usr/bin/env python3
"""
Pytest configuration and shared fixtures.
"""

import sys
import os

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pytest


@pytest.fixture
def fresh_state():
    """Provide a fresh DashboardState instance."""
    from src.state import DashboardState
    return DashboardState()


@pytest.fixture
def mock_config(monkeypatch):
    """Provide mock config values for testing."""
    import config
    
    # Store original values
    original = {
        'FLOW_STOPPED_THRESHOLD': config.FLOW_STOPPED_THRESHOLD,
        'LITERS_TO_GALLONS': config.LITERS_TO_GALLONS,
        'LITERS_PER_SEC_TO_GPM': config.LITERS_PER_SEC_TO_GPM,
    }
    
    yield config
    
    # Restore (not strictly necessary with monkeypatch, but explicit)
    for key, val in original.items():
        setattr(config, key, val)
