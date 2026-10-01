"""Run the field launcher on the Pi -- readiness gates, or the launch itself.

Run ON the Pi via _pi_run_file.py:

    python pi-tools\\_pi_run_file.py pi-tools\\_pi_launch.py /home/pi/robogame-runtime 300 -- preflight
    python pi-tools\\_pi_run_file.py pi-tools\\_pi_launch.py /home/pi/robogame-runtime 60  -- launch - 6000
    python pi-tools\\_pi_run_file.py pi-tools\\_pi_launch.py /home/pi/robogame-runtime 60  -- launch JUNCTION_3_TURN_LEFT 6000

WHY this exists rather than shipping start_full_route.py itself: that script takes
its root from `Path(__file__).resolve().parent`, and _pi_run_file.py writes the
shipped copy to /tmp -- so run from there it looks for /tmp/rg_runtime and dies
`ModuleNotFoundError: No module named 'rg_runtime'`.  It has to execute from its
real home, which is what this does.

Two modes, deliberately separated:

  preflight  `--preflight-only`: every readiness gate, and it NEVER launches.
             The launcher's own docstring promises the readiness stage sends no
             motion command ("no line reading, no motion command"), so this is
             safe to run while deciding.
  launch     detached.  start_full_route.py runs the route with `subprocess.run`
             in the FOREGROUND, so a launcher started through the SSH channel
             would be killed with the session the moment the tool call returns.
             `setsid -f` puts it in its own session, and the launcher writes its
             own .out and telemetry under logs/ from there.

`launch` must be a SEPARATE invocation from any pgrep-based check of the same
name (see the pi-launch-guard note): a pattern that the launching command line
also contains matches its own shell.
"""
import os
import subprocess
import sys

ROOT = "/home/pi/robogame-runtime"
PY = f"{ROOT}/.venv/bin/python"
LAUNCHER = f"{ROOT}/start_full_route.py"


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "preflight"

    if mode == "preflight":
        print(f"[preflight] {LAUNCHER} --preflight-only", flush=True)
        done = subprocess.run([PY, LAUNCHER, "--preflight-only"], cwd=ROOT)
        print(f"[preflight] exit={done.returncode}")
        return done.returncode

    if mode != "launch":
        print(__doc__)
        return 2

    start = sys.argv[2] if len(sys.argv) > 2 else "-"
    timeout = sys.argv[3] if len(sys.argv) > 3 else "6000"
    cmd = [PY, LAUNCHER, "--timeout-s", timeout]
    if start and start != "-":
        cmd += ["--from", start]
    # setsid -f: new session, detached from this SSH channel.  Without it the
    # route dies with the exec channel that started it.
    argv = ["setsid", "-f"] + cmd
    print("[launch] " + " ".join(argv), flush=True)
    subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    print("[launch] detached; the launcher writes its own .out and telemetry "
          "under logs/", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
