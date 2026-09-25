from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from rg_runtime.models import BlockObservation

from .vision_config import BlockVisionProfile, MotionGuard, PickupAreaVisionConfig


class PickupPhase(str, Enum):
    SEARCH_RIGHT = "SEARCH_RIGHT"
    VERIFY_RIGHT_ENDPOINT = "VERIFY_RIGHT_ENDPOINT"
    RETURN_BASELINE = "RETURN_BASELINE"
    SEARCH_LEFT = "SEARCH_LEFT"
    VERIFY_LEFT_ENDPOINT = "VERIFY_LEFT_ENDPOINT"
    CONFIRM_TARGET = "CONFIRM_TARGET"
    ALIGNING = "ALIGNING"
    VERIFY_WINDOW = "VERIFY_WINDOW"
    READY = "READY"
    NO_TARGET = "NO_TARGET"
    FAULT = "FAULT"


@dataclass(frozen=True)
class PickupVisionIntent:
    kind: str
    phase: PickupPhase
    speed: int = 0
    reason: str = ""
    result: str | None = None


@dataclass(frozen=True)
class PurplePrescanResult:
    present: bool | None
    hint: str | None
    frames: int
    region_counts: dict[str, int]
    candidate_centers: tuple[tuple[float, float], ...]


def purple_slots_from_result(
    result: PurplePrescanResult | dict, *, frame_width: int,
) -> tuple[bool, bool, bool]:
    """Adapt existing J3 candidates to left/middle/right slot presence.

    The detector and its temporal confirmation remain unchanged.  This adapter
    only bins the already accepted candidate centres; region counts provide a
    conservative fallback when the terminal frame contains no contours.
    """
    if frame_width <= 0:
        raise ValueError("frame_width must be positive")
    centers = result.candidate_centers if isinstance(result, PurplePrescanResult) else tuple(result.get("candidate_centers", ()))
    counts = result.region_counts if isinstance(result, PurplePrescanResult) else result.get("region_counts", {})
    slots = [False, False, False]
    for center_x, _center_y in centers:
        normalized = center_x / frame_width
        index = 0 if normalized < 1 / 3 else 2 if normalized >= 2 / 3 else 1
        slots[index] = True
    if not any(slots):
        slots = [
            counts.get("left", 0) > 0,
            counts.get("center", 0) > 0,
            counts.get("right", 0) > 0,
        ]
    return tuple(slots)  # type: ignore[return-value]


