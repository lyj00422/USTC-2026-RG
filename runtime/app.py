"""RoboGame runtime command-line entry point."""

from __future__ import annotations

import argparse

from rg_runtime.app_support import load_runtime_config, validate_mode


def main() -> int:
    parser = argparse.ArgumentParser(description="RoboGame Raspberry Pi runtime")
    parser.add_argument("mode", choices=("diagnose", "manual", "dry-run", "auto"))
    parser.add_argument("--config", default="config/runtime.yaml")
    parser.add_argument("--simulate-sensors", action="store_true")
    args = parser.parse_args()
    config = load_runtime_config(args.config)
    validate_mode(
        args.mode,
        config,
        line_available=args.simulate_sensors,
        encoder_available=args.simulate_sensors,
    )
    if args.mode == "auto":
        print("auto mode guard passed; hardware loop is not enabled in this milestone")
    else:
        print(f"{args.mode} mode ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
