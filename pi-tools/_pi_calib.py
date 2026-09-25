"""One-shot D-command calibration probe.

Sends a single D command, waits for DONE D, and reports encoder deltas so the
real motion can be compared against the commanded one.

Usage:
    _pi_calib.py <forward_cm> <right_cm> <rotate_deg> <speed>
    _pi_calib.py 0 0 90 20          # turn right 90 -> expect encoder pattern A
"""
import re
import sys
import time

from rg_runtime.transports import SerialTransport

DEVICE = "/dev/robogame-chassis"
ENC_RE = re.compile(r"LF (-?\d+) RF (-?\d+) LR (-?\d+) RR (-?\d+)")


def open_with_retry(attempts=25):
    for attempt in range(attempts):
        try:
            return SerialTransport(DEVICE, 9600)
        except Exception as exc:
            if attempt == attempts - 1:
                raise
            time.sleep(1.0)


def read_encoder(transport, wait_s=1.2):
    transport.send_line("ENC\r\n")
    deadline = time.monotonic() + wait_s
    latest = None
    while time.monotonic() < deadline:
        for line in transport.read_lines():
            match = ENC_RE.search(line)
            if match:
                latest = tuple(int(match.group(i)) for i in range(1, 5))
        time.sleep(0.05)
    return latest


def main():
    forward_cm, right_cm, rotate_deg, speed = (int(a) for a in sys.argv[1:5])
    transport = open_with_retry()
    try:
        print(f"opened {DEVICE}")
        transport.send_line("STOP\r\n")
        time.sleep(0.3)
        transport.read_lines()

        before = read_encoder(transport)
        print(f"ENC before : {before}")
        if before is None:
            print("could not read baseline encoder; aborting")
            return 1

        time.sleep(0.3)
        command = f"D {forward_cm} {right_cm} {rotate_deg} {speed}"
        print(f"send       : {command}")
        transport.send_line(command + "\r\n")

        start = time.monotonic()
        done = False
        note = ""
        while time.monotonic() - start < 60:
            for line in transport.read_lines():
                text = line.strip()
                if text:
                    print(f"  recv: {text!r}")
                if "DONE D" in line:
                    done = True
                if "ERR" in line and not note:
                    note = text
            if done:
                break
            time.sleep(0.05)
        elapsed = time.monotonic() - start
        print(f"finished   : done={done} elapsed={elapsed:.2f}s {note}")

        transport.send_line("STOP\r\n")
        time.sleep(0.4)
        transport.read_lines()
        after = read_encoder(transport)
        print(f"ENC after  : {after}")

        if before and after:
            delta = tuple(a - b for a, b in zip(after, before))
            print(f"delta      : LF={delta[0]} RF={delta[1]} LR={delta[2]} RR={delta[3]}")
        return 0 if done else 1
    finally:
        transport.close()
        print("closed")


if __name__ == "__main__":
    raise SystemExit(main())
