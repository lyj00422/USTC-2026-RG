import json

from route_v2.calibration import ProbeSample, run_probe_analysis, write_probe_outputs


def samples(*values):
    return [ProbeSample(timestamp_s=t, lateral_cm=cm, contact=contact)
            for t, cm, contact in values]


def test_strafe_probe_reports_operator_stop_and_braking_slip(tmp_path):
    result = run_probe_analysis(
        samples((0, 0, False), (1, 5, False), (2, 12, False), (2.2, 13.5, False)),
        direction="left",
        suggested_key="vision.pickup_areas.purple.search_left.max_distance_cm",
        hard_max_cm=20,
        hard_timeout_s=10,
        operator_stop_at_cm=12,
    )

    assert result.stop_sent is True
    assert result.motion_cm == 12
    assert result.braking_slip_cm == 1.5
    assert result.reason == "operator_stop"
    assert result.suggested_key.endswith("purple.search_left.max_distance_cm")

    jsonl, summary = write_probe_outputs(tmp_path / "purple-left", result)
    first = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[0])
    assert first["lateral_cm"] == 0
    assert "encoder" in first and "sensor_mask" in first
    assert json.loads(summary.read_text(encoding="utf-8"))["reason"] == "operator_stop"


def test_probe_hard_limit_stops_without_operator_input():
    result = run_probe_analysis(
        samples((0, 0, False), (1, 10, False), (2, 16, False), (2.2, 17, False)),
        direction="right",
        suggested_key="vision.pickup_areas.orange.search_right.max_distance_cm",
        hard_max_cm=15,
        hard_timeout_s=10,
    )

    assert result.reason == "hard_distance_limit"
    assert result.stop_sent is True
    assert result.motion_cm == 16


def test_probe_timeout_and_contact_are_machine_readable():
    timeout = run_probe_analysis(
        samples((0, 0, False), (6, 2, False)), direction="left",
        suggested_key="x", hard_max_cm=10, hard_timeout_s=5,
    )
    contact = run_probe_analysis(
        samples((0, 0, False), (1, 3, True), (1.2, 3.3, True)), direction="right",
        suggested_key="y", hard_max_cm=10, hard_timeout_s=5,
    )
    assert timeout.reason == "hard_timeout"
    assert contact.reason == "contact"
    assert contact.contact_detected is True
