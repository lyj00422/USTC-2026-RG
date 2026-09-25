from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import select
import sys
import time

from route_v2.calibration import (
    ProbeSample, read_probe_samples, run_probe_analysis, write_probe_outputs,
)


LATERAL_COUNTS_PER_CM = 56.8


def _operator_requested_stop() -> bool:
    if sys.platform == "win32":
        import msvcrt
        return bool(msvcrt.kbhit() and msvcrt.getwch() in {"\r", "\n"})
    readable, _, _ = select.select([sys.stdin], [], [], 0)
    return bool(readable and sys.stdin.readline() is not None)


def _encoder_lateral(reply) -> tuple[float, tuple[int, int, int, int]] | None:
    if getattr(reply, "kind", None) != "encoder":
        return None
    numbers = [float(item) for item in re.findall(
        r"(?:LF|RF|LR|RR)\s+(-?\d+(?:\.\d+)?)", str(getattr(reply, "value", ""))
    )]
    if len(numbers) != 4:
        return None
    raw = tuple(int(value) for value in numbers)
    lf, rf, lr, rr = raw
    return (lf + rf - lr - rr) / 4.0 / LATERAL_COUNTS_PER_CM, raw


def _capture_profile(args) -> int:
    import cv2
    from rg_runtime.config import load_camera_config
    from run_route_v2 import _configure_camera

    config = load_camera_config(args.camera_config)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    camera = cv2.VideoCapture(config.camera)
    if not camera.isOpened():
        raise RuntimeError("camera did not open")
    for warning in _configure_camera(camera, config, cv2):
        print(f"WARNING: {warning}", flush=True)
    manifest = []
    try:
        for index in range(args.frames):
            ok, frame = camera.read()
            if not ok or frame is None:
                raise RuntimeError(f"camera read failed at frame {index}")
            path = output / f"{args.profile}_{index:04d}.jpg"
            if not cv2.imwrite(str(path), frame):
                raise RuntimeError(f"failed to write {path}")
            manifest.append({"frame": index, "path": path.name,
                             "shape": list(frame.shape), "timestamp_s": time.time()})
    finally:
        camera.release()
    (output / "manifest.json").write_text(
        json.dumps({"profile": args.profile, "frames": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


def _probe_strafe(args) -> int:
    if args.confirm != "MOVE":
        raise ValueError("probe-strafe requires --confirm MOVE")
    from rg_runtime.app_support import load_runtime_config
    from rg_runtime.chassis_lock import ChassisPortLock
    from rg_runtime.devices import ChassisDevice
    from rg_runtime.transports import SerialTransport
    from control_hub.services.line_service import LineSensorService

    runtime = load_runtime_config(args.runtime_config)
    lock = ChassisPortLock()
    lock.acquire()
    transport = SerialTransport(runtime.chassis_device, runtime.chassis_baudrate, timeout_s=0.0)
    chassis = ChassisDevice(transport)
    line = LineSensorService(
        transport=runtime.line_transport,
        device=runtime.line_device,
        rx_gpio=runtime.line_rx_gpio,
        tx_gpio=runtime.line_tx_gpio,
        baudrate=runtime.line_baudrate,
        mode=runtime.line_frame_mode,
        active_level=runtime.line_active_level,
        reverse_order=runtime.line_reverse_order,
        enabled=runtime.line_enabled,
        request_command=runtime.line_request_command,
        startup_delay_s=runtime.line_startup_delay_s,
        request_retry_s=runtime.line_request_retry_s,
    )
    samples: list[ProbeSample] = []
    started = time.monotonic()
    baseline: float | None = None
    stop_reason = "hard_timeout"
    operator_stop_at = None
    last_v = -float("inf")
    last_query = -float("inf")
    direction_speed = abs(args.speed) if args.direction == "left" else -abs(args.speed)
    try:
        line.start()
        if not line.snapshot.connected:
            raise RuntimeError(f"line sensor unavailable: {line.snapshot.error or line.snapshot.state}")
        chassis.stop()
        print("MOVING: press Enter or Ctrl+C, or use the physical emergency stop", flush=True)
        while True:
            now = time.monotonic()
            for reply in chassis.poll():
                reading = _encoder_lateral(reply)
                if reading is not None:
                    lateral, raw = reading
                    if baseline is None:
                        baseline = lateral
                    line_sample = line.poll_once()
                    mask = line_sample.get("sensor_mask") if isinstance(line_sample, dict) else getattr(line_sample, "sensor_mask", None)
                    samples.append(ProbeSample(
                        now - started, lateral - baseline,
                        encoder=raw, sensor_mask=None if mask is None else int(mask),
                    ))
            motion = abs(samples[-1].lateral_cm) if samples else 0.0
            if _operator_requested_stop():
                stop_reason = "operator_stop"
                operator_stop_at = motion
                break
            if motion >= args.hard_max_cm:
                stop_reason = "hard_distance_limit"
                break
            if now - started >= args.hard_timeout_s:
                stop_reason = "hard_timeout"
                break
            if now - last_v >= 0.25:
                chassis.set_velocity(0, direction_speed, 0)
                last_v = now
            elif now - last_query >= 0.1:
                chassis.request_encoder()
                last_query = now
            time.sleep(0.02)
    except KeyboardInterrupt:
        stop_reason = "operator_stop"
        operator_stop_at = abs(samples[-1].lateral_cm) if samples else 0.0
    finally:
        for _ in range(5):
            try:
                chassis.stop()
            except Exception:
                pass
            time.sleep(0.05)
        # Capture the braking tail after STOP without issuing further motion.
        tail_deadline = time.monotonic() + 0.75
        while time.monotonic() < tail_deadline:
            try:
                chassis.request_encoder()
                time.sleep(0.05)
                for reply in chassis.poll():
                    reading = _encoder_lateral(reply)
                    if reading is not None and baseline is not None:
                        lateral, raw = reading
                        line_sample = line.poll_once()
                        mask = line_sample.get("sensor_mask") if isinstance(line_sample, dict) else getattr(line_sample, "sensor_mask", None)
                        samples.append(ProbeSample(
                            time.monotonic() - started, lateral - baseline,
                            encoder=raw, sensor_mask=None if mask is None else int(mask),
                        ))
            except Exception:
                break
        try:
            line.close()
        finally:
            try:
                transport.close()
            finally:
                lock.release()
    if not samples:
        samples.append(ProbeSample(0, 0))
    result = run_probe_analysis(
        samples,
        direction=args.direction,
        suggested_key=args.suggested_key,
        hard_max_cm=args.hard_max_cm,
        hard_timeout_s=args.hard_timeout_s,
        operator_stop_at_cm=operator_stop_at,
    )
    # Preserve the live loop's terminal reason when sampling granularity stopped
    # just short of the exact configured boundary.
    if result.reason == "samples_exhausted":
        result = result.__class__(**{**result.__dict__, "reason": stop_reason})
    paths = write_probe_outputs(args.output, result)
    print(json.dumps({"samples": str(paths[0]), "result": str(paths[1]),
                      **result.to_dict()}, ensure_ascii=False))
    return 0


def _analyze(args) -> int:
    values = read_probe_samples(args.input)
    result = run_probe_analysis(
        values, direction=args.direction, suggested_key=args.suggested_key,
        hard_max_cm=args.hard_max_cm, hard_timeout_s=args.hard_timeout_s,
        operator_stop_at_cm=args.operator_stop_at_cm,
    )
    _, result_path = write_probe_outputs(args.output, result)
    print(result_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Route V2 guarded vision calibration")
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture-profile", help="save camera frames for one visual profile")
    capture.add_argument("--profile", required=True)
    capture.add_argument("--camera-config", default="config/camera_config.yaml")
    capture.add_argument("--output", required=True)
    capture.add_argument("--frames", type=int, default=30)
    capture.set_defaults(handler=_capture_profile)

    probe = sub.add_parser("probe-strafe", help="low-speed strafe with hard guards")
    probe.add_argument("--direction", choices=("left", "right"), required=True)
    probe.add_argument("--speed", type=int, required=True)
    probe.add_argument("--hard-max-cm", type=float, required=True)
    probe.add_argument("--hard-timeout-s", type=float, required=True)
    probe.add_argument("--suggested-key", required=True)
    probe.add_argument("--output", required=True)
    probe.add_argument("--runtime-config", default="config/runtime.yaml")
    probe.add_argument("--confirm", required=True)
    probe.set_defaults(handler=_probe_strafe)

    analyze = sub.add_parser("analyze", help="analyze an existing probe JSONL")
    analyze.add_argument("--input", required=True)
    analyze.add_argument("--direction", choices=("left", "right"), required=True)
    analyze.add_argument("--hard-max-cm", type=float, required=True)
    analyze.add_argument("--hard-timeout-s", type=float, required=True)
    analyze.add_argument("--operator-stop-at-cm", type=float)
    analyze.add_argument("--suggested-key", required=True)
    analyze.add_argument("--output", required=True)
    analyze.set_defaults(handler=_analyze)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "frames", 1) < 1:
        raise ValueError("--frames must be positive")
    if hasattr(args, "speed") and not 1 <= abs(args.speed) <= 30:
        raise ValueError("probe speed must be in 1..30")
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
