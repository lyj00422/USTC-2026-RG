"""Parsing and normalization for the eight-channel UART line sensor."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .models import LineIntersection, LineState


WEIGHTS = (-3.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 3.0)


def line_state_from_mask(mask: int, *, timestamp_ms: int, active_level: int = 0, reverse_order: bool = False) -> LineState:
    mask &= 0xFF
    bits = [(mask >> (7 - index)) & 1 for index in range(8)]
    if reverse_order:
        bits.reverse()
        mask = sum(bit << (7 - index) for index, bit in enumerate(bits))
    active = [bit == active_level for bit in bits]
    count = sum(active)
    lost = count == 0
    error = None if lost else sum(weight for weight, on in zip(WEIGHTS, active) if on) / count / 3.0
    intersection = LineIntersection.CROSS if count >= 6 else LineIntersection.NONE
    return LineState(error, mask, intersection, lost, False, timestamp_ms)


class LineFrameParser:
    def __init__(self, *, mode: str = "ascii_digital", active_level: int = 0, reverse_order: bool = False) -> None:
        if mode not in {"bitmask_byte", "ascii_bits", "ascii_hex", "ascii_digital"}:
            raise ValueError("unsupported line frame mode")
        if active_level not in {0, 1}:
            raise ValueError("active_level must be 0 or 1")
        self.mode = mode
        self.active_level = active_level
        self.reverse_order = reverse_order

    def parse(self, frame: bytes, *, timestamp_ms: int) -> LineState:
        if self.mode == "bitmask_byte":
            if not frame:
                raise ValueError("empty line frame")
            mask = frame[-1]
        elif self.mode == "ascii_digital":
            mask = self._parse_digital(frame)
        else:
            text = frame.decode("ascii").strip()
            if self.mode == "ascii_bits":
                if len(text) != 8 or any(char not in "01" for char in text):
                    raise ValueError("expected eight ASCII bits")
                mask = int(text, 2)
            else:
                mask = int(text.removeprefix("0x"), 16)
        return line_state_from_mask(mask, timestamp_ms=timestamp_ms, active_level=self.active_level, reverse_order=self.reverse_order)

    @staticmethod
    def _parse_digital(frame: bytes) -> int:
        """Parse the module's documented `$D,x1:0,...,x8:1#` response."""
        text = frame.decode("ascii").strip()
        if not (text.startswith("$D,") and text.endswith("#")):
            raise ValueError("expected digital frame $D,x1:0,...,x8:1#")
        values: dict[int, int] = {}
        for field in text[3:-1].split(","):
            match = re.fullmatch(r"x([1-8]):([01])", field.strip())
            if match is None:
                raise ValueError(f"invalid digital field: {field!r}")
            index, value = int(match.group(1)), int(match.group(2))
            if index in values:
                raise ValueError(f"duplicate digital field: x{index}")
            values[index] = value
        if set(values) != set(range(1, 9)):
            raise ValueError("digital frame must contain x1 through x8 exactly once")
        return sum(values[index] << (8 - index) for index in range(1, 9))


@dataclass
class LineSensorSnapshot:
    connected: bool = False
    state: str = "DISCONNECTED"
    error: str | None = None
    raw: str | None = None
    line_state: LineState | None = None
