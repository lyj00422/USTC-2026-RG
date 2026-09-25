"""Compare RFCOMM stability idle vs. with periodic read-only ENC traffic.

Sends ONLY 'ENC' (read-only query). Sends NO motion command.
"""
import subprocess
import time

DEVICE = "/dev/robogame-chassis"


def link_up():
    try:
        out = subprocess.run(["rfcomm", "show", "0"], capture_output=True, text=True, timeout=3)
        return "connected" in out.stdout
    except Exception:
        return False


def observe(label, seconds, traffic):
    print(f"\n=== {label} ({seconds}s, traffic={traffic}) ===", flush=True)
    start = time.monotonic()
    transport = None
    if traffic:
        try:
            from rg_runtime.transports import SerialTransport

            transport = SerialTransport(DEVICE, 9600)
            print("  opened device for ENC polling")
        except Exception as exc:
            print(f"  could not open device: {exc}")
            traffic = False

    was_up = None
    transitions = 0
    samples = 0
    try:
        while time.monotonic() - start < seconds:
            up = link_up()
            samples += 1
            if was_up is None or up != was_up:
                elapsed = time.monotonic() - start
                print(f"  t={elapsed:5.1f}s  link={'UP' if up else 'DOWN'}", flush=True)
                if was_up is not None:
                    transitions += 1
                was_up = up
            if traffic and transport is not None:
                try:
                    transport.send_line("ENC\r\n")
                    transport.read_lines()
                except Exception as exc:
                    print(f"  t={time.monotonic() - start:5.1f}s  write failed: {exc}", flush=True)
                    break
            time.sleep(2.0 if traffic else 0.5)
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
    print(f"  samples={samples} transitions={transitions}")
    return transitions


idle_transitions = observe("PASSIVE IDLE", 30, traffic=False)
traffic_transitions = observe("WITH ENC TRAFFIC", 60, traffic=True)

print("\n=== RESULT ===")
print(f"idle 30s  -> {idle_transitions} link transitions")
print(f"traffic 60s -> {traffic_transitions} link transitions")