class PurplePrescanTracker:
    """Aggregate visible regions without assigning identity to one contour."""

    def __init__(self, *, frame_budget: int, confirm_frames: int,
                 center_left: float, center_right: float,
                 track_radius_px: float = 100.0):
        if frame_budget < 1 or not 1 <= confirm_frames <= frame_budget:
            raise ValueError("prescan confirmation must fit inside frame budget")
        if not 0 <= center_left < center_right <= 1:
            raise ValueError("prescan center bounds must be normalized")
        if track_radius_px <= 0:
            raise ValueError("prescan track radius must be positive")
        self.frame_budget = int(frame_budget)
        self.confirm_frames = int(confirm_frames)
        self.center_left = float(center_left)
        self.center_right = float(center_right)
        self.track_radius_px = float(track_radius_px)
        self.reset()

    def reset(self) -> None:
        self.frames = 0
        self.region_counts = {"left": 0, "center": 0, "right": 0}
        self._tracks = {"left": [], "center": [], "right": []}
        self._terminal: PurplePrescanResult | None = None

    def _region(self, center_x: float, frame_width: int) -> str:
        normalized = center_x / frame_width
        if normalized < self.center_left:
            return "left"
        if normalized > self.center_right:
            return "right"
        return "center"

    def update(self, candidates, *, frame_width: int) -> PurplePrescanResult:
        if frame_width <= 0:
            raise ValueError("frame_width must be positive")
        if self._terminal is not None:
            return self._terminal
        items = tuple(candidates)
        self.frames += 1
        for region in self._tracks:
            region_items = [
                item for item in items
                if self._region(item.center_px[0], frame_width) == region
            ]
            matched_tracks: set[int] = set()
            for item in region_items:
                center = item.center_px
                best_index = None
                best_distance = self.track_radius_px
                for index, track in enumerate(self._tracks[region]):
                    if index in matched_tracks:
                        continue
                    distance = math.hypot(
                        center[0] - track["center"][0],
                        center[1] - track["center"][1],
                    )
                    if distance <= best_distance:
                        best_index = index
                        best_distance = distance
                if best_index is None:
                    self._tracks[region].append({"center": center, "hits": 1})
                    matched_tracks.add(len(self._tracks[region]) - 1)
                else:
                    track = self._tracks[region][best_index]
                    track["center"] = center
                    track["hits"] += 1
                    matched_tracks.add(best_index)
            if self._tracks[region]:
                self.region_counts[region] = max(
                    track["hits"] for track in self._tracks[region]
                )

        hint = None
        present = None
        # Center is the highest-priority answer, so it is the only safe early exit.
        if self.region_counts["center"] >= self.confirm_frames:
            hint, present = "center", True
        elif self.frames >= self.frame_budget:
            # Operator rule (2026-09-18): with no confirmed centre, LEFT wins a
            # two-sided scene.  The target block sits left of centre once the
            # car has stopped against the wall, and the previous right-first
            # order sent the first sweep the wrong way whenever the prescan
            # could only answer "center" (route_v2.md section 21, root cause A).
            for region in ("left", "right"):
                # BOTH side regions take a SINGLE hit at the end of the scan.
                #
                # Operator, 2026-09-22 night, after a right-hand block was missed
                # outright and the route skipped the purple altogether: "那右边改
                # 成左边的一样".  The left side was already relaxed to one hit for
                # exactly the same reason -- the side blocks are seen
                # intermittently, at most once per short track before
                # disappearing, so requiring a stable track there means never
                # seeing the block at all.  The right side's extra stability gate
                # was there to stop a lone transient contour outranking a real
                # left target; with choose_purple_slot now preferring the RIGHT
                # block in a two-sided scene (2026-09-22), that gate only makes
                # the right harder to find than the left.
                threshold = 1
                if self.region_counts[region] >= threshold:
                    hint, present = region, True
                    break
            if present is None:
                present = False

        result = PurplePrescanResult(
            present=present,
            hint=hint,
            frames=self.frames,
            region_counts=dict(self.region_counts),
            candidate_centers=tuple(item.center_px for item in items),
        )
        if result.present is not None:
            self._terminal = result
        return result


