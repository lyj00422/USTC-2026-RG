"""Probe which chassis commands the CURRENT firmware accepts.

Sends only non-motion commands: STOP (safety), ENC, SPD. Never sends V/M/D/SEQ.
"""
import time

from rg_runtime.transports import SerialTransport

transport = SerialTransport("/dev/robogame-chassis", 9600)
print("opened /dev/robogame-chassis @ 9600\n")


def probe(command):
    print(f"--- send {command!r} ---")
    try:
        transport.send_line(command + "\r\n")
    except Exception as exc:
        print(f"    write failed: {exc}")
        return
    deadline = time.monotonic() + 1.5
    got = False
    while time.monotonic() < deadline:
        try:
            for line in transport.read_lines():
                print(f"    recv: {line.strip()!r}")
                got = True
        except Exception as exc:
            print(f"    read failed: {exc}")
            return
        time.sleep(0.05)
    if not got:
        print("    (no reply)")
    print()


try:
    for command in ("STOP", "ENC", "SPD", "ENC RESET"):
        probe(command)
finally:
    transport.close()
    print("closed")
