"""Thread-safe in-memory and JSONL event log."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import threading
import time


class EventLog:
    def __init__(self, path: str | Path | None = None, *, capacity: int = 500) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.path = Path(path).expanduser() if path else None
        self._events: deque[dict] = deque(maxlen=capacity)
        self._condition = threading.Condition()
        self._sequence = 0

    def append(self, event_type: str, source: str, payload: dict | None = None) -> dict:
        with self._condition:
            self._sequence += 1
            event = {
                "sequence": self._sequence,
                "timestamp_ns": time.time_ns(),
                "type": event_type,
                "source": source,
                "payload": payload or {},
            }
            self._events.append(event)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._condition.notify_all()
            return dict(event)

    def sequence(self) -> int:
        with self._condition:
            return self._sequence

    def export_since(self, sequence: int, destination: str | Path) -> Path | None:
        """Write the JSONL events from ``sequence`` onward into a new file.

        The in-memory ring only holds the last ``capacity`` events, so a session
        slice has to be filtered off the file.  Returns None when there is no
        file-backed log to slice.
        """
        destination = Path(destination)
        if self.path is None or not self.path.is_file():
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("r", encoding="utf-8") as source, destination.open("w", encoding="utf-8") as target:
            for line in source:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except ValueError:
                    continue
                if int(event.get("sequence", 0)) >= sequence:
                    target.write(stripped + "\n")
        return destination

    def recent(self, limit: int | None = None) -> list[dict]:
        with self._condition:
            events = list(self._events)
            if limit is not None:
                events = events[-max(0, limit):]
            return [dict(event) for event in events]

    def wait_after(self, sequence: int, timeout_s: float = 15.0) -> list[dict]:
        with self._condition:
            if not any(event["sequence"] > sequence for event in self._events):
                self._condition.wait(timeout_s)
            return [dict(event) for event in self._events if event["sequence"] > sequence]
