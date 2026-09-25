"""Sensor source contracts and explicit unavailable placeholders."""

from __future__ import annotations

from .hardware_models import EncoderState
from .models import LineIntersection, LineState


class NullLineSource:
    def read(self, timestamp_ms: int) -> LineState:
        return LineState(None, 0, LineIntersection.NONE, True, False, timestamp_ms)


class NullEncoderSource:
    def read(self, timestamp_ms: int) -> EncoderState:
        return EncoderState.unavailable(timestamp_ms)
