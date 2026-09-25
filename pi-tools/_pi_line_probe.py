"""Line-sensor-only probe. Sends NO chassis motion commands.

Initializes the pigpio soft UART exactly like run_auto.py --start does,
reads a few frames, then closes cleanly.
"""
import time

from auto_route.config import load_route_config
from auto_route.line_reader import LineReader

config = load_route_config("config/route_v1.yaml")
reader = LineReader(
    rx_gpio=config.line_rx_gpio,
    tx_gpio=config.line_tx_gpio,
    auto_swap=config.line_auto_swap,
    baudrate=config.line_baudrate,
    mode=config.line_frame_mode,
    active_level=config.line_active_level,
    reverse_order=config.line_reverse_order,
    request_command=config.line_request_command,
    startup_timeout_s=config.line_startup_timeout_s,
    request_retry_s=config.line_request_retry_s,
    valid_frames_required=config.line_valid_frames_required,
    stale_timeout_s=config.line_stale_timeout_s,
)

print("initializing line sensor (no motion commands will be sent)...", flush=True)
start = time.monotonic()
try:
    state = reader.initialize()
except Exception as exc:
    print(f"LINE INIT FAILED: {type(exc).__name__}: {exc}")
    raise SystemExit(1)

print(f"locked in {time.monotonic() - start:.1f}s  RX=GPIO{reader.rx_gpio} TX=GPIO{reader.tx_gpio}")
print(f"state={state}")
for index in range(5):
    time.sleep(0.2)
    try:
        print(f"  poll[{index}] = {reader.poll()}")
    except Exception as exc:
        print(f"  poll[{index}] raised {type(exc).__name__}: {exc}")
        break
reader.close()
print("closed cleanly")
