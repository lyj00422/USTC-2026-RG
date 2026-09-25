"""Command-line interface for safe Raspberry Pi arm bring-up."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Callable

from .app_support import load_runtime_config
from .arm_tools import ArmSession, discover_serial_ports, execute_console_command
from .devices import ArmDevice
from .hardware_models import ArmMode
from .transports import SerialTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RoboGame arm control and diagnosis")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config/runtime.yaml"))
    parser.add_argument("--device", help="serial device; defaults to runtime.yaml")
    parser.add_argument("--baudrate", type=int, help="serial baud rate; defaults to runtime.yaml")
    parser.add_argument("--log", default="logs/arm_session.jsonl")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list serial ports and CH340 candidates")
    subparsers.add_parser("probe", help="send only STOP, PING and STATUS")
    subparsers.add_parser("console", help="open the guarded interactive console")
    subparsers.add_parser("stop", help="send ARM,STOP and exit")
    return parser


def _format_reply(reply) -> str:
    if is_dataclass(reply):
        fields = ", ".join(f"{key}={value}" for key, value in asdict(reply).items())
        return f"{reply.__class__.__name__}({fields})"
    return str(reply)


def _show_replies(replies, output_fn: Callable[[str], None]) -> None:
    for reply in replies:
        output_fn(f"RX {_format_reply(reply)}")


def _run_console(
    session: ArmSession,
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> None:
    output_fn("Commands: ping, status, stop, enable, run 0..5, suction on/off, quit")
    while True:
        command_line = input_fn("arm> ")
        result = execute_console_command(session, command_line)
        if result == "quit":
            return
        if result:
            output_fn(result)
        command = command_line.strip().lower().split()
        if not command:
            continue
        if command[0] == "enable":
            session.wait_for_mode(ArmMode.READY)
            output_fn("arm is READY")
        elif command[0] == "run":
            routine = int(command[1])
            replies = session.wait_for_action(routine)
            _show_replies(replies, output_fn)
            output_fn(f"action {routine} done")
        else:
            _show_replies(session.poll(), output_fn)


def main(
    argv=None,
    *,
    transport_factory=None,
    input_fn=input,
    output_fn=print,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list":
        ports = discover_serial_ports()
        if not ports:
            output_fn("No serial ports found")
        for port in ports:
            marker = "CH340 candidate" if port.is_ch340 else "serial"
            output_fn(f"{port.device}: {port.description} [{marker}]")
        return 0

    config = load_runtime_config(args.config)
    device = args.device or config.arm_device
    baudrate = args.baudrate or config.arm_baudrate
    factory = transport_factory or (lambda path, baud: SerialTransport(path, baud, timeout_s=0.0))
    transport = factory(device, baudrate)
    session = ArmSession(ArmDevice(transport), transport, args.log)
    try:
        if args.command == "stop":
            session.stop()
            output_fn("ARM,STOP sent")
            return 0
        replies = session.safe_probe()
        _show_replies(replies, output_fn)
        output_fn(f"arm state: {session.arm.state.mode.value}")
        if args.command == "console":
            _run_console(session, input_fn=input_fn, output_fn=output_fn)
        return 0
    except KeyboardInterrupt:
        output_fn("Interrupted; stopping arm")
        return 130
    finally:
        session.close()
