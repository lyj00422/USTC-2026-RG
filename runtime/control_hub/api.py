"""Pure HTTP application routing for the control hub."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import mimetypes
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .state import LeaseConflict


def _attachment(filename: str) -> str:
    """Content-Disposition for a download whose name may be non-ASCII.

    Chinese package names are normal here, so the UTF-8 form is the real name
    and the ASCII form is only a fallback for clients that ignore RFC 5987.
    """
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "packages.zip"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes
    # Extra headers, so a download can carry its filename instead of the
    # browser naming the file after the URL path.
    headers: tuple[tuple[str, str], ...] = ()
    # A download can hand over a file on disk instead of bytes in memory: the
    # handler streams it in chunks, so a 33 MB recording never becomes a 33 MB
    # string on a Pi that is also running the robot.
    file: Path | None = None

    def payload(self) -> bytes:
        """The body as bytes, for callers that cannot stream (tests, mostly)."""
        return self.file.read_bytes() if self.file is not None else self.body


class HubApplication:
    def __init__(
        self,
        hub_state,
        lease,
        safety,
        arm_service,
        camera_service,
        event_log,
        pickup_service=None,
        *,
        vision_service=None,
        chassis_service=None,
        line_service=None,
        route_capture=None,
        recorder=None,
        bundle=None,
        alignment=None,
        fixed_arm_device: str = "/dev/ttyUSB0",
        fixed_chassis_device: str = "/dev/robogame-chassis",
        static_root: str | Path,
        clock_ms,
    ) -> None:
        self.hub_state = hub_state
        self.lease = lease
        self.safety = safety
        self.arm = arm_service
        self.camera = camera_service
        self.chassis = chassis_service
        self.line = line_service
        self.route_capture = route_capture
        self.event_log = event_log
        self.pickup = pickup_service
        self.vision = vision_service
        self.recorder = recorder
        self.bundle = bundle
        self.alignment = alignment
        self.fixed_arm_device = fixed_arm_device
        self.fixed_chassis_device = fixed_chassis_device
        self.static_root = Path(static_root).resolve()
        self.clock_ms = clock_ms
        # Set by the server at startup: the event-log sequence the console
        # session began at, so its log slice can be exported with the packages.
        self.session_log_sequence = 0

    def handle(self, method: str, target: str, *, body: bytes = b"", headers: dict | None = None) -> Response:
        split = urlsplit(target)
        path = split.path
        # Downloads are plain links, so their options arrive in the query
        # string rather than in a JSON body.
        query = {key: values[0] for key, values in parse_qs(split.query).items() if values}
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        try:
            if path.startswith("/api/"):
                return self._handle_api(method.upper(), path, body, headers, query)
            if method.upper() != "GET":
                return self._json(405, {"ok": False, "code": "METHOD_NOT_ALLOWED"})
            return self._static(path)
        except LeaseConflict as exc:
            return self._error(409, "CONTROL_LEASE_REQUIRED", str(exc))
        except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
            return self._error(400, "BAD_REQUEST", str(exc))
        except RuntimeError as exc:
            return self._error(409, "OPERATION_REJECTED", str(exc))
        except FileNotFoundError as exc:
            return self._error(404, "NOT_FOUND", str(exc))
        except Exception as exc:  # pragma: no cover - defensive boundary
            self.event_log.append("fault", "server", {"message": str(exc), "path": path})
            return self._error(500, "INTERNAL_ERROR", str(exc))

    def _handle_api(self, method: str, path: str, raw_body: bytes, headers: dict, query: dict | None = None) -> Response:
        payload = self._parse_json(raw_body) if method == "POST" else {}
        query = query or {}
        now_ms = self.clock_ms()
        token = headers.get("x-control-token") or payload.get("token")

        if method == "GET" and path == "/api/system/modules":
            return self._json(200, {"ok": True, "modules": [asdict(item) for item in self.hub_state.modules()]})
        if method == "GET" and path == "/api/system/status":
            return self._json(
                200,
                {
                    "ok": True,
                    "arm": self.arm.status(),
                    "chassis": self.chassis.status() if self.chassis is not None else {"connected": False, "state": "UNAVAILABLE"},
                    "line": self.line.status() if self.line is not None else {"connected": False, "state": "UNAVAILABLE"},
                    "camera": self.camera.status(),
                    "safety": {"latched": self.safety.latched, "reason": self.safety.reason, "stop_failed": self.safety.stop_failed},
                    "lease": self.lease.snapshot(now_ms=now_ms),
                },
            )
        if method == "GET" and path == "/api/calibration/bundle":
            return self._json(200, {"ok": True, "bundle": self.bundle.load() if self.bundle else {}})
        if method == "GET" and path == "/api/calibration/locations":
            return self._json(200, {"ok": True, "locations": (self.bundle.load().get("locations", []) if self.bundle else [])})
        if method == "GET" and path == "/api/route-capture/status":
            return self._json(200, {"ok": True, "recording": bool(getattr(self, "_capture_recording", False)), "session_id": getattr(self, "_capture_session_id", None)})
        if method == "POST" and path == "/api/route-capture/start":
            self._require_control(token, now_ms); self._require_safety_clear()
            if self.route_capture is None: return self._error(409, "OPERATION_REJECTED", "route capture is not configured")
            self._capture_session_id = self.route_capture.begin_session(payload.get("title", "route")); self._capture_recording = True
            return self._json(200, {"ok": True, "recording": True, "session_id": self._capture_session_id})
        if method == "POST" and path == "/api/route-capture/stop":
            self._require_control(token, now_ms)
            session_id = self.route_capture.end_session() if self.route_capture else None; self._capture_recording = False; self._capture_session_id = None
            return self._json(200, {"ok": True, "recording": False, "session_id": session_id})
        if method == "POST" and path == "/api/route-capture/capture":
            self._require_control(token, now_ms); self._require_safety_clear()
            if self.route_capture is None: return self._error(409, "OPERATION_REJECTED", "route capture is not configured")
            title = str(payload.get("title", "")).strip()
            if not title: raise ValueError("title is required")
            snap = self.camera.latest_snapshot() if hasattr(self.camera, "latest_snapshot") else {}
            chassis = self.chassis.status() if self.chassis else {}
            line = self.line.status() if self.line else {}
            tags = payload.get("apriltags") or []
            result = self.route_capture.capture_point(title, image=snap.get("jpeg"), chassis=chassis, line=line, apriltags=tags)
            if self.bundle is not None:
                self.bundle.save_location(title, chassis.get("pose") or chassis.get("distance") or {}, chassis, tags, snap.get("jpeg"), role=payload.get("role", "route_point"), tag_status="detected" if tags else "not_detected")
            reset = self.chassis.reset_distance() if self.chassis and hasattr(self.chassis, "reset_distance") else {"supported": False, "message": "software tracking reset is unavailable"}
            return self._json(200, {"ok": True, "point": result, "distance_reset": reset, "encoder_reset": {"supported": False, "message": "firmware encoder reset was not requested"}})
        if method == "GET" and path in {"/api/route-capture/export/camera", "/api/route-capture/export/arm", "/api/route-capture/export/session"}:
            if self.route_capture is None: return self._error(404, "NOT_FOUND", "route capture is not configured")
            kind = "session" if path.endswith("session") else ("camera" if path.endswith("camera") else "arm")
            out = self.route_capture.export(kind)
            return Response(200, "application/zip", b"", headers=(("Content-Disposition", _attachment(out.name)),), file=out)
        if method == "POST" and path == "/api/workflow/validate":
            if self.bundle is None:
                return self._error(409, "OPERATION_REJECTED", "calibration bundle is not configured")
            bundle = self.bundle.load()
            workflow = payload.get("workflow", bundle.get("workflow", []))
            location_ids = {item.get("id") for item in bundle.get("locations", [])}
            zone_ids = {item.get("id") for item in bundle.get("zones", [])}
            action_ids = {item.get("id") for item in bundle.get("actions", [])}
            errors = []
            for index, step in enumerate(workflow):
                if step.get("location_id") not in location_ids: errors.append(f"step {index}: unknown location")
                if step.get("zone_id") not in zone_ids: errors.append(f"step {index}: unknown zone")
                if step.get("action_id") not in action_ids: errors.append(f"step {index}: unknown action")
            return self._json(200, {"ok": not errors, "valid": not errors, "errors": errors, "steps": len(workflow)})
        if method == "GET" and path == "/api/vision/status":
            if self.vision is None:
                return self._json(200, {"ok": True, "sequence": None, "frame_available": False, "apriltags": [], "blocks": [], "error": "vision is not configured"})
            return self._json(200, {"ok": True, **self.vision.status()})
        if method == "GET" and path == "/api/pickup/align":
            return self._json(200, {"ok": True, **(self.alignment.status() if self.alignment else {"state": "UNAVAILABLE"})})
        if method == "POST" and path == "/api/pickup/align":
            self._require_control(token, now_ms); self._require_safety_clear()
            if self.alignment is None or self.bundle is None:
                return self._error(409, "OPERATION_REJECTED", "alignment is not configured")
            bundle = self.bundle.load()
            zone_id = str(payload.get("zone_id", ""))
            zone = next((item for item in bundle.get("zones", []) if item.get("id") == zone_id), None)
            if zone is None:
                raise ValueError("unknown zone_id")
            if self.alignment.state.value in {"IDLE", "COMPLETE", "FAULT"}:
                self.alignment.start(zone)
            vision = self._vision_payload()
            target = next((item for item in vision["blocks"] if item.get("color") == zone.get("color")), None)
            return self._json(200, {"ok": True, **self.alignment.tick(target)})
        if method == "POST" and path == "/api/calibration/locations/capture":
            self._require_control(token, now_ms); self._require_safety_clear()
            if self.bundle is None: return self._error(409, "OPERATION_REJECTED", "calibration bundle is not configured")
            snap = self.camera.latest_snapshot() if hasattr(self.camera, "latest_snapshot") else {}
            chassis_status = self.chassis.status() if self.chassis else {}
            command_pose = chassis_status.get("pose") or chassis_status.get("distance") or {}
            tags = payload.get("apriltags")
            tag_status = "provided" if tags else "not_detected"
            if tags is None and snap.get("frame") is not None:
                try:
                    from rg_runtime.apriltag import AprilTagDetector
                    tags = [item.to_dict() for item in AprilTagDetector().detect(snap["frame"], frame_index=int(snap.get("sequence") or 0))]
                    tag_status = "detected" if tags else "not_detected"
                except Exception:
                    tags = []
            location = self.bundle.save_location(payload.get("name", "location"), command_pose, chassis_status, tags or [], snap.get("jpeg"), role=payload.get("role", "unknown"), tag_status=tag_status)
            return self._json(200, {"ok": True, "location": location})
        if method == "POST" and path == "/api/calibration/zones":
            self._require_control(token, now_ms); self._require_safety_clear()
            zone = self.bundle.save_zone(payload.get("id") or payload.get("name", "zone"), payload.get("color", "purple"), payload.get("rect", payload), payload.get("action_id"), tolerance_px=payload.get("tolerance_px", 0))
            return self._json(200, {"ok": True, "zone": zone})
        if method == "GET" and path == "/api/calibration/export":
            if self.bundle is None: return self._error(404, "NOT_FOUND", "calibration bundle is not configured")
            out = self.bundle.export_zip()
            return Response(200, "application/zip", b"", headers=(("Content-Disposition", _attachment(out.name)),), file=out)
        if method == "GET" and path == "/api/camera/snapshot/latest":
            latest = self.camera.latest_snapshot_path() if hasattr(self.camera, "latest_snapshot_path") else None
            if latest is None or not latest.is_file():
                return self._error(404, "NOT_FOUND", "no camera snapshot is available")
            return Response(200, "image/jpeg", latest.read_bytes())
        if method == "POST" and path == "/api/control/acquire":
            owner = str(payload.get("owner", "browser"))[:80]
            return self._json(200, {"ok": True, "token": self.lease.acquire(owner, now_ms=now_ms)})
        if method == "POST" and path == "/api/control/heartbeat":
            self.lease.heartbeat(str(token or ""), now_ms=now_ms)
            return self._json(200, {"ok": True})
        if method == "POST" and path == "/api/control/release":
            self.lease.release(str(token or ""))
            return self._json(200, {"ok": True})
        if method == "POST" and path == "/api/devices/auto-connect":
            self._require_control(token, now_ms)
            result = self._auto_connect_devices()
            # A fixed-device reconnect is the explicit operator acknowledgement
            # after a latched global STOP; no separate unlock UI is required.
            self.safety.reset()
            return self._json(200, {"ok": True, **result})
        if method == "POST" and path == "/api/safety/stop":
            self.safety.global_stop(str(payload.get("reason", "operator")))
            return self._json(200, {"ok": True, "latched": True})
        if method == "POST" and path == "/api/safety/reset":
            self._require_control(token, now_ms)
            self.safety.reset()
            return self._json(200, {"ok": True})

        if method == "GET" and path == "/api/arm/ports":
            return self._json(200, {"ok": True, "ports": self.arm.ports()})
        if method == "GET" and path == "/api/arm/status":
            return self._json(200, {"ok": True, **self.arm.status()})
        if method == "POST" and path == "/api/arm/stop":
            return self._json(200, {"ok": True, **self.arm.stop()})
        if method == "GET" and path == "/api/arm/actions":
            return self._json(200, {"ok": True, **(self.recorder.status() if self.recorder else {"recording": False, "action": None, "zones": {}}), "actions": self.recorder.actions() if self.recorder else []})
        if method == "POST" and path == "/api/arm/recording":
            self._require_control(token, now_ms); self._require_safety_clear()
            # No name is the console flow: the operator names the package after
            # the recording ends, so start() opens an unnamed one.
            if payload.get("enabled"):
                result = self.recorder.start(payload.get("name"))
            else:
                result = self.recorder.finish()
            return self._json(200, {"ok": True, **result})
        if method == "GET" and path == "/api/arm/packages":
            if self.route_capture is None:
                return self._json(200, {"ok": True, "session_id": None, "packages": [], "count": 0, "history_count": 0})
            session_id = self.route_capture.package_session_id
            packages = self.route_capture.list_packages()
            return self._json(200, {"ok": True, "session_id": session_id, "packages": packages, "count": len(packages), "history_count": len(self.route_capture.package_folders())})
        if method == "GET" and path == "/api/arm/packages/export":
            if self.route_capture is None:
                return self._error(409, "OPERATION_REJECTED", "route capture is not configured")
            scope = str(payload.get("scope") or query.get("scope") or "session")
            if scope not in {"session", "all"}:
                raise ValueError("scope must be session or all")
            archive = self._export_action_packages(scope)
            return Response(200, "application/zip", b"", headers=(("Content-Disposition", _attachment(archive.name)),), file=archive)
        if method == "POST" and path in {"/api/arm/actions/confirm", "/api/arm/actions/discard"}:
            self._require_control(token, now_ms)
            if self.recorder is None:
                return self._error(409, "OPERATION_REJECTED", "action recorder is not configured")
            if path.endswith("confirm"):
                if payload.get("title"):
                    self.recorder.set_draft_name(payload["title"])
                result = self.recorder.confirm_draft()
            else:
                result = self.recorder.discard_draft()
            return self._json(200, {"ok": True, **result})
        if method == "POST" and path in {"/api/arm/actions/start", "/api/arm/actions/stop", "/api/arm/actions/zone"}:
            self._require_control(token, now_ms)
            self._require_safety_clear()
            if self.recorder is None:
                return self._error(409, "OPERATION_REJECTED", "action recorder is not configured")
            if path.endswith("/start"):
                result = self.recorder.start(payload.get("name", "action"))
            elif path.endswith("/stop"):
                result = self.recorder.finish()
            else:
                if not self.recorder.status().get("recording"):
                    raise RuntimeError("start arm recording before dragging a capture zone")
                result = self.recorder.save_zone(
                    payload.get("name", "target"),
                    payload.get("rect", payload),
                    payload.get("snapshot"),
                )
            return self._json(200, {"ok": True, **result})
        if method == "POST" and path.startswith("/api/arm/"):
            self._require_control(token, now_ms)
            self._require_safety_clear()
            if path == "/api/arm/connect":
                device = str(payload["device"])
                self._require_fixed_arm_device(device)
                result = self.arm.connect(device, 115200)
            elif path == "/api/arm/disconnect":
                result = self.arm.disconnect()
            elif path == "/api/arm/probe":
                result = self.arm.probe()
            elif path == "/api/arm/enable":
                result = self.arm.enable()
            elif path == "/api/arm/run":
                result = self.arm.run(int(payload["routine"]))
                if self.recorder: self.recorder.append("RUN", {"routine": int(payload["routine"])})
            elif path == "/api/arm/suction":
                if type(payload["enabled"]) is not bool:
                    raise ValueError("enabled must be a boolean")
                result = self.arm.suction(payload["enabled"])
                if self.recorder: self.recorder.append("SUCTION", {"enabled": payload["enabled"]})
            elif path == "/api/arm/servo":
                result = self.arm.servo(payload["id"], payload["position"], payload["time_ms"])
                if self.recorder: self.recorder.append("SERVO", {"id": payload["id"], "position": payload["position"], "time_ms": payload["time_ms"]})
            elif path == "/api/arm/move":
                result = self.arm.move(payload["positions"], payload.get("time_ms", 1000))
                if self.recorder: self.recorder.append("MOVE", {"positions": payload["positions"], "time_ms": payload.get("time_ms", 1000)})
            else:
                return self._error(404, "NOT_FOUND", "unknown arm endpoint")
            return self._json(200, {"ok": True, **result})

        if method == "GET" and path == "/api/chassis/ports":
            if self.chassis is None:
                return self._json(200, {"ok": True, "ports": []})
            return self._json(200, {"ok": True, "ports": self.chassis.ports()})
        if method == "GET" and path == "/api/chassis/status":
            if self.chassis is None:
                return self._json(200, {"ok": True, "connected": False, "state": "UNAVAILABLE"})
            return self._json(200, {"ok": True, **self.chassis.status()})
        if method == "GET" and path == "/api/line/status":
            if self.line is None:
                return self._json(200, {"ok": True, "connected": False, "state": "UNAVAILABLE"})
            return self._json(200, {"ok": True, **self.line.status()})
        if method == "POST" and path == "/api/chassis/stop":
            if self.chassis is None:
                return self._json(200, {"ok": True, "connected": False, "state": "UNAVAILABLE"})
            result = self.chassis.stop()
            # The release of a held drive button is part of the taught motion:
            # without it the package replays a move that never ends.
            if self.recorder: self.recorder.append("CHASSIS", {"command": "stop"})
            return self._json(200, {"ok": True, **result})
        if method == "POST" and path in {"/api/line/release", "/api/line/start"}:
            self._require_control(token, now_ms)
            if self.line is None:
                return self._error(409, "OPERATION_REJECTED", "line sensor is not configured")
            if path.endswith("release"):
                # Frees the pigpio GPIO claim so run_route_v2.py can open the
                # line sensor without restarting pigpiod underneath the console.
                self.line.close()
            else:
                self.line.start()
            return self._json(200, {"ok": True, **self.line.status()})
        if method == "POST" and path.startswith("/api/chassis/"):
            self._require_control(token, now_ms)
            self._require_safety_clear()
            if self.chassis is None:
                return self._error(409, "OPERATION_REJECTED", "chassis is not configured")
            if path == "/api/chassis/connect":
                device = str(payload["device"])
                self._require_fixed_chassis_device(device)
                result = self.chassis.connect(device, 9600)
            elif path == "/api/chassis/disconnect":
                result = self.chassis.disconnect()
            elif path == "/api/chassis/velocity":
                result = self.chassis.set_velocity(payload["vx"], payload["vy"], payload["wz"])
                if self.recorder: self.recorder.append("CHASSIS", {"command": "velocity", "vx": payload["vx"], "vy": payload["vy"], "wz": payload["wz"]})
            elif path == "/api/chassis/distance":
                result = self.chassis.run_distance(payload["forward_cm"], payload["right_cm"], payload["rotate_deg"], payload["speed"])
            elif path == "/api/chassis/sequence":
                result = self.chassis.run_sequence()
            elif path == "/api/chassis/encoder":
                result = self.chassis.request_encoder()
            elif path == "/api/chassis/speed":
                result = self.chassis.request_speed()
            elif path == "/api/chassis/motor":
                result = self.chassis.motor_test(str(payload["wheel"]), payload["speed"])
            elif path == "/api/chassis/encoder/reset":
                result = self.chassis.reset_encoder()
            elif path == "/api/chassis/distance/reset":
                result = self.chassis.reset_distance()
            else:
                return self._error(404, "NOT_FOUND", "unknown chassis endpoint")
            return self._json(200, {"ok": True, **result})


        if method == "GET" and path == "/api/camera/status":
            return self._json(200, {"ok": True, **self.camera.status()})
        # Handing recordings back is a read of what the console already wrote, so
        # it asks for neither the control lease nor a running camera -- the same
        # terms as "download the latest photo".  Deleting is the exception: it is
        # irreversible and takes the lease.
        if method == "GET" and path == "/api/camera/recordings":
            recordings = self.camera.list_recordings()
            return self._json(200, {
                "ok": True,
                "count": len(recordings),
                "total_bytes": sum(item["size_bytes"] for item in recordings),
                "recordings": recordings,
            })
        if method == "GET" and path == "/api/camera/recordings/file":
            file = self.camera.recording_file(query.get("name") or "")
            return Response(
                200,
                mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                b"",
                headers=(("Content-Disposition", _attachment(file.name)),),
                file=file,
            )
        if method == "GET" and path == "/api/camera/recordings/export":
            archive = self.camera.recordings_zip()
            return Response(200, "application/zip", b"", headers=(("Content-Disposition", _attachment(archive.name)),), file=archive)
        if method == "POST" and path == "/api/camera/recordings/delete":
            self._require_control(token, now_ms)
            return self._json(200, {"ok": True, **self.camera.delete_recording(str(payload.get("name") or ""))})
        if method == "POST" and path == "/api/camera/recordings/delete-all":
            self._require_control(token, now_ms)
            # An explicit flag, so a stray or replayed POST cannot empty the card.
            if payload.get("confirm") is not True:
                raise ValueError("confirm must be true to delete every recording")
            return self._json(200, {"ok": True, **self.camera.delete_all_recordings()})
        if method == "GET" and path == "/api/vision/pickup/status":
            if self.pickup is None:
                return self._json(200, {"ok": True, "state": "UNAVAILABLE", "target": None, "error": "vision pickup is not configured"})
            return self._json(200, {"ok": True, **self.pickup.status()})
        if method == "POST" and path == "/api/vision/pickup/start":
            self._require_control(token, now_ms)
            self._require_safety_clear()
            self._require_camera_running()
            if self.pickup is None:
                return self._error(409, "OPERATION_REJECTED", "vision pickup is not configured")
            from rg_runtime.models import BlockColor
            preferred = payload.get("preferred_color", "purple")
            if preferred not in {BlockColor.ORANGE.value, BlockColor.PURPLE.value}:
                raise ValueError("preferred_color must be orange or purple")
            current = self.pickup.config
            from .services.pickup_service import PickupConfig
            config = PickupConfig(
                preferred_color=BlockColor(preferred),
                min_confidence=float(payload.get("min_confidence", current.min_confidence)),
                confirm_frames=int(payload.get("confirm_frames", current.confirm_frames)),
                min_area=float(payload.get("min_area", current.min_area)),
                action_timeout_s=float(payload.get("action_timeout_s", current.action_timeout_s)),
            )
            return self._json(200, {"ok": True, **self.pickup.start(config)})
        if method == "POST" and path == "/api/vision/pickup/stop":
            self._require_control(token, now_ms)
            self._require_safety_clear()
            if self.pickup is None:
                return self._error(409, "OPERATION_REJECTED", "vision pickup is not configured")
            return self._json(200, {"ok": True, **self.pickup.stop()})
        if method == "POST" and path.startswith("/api/camera/"):
            self._require_control(token, now_ms)
            self._require_safety_clear()
            if path == "/api/camera/start":
                result = self.camera.start()
            elif path == "/api/camera/stop":
                if self.pickup is not None and self.pickup.status().get("state") in {"SEARCHING", "RUNNING"}:
                    self.pickup.stop()
                result = self.camera.stop()
            elif path == "/api/camera/snapshot":
                self._require_camera_running()
                result = {"path": str(self.camera.snapshot())}
            elif path == "/api/camera/record/start":
                self._require_camera_running()
                # The note is optional and may be Chinese; the service sanitizes
                # it into the filename instead of rejecting the request.
                result = self.camera.start_recording(str(payload.get("label") or ""))
            elif path == "/api/camera/record/stop":
                result = self.camera.stop_recording()
            else:
                return self._error(404, "NOT_FOUND", "unknown camera endpoint")
            return self._json(200, {"ok": True, **result})

        if method == "GET" and path == "/api/logs/recent":
            return self._json(200, {"ok": True, "events": self.event_log.recent(200)})
        return self._error(404, "NOT_FOUND", "endpoint not found")

    def _vision_payload(self):
        status = self.vision.status() if self.vision is not None else {}
        return {"tags": status.get("apriltags", []), "blocks": status.get("blocks", [])}

    def _export_action_packages(self, scope: str) -> Path:
        """Zip the session's action packages plus the material to hand back."""
        extra: list[tuple[str, Path]] = []
        if self.recorder is not None:
            extra.append(("data/actions.json", Path(self.recorder.path)))
        if self.bundle is not None:
            extra.append(("data/calibration_bundle/calibration_bundle.json", Path(self.bundle.path)))
        if self.event_log.path is not None:
            session_id = self.route_capture.package_session_id or "session"
            slice_path = self.route_capture.root / "exports" / f"control_hub_{session_id}.jsonl"
            # +1 because the stored sequence is the last event *before* this
            # console session started.
            if self.event_log.export_since(self.session_log_sequence + 1, slice_path) is not None:
                extra.append((f"logs/{slice_path.name}", slice_path))
        if scope == "all":
            return self.route_capture.export_all_packages(extra_files=extra)
        return self.route_capture.export_packages(extra_files=extra)

    def _require_camera_running(self) -> None:
        status = self.camera.status()
        if not status.get("running"):
            raise RuntimeError("camera must be started explicitly before this operation")

    def _auto_connect_devices(self) -> dict:
        """Connect only hardware at the fixed competition wiring locations."""
        result = {"arm": self.arm.status(), "chassis": self.chassis.status() if self.chassis else None, "camera": self.camera.status()}
        if not result["arm"].get("connected"):
            port = next(
                (
                    item for item in self.arm.ports()
                    if item.get("device") == self.fixed_arm_device and item.get("is_ch340") is True
                ),
                None,
            )
            # The escape hatch the chassis branch below already uses, and what
            # makes an alias usable at all: pyserial enumerates /sys/class/tty,
            # so ports() reports the raw node ("/dev/ttyUSB1") and never the
            # udev symlink ("/dev/robogame-arm").  Without this the alias would
            # fail the lookup every time and the arm would never connect.
            if port is None and not Path(self.fixed_arm_device).exists():
                raise RuntimeError(f"expected CH340 arm was not found at {self.fixed_arm_device}")
            result["arm"] = self.arm.connect(self.fixed_arm_device, 115200)
            # The competition console has no separate probe/unlock controls.
            # This keeps the firmware state transition under the fixed-port path.
            result["arm"] = self.arm.probe()
            result["arm"] = self.arm.enable()
        if self.chassis is not None and not result["chassis"].get("connected"):
            port = next((item for item in self.chassis.ports() if item.get("device") == self.fixed_chassis_device and item.get("is_chassis") is True), None)
            if port is None and not Path(self.fixed_chassis_device).exists():
                raise RuntimeError(f"expected chassis was not found at {self.fixed_chassis_device}")
            result["chassis"] = self.chassis.connect(self.fixed_chassis_device, 9600)
        return result

    def _require_control(self, token: str | None, now_ms: int) -> None:
        if not self.lease.is_valid(token, now_ms=now_ms):
            raise LeaseConflict("take control before operating hardware")

    def _require_safety_clear(self) -> None:
        if self.safety.latched:
            raise RuntimeError("safety stop is latched; reconnect fixed devices before operating")

    def _require_fixed_arm_device(self, device: str) -> None:
        if device != self.fixed_arm_device:
            raise RuntimeError(f"arm must use fixed device {self.fixed_arm_device}")
        if not any(item.get("device") == device and item.get("is_ch340") is True for item in self.arm.ports()):
            raise RuntimeError(f"expected CH340 arm was not found at {device}")

    def _require_fixed_chassis_device(self, device: str) -> None:
        if device != self.fixed_chassis_device:
            raise RuntimeError(f"chassis must use fixed device {self.fixed_chassis_device}")
        if self.chassis is None or (
            not Path(device).exists()
            and not any(item.get("device") == device and item.get("is_chassis") is True for item in self.chassis.ports())
        ):
            raise RuntimeError(f"expected chassis was not found at {device}")

    @staticmethod
    def _parse_json(raw: bytes) -> dict:
        if not raw:
            return {}
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request JSON must be an object")
        return value

    def _static(self, path: str) -> Response:
        aliases = {"/": "index.html", "/arm": "arm.html", "/camera": "camera.html", "/chassis": "chassis.html", "/operate": "operate.html", "/logs": "logs.html"}
        relative = aliases.get(path)
        if relative is None and path.startswith("/static/"):
            relative = path.removeprefix("/static/")
        if relative is None:
            return self._error(404, "NOT_FOUND", "page not found")
        candidate = (self.static_root / relative).resolve()
        if not candidate.is_relative_to(self.static_root) or not candidate.is_file():
            return self._error(404, "NOT_FOUND", "page not found")
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        return Response(200, content_type, candidate.read_bytes())

    @staticmethod
    def _json(status: int, payload: dict) -> Response:
        return Response(status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _error(self, status: int, code: str, message: str) -> Response:
        return self._json(status, {"ok": False, "code": code, "message": message})