class PickupVisionController:
    SCENE_STAGNATION_FRAMES = 8
    SCENE_CHANGE_FRACTION = 0.02

    def __init__(self, area: str, config: PickupAreaVisionConfig,
                 profile: BlockVisionProfile, *, frame_size: tuple[int, int],
                 initial_search: str | None = None):
        if area not in {"purple", "orange"}:
            raise ValueError("area must be purple or orange")
        if frame_size[0] <= 0 or frame_size[1] <= 0:
            raise ValueError("frame_size must be positive")
        if initial_search is None:
            initial_search = "left" if area == "orange" else "right"
        if initial_search not in {"left", "center", "right"}:
            raise ValueError("initial_search must be left, center or right")
        self.area = area
        self.config = config
        self.profile = profile
        self.frame_size = frame_size
        self.initial_search = initial_search
        self.reset()

    def reset(self) -> None:
        self._first_search = (
            PickupPhase.SEARCH_LEFT
            if self.initial_search == "left"
            else PickupPhase.SEARCH_RIGHT
        )
        self._second_search = (
            PickupPhase.SEARCH_RIGHT
            if self._first_search is PickupPhase.SEARCH_LEFT
            else PickupPhase.SEARCH_LEFT
        )
        self.phase = self._first_search
        self._first_search_complete = False
        self._baseline_cm: float | None = None
        self._phase_started: float | None = None
        self._phase_origin_cm: float | None = None
        self._resume_search = PickupPhase.SEARCH_RIGHT
        # One candidate that could not be reached gives up on its side, not on the
        # whole pickup -- but only once (see _search_the_other_side).
        self._side_switched = False
        self._target: BlockObservation | None = None
        self._target_frames = 0
        self._verify_frames = 0
        self._last_frame: int | None = None
        self._endpoint_frames = 0
        self._endpoint_last_frame: int | None = None
        self._alignment_started: float | None = None
        self._alignment_origin_cm: float | None = None
        self._locked_lost_started: float | None = None
        self._alignment_direction: str | None = None
        self._alignment_reversals = 0
        self._last_scene_frame_id: int | None = None
        self._last_scene_signature: bytes | None = None
        self._scene_stagnant_frames = 0
        self._motion_expected = False

    def _intent(self, kind: str, *, speed: int = 0, reason: str = "",
                result: str | None = None) -> PickupVisionIntent:
        self._motion_expected = kind in {
            "strafe_left", "strafe_right", "align_left", "align_right",
        }
        return PickupVisionIntent(kind, self.phase, speed, reason, result)

    def _camera_view_stagnant(self, *, frame_id: int | None,
                              frame_signature: bytes | None) -> bool:
        if (self.area != "orange" or frame_id is None
                or frame_signature is None
                or self.phase not in {
                    PickupPhase.SEARCH_LEFT, PickupPhase.SEARCH_RIGHT,
                    PickupPhase.ALIGNING,
                }):
            self._scene_stagnant_frames = 0
            return False
        if (self._last_scene_frame_id is not None
                and frame_id <= self._last_scene_frame_id):
            return False

        previous = self._last_scene_signature
        self._last_scene_frame_id = frame_id
        self._last_scene_signature = frame_signature
        if not self._motion_expected or previous is None:
            self._scene_stagnant_frames = 0
            return False

        if len(previous) != len(frame_signature) or not previous:
            changed_fraction = 1.0
        else:
            changed_fraction = sum(
                before != after for before, after in zip(previous, frame_signature)
            ) / len(previous)
        if changed_fraction < self.SCENE_CHANGE_FRACTION:
            self._scene_stagnant_frames += 1
        else:
            self._scene_stagnant_frames = 0
        return self._scene_stagnant_frames >= self.SCENE_STAGNATION_FRAMES

    def _stagnant_endpoint(self) -> PickupVisionIntent:
        self.phase = (PickupPhase.VERIFY_LEFT_ENDPOINT
                      if self.phase is PickupPhase.SEARCH_LEFT
                      else PickupPhase.VERIFY_RIGHT_ENDPOINT)
        self._endpoint_frames = 0
        self._endpoint_last_frame = None
        return self._intent("stop", reason="camera_view_stagnant")

    def _enter_motion_phase(self, phase: PickupPhase, now: float, lateral_cm: float) -> None:
        self.phase = phase
        self._phase_started = now
        self._phase_origin_cm = lateral_cm
        self._scene_stagnant_frames = 0

    def _guard_exhausted(self, guard: MotionGuard, now: float, lateral_cm: float) -> bool:
        return (
            abs(lateral_cm - self._phase_origin_cm) >= guard.max_distance_cm
            or now - self._phase_started >= guard.timeout_s
        )

    def _same_target(self, target: BlockObservation) -> bool:
        if self._target is None:
            return True
        return math.hypot(
            target.center_px[0] - self._target.center_px[0],
            target.center_px[1] - self._target.center_px[1],
        ) <= 100

    def _new_frame(self, target: BlockObservation) -> bool:
        if self._last_frame is not None and target.frame_index <= self._last_frame:
            return False
        self._last_frame = target.frame_index
        return True

    def _begin_candidate(self, target: BlockObservation) -> PickupVisionIntent:
        self._resume_search = self.phase
        self.phase = PickupPhase.CONFIRM_TARGET
        self._target = target
        self._target_frames = 1
        self._last_frame = target.frame_index
        return self._intent("stop", reason="candidate_requires_stopped_confirmation")

    def _inside_window(self, target: BlockObservation) -> bool:
        width, height = self.frame_size
        x, y = target.center_px[0] / width, target.center_px[1] / height
        window = self.profile.capture_window
        if not window.left <= x <= window.right:
            return False
        return window.top <= y <= window.bottom or self._bbox_depth_valid(target)

    def _bbox_depth_valid(self, target: BlockObservation) -> bool:
        _, height = self.frame_size
        box = getattr(target, "bounding_box", None)
        if box is None or len(box) != 4:
            return False
        _, box_top, _, box_height = box
        box_bottom = (box_top + box_height) / height
        height_ok = (
            self.profile.height_px.minimum
            <= box_height <= self.profile.height_px.maximum
        )
        bottom_range = self.profile.bottom_y
        bottom_ok = (
            bottom_range is None
            or bottom_range.minimum <= box_bottom <= bottom_range.maximum
        )
        return height_ok and bottom_ok

    def _depth_out_of_range(self, target: BlockObservation) -> bool:
        width, height = self.frame_size
        x, y = target.center_px[0] / width, target.center_px[1] / height
        window = self.profile.capture_window
        if not (window.left <= x <= window.right
                and not window.top <= y <= window.bottom):
            return False

        # The reported center may be the bright-core centroid.  On the left
        # approach, a second purple region can merge into the contour and pull
        # that centroid downward in one frame, even though the accepted bbox
        # still has the measured near-field height and bottom edge.  Those bbox
        # gates are computed from the full contour and are stable under this
        # horizontal merge, so use them as the depth check in that case.
        return not self._bbox_depth_valid(target)

    def _depth_fault(self) -> PickupVisionIntent:
        self.phase = PickupPhase.FAULT
        return self._intent(
            "fault",
            reason="pickup_depth_out_of_range",
            result="pickup_depth_out_of_range",
        )

    def _search_the_other_side(self, now: float, lateral_cm: float, *,
                               reason: str) -> PickupVisionIntent:
        """Give up on this candidate and sweep the side it did NOT come from.

        `_resume_search` holds the sweep the candidate was spotted during (set by
        `_begin_candidate`), so the other side is its opposite.  The sweep restarts
        at the CURRENT pose, so the next attempt gets a full guard from here instead
        of inheriting a partly-spent one.

        It switches at most ONCE.  Without that cap a weak candidate on each side in
        turn would keep handing the car a fresh sweep, and every restart re-origins
        the guard -- the car walks sideways a little further each time, outside the
        distances the pickup areas have actually been measured over.  After one
        switch the pickup ends with `no_target`, which is the normal "nothing usable
        in this area" result the strategy already handles.
        """
        if self._side_switched:
            self.phase = PickupPhase.NO_TARGET
            return self._intent(
                "no_target", reason="both_sides_tried", result=(
                    "bypass_to_pickup_1" if self.area == "purple" else "orange_exhausted"))
        self._side_switched = True
        other = (PickupPhase.SEARCH_RIGHT
                 if self._resume_search is PickupPhase.SEARCH_LEFT
                 else PickupPhase.SEARCH_LEFT)
        self._enter_motion_phase(other, now, lateral_cm)
        self._target = None
        self._target_frames = 0
        self._locked_lost_started = None
        self._alignment_direction = None
        self._alignment_reversals = 0
        speed = (abs(self.config.search_speed) if other is PickupPhase.SEARCH_LEFT
                 else -abs(self.config.search_speed))
        return self._intent("strafe_left" if speed > 0 else "strafe_right",
                            speed=speed, reason=reason)

    def select_target(self, candidates) -> BlockObservation | None:
        items = tuple(candidates)
        if not items:
            return None
        if self._target is None:
            width, height = self.frame_size
            window = self.profile.capture_window
            center = ((window.left + window.right) * width / 2,
                      (window.top + window.bottom) * height / 2)
        else:
            center = self._target.center_px
            matching = [item for item in items if math.hypot(
                item.center_px[0] - center[0], item.center_px[1] - center[1]
            ) <= 100]
            if matching:
                items = tuple(matching)
        return min(items, key=lambda item: math.hypot(
            item.center_px[0] - center[0], item.center_px[1] - center[1]
        ))

    def step(self, *, now: float, lateral_cm: float,
             target: BlockObservation | None, contact: bool = False,
             stopped: bool = True, frame_id: int | None = None,
             frame_signature: bytes | None = None) -> PickupVisionIntent:
        if self._baseline_cm is None:
            self._baseline_cm = lateral_cm
            self._phase_started = now
            self._phase_origin_cm = lateral_cm

        camera_stagnant = self._camera_view_stagnant(
            frame_id=frame_id, frame_signature=frame_signature,
        )

        if self.phase in {PickupPhase.SEARCH_RIGHT, PickupPhase.SEARCH_LEFT,
                          PickupPhase.RETURN_BASELINE,
                          PickupPhase.VERIFY_RIGHT_ENDPOINT,
                          PickupPhase.VERIFY_LEFT_ENDPOINT} and target is not None:
            return self._begin_candidate(target)

        if camera_stagnant:
            if self.phase in {PickupPhase.SEARCH_LEFT, PickupPhase.SEARCH_RIGHT}:
                return self._stagnant_endpoint()
            if self.phase is PickupPhase.ALIGNING:
                return self._search_the_other_side(
                    now, lateral_cm, reason="camera_view_stagnant",
                )

        if self.phase is PickupPhase.SEARCH_RIGHT:
            if self._guard_exhausted(self.config.search_right, now, lateral_cm):
                self.phase = PickupPhase.VERIFY_RIGHT_ENDPOINT
                self._endpoint_frames = 0
                self._endpoint_last_frame = None
                return self._intent("stop", reason="verify_right_endpoint")
            return self._intent("strafe_right", speed=-abs(self.config.search_speed),
                                reason="search_right")

        if self.phase is PickupPhase.RETURN_BASELINE:
            error = self._baseline_cm - lateral_cm
            if abs(error) <= self.config.return_tolerance_cm:
                self._enter_motion_phase(self._second_search, now, lateral_cm)
                if self._second_search is PickupPhase.SEARCH_LEFT:
                    return self._intent("strafe_left", speed=abs(self.config.search_speed),
                                        reason="search_left")
                return self._intent("strafe_right", speed=-abs(self.config.search_speed),
                                    reason="search_right")
            return_guard = (
                self.config.search_left
                if self._first_search is PickupPhase.SEARCH_LEFT
                else self.config.search_right
            )
            # The return must cover the ground the sweep ACTUALLY took, and a sweep
            # stops a little past its own guard: measured on run 20260924_213236, the
            # right sweep reached 77.49 cm against a 75 cm guard.  Reusing the sweep's
            # guard here therefore faulted the return 2.5 cm short of the baseline --
            # the car stopped with the block still unfound and never searched the
            # other side, which is exactly what the operator saw:
            # 「回到正中 停止 没有往左边找」.
            #
            # So bound it by the distance it has to cover -- |return origin - baseline|
            # -- plus a margin for its own braking overshoot.
            needed_cm = abs(self._phase_origin_cm - self._baseline_cm)
            if (abs(lateral_cm - self._phase_origin_cm)
                    >= needed_cm + self.config.return_guard_margin_cm
                    or now - self._phase_started >= return_guard.timeout_s):
                self.phase = PickupPhase.FAULT
                return self._intent(
                    "fault",
                    reason="return_baseline_guard_exhausted",
                    result="return_baseline_guard_exhausted",
                )
            speed = abs(self.config.search_speed) if error > 0 else -abs(self.config.search_speed)
            return self._intent("return_baseline", speed=speed, reason="return_to_search_origin")

        if self.phase is PickupPhase.SEARCH_LEFT:
            if self._guard_exhausted(self.config.search_left, now, lateral_cm):
                self.phase = PickupPhase.VERIFY_LEFT_ENDPOINT
                self._endpoint_frames = 0
                self._endpoint_last_frame = None
                return self._intent("stop", reason="verify_left_endpoint")
            return self._intent("strafe_left", speed=abs(self.config.search_speed),
                                reason="search_left")

        if self.phase in {PickupPhase.VERIFY_RIGHT_ENDPOINT,
                          PickupPhase.VERIFY_LEFT_ENDPOINT}:
            if (stopped and frame_id is not None
                    and (self._endpoint_last_frame is None
                         or frame_id > self._endpoint_last_frame)):
                self._endpoint_last_frame = frame_id
                self._endpoint_frames += 1
            if self._endpoint_frames < self.config.confirm_frames:
                return self._intent("stop", reason="endpoint_new_frame_confirmation")
            verified_search = (
                PickupPhase.SEARCH_RIGHT
                if self.phase is PickupPhase.VERIFY_RIGHT_ENDPOINT
                else PickupPhase.SEARCH_LEFT
            )
            if verified_search is self._first_search and not self._first_search_complete:
                self._first_search_complete = True
                self._enter_motion_phase(PickupPhase.RETURN_BASELINE, now, lateral_cm)
                error = self._baseline_cm - lateral_cm
                speed = abs(self.config.search_speed) if error > 0 else -abs(self.config.search_speed)
                return self._intent("return_baseline", speed=speed,
                                    reason=f"{verified_search.value.lower()}_endpoint_clear")
            if self.area == "purple":
                self.phase = PickupPhase.NO_TARGET
                return self._intent("no_target", reason="bounded_search_exhausted",
                                    result="bypass_to_pickup_1")
            # Orange exhaustion is a normal round result.  The strategy layer
            # decides whether the collected count goes to BUILD_2/BUILD_3 or
            # terminates; it is not a camera fault and must not strand the car.
            self.phase = PickupPhase.NO_TARGET
            return self._intent("no_target", reason="bounded_search_exhausted",
                                result="orange_exhausted")

        if self.phase is PickupPhase.CONFIRM_TARGET:
            if target is None or not self._same_target(target):
                self.phase = self._resume_search
                self._target = None
                self._target_frames = 0
                return self._intent("stop", reason="candidate_lost_before_lock")
            self._target = target
            if stopped and self._new_frame(target):
                self._target_frames += 1
            if self._target_frames >= self.config.confirm_frames:
                self.phase = PickupPhase.ALIGNING
                self._alignment_started = now
                self._alignment_origin_cm = lateral_cm
                self._locked_lost_started = None
                self._alignment_direction = None
                self._alignment_reversals = 0
                return self._intent("stop", reason="target_locked")
            return self._intent("stop", reason="confirming_target")

        if self.phase is PickupPhase.ALIGNING:
            if (now - self._alignment_started >= self.config.alignment_timeout_s
                    or abs(lateral_cm - self._alignment_origin_cm)
                    >= self.config.alignment_max_distance_cm):
                # This candidate is not usable FROM HERE -- the car could not reach
                # it inside the window, guard or time.  Hunt for the NEXT one on the
                # other side instead of ending the run: a weak or partly-occluded
                # candidate on one side must not strand the car.
                #
                # Operator, 2026-09-24: 「明明有一点 但是左移不过去 直接右移找下一个」.
                #
                # The other side, not a full two-phase restart: the side that just
                # failed is exactly the side worth skipping here.
                return self._search_the_other_side(now, lateral_cm,
                                                   reason="alignment_guard_exhausted")
            if target is None or not self._same_target(target):
                if self._locked_lost_started is None:
                    self._locked_lost_started = now
                elif now - self._locked_lost_started >= self.config.locked_reacquire_timeout_s:
                    # The block is GONE -- not briefly missed.  Go and look for
                    # another one instead of holding still and faulting, which is a
                    # dead end: the car sits there until someone kills it.
                    #
                    # Operator, 2026-09-24: 「方块消失了 就应该去搜寻别的方块 而不是
                    # 停止」.  Field case, run 20260924_215333: the orange pickup locked
                    # a block, the detector then stopped reporting it while the car
                    # stood still (lateral frozen at 0.09 cm, every intent a stop),
                    # and 2.0 s later the whole route went to FAULT.
                    #
                    # It restarts the hunt from _first_search and re-baselines to the
                    # CURRENT pose, NOT `self._resume_search`: a hunt whose first
                    # sweep is already marked complete ends on `no_target` when its
                    # sweep runs out, and that tells the strategy the orange supply is
                    # exhausted -- a wrong answer that would change what the route
                    # builds.  A full two-phase hunt from here can only report
                    # exhaustion after BOTH sweeps really found nothing.
                    #
                    # Every sweep stays bounded by its own MotionGuard and timeout, so
                    # this cannot turn into unbounded motion.
                    self._baseline_cm = lateral_cm
                    self._first_search_complete = False
                    self._enter_motion_phase(self._first_search, now, lateral_cm)
                    self._target = None
                    self._target_frames = 0
                    self._locked_lost_started = None
                    self._alignment_direction = None
                    self._alignment_reversals = 0
                    speed = (abs(self.config.search_speed)
                             if self._first_search is PickupPhase.SEARCH_LEFT
                             else -abs(self.config.search_speed))
                    return self._intent(
                        "strafe_left" if speed > 0 else "strafe_right", speed=speed,
                        reason="locked_target_lost_restart_search")
                return self._intent("stop", reason="locked_target_lost")
            self._locked_lost_started = None
            self._target = target
            if self._inside_window(target):
                self.phase = PickupPhase.VERIFY_WINDOW
                self._verify_frames = 1 if stopped and self._new_frame(target) else 0
                return self._intent("stop", reason="capture_window_verify")
            width = self.frame_size[0]
            window = self.profile.capture_window
            target_x = target.center_px[0] / width
            if self._depth_out_of_range(target):
                return self._depth_fault()
            window_center = (window.left + window.right) / 2
            error = target_x - window_center
            fine = abs(error) <= 0.12
            magnitude = abs(self.config.fine_speed if fine else self.config.coarse_speed)
            direction = "align_left" if error < 0 else "align_right"
            if self._alignment_direction is not None and direction != self._alignment_direction:
                self._alignment_reversals += 1
            self._alignment_direction = direction
            if self._alignment_reversals > self.config.max_alignment_reversals:
                self.phase = PickupPhase.FAULT
                return self._intent("fault", reason="alignment_reversal_limit",
                                    result="alignment_guard_exhausted")
            if direction == "align_left":
                return self._intent(direction, speed=magnitude, reason="target_left_of_window")
            return self._intent(direction, speed=-magnitude, reason="target_right_of_window")

        if self.phase is PickupPhase.VERIFY_WINDOW:
            if (target is not None and self._same_target(target)
                    and self._depth_out_of_range(target)):
                return self._depth_fault()
            if target is None or not self._same_target(target) or not self._inside_window(target):
                self.phase = PickupPhase.ALIGNING
                self._verify_frames = 0
                return self._intent("stop", reason="capture_window_lost")
            self._target = target
            if stopped and self._new_frame(target):
                self._verify_frames += 1
            if self._verify_frames >= self.config.confirm_frames:
                self.phase = PickupPhase.READY
                return self._intent("pickup_ready", reason="capture_window_confirmed")
            return self._intent("stop", reason="capture_window_verify")

        if self.phase is PickupPhase.READY:
            return self._intent("pickup_ready", reason="capture_window_confirmed")
        if self.phase is PickupPhase.NO_TARGET:
            return self._intent("no_target", result="bypass_to_pickup_1")
        return self._intent("fault", result="orange_target_not_found")


