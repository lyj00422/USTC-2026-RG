"""Read the line sensor repeatedly and report the mask.  Read-only.

Run before every launch: never drive the car when the line sensor is silent.

_pi_run_file.py drops the script in /tmp and runs it from there, so sys.path[0]
is /tmp -- the runtime package has to be added by hand, which is also why
_pi_line_sanity.py fails with ModuleNotFoundError on the Pi.
"""
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

from rg_runtime.devices import LineSensor  # noqa: E402


def main():
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    sensor = LineSensor()
    sensor.open()
    try:
        seen = []
        for _ in range(samples):
            seen.append(sensor.read_mask())
            time.sleep(0.1)
    finally:
        sensor.close()

    live = [m for m in seen if m is not None]
    for i, m in enumerate(seen):
        print("%2d  %s  0x%02X" % (i, format(m, "08b") if m is not None
                                   else 0, m if m is not None else 0))
    if not live:
        print("LINE SENSOR DEAD -- no readings at all")
        return 1
    print("ok: %d/%d readings, distinct %s"
          % (len(live), samples, sorted({"0x%02X" % m for m in live})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
