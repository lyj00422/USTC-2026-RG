"""Read the chassis encoder once and print every projection.  Short-lived by design.

The chain drops the RFCOMM link every ~10 s on this chassis (measured
2026-09-15: 11:56:12, :25, :38, :52, 11:57:05, :27 -- the maintainer service
reconnects each time).  Any script that holds the port for longer than that gets
a stale fd and dies with

    device reports readiness to read but returned no data

The firmware's counters are CUMULATIVE and survive the drop, so a reading taken
after reconnecting still contains all the movement that happened while the link
was down.  That is how a measurement is recovered from a run that died mid-move:
take the ENC before the move, and read it again whenever the link is next alive.

Usage:
    python3 _pi_enc_read.py
"""

import re
import sys
import time

import serial

DEV = "/dev/robogame-chassis"
BAUD = 9600
# Anchored on "ENC " on purpose.  The unanchored pattern also matches the
# SPD reply -- "SPD LF 0 RF 0 LR 0 RR 0 OUT 0 0 0 0" -- so a probe that
# alternates ENC and SPD queries reads zeros as real encoder counts.
# Measured 2026-09-15: the odometer column alternated between the true
# distance and ~0, and the baseline could be taken from a zero.
ENC_RE = re.compile(r"ENC\s+LF (-?\d+) RF (-?\d+) LR (-?\d+) RR (-?\d+)")


def main():
    ser = serial.Serial(DEV, BAUD, timeout=0.05)
    try:
        ser.reset_input_buffer()
        latest = None
        # Retry a few times: the firmware answers only the first command of a
        # burst, and a write that lands while the link is being rebuilt is lost.
        for _ in range(6):
            ser.write(b"ENC\r\n")
            ser.flush()
            deadline = time.monotonic() + 0.25
            while time.monotonic() < deadline:
                chunk = ser.read(4096)
                if chunk:
                    match = ENC_RE.search(chunk.decode("ascii", "replace"))
                    if match:
                        latest = tuple(int(match.group(i)) for i in range(1, 5))
            if latest is not None:
                break
        if latest is None:
            print("no ENC reply")
            return 1
        lf, rf, lr, rr = latest
        print(f"ENC LF {lf} RF {rf} LR {lr} RR {rr}")
        print(f"  forward = {(-lf + rf - lr + rr) / 4.0:+10.1f} counts"
              f"   ({( -lf + rf - lr + rr) / 4.0 / 58.8:+8.2f} cm)")
        print(f"  lateral = {( lf + rf - lr - rr) / 4.0:+10.1f} counts"
              f"   ({( lf + rf - lr - rr) / 4.0 / 56.8:+8.2f} cm, positive = left)")
        total = lf + rf + lr + rr
        print(f"  yaw     = {total / 4.0:+10.1f} counts"
              f"   (sum of the four; positive = left rotation)")
        print(f"  raw sum = {total:+d}")
        return 0
    finally:
        try:
            for _ in range(3):
                ser.write(b"STOP\r\n")
                ser.flush()
                time.sleep(0.02)
        except Exception:                 # noqa: BLE001 - best effort on the way out
            pass
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
