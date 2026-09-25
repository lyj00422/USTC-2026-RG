"""Hardware-free command adapters and structured event logging."""

from __future__ import annotations

import json
from pathlib import Path

from .models import MotionCommand, TaskCommand


class FakeMotionAdapter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def send_motion(self, command: MotionCommand) -> None:
        self.events.append({"type": "motion", "command": command.to_dict()})

    def send_task(self, command: TaskCommand) -> None:
        self.events.append({"type": "task", "command": command.to_dict()})

    def write_jsonl(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as stream:
            for event in self.events:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        return output
