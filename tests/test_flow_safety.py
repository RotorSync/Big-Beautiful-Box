import config
from src.flow_safety import (
    negative_flow_status,
    negative_totalizer_status,
    positive_drift_status,
)


def test_negative_totalizer_fault_latches_above_one_gallon():
    status = negative_totalizer_status(
        signed_totalizer_liters=-2 / config.LITERS_TO_GALLONS,
        liters_to_gallons=config.LITERS_TO_GALLONS,
        fault_threshold_gallons=1.0,
        clear_threshold_gallons=0.05,
    )

    assert status.fault is True
    assert status.reset_clear is False
    assert status.signed_gallons == -2
    assert status.reason == "NEGATIVE FLOW METER -2.0 GAL - RESET REQUIRED"


def test_negative_totalizer_fault_requires_reset_near_zero_to_clear():
    drifting = negative_totalizer_status(
        signed_totalizer_liters=-0.5 / config.LITERS_TO_GALLONS,
        liters_to_gallons=config.LITERS_TO_GALLONS,
        fault_threshold_gallons=1.0,
        clear_threshold_gallons=0.05,
    )
    reset = negative_totalizer_status(
        signed_totalizer_liters=-0.02 / config.LITERS_TO_GALLONS,
        liters_to_gallons=config.LITERS_TO_GALLONS,
        fault_threshold_gallons=1.0,
        clear_threshold_gallons=0.05,
    )

    assert drifting.fault is False
    assert drifting.reset_clear is False
    assert reset.fault is False
    assert reset.reset_clear is True


def test_negative_flow_fault_requires_sustained_negative_flow():
    status = negative_flow_status(
        flow_rate_l_per_s=-1 / config.LITERS_PER_SEC_TO_GPM,
        negative_flow_elapsed_seconds=5.1,
        liters_per_sec_to_gpm=config.LITERS_PER_SEC_TO_GPM,
        fault_threshold_gpm=0.25,
        min_negative_flow_seconds=5.0,
    )

    assert status.negative_flow is True
    assert status.fault is True
    assert round(status.flow_gpm, 1) == -1.0
    assert status.reason == "NEGATIVE FLOW METER -1.0 GPM FOR 5S - GALLON RESET REQUIRED"


def test_negative_flow_ignores_noise_and_short_duration():
    noise = negative_flow_status(
        flow_rate_l_per_s=-0.1 / config.LITERS_PER_SEC_TO_GPM,
        negative_flow_elapsed_seconds=10.0,
        liters_per_sec_to_gpm=config.LITERS_PER_SEC_TO_GPM,
        fault_threshold_gpm=0.25,
        min_negative_flow_seconds=5.0,
    )
    too_soon = negative_flow_status(
        flow_rate_l_per_s=-1 / config.LITERS_PER_SEC_TO_GPM,
        negative_flow_elapsed_seconds=4.9,
        liters_per_sec_to_gpm=config.LITERS_PER_SEC_TO_GPM,
        fault_threshold_gpm=0.25,
        min_negative_flow_seconds=5.0,
    )

    assert noise.negative_flow is False
    assert noise.fault is False
    assert too_soon.negative_flow is True
    assert too_soon.fault is False


def test_positive_drift_fault_requires_low_flow_time_and_three_gallon_gain():
    status = positive_drift_status(
        baseline_totalizer_liters=10 / config.LITERS_TO_GALLONS,
        current_totalizer_liters=13.2 / config.LITERS_TO_GALLONS,
        flow_rate_l_per_s=14 / config.LITERS_PER_SEC_TO_GPM,
        low_flow_elapsed_seconds=10.1,
        liters_to_gallons=config.LITERS_TO_GALLONS,
        liters_per_sec_to_gpm=config.LITERS_PER_SEC_TO_GPM,
        low_flow_threshold_gpm=15.0,
        drift_threshold_gallons=3.0,
        min_low_flow_seconds=10.0,
    )

    assert status.low_flow is True
    assert status.fault is True
    assert round(status.drift_gallons, 1) == 3.2
    assert status.reason == "FLOW METER DRIFT +3.2 GAL BELOW 15 GPM - GALLON RESET REQUIRED"


def test_positive_drift_does_not_fault_before_ten_seconds_or_above_flow_limit():
    too_soon = positive_drift_status(
        baseline_totalizer_liters=0.0,
        current_totalizer_liters=4 / config.LITERS_TO_GALLONS,
        flow_rate_l_per_s=14 / config.LITERS_PER_SEC_TO_GPM,
        low_flow_elapsed_seconds=9.9,
        liters_to_gallons=config.LITERS_TO_GALLONS,
        liters_per_sec_to_gpm=config.LITERS_PER_SEC_TO_GPM,
        low_flow_threshold_gpm=15.0,
        drift_threshold_gallons=3.0,
        min_low_flow_seconds=10.0,
    )
    real_flow = positive_drift_status(
        baseline_totalizer_liters=0.0,
        current_totalizer_liters=4 / config.LITERS_TO_GALLONS,
        flow_rate_l_per_s=15.1 / config.LITERS_PER_SEC_TO_GPM,
        low_flow_elapsed_seconds=10.1,
        liters_to_gallons=config.LITERS_TO_GALLONS,
        liters_per_sec_to_gpm=config.LITERS_PER_SEC_TO_GPM,
        low_flow_threshold_gpm=15.0,
        drift_threshold_gallons=3.0,
        min_low_flow_seconds=10.0,
    )

    assert too_soon.fault is False
    assert real_flow.low_flow is False
    assert real_flow.fault is False


def test_positive_drift_threshold_treats_fifteen_gpm_as_real_flow():
    status = positive_drift_status(
        baseline_totalizer_liters=0.0,
        current_totalizer_liters=4 / config.LITERS_TO_GALLONS,
        flow_rate_l_per_s=15 / config.LITERS_PER_SEC_TO_GPM,
        low_flow_elapsed_seconds=10.1,
        liters_to_gallons=config.LITERS_TO_GALLONS,
        liters_per_sec_to_gpm=config.LITERS_PER_SEC_TO_GPM,
        low_flow_threshold_gpm=15.0,
        drift_threshold_gallons=3.0,
        min_low_flow_seconds=10.0,
    )

    assert status.low_flow is False
    assert status.fault is False
