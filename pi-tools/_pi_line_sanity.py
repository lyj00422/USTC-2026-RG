"""Read the line sensor a few times and report the mask.

Run before any motion command: the standing rule is never to drive the car when
the line sensor is not delivering readings.
"""
import sys
import time

from rg_runtime.devices import LineSensor


def main():
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    sensor = LineSensor()
    sensor.open()
    try:
        seen = []
        for i in range(samples):
            mask = sensor.read_mask()
            seen.append(mask)
            print("%2d  %s  0x%02X" % (i, format(mask, "08b"), mask))
            time.sleep(0.12)
    finally:
        sensor.close()

    if all(m is None for m in seen):
        print("LINE SENSOR DEAD -- no readings at all")
        return 1
    print("ok: %d/%d readings" % (sum(m is not None for m in seen), samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
