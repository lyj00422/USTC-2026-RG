"""Singleton camera capture, MJPEG preview, snapshot, and recording service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import shutil
import threading
import zipfile

from ..state import HubState
from .event_log import EventLog


LABEL_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
# What the console shows and hands back as a recording.  Only .avi is written
# today (see start_recording); .mp4 is accepted so that a future writer does not
# silently make every recording invisible to the console.
RECORDING_SUFFIXES = (".avi", ".mp4")
# zipfile refuses any entry stamped before 1980.
ZIP_EPOCH = datetime(1980, 1, 1).timestamp()


def zip_stamp(epoch: float) -> tuple[int, int, int, int, int, int]:
    """A zip-safe date_time for a file's mtime.

    A Raspberry Pi has no clock of its own: booted off the network it can date
    its files 1970, and zipfile rejects anything before 1980 outright -- which
    would fail "download every recording" at exactly the moment the operator is
    at the field with no network.  Clamping the stamp beats failing the
    transfer; the filename, not the archive stamp, is the recording's identity.
    """
    stamp = datetime.fromtimestamp(max(epoch, ZIP_EPOCH))
    return (stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)


def split_recording_name(name: str, fallback_epoch: float) -> tuple[str, str]:
    """Return (label, recorded_at) for a recording filename.

    start_recording() writes <YYYYMMDD>_<HHMMSS>_<micros>_<label>.avi, so the
    stamp comes back out of the name.  A file that does not follow the pattern
    (hand-copied onto the Pi) keeps its own stem as the label and falls back to
    the filesystem time, rather than being reported with a made-up timestamp.
    """
    stem = Path(name).stem
    parts = stem.split("_")
    if (len(parts) >= 4 and len(parts[0]) == 8 and parts[0].isdigit()
            and len(parts[1]) == 6 and parts[1].isdigit() and parts[2].isdigit()):
        stamp = (f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}"
                 f"T{parts[1][:2]}:{parts[1][2:4]}:{parts[1][4:6]}")
        return "_".join(parts[3:]), stamp
    return stem, datetime.fromtimestamp(fallback_epoch).strftime("%Y-%m-%dT%H:%M:%S")


class CameraServiceError(RuntimeError):
    pass


class RecordingLibrary:
    """The recordings folder, read back: listing, download target, cleanup.

    The folder is written by start_recording() on this same service, so the
    layout stays known in one place instead of being re-derived in the HTTP
    layer.  Filenames arrive from a query string, so what can be addressed is
    checked against the folder itself (see recording_file) rather than cleaned
    up and hoped for.

    The host service supplies output_dir and event_log, and reports the file it
    is currently writing through _active_recording_path().
    """

    def recordings_dir(self) -> Path:
        return Path(self.output_dir).expanduser() / "recordings"

    def list_recordings(self) -> list[dict]:
        """Every recording, newest first -- the operator wants the run they just did."""
        directory = self.recordings_dir()
        if not directory.is_dir():
            return []
        active = self._active_recording_path()
        items = []
        for path in directory.iterdir():
            if not path.is_file() or path.suffix.lower() not in RECORDING_SUFFIXES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            label, recorded_at = split_recording_name(path.name, stat.st_mtime)
            items.append({
                "name": path.name,
                "label": label,
                "recorded_at": recorded_at,
                "size_bytes": stat.st_size,
                "modified_ms": int(stat.st_mtime * 1000),
                # A recording still being written downloads as a truncated file,
                # so the console offers it for neither download nor deletion.
                "active": active is not None and path.resolve() == active,
            })
        items.sort(key=lambda item: (item["modified_ms"], item["name"]), reverse=True)
        return items

    def recording_file(self, name: str) -> Path:
        """Resolve one recording by filename, refusing anything outside the folder.

        The name is checked, not sanitized: it must be a bare filename of an
        allowed type that resolves directly inside the recordings folder, so
        "..", a path separator or a subdirectory cannot be addressed at all.
        """
        directory = self.recordings_dir().resolve()
        candidate = (directory / str(name)).resolve()
        if (not str(name) or candidate.parent != directory
                or candidate.suffix.lower() not in RECORDING_SUFFIXES
                or not candidate.is_file()):
            raise FileNotFoundError(f"no such recording: {name}")
        return candidate

    def delete_recording(self, name: str) -> dict:
        path = self.recording_file(name)
        if self._active_recording_path() == path:
            raise RuntimeError(f"{path.name} is still being written; stop the recording first")
        size = path.stat().st_size
        path.unlink()
        self.event_log.append("recording_deleted", "camera", {"name": path.name, "size_bytes": size})
        return {"name": path.name, "size_bytes": size}

    def delete_all_recordings(self) -> dict:
        """Remove every finished recording; the one being written is kept.

        Deleting the file under an open writer would leave the writer and the
        name disagreeing, so the active recording is left alone -- the operator
        stops it first and deletes it on the next pass.
        """
        removed = [item["name"] for item in self.list_recordings() if not item["active"]]
        count = 0
        total = 0
        for name in removed:
            try:
                total += self.delete_recording(name)["size_bytes"]
                count += 1
            except FileNotFoundError:
                # Vanished between the listing and the unlink: nothing to
                # report and nothing left to free, so it is not a failure.
                continue
        return {"count": count, "size_bytes": total}

    def recordings_zip(self) -> Path:
        """Package every finished recording for one-shot transfer.

        Stored, not deflated: MJPG frames are already JPEG-compressed, so
        deflating the archive buys about a percent and spends Pi CPU on every
        byte to get it.  The archive is a container here, not a compressor.
        """
        names = [item["name"] for item in self.list_recordings() if not item["active"]]
        if not names:
            raise FileNotFoundError("there are no finished recordings to package")
        # Written outside the recordings folder, so the archive never appears in
        # the listing it was made from.
        destination = Path(self.output_dir).expanduser() / "exports" / "录像_全部.zip"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_STORED) as archive:
            for name in names:
                path = self.recording_file(name)
                info = zipfile.ZipInfo(name, date_time=zip_stamp(path.stat().st_mtime))
                info.compress_type = zipfile.ZIP_STORED
                # Copied into the archive rather than read into memory, so
                # packaging 200 MB of recordings does not need 200 MB of RAM.
                with open(path, "rb") as source, archive.open(info, "w") as target:
                    shutil.copyfileobj(source, target, 1 << 20)
        return destination

    def _active_recording_path(self) -> Path | None:
        """The file currently being written, if any."""
        return None


@dataclass(frozen=True)
class CameraSettings:
    device: int = 0
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    pixel_format: str = "MJPG"


class CameraService(RecordingLibrary):
    def __init__(
        self,
        settings: CameraSettings,
        output_dir: str | Path,
        hub_state: HubState,
        event_log: EventLog,
        *,
        cv2_module=None,
        capture_factory=None,
        background: bool = True,
    ) -> None:
        if cv2_module is None:
            import cv2 as cv2_module
        self.cv2 = cv2_module
        self.settings = settings
        self.output_dir = Path(output_dir).expanduser()
        self.hub_state = hub_state
        self.event_log = event_log
        self.capture_factory = capture_factory or self._open_capture
        self.background = background
        self._condition = threading.Condition()
        self._capture = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._frame = None
        self._jpeg: bytes | None = None
        self._sequence = 0
        self._error: str | None = None
        self._recording_path: Path | None = None
        self._writer = None
        self._recording_frames = 0
        self._last_snapshot_path: Path | None = None

    def _open_capture(self, settings: CameraSettings):  # pragma: no cover - hardware dependent
        capture = self.cv2.VideoCapture(settings.device)
        if not capture.isOpened():
            capture.release()
            raise CameraServiceError(f"unable to open camera device {settings.device}")
        properties = (
            (getattr(self.cv2, "CAP_PROP_FRAME_WIDTH", 3), settings.width),
            (getattr(self.cv2, "CAP_PROP_FRAME_HEIGHT", 4), settings.height),
            (getattr(self.cv2, "CAP_PROP_FPS", 5), settings.fps),
        )
        for prop, value in properties:
            capture.set(prop, value)
        fourcc_prop = getattr(self.cv2, "CAP_PROP_FOURCC", 6)
        capture.set(fourcc_prop, self.cv2.VideoWriter_fourcc(*settings.pixel_format))
        return capture

    def start(self) -> dict:
        with self._condition:
            if self._running:
                raise CameraServiceError("camera is already running")
            capture = self.capture_factory(self.settings)
            if not capture.isOpened():
                capture.release()
                raise CameraServiceError("unable to open camera")
            self._capture = capture
            self._running = True
            self._error = None
            self.hub_state.update_module("camera", state="RUNNING", detail="实时画面可用")
            self.event_log.append("started", "camera", {"device": self.settings.device})
            if self.background:
                self._thread = threading.Thread(target=self._capture_loop, name="camera-capture", daemon=True)
                self._thread.start()
            return self.status()

    def process_once(self) -> bool:
        with self._condition:
            if not self._running or self._capture is None:
                raise CameraServiceError("camera is not running")
            capture = self._capture
        ok, frame = capture.read()
        if not ok:
            return False
        encoded_ok, encoded = self.cv2.imencode(
            ".jpg", frame, [getattr(self.cv2, "IMWRITE_JPEG_QUALITY", 1), 80]
        )
        if not encoded_ok:
            raise CameraServiceError("unable to encode camera frame as JPEG")
        with self._condition:
            self._write_recording_frame(frame)
            self._frame = frame.copy()
            self._jpeg = encoded.tobytes()
            self._sequence += 1
            self._condition.notify_all()
        return True

    def wait_for_jpeg(self, after_sequence: int, timeout_s: float = 1.0) -> tuple[int, bytes | None]:
        with self._condition:
            self._condition.wait_for(
                lambda: self._sequence > after_sequence or not self._running,
                timeout=timeout_s,
            )
            return self._sequence, self._jpeg

    def latest_frame(self):
        """Return a copy of the latest BGR frame for consumers such as vision."""
        with self._condition:
            return None if self._frame is None else self._frame.copy()

    def latest_sample(self):
        """Atomically return the frame sequence and a copy of its BGR image."""
        with self._condition:
            return self._sequence, None if self._frame is None else self._frame.copy()

    def latest_sequence(self) -> int:
        """Return the current frame number without copying image data."""
        with self._condition:
            return self._sequence

    def latest_snapshot(self):
        with self._condition:
            return {"sequence": self._sequence, "jpeg": self._jpeg, "frame": None if self._frame is None else self._frame.copy()}

    def snapshot(self) -> Path:
        with self._condition:
            frame = None if self._frame is None else self._frame.copy()
        if frame is None:
            raise CameraServiceError("no camera frame is available yet")
        directory = self.output_dir / "snapshots"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        if not self.cv2.imwrite(str(path), frame):
            raise CameraServiceError(f"unable to write snapshot: {path}")
        with self._condition:
            self._last_snapshot_path = path
        self.event_log.append("snapshot", "camera", {"path": str(path)})
        return path

    def latest_snapshot_path(self) -> Path | None:
        with self._condition:
            path = self._last_snapshot_path
        return path if path is not None and path.is_file() else None

    def _active_recording_path(self) -> Path | None:
        with self._condition:
            path = self._recording_path
        return None if path is None else Path(path).resolve()

    def start_recording(self, label: str = "") -> dict:
        with self._condition:
            if not self._running:
                raise CameraServiceError("camera is not running")
            if self._recording_path is not None:
                raise CameraServiceError("a recording is already in progress")
            directory = self.output_dir / "recordings"
            directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            self._recording_path = directory / f"{timestamp}_{self.recording_label(label)}.avi"
            self._recording_frames = 0
            self._writer = None
            path = str(self._recording_path)
        self.event_log.append("recording_started", "camera", {"path": path})
        return self.status()

    @staticmethod
    def recording_label(label: str = "") -> str:
        """Sanitize an operator-supplied note into a filename-safe label.

        The operator types Chinese, so the note is sanitized rather than
        rejected; an empty note simply leaves the timestamp to name the file.
        """
        note = re.sub(r"[^\w-]+", "_", str(label or "").strip(), flags=re.UNICODE).strip("_")
        return note[:48] or "recording"

    def stop_recording(self) -> dict:
        with self._condition:
            if self._recording_path is None:
                raise CameraServiceError("no recording is in progress")
            path = self._recording_path
            frames = self._recording_frames
            if self._writer is not None:
                self._writer.release()
            self._writer = None
            self._recording_path = None
            self._recording_frames = 0
        result = {"path": str(path), "frames": frames}
        self.event_log.append("recording_stopped", "camera", result)
        return result

    def stop(self, error: str | None = None) -> dict:
        with self._condition:
            if not self._running and self._capture is None:
                return self.status()
            self._running = False
            self._error = error
            capture = self._capture
            self._capture = None
            self._frame = None
            self._jpeg = None
            if self._writer is not None:
                self._writer.release()
            self._writer = None
            self._recording_path = None
            self._recording_frames = 0
            self._condition.notify_all()
        if capture is not None:
            capture.release()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        state = "FAULT" if error else "STOPPED"
        detail = error or "摄像头未启动"
        self.hub_state.update_module("camera", state=state, detail=detail)
        self.event_log.append("stopped", "camera", {"error": error})
        return self.status()

    def status(self) -> dict:
        with self._condition:
            return {
                "running": self._running,
                "frame_available": self._frame is not None,
                "sequence": self._sequence,
                "recording": self._recording_path is not None,
                "recording_path": str(self._recording_path) if self._recording_path else None,
                "recording_frames": self._recording_frames,
                "snapshot_available": self._last_snapshot_path is not None and self._last_snapshot_path.is_file(),
                "error": self._error,
                "actual": {
                    "device": self.settings.device,
                    "width": self.settings.width,
                    "height": self.settings.height,
                    "fps": self.settings.fps,
                    "pixel_format": self.settings.pixel_format,
                },
            }

    def _write_recording_frame(self, frame) -> None:
        if self._recording_path is None:
            return
        if self._writer is None:
            height, width = frame.shape[:2]
            codec = self.cv2.VideoWriter_fourcc(*"MJPG")
            self._writer = self.cv2.VideoWriter(
                str(self._recording_path), codec, self.settings.fps, (width, height)
            )
            if not self._writer.isOpened():
                self._writer.release()
                self._writer = None
                self._recording_path = None
                raise CameraServiceError("unable to open MJPG AVI recording writer")
        self._writer.write(frame)
        self._recording_frames += 1

    def _capture_loop(self) -> None:  # pragma: no cover - hardware timing dependent
        failures = 0
        error = None
        try:
            while self.status()["running"]:
                if self.process_once():
                    failures = 0
                else:
                    failures += 1
                    if failures >= 3:
                        error = "camera read failed repeatedly"
                        break
        except Exception as exc:
            error = str(exc)
        finally:
            self.stop(error)
