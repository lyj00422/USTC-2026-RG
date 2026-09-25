from __future__ import annotations

import argparse
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "app.py",
    "run_route_v2.py",
    "run_replay.py",
    "arm_control.py",
    "requirements.txt",
    "tools/capture_orange_evidence.py",
)
DIRECTORIES = ("route_v2", "rg_runtime")
SMOKE_TESTS = (
    "tests/test_inventory_strategy_new.py",
    "tests/test_route_inventory_loop.py",
    "tests/test_suction_latch.py",
    "tests/test_pickup_vision.py",
)


def build_bundle(output: str | Path) -> Path:
    destination = Path(output).resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for relative in FILES:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    for relative in DIRECTORIES:
        shutil.copytree(ROOT / relative, destination / relative)
    config_target = destination / "config" / "route_v2.yaml"
    config_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "config" / "route_v2.yaml", config_target)
    for relative in SMOKE_TESTS:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(build_bundle(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
