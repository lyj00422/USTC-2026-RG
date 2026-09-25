from __future__ import annotations

from dataclasses import dataclass


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


@dataclass(frozen=True)
class LineOutput:
    vx: int
    vy: int
    wz: int
    line_lost: bool = False


class LinePidController:
    def __init__(self, *, kp: float = 8.0, ki: float = 0.3, kd: float = 1.2,
                 integral_limit: float = 2.0, lateral_limit: int = 8,
                 yaw_limit: int = 4, forward_sign: int = 1,
                 reverse_sign: int = -1):
        self.kp, self.ki, self.kd = float(kp), float(ki), float(kd)
        self.integral_limit = float(integral_limit)
        self.lateral_limit, self.yaw_limit = int(lateral_limit), int(yaw_limit)
        self.forward_sign, self.reverse_sign = int(forward_sign), int(reverse_sign)
        self.reset()

    def reset(self) -> None:
        self._integral = 0.0
        self._previous_error: float | None = None

    def update(self, error: float | None, dt_s: float, *, vx: int) -> LineOutput:
        if error is None:
            return LineOutput(vx, 0, 0, True)
        dt = max(1e-3, float(dt_s))
        self._integral = _clamp(self._integral + error * dt, self.integral_limit)
        derivative = 0.0 if self._previous_error is None else (error - self._previous_error) / dt
        self._previous_error = error
        correction = self.kp * error + self.ki * self._integral + self.kd * derivative
        sign = self.forward_sign if vx >= 0 else self.reverse_sign
        correction *= sign
        return LineOutput(
            vx,
            int(round(_clamp(-correction, self.lateral_limit))),
            int(round(_clamp(-0.5 * correction, self.yaw_limit))),
        )


class JunctionDebouncer:
    """Fires when the sensor shows the junction pattern for several frames.

    The pattern is a *branch*, not an all-black reading.  Field measurement on
    2026-09-14, with the probe order x1..x8 running left to right across the car
    (x1 is the leftmost probe, confirmed by placing the line under each end):

        normal, centred on the line   10000001   (0x81)
        J1, branch leaving to the right 10000000   (0x80)

    At J1 the rightmost probe x8 flips from white to black because the branch's
    black joins the main line under that end of the bar; the operator confirmed
    both the reading and that the branch leaves to the right.

    The previous detector keyed on 0x00 ("every probe on black").  That can
    never fire at J1, and it fires spuriously on any black area wider than the
    bar -- including the 5 cm band a few centimetres after the start, and any
    moment the line sensor streams nothing at all (a missing mask used to be
    coerced to 0 by the route runner, i.e. a dropped frame read as a junction).
    """

    def __init__(self, *, junction_mask: int = 0x80, confirm_frames: int = 3,
                 leave_frames: int = 3):
        if confirm_frames < 1 or leave_frames < 1:
            raise ValueError("debounce frame counts must be positive")
        self.junction_mask = junction_mask & 0xFF
        self.confirm_frames = confirm_frames
        self.leave_frames = leave_frames
        self.reset()

    def reset(self) -> None:
        self._junction_count = 0
        self._clear_count = 0
        # Disarmed until the car has tracked the line normally.  A detector that
        # arms immediately fires on whatever it is already sitting on: on
        # 2026-09-14 the vehicle started slightly off-centre, which is itself a
        # 0x80, and the route skipped two junctions inside the first second.
        # A junction only counts after the sensor has shown a non-junction
        # reading for leave_frames in a row.
        self._armed = False

    def update(self, sensor_mask: int) -> bool:
        is_junction = (sensor_mask & 0xFF) == self.junction_mask
        if is_junction:
            self._clear_count = 0
            self._junction_count += 1
            if self._armed and self._junction_count >= self.confirm_frames:
                self._armed = False
                return True
            return False
        self._junction_count = 0
        self._clear_count += 1
        if self._clear_count >= self.leave_frames:
            self._armed = True
        return False
