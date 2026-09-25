"""Test whether back-to-back startup commands get corrupted.

Mimics run_auto.py's startup burst: STOP + ENC + SPD with no pacing.
Sends NO motion commands. Keeps one connection open (the RFCOMM link drops
when idle, so reopening per round would just measure the flapping).
"""
import time

from rg_runtime.transports import SerialTransport

ROUNDS = 12
DEVICE = "/dev/robogame-chassis"


def open_with_retry(attempts=20):
    for attempt in range(attempts):
        try:
            return SerialTransport(DEVICE, 9600)
        except Exception as exc:
            if attempt == attempts - 1:
                raise
            print(f"    (open retry {attempt + 1}: {exc})")
            time.sleep(1.0)


def drain(transport, seconds):
    deadline = time.monotonic() + seconds
    replies = []
    while time.monotonic() < deadline:
        replies.extend(transport.read_lines())
        time.sleep(0.05)
    return [line.strip() for line in replies if line.strip()]


def run_round(transport, paced):
    for command in ("STOP", "ENC", "SPD"):
        transport.send_line(command + "\r\n")
        if paced:
            time.sleep(0.25)
            transport.read_lines()
    return drain(transport, 1.5)


for paced in (False, True):
    label = "PACED (0.25s gaps)" if paced else "BACK-TO-BACK (current code)"
    print(f"\n=== {label} ===")
    transport = open_with_retry()
    errors = 0
    try:
        for index in range(ROUNDS):
            try:
                replies = run_round(transport, paced)
            except Exception as exc:
                print(f"  round {index + 1:2d}: link error: {exc} -- reopening")
                transport.close()
                transport = open_with_retry()
                continue
            bad = [r for r in replies if "use V, M, D" in r]
            if bad:
                errors += 1
            print(f"  round {index + 1:2d}: {replies}{'  <-- CORRUPTED' if bad else ''}")
            time.sleep(0.3)
    finally:
        transport.close()
    print(f"  --> {errors}/{ROUNDS} rounds produced an unknown-command error")
