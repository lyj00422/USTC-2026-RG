from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import zipfile
import uuid
import os


class CalibrationBundleRepository:
    """Versioned calibration exchange package shared by console and route runner."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / "calibration_bundle.json"
        self.screenshots = self.root / "screenshots"
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        if not self.path.exists():
            return {"schema_version": 1, "bundle_id": str(uuid.uuid4()), "created_at": _now(), "protocol": {}, "locations": [], "zones": [], "actions": [], "workflow": []}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("unsupported calibration bundle schema")
        return value

    def save_location(self, name, command_pose, telemetry, apriltags, screenshot=None, *, role="unknown", tag_status=None):
        name = str(name).strip()
        if not name:
            raise ValueError("location name is required")
        bundle = self.load()
        location = {"id": _slug(name), "name": name, "role": role, "command_pose": dict(command_pose or {}), "telemetry": dict(telemetry or {}), "apriltags": list(apriltags or []), "tag_status": tag_status or ("detected" if apriltags else "not_detected"), "captured_at": _now()}
        if screenshot:
            target = self._copy_screenshot(screenshot, location["id"])
            location["screenshot"] = str(target.relative_to(self.root)).replace("\\", "/")
        bundle["locations"] = [item for item in bundle["locations"] if item.get("id") != location["id"]] + [location]
        self._write(bundle)
        return location

    def save_zone(self, zone_id, color, rect, action_id=None, *, tolerance_px=0):
        required = ("x", "y", "width", "height")
        if any(key not in rect for key in required):
            raise ValueError("zone requires x, y, width and height")
        values = {key: float(rect[key]) for key in required}
        if any(value < 0 for value in values.values()) or values["width"] <= 0 or values["height"] <= 0:
            raise ValueError("zone dimensions must be positive")
        bundle = self.load()
        zone = {"id": str(zone_id), "color": str(color), "rect": values, "tolerance_px": float(tolerance_px), "action_id": action_id}
        bundle["zones"] = [item for item in bundle["zones"] if item.get("id") != zone["id"]] + [zone]
        self._write(bundle)
        return zone

    def save_action(self, action: dict):
        if not action.get("id"):
            action = {**action, "id": _slug(action.get("name", "action"))}
        bundle = self.load()
        bundle["actions"] = [item for item in bundle["actions"] if item.get("id") != action["id"]] + [dict(action)]
        self._write(bundle)
        return action

    def set_workflow(self, workflow):
        bundle = self.load()
        bundle["workflow"] = list(workflow)
        self._write(bundle)
        return bundle["workflow"]

    def export_zip(self, destination: str | Path | None = None) -> Path:
        destination = Path(destination) if destination else self.root.parent / f"{self.root.name}.zip"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            if self.path.exists():
                archive.write(self.path, "calibration_bundle.json")
            else:
                archive.writestr("calibration_bundle.json", json.dumps(self.load(), ensure_ascii=False, indent=2))
            for file in self.root.rglob("*"):
                if file.is_file() and file != self.path and destination.resolve() != file.resolve():
                    archive.write(file, file.relative_to(self.root).as_posix())
            archive.writestr("README.txt", "This exchange package must be reviewed and converted by the electrical-control firmware team.\n")
        return destination

    def _copy_screenshot(self, screenshot, location_id):
        self.screenshots.mkdir(parents=True, exist_ok=True)
        target = self.screenshots / f"{location_id}.jpg"
        if isinstance(screenshot, (bytes, bytearray)):
            target.write_bytes(bytes(screenshot))
        else:
            source = Path(screenshot).resolve()
            if not source.is_file():
                raise FileNotFoundError(str(source))
            target.write_bytes(source.read_bytes())
        return target

    def _write(self, value):
        self.root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="bundle-", suffix=".json", dir=self.root)
        os.close(fd)
        Path(name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        Path(name).replace(self.path)
        try:
            Path(name).unlink(missing_ok=True)
        except OSError:
            pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _slug(value):
    cleaned = "".join(char.lower() if char.isalnum() else "_" for char in str(value)).strip("_")
    return cleaned or "item"
