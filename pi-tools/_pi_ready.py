"""Read-only readiness check before a motion run.

Reports whether the chassis link is up and what the line sensor is reading right
now, so a run is never started from an unknown pose.  Sends STOP only -- no
motion command of any kind.
"""
import os
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

from control_hub.services.line_service import LineSensorService  # noqa: E402
from rg_runtime.app_support import load_runtime_config  # noqa: E402

RUNTIME_CONFIG = "/home/pi/robogame-runtime/config/runtime.yaml"
DEV = "/dev/robogame-chassis"


def bits(mask):
    return "".join(str((mask >> (7 - i)) & 1) for i in range(8)) if mask is not None else "--------"


def main():
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 8

    print(f"chassis node : {'present' if os.path.exists(DEV) else 'ABSENT'}  ({DEV})")

    cfg = load_runtime_config(RUNTIME_CONFIG)
    service = LineSensorService(
        transport=cfg.line_transport, rx_gpio=cfg.line_rx_gpio, tx_gpio=cfg.line_tx_gpio,
        baudrate=cfg.line_baudrate, mode=cfg.line_frame_mode, active_level=cfg.line_active_level,
        reverse_order=cfg.line_reverse_order, enabled=cfg.line_enabled,
        request_command=cfg.line_request_command, startup_delay_s=0.0,
        request_retry_s=cfg.line_request_retry_s,
    )
    service.start()
    try:
        if not service.snapshot.connected:
            print(f"line sensor  : NOT CONNECTED  ({service.snapshot.error})")
            return 1
        seen = []
        for _ in range(samples):
            state = service.poll_once()
            mask = state["sensor_mask"]
            seen.append(mask)
            print(f"  mask {mask if mask is not None else '--':>4}  {bits(mask)}")
            time.sleep(0.1)
    finally:
        service.close()

    live = [m for m in seen if m is not None]
    if not live:
        print("line sensor  : DEAD -- no readings at all")
        return 1
    final = live[-1]
    print(f"\nline sensor  : {len(live)}/{samples} readings, final mask "
          f"{final} {bits(final)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