class PickupReturnPhase(str, Enum):
    RETURN_BASELINE = "RETURN_BASELINE"
    SEEK_OUT = "SEEK_OUT"
    SEEK_BACK = "SEEK_BACK"
    DONE = "DONE"
    FAULT = "FAULT"


@dataclass(frozen=True)
class PickupReturnIntent:
    kind: str
    phase: PickupReturnPhase
    speed: int = 0
    reason: str = ""


def swing_targets(amplitudes) -> tuple[float, ...]:
    """Signed alternating targets from a list of amplitudes.

    (20, 40, 60, 80) -> (+20, -20, +40, -40, +60, -60, +80, -80).

    Positive is LEFT, the route's usual lateral sign.  Operator, 2026-09-24:
    「左右20 左右40 左右60 左右80 一定能找到线了」-- the car swings to each
    amplitude in turn, both ways, and the amplitudes GROW, so the search reaches
    further from the line each round instead of re-covering the same ground.
    """
    out: list[float] = []
    for amplitude in amplitudes:
        magnitude = abs(float(amplitude))
        out.extend((magnitude, -magnitude))
    return tuple(out)


class PickupReturnController:
    """Return to a saved absolute lateral pose, then confirm/reacquire the line."""

    def __init__(self, *, baseline_cm: float, tolerance_cm: float, speed: int,
                 seek_max_cm: float, timeout_s: float, confirm_frames: int,
                 one_way_direction: int = 0, swing_cm: tuple[float, ...] = ()):
        if tolerance_cm < 0 or speed == 0 or seek_max_cm <= 0 or timeout_s <= 0 or confirm_frames < 1:
            raise ValueError("invalid return-to-line limits")
        if one_way_direction not in (-1, 0, 1):
            raise ValueError("one_way_direction must be -1, 0 or 1")
        self.baseline_cm = float(baseline_cm)
        self.tolerance_cm = float(tolerance_cm)
        self.speed = abs(int(speed))
        self.seek_max_cm = float(seek_max_cm)
        self.timeout_s = float(timeout_s)
        self.confirm_frames = int(confirm_frames)
        # Growing swing, used only when the direction is NOT known (the orange
        # area): +20, -20, +40, -40, ...  Empty falls back to the old single
        # both-ways sweep at seek_max_cm, so a config without the key behaves as
        # it did before 2026-09-24.
        self.swing_cm = tuple(float(value) for value in swing_cm)
        self._targets: tuple[float, ...] = ()
        self._target_index = 0
        # 0 keeps the original both-ways sweep.  -1/+1 pin the reacquire to ONE
        # direction: the route knows which purple block the arm took, and that
        # says which side of the line the car was left on.
        #   Operator, 2026-09-22 night: "是取左边 就往右边一直找线"
        self.one_way_direction = int(one_way_direction)
        self.phase = PickupReturnPhase.RETURN_BASELINE
        self._origin_cm: float | None = None
        self._started_at: float | None = None
        self._line_frames = 0

    def _intent(self, kind: str, speed: int = 0, reason: str = "") -> PickupReturnIntent:
        return PickupReturnIntent(kind, self.phase, speed, reason)

    def step(self, *, now: float, absolute_lateral_cm: float,
             line_found: bool) -> PickupReturnIntent:
        if self.phase is PickupReturnPhase.RETURN_BASELINE:
            error = self.baseline_cm - absolute_lateral_cm
            if abs(error) > self.tolerance_cm:
                return self._intent("return_baseline", self.speed if error > 0 else -self.speed,
                                    "saved_absolute_baseline")
            self.phase = PickupReturnPhase.SEEK_OUT
            self._origin_cm = absolute_lateral_cm
            self._started_at = now

        if self.phase in {PickupReturnPhase.SEEK_OUT, PickupReturnPhase.SEEK_BACK}:
            self._line_frames = self._line_frames + 1 if line_found else 0
            if self._line_frames >= self.confirm_frames:
                self.phase = PickupReturnPhase.DONE
                return self._intent("return_line_done", reason="probe_line_confirmed")
            if self.one_way_direction:
                exhausted = (
                    abs(absolute_lateral_cm - self._origin_cm) >= self.seek_max_cm
                    or now - self._started_at >= self.timeout_s
                )
                if exhausted:
                    self.phase = PickupReturnPhase.FAULT
                    return self._intent("fault", reason="one_way_return_line_exhausted")
                return self._intent("seek_line", self.one_way_direction * self.speed,
                                    "one_way_probe_reacquire")
            # GROWING SWING -- the direction is NOT known (orange area), so sweep
            # both ways, and make each round reach further than the last.
            #
            # Operator, 2026-09-24: 「左右20 左右40 左右60 左右80 一定能找到线了」.
            # This replaces the old single both-ways sweep at seek_max_cm (35 cm),
            # which covered +35 then -35 and then gave up -- a block grabbed off
            # to one side can leave the car further from the line than that, and
            # the search then faulted instead of finding it.
            #
            # Each target is an ABSOLUTE lateral position, so the car drives to
            # +20, then to -20, then +40, -40, and so on; the move lengths are
            # therefore 20, 40, 60, 80 ... which is what makes the excursion
            # grow.  The line is confirmed by the usual confirm_frames rule
            # above, on every tick regardless of which leg of the swing is
            # running.
            if not self._targets:
                self._targets = swing_targets(self.swing_cm or (self.seek_max_cm,))
                self._target_index = 0
                self._started_at = now
            target = self._targets[self._target_index]
            if (abs(absolute_lateral_cm - target) <= self.tolerance_cm
                    or now - self._started_at >= self.timeout_s):
                self._target_index += 1
                if self._target_index >= len(self._targets):
                    self.phase = PickupReturnPhase.FAULT
                    return self._intent("fault", reason="return_line_swing_exhausted")
                self._started_at = now
                self._line_frames = 0
                target = self._targets[self._target_index]
            left = target > absolute_lateral_cm
            self.phase = (PickupReturnPhase.SEEK_OUT if left
                          else PickupReturnPhase.SEEK_BACK)
            return self._intent("seek_line", self.speed if left else -self.speed,
                                "swing_probe_reacquire")
        if self.phase is PickupReturnPhase.DONE:
            return self._intent("return_line_done", reason="probe_line_confirmed")
        return self._intent("fault", reason="return_line_search_exhausted")
