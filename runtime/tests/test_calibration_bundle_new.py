import json
from pathlib import Path
from control_hub.services.calibration_bundle import CalibrationBundleRepository

def test_bundle_location_zone_action_and_export(tmp_path):
    repo = CalibrationBundleRepository(tmp_path / "bundle")
    loc = repo.save_location("pickup_1", {"forward_cm": 100}, {"encoder": {"lf": 1}}, [{"id": 7}], b"jpg")
    assert loc["name"] == "pickup_1"
    repo.save_zone("purple", "purple", {"x": 1, "y": 2, "width": 3, "height": 4}, "a1")
    repo.save_action({"id": "a1", "name": "pickup", "steps": []})
    loaded = repo.load()
    assert loaded["locations"][0]["apriltags"][0]["id"] == 7
    out = repo.export_zip(tmp_path / "out.zip")
    assert out.is_file()
    import zipfile
    with zipfile.ZipFile(out) as z:
        assert "calibration_bundle.json" in z.namelist()

