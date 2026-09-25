from pathlib import Path

from tools.build_pi_test_bundle import build_bundle


def test_pi_bundle_contains_runtime_and_excludes_development_assets(tmp_path):
    output = build_bundle(tmp_path / "bundle")
    assert (output / "run_route_v2.py").exists()
    assert (output / "route_v2" / "loop_strategy.py").exists()
    assert (output / "config" / "route_v2.yaml").exists()
    assert not (output / "logs").exists()
    assert not (output / "data" / "route_vision_calibration").exists()
