"""Live line-sensor monitor: prints the mask whenever it changes.

Read-only, no chassis commands.  Use it to settle the bit polarity: pass a white
sheet under the probes, then a piece of black tape, and watch which bits flip.

Usage: set DURATION below (seconds) and run.
"""

import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

from control_hub.services.line_service import LineSensorService
from rg_runtime.app_support import load_runtime_config

DURATION = 5.0
CONFIG = "/home/pi/robogame-runtime/config/runtime.yaml"

cfg = load_runtime_config(CONFIG)
service = LineSensorService(
    transport=cfg.line_transport, rx_gpio=cfg.line_rx_gpio, tx_gpio=cfg.line_tx_gpio,
    baudrate=cfg.line_baudrate, mode=cfg.line_frame_mode, active_level=cfg.line_active_level,
    reverse_order=cfg.line_reverse_order, enabled=cfg.line_enabled,
    request_command=cfg.line_request_command, startup_delay_s=0.0,
    request_retry_s=cfg.line_request_retry_s,
)
service.start()
print(f"watching for {DURATION:.0f}s (active_level={cfg.line_active_level})", flush=True)

previous = None
started = time.time()
while time.time() - started < DURATION:
    time.sleep(0.05)
    status = service.poll_once()
    mask = status["sensor_mask"]
    if mask is not None and mask != previous:
        previous = mask
        bits = "".join(str(int((mask >> (7 - i)) & 1)) for i in range(8))
        print(
            f"  t={time.time() - started:5.1f}s mask={mask:3d} bits(x1..x8)={bits} "
            f"sensors={status['sensors']} line_error={status['line_error']} lost={status['line_lost']}",
            flush=True,
        )
print("watch finished", flush=True)
service.close()
