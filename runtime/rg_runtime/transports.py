"""Small line-oriented transports shared by hardware and tests."""

from __future__ import annotations

from collections import deque
from typing import Protocol


class LineTransport(Protocol):
    def send_line(self, line: str) -> None: ...
    def read_lines(self) -> list[str]: ...
    def close(self) -> None: ...


class MemoryTransport:
    def __init__(self, incoming: list[str] | None = None) -> None:
        self._incoming = deque(incoming or [])
        self.sent: list[str] = []

    def send_line(self, line: str) -> None:
        self.sent.append(line)

    def read_lines(self) -> list[str]:
        lines = list(self._incoming)
        self._incoming.clear()
        return [line.rstrip("\r\n") for line in lines]

    def feed(self, *lines: str) -> None:
        self._incoming.extend(lines)

    def close(self) -> None:
        return None


class SerialTransport:
    def __init__(self, device: str, baudrate: int, *, timeout_s: float = 0.0) -> None:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("pyserial is required for hardware transport") from exc
        self._serial = serial.Serial(device, baudrate=baudrate, timeout=timeout_s)
        self._rx_buffer = bytearray()

    @classmethod
    def from_serial(cls, serial_instance) -> "SerialTransport":
        transport = cls.__new__(cls)
        transport._serial = serial_instance
        transport._rx_buffer = bytearray()
        return transport

    def send_line(self, line: str) -> None:
        self._serial.write(line.encode("ascii"))

    def read_lines(self) -> list[str]:
        waiting = self._serial.in_waiting
        if waiting:
            self._rx_buffer.extend(self._serial.read(waiting))
        lines: list[str] = []
        while b"\n" in self._rx_buffer:
            raw, _, remaining = self._rx_buffer.partition(b"\n")
            self._rx_buffer = bytearray(remaining)
            lines.append(raw.rstrip(b"\r").decode("ascii", errors="replace"))
        return lines

    def close(self) -> None:
        self._serial.close()
