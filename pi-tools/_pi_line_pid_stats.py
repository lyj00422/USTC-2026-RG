"""Summarise the line-following PID's own inputs and outputs per state.

Two knobs can be argued for a car that follows the line with a small residual
yaw: `kd` (reacts to the RATE the error is closing at, i.e. to how fast the car
is converging on the line) and `ki` (removes a CONSTANT bias).  They look the
same from the driver's seat, so read it off the data instead: a persistent
one-sided `line_error` is a bias and wants the integral; an error that crosses
zero but with the correction small while the car is off the line wants gain.

Run it ON the Pi:
    python _pi_run_file.py pi-tools/_pi_line_pid_stats.py /home/pi/robogame-runtime 300 \
        -- /home/pi/robogame-runtime/logs/route_v2_full_XXXX.jsonl
"""
import json
import statistics
import sys

MIN_SAMPLES = 40


def main():
    path = sys.argv[1]
    per_state = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            error = row.get("line_error")
            if error is None:
                continue
            per_state.setdefault(row.get("state"), []).append(
                (error, row.get("vy"), row.get("wz"), row.get("actual_speed"))
            )
    print(f"{'state':<26}{'n':>6}{'err_mean':>10}{'err_med':>9}"
          f"{'err_p90':>9}{'pos%':>7}{'vy_mean':>9}{'wz_mean':>9}")
    for state, rows in sorted(per_state.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < MIN_SAMPLES:
            continue
        errors = [r[0] for r in rows]
        vys = [r[1] for r in rows if r[1] is not None]
        wzs = [r[2] for r in rows if r[2] is not None]
        ordered = sorted(errors)
        p90 = ordered[int(0.9 * (len(ordered) - 1))]
        print(f"{state:<26}{len(rows):>6}"
              f"{statistics.mean(errors):>10.3f}"
              f"{statistics.median(errors):>9.3f}"
              f"{p90:>9.3f}"
              f"{100.0 * sum(e > 0 for e in errors) / len(errors):>6.0f}%"
              f"{(statistics.mean(vys) if vys else float('nan')):>9.2f}"
              f"{(statistics.mean(wzs) if wzs else float('nan')):>9.2f}")


if __name__ == "__main__":
    main()
