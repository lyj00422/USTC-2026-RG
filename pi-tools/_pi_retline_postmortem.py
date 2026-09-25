"""READ-ONLY post-mortem of the pickup return-to-line legs.

The 2026-09-24 change made "one-way return" the primary logic for the orange
area and demoted the growing swing to a fallback, so a FAULT in
PICKUP_*_RETURN_TO_LINE can now come from either branch.  The telemetry does not
carry the controller's `reason` string, but it does carry `pickup_phase`, and the
two branches leave different phase signatures:

  one-way      RETURN_BASELINE -> one SEEK_OUT/SEEK_BACK stretch -> FAULT
  growing swing RETURN_BASELINE -> many alternating SEEK_OUT/SEEK_BACK -> FAULT

This dumps every return-line episode with its phase transitions and the lateral
progression, so the branch can be identified from the recorded run.

Read-only: opens the telemetry file and nothing else.

Usage:
    python _pi_retline_postmortem.py [telemetry_filename]
"""

import json
import os
import sys
from pathlib import Path

LOGS = Path("/home/pi/robogame-runtime/logs")
PHASES = ("RETURN_BASELINE", "SEEK_OUT", "SEEK_BACK", "DONE", "FAULT")


def load(path):
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def episodes(rows):
    """Contiguous runs of a *_RETURN_TO_LINE state."""
    out = []
    for row in rows:
        state = row.get("state") or ""
        if state.endswith("RETURN_TO_LINE"):
            if out and out[-1]["state"] == state:
                out[-1]["rows"].append(row)
            else:
                out.append({"state": state, "rows": [row]})
    return out


def phase_chain(rows):
    """The pickup_phase values in order, collapsing repeats."""
    chain = []
    for row in rows:
        phase = row.get("pickup_phase")
        if not chain or chain[-1][0] != phase:
            chain.append([phase, 1])
        else:
            chain[-1][1] += 1
    return chain


def summarise(idx, ep):
    rows = ep["rows"]
    print("=" * 72)
    print(f"[{idx}] {ep['state']}   {len(rows)} ticks   "
          f"t {rows[0]['t']:.1f} -> {rows[-1]['t']:.1f} "
          f"({rows[-1]['t'] - rows[0]['t']:.1f}s)")

    chain = phase_chain(rows)
    print(f"  pickup_phase chain ({len(chain)} segments):")
    for phase, n in chain[:30]:
        print(f"     {str(phase):<18} {n:>6} ticks")
    if len(chain) > 30:
        print(f"     ... {len(chain) - 30} more")

    lat = [r.get("lateral_cm") for r in rows if r.get("lateral_cm") is not None]
    if lat:
        print(f"  lateral_cm: {lat[0]:.1f} -> {lat[-1]:.1f} "
              f"(min {min(lat):.1f} max {max(lat):.1f})")

    seeks = [r for r in rows if r.get("pickup_phase") in ("SEEK_OUT", "SEEK_BACK")]
    if seeks:
        flips = 0
        prev = None
        for r in seeks:
            cur = r["pickup_phase"]
            if prev is not None and cur != prev:
                flips += 1
            prev = cur
        print(f"  seek ticks: {len(seeks)}   phase flips: {flips}  "
              f"-> {'GROWING SWING' if flips > 2 else 'ONE-WAY'}")
        print(f"  seek lateral span: {min(r['lateral_cm'] for r in seeks if r['lateral_cm'] is not None):.1f}"
              f" .. {max(r['lateral_cm'] for r in seeks if r['lateral_cm'] is not None):.1f}")

    # The route counts a SET bit as WHITE and a CLEAR bit as BLACK:
    #   probes = 8 - popcount(mask)          (run_route_v2.py:203-207)
    #   line_found = mask in seek_line_masks or (min_black <= probes < 8)
    # so probes==8 (mask 0x00, all black) and probes==0 (mask 0xFF, all white OR
    # no sensor reading at all -- run_route_v2.py:975 coerces None to 0xFF) both
    # FAIL the test.  Counting "mask >= 4 as black" is the wrong polarity.
    masks = [r.get("mask") for r in seeks] if seeks else []
    masks = [m for m in masks if m is not None]
    if masks:
        probes = [8 - bin(m & 0xFF).count("1") for m in masks]
        ok = sum(1 for p in probes if 4 <= p < 8)
        dead = sum(1 for m in masks if m == 0xFF and m == masks[-1])  # noqa: F841
        print(f"  seek mask probes (clear bits = black): min {min(probes)} max {max(probes)}")
        print(f"  line_found-satisfying ticks: {ok}/{len(probes)}")
        print(f"  mask 0xFF (all-white OR no reading): {sum(1 for m in masks if m == 0xFF)}"
              f"   mask 0x00 (all-black, rejected): {sum(1 for m in masks if m == 0x00)}")
        # Longest run of consecutive satisfying ticks -- confirm_frames needs 2.
        best = run = 0
        for p in probes:
            run = run + 1 if 4 <= p < 8 else 0
            best = max(best, run)
        print(f"  longest consecutive satisfying run: {best} "
              f"(confirm_frames needs 2)")
        from collections import Counter
        print("  top masks:", ", ".join(f"0x{m:02X}x{n}" for m, n in
                                        Counter(masks).most_common(6)))
        # 0xFF is ambiguous: the sensor really reading all-white, or the sensor
        # returning nothing at all (run_route_v2.py:975 maps None -> 0xFF).
        # line_error tells them apart: None == no reading, a number == a real read.
        ff = [r for r in seeks if r.get("mask") == 0xFF]
        if ff:
            no_read = sum(1 for r in ff if r.get("line_error") is None)
            print(f"  of the 0xFF ticks: line_error None (NO READING) {no_read}/{len(ff)}, "
                  f"numeric (real all-white) {len(ff) - no_read}/{len(ff)}")

    last = rows[-1]
    print(f"  last tick: pickup_phase={last.get('pickup_phase')} "
          f"intent={last.get('intent')} mask={last.get('mask')} "
          f"line_error={last.get('line_error')}")


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "route_v2_telemetry.jsonl"
    path = LOGS / name
    if not path.exists():
        print("missing:", path)
        return 1

    rows = load(path)
    print("file:", name, " rows:", len(rows),
          " size:", os.path.getsize(path))
    if not rows:
        return 1
    print("t: %.1f -> %.1f (%.1fs)" % (rows[0]["t"], rows[-1]["t"],
                                       rows[-1]["t"] - rows[0]["t"]))

    eps = episodes(rows)
    print(f"\nreturn-line episodes: {len(eps)}")
    for idx, ep in enumerate(eps, 1):
        summarise(idx, ep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
