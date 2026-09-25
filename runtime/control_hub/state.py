"""Shared module state and single-controller lease."""

from __future__ import annotations

from dataclasses import dataclass, replace
import secrets
import threading
from typing import Callable


class LeaseConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ModuleSnapshot:
    key: str
    title: str
    href: str | None
    available: bool
    state: str
    detail: str


class HubState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._modules = {
            "arm": ModuleSnapshot("arm", "机械臂调试", "/arm", True, "DISCONNECTED", "USB 串口未连接"),
            "camera": ModuleSnapshot("camera", "摄像头采集", "/camera", True, "STOPPED", "摄像头未启动"),
            "logs": ModuleSnapshot("logs", "运行日志", "/logs", True, "READY", "查看本次会话事件"),
            "chassis": ModuleSnapshot("chassis", "底盘调试", "/chassis", True, "DISCONNECTED", "蓝牙串口未连接"),
            "line": ModuleSnapshot("line", "巡线调试", "/operate", True, "DISCONNECTED", "UART巡线模块未连接"),
            "encoders": ModuleSnapshot("encoders", "编码器调试", None, False, "UNAVAILABLE", "编码器未接入"),
        }

    def modules(self) -> tuple[ModuleSnapshot, ...]:
        with self._lock:
            return tuple(self._modules.values())

    def update_module(self, key: str, *, state: str, detail: str) -> ModuleSnapshot:
        with self._lock:
            current = self._modules[key]
            updated = replace(current, state=state, detail=detail)
            self._modules[key] = updated
            return updated


class ControlLease:
    def __init__(self, ttl_ms: int = 900000, token_factory: Callable[[], str] | None = None) -> None:
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive")
        self.ttl_ms = ttl_ms
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._lock = threading.Lock()
        self._token: str | None = None
        self._owner: str | None = None
        self._expires_ms = -1

    def acquire(self, owner: str, *, now_ms: int) -> str:
        with self._lock:
            if self._token is not None and now_ms <= self._expires_ms:
                if owner == self._owner:
                    self._expires_ms = now_ms + self.ttl_ms
                    return self._token
                raise LeaseConflict("another browser currently controls the hub")
            self._token = self._token_factory()
            self._owner = owner
            self._expires_ms = now_ms + self.ttl_ms
            return self._token

    def heartbeat(self, token: str, *, now_ms: int) -> None:
        with self._lock:
            if token != self._token or now_ms > self._expires_ms:
                raise LeaseConflict("control lease is missing or expired")
            self._expires_ms = now_ms + self.ttl_ms

    def release(self, token: str) -> None:
        with self._lock:
            if token == self._token:
                self._token = None
                self._owner = None
                self._expires_ms = -1

    def is_valid(self, token: str | None, *, now_ms: int) -> bool:
        with self._lock:
            return token is not None and token == self._token and now_ms <= self._expires_ms

    def expired(self, *, now_ms: int) -> bool:
        with self._lock:
            return self._token is not None and now_ms > self._expires_ms

    def snapshot(self, *, now_ms: int) -> dict:
        with self._lock:
            active = self._token is not None and now_ms <= self._expires_ms
            return {"active": active, "owner": self._owner if active else None, "expires_ms": self._expires_ms}
