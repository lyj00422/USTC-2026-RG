from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
import zipfile
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RouteCaptureService:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.camera_root = self.root / "camera"
        self.arm_root = self.root / "arm"
        self.camera_root.mkdir(parents=True, exist_ok=True)
        self.arm_root.mkdir(parents=True, exist_ok=True)
        self.session_root = self.root / "sessions"
        self.session_root.mkdir(parents=True, exist_ok=True)
        self._session_id: str | None = None
        self._last_session_id: str | None = None
        self._session_index = 0
        # Action packages are grouped per console session so "export everything
        # I taught in this sitting" is a well-defined question.  Package names
        # repeat freely (every grab is called 抓取), so the folder carries an
        # index as well -- a name is a label, not a key.
        self._package_session_id: str | None = None
        self._package_index = 0

    def begin_session(self, title: str = "route") -> str:
        self._session_id = f"{self.slug(title)}_{uuid.uuid4().hex[:8]}"
        self._session_index = 0
        (self.session_root / self._session_id).mkdir(parents=True, exist_ok=True)
        return self._session_id

    def end_session(self) -> str | None:
        session_id = self._session_id
        self._last_session_id = session_id
        self._session_id = None
        self._session_index = 0
        return session_id

    @staticmethod
    def slug(title: str) -> str:
        value = re.sub(r"[^\w\-]+", "_", str(title).strip(), flags=re.UNICODE).strip("_")
        return value or "point"

    def capture_point(self, title, *, image, chassis, line, apriltags, segment_distance=None):
        title = str(title).strip()
        if not title:
            raise ValueError("title is required")
        slug = self.slug(title)
        if self._session_id:
            self._session_index += 1
            folder = self.session_root / self._session_id / f"{self._session_index:04d}_{slug}"
        else:
            folder = self.camera_root / slug
        folder.mkdir(parents=True, exist_ok=True)
        metadata = {
            "title": title,
            "slug": slug,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "chassis_status": chassis or {},
            "line_status": line or {},
            "apriltags": list(apriltags or []),
            "segment_distance": segment_distance if segment_distance is not None else (chassis or {}).get("pose", {}),
            "motion_history": list((chassis or {}).get("motion_history", [])),
            "image": "image.jpg" if image else None,
        }
        if image is not None:
            (folder / "image.jpg").write_bytes(bytes(image))
        (folder / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata

    # --------------------------------------------------------- action packages
    def begin_package_session(self, title: str = "动作包") -> str:
        """Open the "this sitting" group every saved action package joins.

        One session per console run: the operator's question is always "give me
        back everything I taught just now", and a timestamp is the only thing
        that answers it without asking them to manage session state mid-field.
        """
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{self.slug(title)}_{stamp}"
        session_id, suffix = base, 1
        while (self.arm_root / session_id).exists():
            suffix += 1
            session_id = f"{base}_{suffix}"
        # Nothing is written until a package is actually saved: a console run
        # where the operator taught nothing must not leave empty folders behind.
        self._package_session_id = session_id
        self._package_index = 0
        return session_id

    @property
    def package_session_id(self) -> str | None:
        return self._package_session_id

    def _require_package_session(self) -> str:
        if self._package_session_id is None:
            return self.begin_package_session()
        return self._package_session_id

    def _next_package_folder(self, session_root: Path, slug: str) -> Path:
        """序号 keeps repeats apart: every grab is called 抓取, none may win."""
        index = self._package_index
        while True:
            index += 1
            folder = session_root / f"{index:04d}_{slug}"
            if not folder.exists():
                self._package_index = index
                return folder

    def save_arm(self, action: dict, *, image=None, annotated_image=None, chassis=None, zones=None, arm=None, line=None):
        title = str(action.get("name", "")).strip()
        if not title:
            raise ValueError("title is required")
        session_id = self._require_package_session()
        session_root = self.arm_root / session_id
        session_root.mkdir(parents=True, exist_ok=True)
        slug = self.slug(title)
        folder = self._next_package_folder(session_root, slug)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "action.json").write_text(json.dumps(action, ensure_ascii=False, indent=2), encoding="utf-8")
        if image is not None:
            (folder / "camera.jpg").write_bytes(bytes(image))
        if annotated_image is not None:
            (folder / "camera_zone.jpg").write_bytes(bytes(annotated_image))
        (folder / "zones.json").write_text(json.dumps(zones or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "chassis.json").write_text(json.dumps(chassis or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "arm.json").write_text(json.dumps(arm or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        files = {
            "action": "action.json",
            "camera": "camera.jpg" if image is not None else None,
            "camera_zone": "camera_zone.jpg" if annotated_image is not None else None,
            "zones": "zones.json",
            "chassis": "chassis.json",
            "arm": "arm.json",
        }
        metadata = {
            "name": title,
            "slug": slug,
            "index": int(folder.name.split("_", 1)[0]),
            "session_id": session_id,
            "captured_at": _now(),
            "files": files,
            "zones": sorted(str(key) for key in (zones or {})),
            "step_count": len(action.get("steps", [])),
            # Line telemetry rides in the manifest rather than its own file: it
            # is context for the package, not a package artifact.
            "line_status": dict(line or {}),
        }
        (folder / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_session_manifest(session_id)
        return {**action, "export_dir": str(folder), "session_id": session_id, "package_index": metadata["index"]}

    def list_packages(self, session_id: str | None = None) -> list[dict]:
        session_id = session_id or self._package_session_id
        if not session_id:
            return []
        session_root = self.arm_root / session_id
        if not session_root.is_dir():
            return []
        packages = []
        for folder in sorted(session_root.iterdir()):
            if not folder.is_dir():
                continue
            metadata = folder / "metadata.json"
            if not metadata.is_file():
                continue
            try:
                packages.append(json.loads(metadata.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                continue
        return packages

    def package_sessions(self) -> list[str]:
        return sorted(folder.name for folder in self.arm_root.iterdir() if folder.is_dir())

    def package_folders(self) -> list[Path]:
        """Every saved package folder, across all sessions."""
        folders: list[Path] = []
        for session_id in self.package_sessions():
            session_root = self.arm_root / session_id
            folders.extend(folder for folder in session_root.iterdir() if folder.is_dir())
        return folders

    def _write_session_manifest(self, session_id: str) -> None:
        session_root = self.arm_root / session_id
        if not session_root.is_dir():
            return
        manifest = {"session_id": session_id, "updated_at": _now(), "packages": self.list_packages(session_id)}
        (session_root / "session.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def export_packages(self, session_id: str | None = None, destination=None, extra_files=()) -> Path:
        """Zip one session's packages, plus whatever the operator must hand back."""
        session_id = session_id or self._require_package_session()
        source = self.arm_root / session_id
        if not source.is_dir():
            raise FileNotFoundError(f"no action packages were saved in session {session_id}")
        destination = Path(destination) if destination else (self.root / "exports" / f"{self.slug(session_id)}.zip")
        manifest = {
            "session_id": session_id,
            "exported_at": _now(),
            "scope": "session",
            "package_count": len(self.list_packages(session_id)),
            "packages": self.list_packages(session_id),
        }
        return self._write_packages_zip(destination, {session_id: source}, manifest, extra_files)

    def export_all_packages(self, destination=None, extra_files=()) -> Path:
        sessions = {session_id: self.arm_root / session_id for session_id in self.package_sessions()}
        sessions.pop("exports", None)
        if not sessions:
            raise FileNotFoundError("no action packages have been saved yet")
        destination = Path(destination) if destination else (self.root / "exports" / "动作包_全部.zip")
        manifest = {
            "exported_at": _now(),
            "scope": "all",
            "sessions": sorted(sessions),
            "package_count": sum(len(self.list_packages(session_id)) for session_id in sessions),
        }
        return self._write_packages_zip(destination, sessions, manifest, extra_files)

    def _write_packages_zip(self, destination: Path, sessions: dict, manifest: dict, extra_files) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for session_id, source in sessions.items():
                for file in sorted(source.rglob("*")):
                    if file.is_file() and file.resolve() != destination.resolve():
                        archive.write(file, (Path(session_id) / file.relative_to(source)).as_posix())
            for arcname, path in extra_files:
                path = Path(path)
                if path.is_file() and path.resolve() != destination.resolve():
                    archive.write(path, str(arcname))
        return destination

    def export(self, kind: str, destination: str | Path | None = None) -> Path:
        if kind not in {"camera", "arm", "session"}:
            raise ValueError("kind must be camera, arm or session")
        source = (self.session_root / self._last_session_id) if kind == "session" and self._last_session_id else (self.root if kind == "session" else (self.camera_root if kind == "camera" else self.arm_root))
        destination = Path(destination) if destination else self.root / f"{kind}_capture.zip"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in source.rglob("*"):
                if file.is_file() and file.resolve() != destination.resolve():
                    archive.write(file, file.relative_to(source).as_posix())
        return destination
