"""Hardware diagnosis entry point; does not move devices in this milestone."""

from __future__ import annotations

import argparse

from rg_runtime.app_support import load_runtime_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Check RoboGame runtime configuration")
    parser.add_argument("--config", default="config/runtime.yaml")
    args = parser.parse_args()
    config = load_runtime_config(args.config)
    print(f"chassis={config.chassis_device}@{config.chassis_baudrate}")
    print(f"arm={config.arm_device}@{config.arm_baudrate}")
    print("hardware probing is disabled until explicit diagnose transport checks are enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
