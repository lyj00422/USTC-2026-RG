from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ProbeSample:
    timestamp_s: float
    lateral_cm: float
    contact: bool = False
    encoder: tuple[int, int, int, int] | None = None
    sensor_mask: int | None = None


@dataclass(frozen=True)
class ProbeResult:
    direction: str
    reason: str
    motion_cm: float
    braking_slip_cm: float
    stop_sent: bool
    contact_detected: bool
    suggested_key: str
    hard_max_cm: float
    hard_timeout_s: float
    samples: tuple[ProbeSample, ...]

    def to_dict(self, *, include_samples: bool = False) -> dict:
        result = asdict(self)
        if not include_samples:
            result.pop("samples")
        return result


def run_probe_analysis(samples: Iterable[ProbeSample], *, direction: str,
                       suggested_key: str, hard_max_cm: float,
                       hard_timeout_s: float,
                       operator_stop_at_cm: float | None = None) -> ProbeResult:
    values = tuple(samples)
    if direction not in {"left", "right"}:
        raise ValueError("direction must be left or right")
    if not values:
        raise ValueError("at least one probe sample is required")
    if hard_max_cm <= 0 or hard_timeout_s <= 0 or not suggested_key:
        raise ValueError("hard guards and suggested_key are required")
    origin_cm = values[0].lateral_cm
    origin_s = values[0].timestamp_s
    trigger_index = len(values) - 1
    reason = "samples_exhausted"
    for index, sample in enumerate(values):
        motion = abs(sample.lateral_cm - origin_cm)
        elapsed = sample.timestamp_s - origin_s
        if operator_stop_at_cm is not None and motion >= operator_stop_at_cm:
            reason, trigger_index = "operator_stop", index
            break
        if sample.contact:
            reason, trigger_index = "contact", index
            break
        if motion >= hard_max_cm:
            reason, trigger_index = "hard_distance_limit", index
            break
        if elapsed >= hard_timeout_s:
            reason, trigger_index = "hard_timeout", index
            break
    trigger_motion = abs(values[trigger_index].lateral_cm - origin_cm)
    final_motion = abs(values[-1].lateral_cm - origin_cm)
    return ProbeResult(
        direction=direction,
        reason=reason,
        motion_cm=round(trigger_motion, 4),
        braking_slip_cm=round(max(0.0, final_motion - trigger_motion), 4),
        stop_sent=True,
        contact_detected=any(item.contact for item in values[:trigger_index + 1]),
        suggested_key=suggested_key,
        hard_max_cm=float(hard_max_cm),
        hard_timeout_s=float(hard_timeout_s),
        samples=values,
    )


def write_probe_outputs(prefix: str | Path, result: ProbeResult) -> tuple[Path, Path]:
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    samples_path = Path(f"{prefix}.samples.jsonl")
    result_path = Path(f"{prefix}.result.json")
    with samples_path.open("w", encoding="utf-8") as sink:
        for sample in result.samples:
            sink.write(json.dumps(asdict(sample), ensure_ascii=False) + "\n")
    result_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return samples_path, result_path


def read_probe_samples(path: str | Path) -> tuple[ProbeSample, ...]:
    result = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            result.append(ProbeSample(**json.loads(line)))
    return tuple(result)
