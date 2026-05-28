#!/usr/bin/env python3
"""Tests for flow curve learning and factory fallback behavior."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src import flow_curve


def test_factory_curve_uses_piecewise_config_values():
    curve = flow_curve.FlowCurve.factory()
    assert curve.split_gpm == config.FLOW_CURVE_SPLIT_GPM
    assert curve.threshold_gpm(60) > curve.threshold_gpm(30)
    assert curve.threshold_gpm(90) > curve.threshold_gpm(60)


def test_manual_fill_is_not_learning_sample():
    sample, reason = flow_curve.make_confirmed_auto_sample(
        requested_gallons=20,
        actual_gallons=20.2,
        flow_gpm=70,
        threshold_gallons=1.8,
        shutoff_type="Manual",
    )
    assert sample is None
    assert "not auto" in reason


def test_last_three_auto_samples_save_learned_proposal_only(tmp_path):
    sample_path = str(tmp_path / "samples.json")
    proposal_path = str(tmp_path / "proposal.json")
    override_path = str(tmp_path / "override.json")

    for index, diff in enumerate([0.2, 0.3, 0.4]):
        sample, reason = flow_curve.make_confirmed_auto_sample(
            requested_gallons=20,
            actual_gallons=20 + diff,
            flow_gpm=65 + index,
            threshold_gallons=flow_curve.calculate_trigger_threshold_gpm(65 + index),
            shutoff_type="Auto",
            timestamp=1000 + index,
        )
        assert sample is not None, reason
        result = flow_curve.record_learning_sample(sample_path, proposal_path, sample)

    assert result["proposal_saved"] is True
    assert not os.path.exists(override_path)

    proposal = flow_curve.load_curve_proposal(proposal_path)
    assert proposal is not None
    assert proposal["source"] == "pending_last_confirmed_auto_fills"

    accepted = flow_curve.accept_curve_proposal(proposal_path, override_path)
    assert accepted["source"] == "learned_last_confirmed_auto_fills"
    assert flow_curve.load_curve_proposal(proposal_path) is None
    learned, metadata = flow_curve.load_curve_override(override_path)
    assert metadata["source"] == "learned"
    assert learned.low_intercept > flow_curve.FlowCurve.factory().low_intercept
    assert learned.high_intercept > flow_curve.FlowCurve.factory().high_intercept


def test_reset_learning_archives_files(tmp_path):
    sample_path = str(tmp_path / "samples.json")
    proposal_path = str(tmp_path / "proposal.json")
    override_path = str(tmp_path / "override.json")
    for path in (sample_path, proposal_path, override_path):
        with open(path, "w") as f:
            f.write("{}")

    archived = flow_curve.reset_learning(sample_path, proposal_path, override_path)

    assert len(archived) == 3
    assert not os.path.exists(sample_path)
    assert not os.path.exists(proposal_path)
    assert not os.path.exists(override_path)
    assert all(os.path.exists(path) for path in archived)
