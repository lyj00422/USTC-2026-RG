"""Read-only line-sensor diagnostic for the RoboGame Pi.

Sends NO chassis motion commands.  It only talks to the eight-channel line
module on its software UART, then exercises the same LineSensorService that
run_route_v2.py builds.

Part A: raw pigpio, both GPIO directions (RX=23/TX=24 then the swap).
Part B: LineSensorService exactly as run_route_v2._run_hardware() builds it.
"""

import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

BAUD = 115200
REQUEST = b"$0,0,1#"
STOP_STREAM = b"$0,0,0#"


def _send(g, tx, payload):
    g.wave_clear()
    g.wave_add_serial(tx, BAUD, payload, bb_bits=8)
    wave_id = g.wave_create()
    if wave_id < 0:
        raise RuntimeError(f"wave_create returned {wave_id}")
    try:
        g.wave_send_once(wave_id)
        deadline = time.time() + 0.5
        while g.wave_tx_busy() and time.time() < deadline:
            time.sleep(0.001)
    finally:
        try:
            g.wave_delete(wave_id)
        except Exception:
            pass


def part_a(rx, tx, seconds=3.0):
    print(f"\n=== Part A: raw pigpio  RX=GPIO{rx} TX=GPIO{tx} ===", flush=True)
    import pigpio

    g = pigpio.pi()
    print(f"pigpio daemon connected: {g.connected}", flush=True)
    if not g.connected:
        return
    opened = False
    try:
        g.set_mode(rx, pigpio.INPUT)
        g.set_mode(tx, pigpio.INPUT)
        g.set_mode(tx, pigpio.OUTPUT)
        result = g.bb_serial_read_open(rx, BAUD, 8)
        print(f"bb_serial_read_open(GPIO{rx}) -> {result!r}", flush=True)
        if result not in (0, None):
            print("  => open REJECTED by pigpio (GPIO busy / already claimed)", flush=True)
            return
        opened = True
        _send(g, tx, REQUEST)
        received = bytearray()
        deadline = time.time() + seconds
        next_request = time.time() + 1.0
        while time.time() < deadline:
            count, data = g.bb_serial_read(rx)
            if count:
                received.extend(bytes(data[:count]))
            if time.time() >= next_request:
                next_request = time.time() + 1.0
                _send(g, tx, REQUEST)
            time.sleep(0.02)
        print(f"received {len(received)} bytes: {bytes(received)[:160]!r}", flush=True)
    except Exception as exc:
        print(f"EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
    finally:
        if opened:
            try:
                _send(g, tx, STOP_STREAM)
            except Exception as exc:
                print(f"  stop-stream send failed: {exc}", flush=True)
            try:
                print(f"  bb_serial_read_close(GPIO{rx}) -> {g.bb_serial_read_close(rx)!r}", flush=True)
            except Exception as exc:
                print(f"  read_close failed: {exc}", flush=True)
        try:
            g.wave_clear()
        except Exception:
            pass
        g.stop()


def part_b():
    print("\n=== Part B: LineSensorService (what route v2 builds) ===", flush=True)
    from control_hub.services.line_service import LineSensorService
    from rg_runtime.app_support import load_runtime_config

    cfg = load_runtime_config("/home/pi/robogame-runtime/config/runtime.yaml")
    print(
        f"config: transport={cfg.line_transport} rx=GPIO{cfg.line_rx_gpio} tx=GPIO{cfg.line_tx_gpio} "
        f"baud={cfg.line_baudrate} mode={cfg.line_frame_mode} active_level={cfg.line_active_level} "
        f"enabled={cfg.line_enabled} startup_delay_s={cfg.line_startup_delay_s}",
        flush=True,
    )
    service = LineSensorService(
        transport=cfg.line_transport,
        device=cfg.line_device,
        rx_gpio=cfg.line_rx_gpio,
        tx_gpio=cfg.line_tx_gpio,
        baudrate=cfg.line_baudrate,
        mode=cfg.line_frame_mode,
        active_level=cfg.line_active_level,
        reverse_order=cfg.line_reverse_order,
        enabled=cfg.line_enabled,
        request_command=cfg.line_request_command,
        startup_delay_s=0.0,
        request_retry_s=cfg.line_request_retry_s,
    )
    service.start()
    snap = service.snapshot
    print(f"after start(): connected={snap.connected} state={snap.state} error={snap.error}", flush=True)
    for index in range(15):
        time.sleep(0.2)
        status = service.poll_once()
        print(
            f"  poll[{index:02d}] state={status['state']} connected={status['connected']} "
            f"mask={status['sensor_mask']} line_error={status['line_error']} "
            f"attempts={status['request_attempts']} raw={status['raw']!r} error={status['error']!r}",
            flush=True,
        )
    service.close()
    print("closed", flush=True)


if __name__ == "__main__":
    part_a(23, 24)
    part_a(24, 23)
    part_b()
