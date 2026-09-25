"""Small local action/vision-zone recorder used by the operator console."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import threading


def annotate_zones(frame, zones: dict) -> bytes | None:
    """Draw the saved grab windows onto a copy of the frame.

    Best effort by design: a missing cv2, a frame that is not an image, or an
    encoder failure must never cost the operator the package they just taught.
    """
    if not zones or frame is None:
        return None
    try:
        import cv2
        canvas = frame.copy()
        for name, zone in zones.items():
            x, y = int(zone.get("x", 0)), int(zone.get("y", 0))
            width, height = int(zone.get("width", 0)), int(zone.get("height", 0))
            cv2.rectangle(canvas, (x, y), (x + width, y + height), (92, 206, 255), 3)
            cv2.putText(canvas, str(name), (x, max(26, y - 12)), cv2.FONT_HERSHEY_SIMPLEX, .8, (92, 206, 255), 2, cv2.LINE_AA)
        ok, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 88])
        return encoded.tobytes() if ok else None
    except Exception:
        return None


class ActionRecorder:
    def __init__(self, path: str | Path, repository=None, capture_service=None, camera_service=None, chassis_service=None, arm_service=None, line_service=None):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._active: dict | None = None
        self._draft: dict | None = None
        self._zones: dict[str, dict] = {}
        self.repository = repository
        self.capture_service = capture_service
        self.camera_service = camera_service
        self.chassis_service = chassis_service
        self.arm_service = arm_service
        self.line_service = line_service

    def status(self) -> dict:
        with self._lock:
            return {
                "recording": self._active is not None,
                "action": self._active,
                "draft": self._draft,
                "zones": dict(self._zones),
            }

    def start(self, name: str | None = None) -> dict:
        """Open a package.  No name means the operator names it at the end.

        The console flow is press-to-start / press-again-to-name, so the name
        is only known after the fact.  An explicit name keeps the immediate-save
        path for callers (and tests) that already know what they are recording.
        """
        with self._lock:
            if self._active is not None:
                raise RuntimeError("an action recording is already active")
            if self._draft is not None:
                raise RuntimeError("the previous action package still needs a name: name it or discard it first")
            explicit = str(name).strip() if name is not None else ""
            self._active = {
                "name": explicit or f"动作包_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "named": bool(explicit),
                "protocol": "RG-ARM-1",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "steps": [],
                "zones": {},
            }
            return self.status()

    def append(self, kind: str, payload: dict) -> None:
        with self._lock:
            if self._active is not None:
                self._active["steps"].append({"kind": kind, **payload, "timestamp": datetime.now(timezone.utc).isoformat()})

    def save_zone(self, name: str, rect: dict, snapshot: str | None = None) -> dict:
        required = ("x", "y", "width", "height")
        if any(key not in rect for key in required):
            raise ValueError("zone requires x, y, width and height")
        zone = {key: float(rect[key]) for key in required}
        if any(value < 0 for value in zone.values()):
            raise ValueError("zone values must be non-negative")
        if snapshot:
            zone["snapshot"] = str(snapshot)
        with self._lock:
            self._zones[str(name)] = zone
            if self._active is not None:
                self._active["zones"][str(name)] = zone
            return self.status()

    def stop(self) -> dict:
        """Compatibility alias for callers that still use the old endpoint."""
        return self.finish()

    def finish(self) -> dict:
        with self._lock:
            if self._active is None:
                raise RuntimeError("no action recording is active")
            self._draft = self._active
            self._draft["finished_at"] = datetime.now(timezone.utc).isoformat()
            self._active = None
            if not self._draft.get("named"):
                # Hold it as a draft: the console prompts for the name and then
                # confirms.  Saving here would either invent a name or race the
                # operator's answer.
                return self.status()
            return self._save_draft()

    def confirm_draft(self) -> dict:
        with self._lock:
            return self._save_draft()

    def _save_draft(self) -> dict:
        """Persist the completed action as one package, retaining its draft on failure."""
        action = self._require_draft()
        snapshot = self.camera_service.latest_snapshot() if self.camera_service is not None else {}
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        chassis = self.chassis_service.status() if self.chassis_service is not None else {}
        arm = self.arm_service.status() if self.arm_service is not None else {}
        line = self.line_service.status() if self.line_service is not None else {}
        jpeg = snapshot.get("jpeg")
        # A5: the package carries its own photo, and the console's snapshot
        # folder keeps a copy so "download the latest photo" matches the last
        # saved package.  Neither may be allowed to fail the save: the camera is
        # optional during teaching, and a taught package is expensive to redo.
        if self.camera_service is not None and hasattr(self.camera_service, "snapshot"):
            try:
                self.camera_service.snapshot()
            except Exception:
                pass
        annotated = annotate_zones(snapshot.get("frame"), action.get("zones", {}))
        if self.capture_service is not None:
            saved = self.capture_service.save_arm(
                action,
                image=jpeg,
                annotated_image=annotated,
                chassis=chassis,
                zones=action.get("zones", {}),
                arm=arm,
                line=line,
            )
            if isinstance(saved, dict):
                action["session_id"] = saved.get("session_id")
                action["package_index"] = saved.get("package_index")
                action["package_dir"] = saved.get("export_dir")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = []
        if not isinstance(data, list):
            data = []
        data.append(action)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.repository is not None:
            self.repository.save_action(action)
        self._draft = None
        return {"saved": action, **self.status()}

    def set_draft_name(self, name: str) -> None:
        with self._lock:
            action = self._require_draft()
            name = str(name).strip()
            if not name:
                raise ValueError("title is required")
            action["name"] = name
            action["named"] = True

    def discard_draft(self) -> dict:
        with self._lock:
            # Idempotent: an explicit name already saved on finish, so a late
            # discard from an older client has nothing left to throw away.
            if self._draft is None:
                return self.status()
            self._draft = None
            return self.status()

    def _require_draft(self) -> dict:
        if self._draft is None:
            raise RuntimeError("no action draft is awaiting confirmation")
        return self._draft

    def export(self) -> dict:
        with self._lock:
            return {"path": str(self.path), "actions": json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else []}

    def actions(self) -> list[dict]:
        with self._lock:
            if self.repository is not None:
                return list(self.repository.load().get("actions", []))
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else []
