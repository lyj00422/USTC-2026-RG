from __future__ import annotations

import argparse
import json
import os
import re
import signal
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from route_v2.config import RouteV2Config, load_route_v2_config
from route_v2.line_control import LinePidController
from route_v2.pickup_action import (
    ActionCatalogExecutor,
    ActionPackageExecutor,
    PlaceholderActionExecutor,
    VisionOnlyPickupExecutor,
    compile_action,
    load_action_catalog,
    load_action_package,
)
from route_v2.pickup_vision import (
    PickupPhase,
    PickupReturnController,
    PickupVisionController,
    PurplePrescanTracker,
    purple_slots_from_result,
)
from route_v2.evidence import OrangeEvidenceRecorder
from route_v2.loop_strategy import apply_build_action, choose_purple_slot
from route_v2.state_machine import (
    RouteIntent, RouteState, RouteV2StateMachine, VisionRouteInput,
)
from route_v2.vision_worker import (
    CameraCaptureWorker, LatestFrameBuffer, VisionTask, VisionWorker,
)
from rg_runtime.blocks import ProfiledBlockDetector
from rg_runtime.models import BlockColor
from rg_runtime.tag_tracker import TagTracker, TagTrackingResult


class RouteSelectionError(ValueError):
    pass


def vision_task_for_state(state: RouteState) -> VisionTask:
    return {
        RouteState.JUNCTION_1_STRAFE_TO_TAG_2: VisionTask.TAG2,
        RouteState.PICKUP_SEEK_LINE: VisionTask.TAG3,
        RouteState.PICKUP_TAG_LINE_TAKEOVER: VisionTask.TAG3,
        RouteState.PURPLE_PRESCAN: VisionTask.PURPLE_PRESCAN,
        RouteState.PICKUP_VISION_ONLY: VisionTask.PURPLE_CLOSE,
        RouteState.PICKUP_2_SEEK_LINE: VisionTask.TAG4,
        RouteState.PICKUP_2_TAG_LINE_TAKEOVER: VisionTask.TAG4,
        RouteState.PICKUP_2_VISION_ONLY: VisionTask.ORANGE_CLOSE,
        RouteState.BUILD_AREA: VisionTask.BUILD_OCCUPANCY,
    }.get(state, VisionTask.NONE)


def vision_result_is_fresh(result, *, generation: int, now: float,
                           max_age_s: float) -> bool:
    return (
        result is not None
        and result.generation == generation
        and 0 <= now - result.captured_at <= max_age_s
    )


class RouteVisionRuntime:
    """Single-camera, latest-frame visual adapter for the pure route machine."""

    def __init__(self, config: RouteV2Config, camera, tag_detector, *, clock=time.monotonic):
        if config.vision is None:
            raise ValueError("RouteVisionRuntime requires vision configuration")
        if tag_detector is None:
            raise RuntimeError("AprilTag detector is required for the visual route")
        self.config = config
        self.vision = config.vision
        self.clock = clock
        self.frames = LatestFrameBuffer(clock=clock)
        self.capture = CameraCaptureWorker(camera, self.frames, clock=clock)
        self.tag_detector = tag_detector
        self._tag_trackers = {VisionTask.TAG2: TagTracker(target_id=2)}
        profiles = self.vision.block_profiles
        # Kept so the build-area reading can report WHERE the blob is, not just that
        # one exists: BUILD_AREA centres on it before a cap, and the centre is
        # measured against this profile's own capture window.
        self._build_profile = profiles["build_occupancy"]
        self._block_detectors = {
            VisionTask.PURPLE_PRESCAN: ProfiledBlockDetector(
                profiles["purple_j3_prescan"], color=BlockColor.PURPLE),
            VisionTask.PURPLE_CLOSE: ProfiledBlockDetector(
                profiles["purple_pickup_close"], color=BlockColor.PURPLE),
            VisionTask.ORANGE_CLOSE: ProfiledBlockDetector(
                profiles["orange_pickup_close"], color=BlockColor.ORANGE),
            VisionTask.BUILD_OCCUPANCY: ProfiledBlockDetector(
                profiles["build_occupancy"], color=BlockColor.ORANGE),
        }
        self.worker = VisionWorker(self.frames, detectors={
            VisionTask.TAG2: lambda image, **kw: self._detect_tag(VisionTask.TAG2, image, **kw),
            VisionTask.TAG3: lambda image, **kw: self._detect_tag(VisionTask.TAG3, image, **kw),
            VisionTask.TAG4: lambda image, **kw: self._detect_tag(VisionTask.TAG4, image, **kw),
            VisionTask.PURPLE_PRESCAN: self._detect_prescan,
            VisionTask.PURPLE_CLOSE: lambda image, **kw: self._detect_block(VisionTask.PURPLE_CLOSE, image, **kw),
            VisionTask.ORANGE_CLOSE: lambda image, **kw: self._detect_block(VisionTask.ORANGE_CLOSE, image, **kw),
            VisionTask.BUILD_OCCUPANCY: lambda image, **kw: self._detect_block(VisionTask.BUILD_OCCUPANCY, image, **kw),
        }, clock=clock)
        self._started = False
        self._task = VisionTask.NONE
        self._generation = 0
        self._task_selected_at = clock()
        self._task_first_fresh_frame_id: int | None = None
        self._task_ready = False
        prescan_window = profiles["purple_j3_prescan"].capture_window
        self._prescan_generation = -1
        self._prescan_tracker = PurplePrescanTracker(
            frame_budget=self.vision.prescan_frames,
            confirm_frames=self.vision.prescan_confirm_frames,
            center_left=prescan_window.left,
            center_right=prescan_window.right,
        )
        self._purple_search_hint: str | None = None
        self._purple_center_pickup_pending = False
        self._pickup_controller: PickupVisionController | None = None
        self._pickup_area: str | None = None
        self._pickup_baseline_cm: float | None = None
        # The purple block the route is going for, by the state machine's own
        # rule (loop_strategy.choose_purple_slot), decided at J3.  Kept here so
        # the return hunt's direction cannot disagree with the pickup.
        self._purple_target_slot: int | None = None
        # Sign of the LAST lateral command the pickup issued on its way to a block
        # (+1 = LEFT, the route's convention; -1 = RIGHT), latched across the
        # whole pickup and NOT cleared by restart_pickup_search.
        #
        # Operator, 2026-09-24: 「通过找物块最后的一次速度反推 比如找到物块那一刻
        # 前面的命令是向左 找线就一直向右找」.  The car drove that way to reach the
        # block, so the line it left behind is on the other side: the post-grab hunt
        # goes back the way it came, one way, instead of sweeping both ways and
        # guessing.  This is the orange area's version of what the purple area gets
        # from the prescan slot (see one_way_direction).
        #
        # 0 means "no lateral command was ever seen", and the hunt then falls back
        # to the configured both-ways swing rather than inventing a direction.
        self._last_pickup_lateral: int = 0
        self._return_controller: PickupReturnController | None = None

    def _detect_tag(self, task: VisionTask, image, *, frame_id: int,
                    captured_at: float, **_kwargs):
        observations = self.tag_detector.detect(
            image, timestamp_ns=int(captured_at * 1e9), frame_index=frame_id
        )
        tracker = self._tag_trackers.get(task)
        if tracker is None:
            gate_name = "pickup_seek_line" if task is VisionTask.TAG3 else "pickup_2_seek_line"
            gate = self.vision.tag_gates[gate_name]
            height, width = image.shape[:2]
            tracker = TagTracker(
                target_id=gate.target_id, gate=gate, frame_size=(width, height)
            )
            self._tag_trackers[task] = tracker
        return tracker.update(observations)

    def _detect_block(self, task: VisionTask, image, *, frame_id: int,
                      captured_at: float, **_kwargs):
        return self._block_detectors[task].detect(
            image, frame_index=frame_id, timestamp_ns=int(captured_at * 1e9)
        )

    def _detect_prescan(self, image, *, frame_id: int, captured_at: float,
                        generation: int, **_kwargs):
        if generation != self._prescan_generation:
            self._prescan_generation = generation
            self._prescan_tracker.reset()
            self._purple_search_hint = None
            self._purple_center_pickup_pending = False
        detection = self._detect_block(
            VisionTask.PURPLE_PRESCAN, image, frame_id=frame_id,
            captured_at=captured_at,
        )
        decision = self._prescan_tracker.update(
            detection.accepted, frame_width=image.shape[1]
        )
        return {
            "present": decision.present,
            "hint": decision.hint,
            "detection": detection,
            "frames": decision.frames,
            "region_counts": decision.region_counts,
            "candidate_centers": decision.candidate_centers,
        }

    def start(self) -> None:
        if self._started:
            return
        self.capture.capture_once()
        self.capture.start()
        self.worker.start()
        self._started = True

    @staticmethod
    def _line_found(mask: int, config: RouteV2Config) -> bool:
        probes = 8 - bin(mask & 0xFF).count("1")
        return mask in config.seek_line_masks or (
            config.seek_line_min_black_probes <= probes < 8
        )

    def _select(self, task: VisionTask, state: RouteState,
                absolute_lateral_cm: float | None) -> None:
        if task is self._task:
            return
        self._task = task
        self._generation = self.worker.select(task)
        self._task_selected_at = self.clock()
        self._task_first_fresh_frame_id = None
        self._task_ready = False
        if task in {VisionTask.PURPLE_CLOSE, VisionTask.ORANGE_CLOSE}:
            area = "purple" if task is VisionTask.PURPLE_CLOSE else "orange"
            area_cfg = self.vision.pickup_areas[area]
            profile = self.vision.block_profiles[area_cfg.profile]
            snapshot = self.frames.snapshot(max_age_s=365 * 24 * 3600)
            if snapshot.image is None:
                frame_size = (1280, 720)
            else:
                height, width = snapshot.image.shape[:2]
                frame_size = (width, height)
            # Which block the car goes for is already known (the prescan slot), so
            # the pickup's FIRST look goes straight at it instead of sweeping.
            #   Operator, 2026-09-22 night: "只有一个紫色在左边 就立马去左边取".
            # The hint alone is not enough -- it is None whenever the prescan ends
            # on region counts rather than a confirmed centre, and the old
            # `hint or "right"` default is what sent the first sweep right past a
            # left-hand block in run 20260922_214120.
            # Which direction the first sweep goes.  The orange area looks RIGHT
            # first, then LEFT -- the controller has exactly two sweeps and they are
            # opposites, so the left sweep is the last one.
            #
            # Operator, 2026-09-24: 「抓取的逻辑改成优先右边」, after a round of
            # 「优先往左边找」.  Both directions have now been tried on the car; this
            # is the current answer.
            first_look = "right"
            if area == "purple":
                first_look = ({1: "left", 2: "center", 3: "right"}.get(
                    self._purple_target_slot) or self._purple_search_hint or "right")
            self._pickup_controller = PickupVisionController(
                area,
                area_cfg,
                profile,
                frame_size=frame_size,
                initial_search=first_look,
            )
            self._pickup_area = area
            self._pickup_baseline_cm = absolute_lateral_cm
            self._return_controller = None
        elif state not in {RouteState.PICKUP_RETURN_TO_LINE,
                           RouteState.PICKUP_2_RETURN_TO_LINE,
                           RouteState.PURPLE_RETURN_TO_LINE}:
            # PURPLE_RETURN_TO_LINE is listed for symmetry with the other two,
            # NOT because it is currently load-bearing: its vision task is NONE
            # and the return branch rebuilds the controller in the very same
            # call that would clear it, so removing this line changes nothing
            # observable today (checked by reverting it -- the wiring test still
            # passed).  It is here so that stays true if this state is ever
            # given a vision task of its own.
            self._return_controller = None

    def observe(self, *, state: RouteState, now: float,
                absolute_lateral_cm: float | None, wall_contact: bool,
                stopped: bool, sensor_mask: int, **_kwargs):
        self.start()
        task = vision_task_for_state(state)
        self._select(task, state, absolute_lateral_cm)
        frame = self.frames.snapshot(max_age_s=self.vision.frame_timeout_s)
        worker_status = self.worker.status()
        camera_status = self.capture.status()
        diagnostics = {
            "camera": {"fresh": frame.fresh, "frame_id": frame.frame_id,
                       "age_s": None if frame.captured_at is None else round(frame.age_s, 4),
                       "error": camera_status.error},
            "vision": {"task": task.value, "generation": self._generation,
                       "error": worker_status.error,
                       "worker_running": getattr(worker_status, "running", True),
                       "worker_frame_id": getattr(worker_status, "frame_id", None)},
        }
        result = self.worker.result()
        result_fresh = vision_result_is_fresh(
            result, generation=self._generation, now=now,
            max_age_s=self.vision.frame_timeout_s,
        )
        if result_fresh and not self._task_ready:
            if self._task_first_fresh_frame_id is None:
                self._task_first_fresh_frame_id = result.frame_id
            elif result.frame_id != self._task_first_fresh_frame_id:
                self._task_ready = True
        task_active = task is not VisionTask.NONE
        vision_pending = task_active and not self._task_ready
        startup_timeout = (
            vision_pending
            and now - self._task_selected_at > self.vision.task_startup_timeout_s
        )
        result_completed_at = None if result is None else getattr(
            result, "completed_at", result.captured_at
        )
        steady_stale = task_active and self._task_ready and not result_fresh
        steady_timeout = (
            steady_stale
            and result_completed_at is not None
            and now - result_completed_at > self.vision.result_stall_timeout_s
        )
        fault_reason = None
        if task_active and not frame.fresh:
            fault_reason = "camera_frame_stale"
        elif task_active and worker_status.error is not None:
            fault_reason = "vision_worker_error"
        elif startup_timeout:
            fault_reason = "vision_startup_timeout"
        elif steady_timeout:
            fault_reason = "vision_result_timeout"
        camera_fault = fault_reason is not None
        vision_pending = vision_pending and not camera_fault
        vision_hold = steady_stale and not camera_fault
        diagnostics["vision"]["fresh"] = result_fresh
        diagnostics["vision"]["pending"] = vision_pending
        diagnostics["vision"]["ready"] = self._task_ready
        diagnostics["vision"]["hold"] = vision_hold
        diagnostics["vision"]["fault_reason"] = fault_reason
        if result is not None:
            diagnostics["vision"]["processing_s"] = getattr(result, "processing_s", None)
        route_input = VisionRouteInput(
            camera_fault=camera_fault,
            vision_pending=vision_pending,
            vision_hold=vision_hold,
        )
        value = result.value if result_fresh and self._task_ready else None
        if result is not None:
            diagnostics["vision"]["result_frame_id"] = result.frame_id

        search_phases = {
            PickupPhase.SEARCH_RIGHT,
            PickupPhase.RETURN_BASELINE,
            PickupPhase.SEARCH_LEFT,
        }
        if (vision_hold
                and task in {VisionTask.PURPLE_CLOSE, VisionTask.ORANGE_CLOSE}
                and self._pickup_controller is not None
                and self._pickup_controller.phase in search_phases
                and absolute_lateral_cm is not None):
            pickup = self._pickup_controller.step(
                now=now,
                lateral_cm=absolute_lateral_cm,
                target=None,
                contact=wall_contact,
                stopped=stopped,
                frame_id=None,
            )
            diagnostics["vision"]["search_continued"] = True
            diagnostics["pickup_phase"] = pickup.phase.value
            diagnostics["pickup_reason"] = pickup.reason
            diagnostics["pickup_result"] = pickup.result
            return VisionRouteInput(
                pickup_kind=pickup.kind,
                pickup_speed=pickup.speed,
                camera_fault=False,
                vision_hold=False,
            ), diagnostics

        # A center hit in the J3 prescan is already at the collision approach
        # pose. Start the existing purple pickup package at the first stopped
        # pickup tick instead of moving the car away from that target.
        if (task is VisionTask.PURPLE_CLOSE
                and state is RouteState.PICKUP_VISION_ONLY
                and getattr(self, "_purple_center_pickup_pending", False)):
            self._purple_center_pickup_pending = False
            diagnostics["pickup_phase"] = "DIRECT_CENTER_READY"
            diagnostics["pickup_reason"] = "prescan_center_at_collision"
            return VisionRouteInput(
                pickup_kind="pickup_ready",
                camera_fault=camera_fault,
            ), diagnostics

        if vision_pending or vision_hold or camera_fault:
            return route_input, diagnostics

        if task in {VisionTask.TAG2, VisionTask.TAG3, VisionTask.TAG4} and value is not None:
            tag_payload = {
                "stable": value.stable,
                "id": None if value.observation is None else value.observation.id,
                "consecutive_frames": value.consecutive_frames,
                "missed_frames": value.missed_frames,
            }
            diagnostics["tag"] = tag_payload
            if task is VisionTask.TAG2:
                diagnostics["tag2_stable"] = value.stable
            elif task is VisionTask.TAG3:
                route_input = VisionRouteInput(tag3_stable=value.stable, camera_fault=camera_fault)
            else:
                route_input = VisionRouteInput(tag4_stable=value.stable, camera_fault=camera_fault)
        elif task is VisionTask.PURPLE_PRESCAN and value is not None:
            frame_width = 1280 if frame.image is None else int(frame.image.shape[1])
            purple_slots = purple_slots_from_result(value, frame_width=frame_width)
            # The block this round goes for, by the state machine's own rule --
            # taken from the REGION COUNTS, never from `present`.
            #
            # Measured 2026-09-22 night (run 20260922_214120): the prescan reports
            # present=None AND hint=None while still producing a perfectly usable
            # slot tuple (region_counts left=3, right=0).  An earlier version of
            # this gated on `present is True`, so the slot stayed unset and two
            # things went wrong at once: the pickup searched RIGHT for a
            # left-hand block ("明明只有左边有方块 为什么还先右边找"), and the
            # return hunt fell back to sweeping both ways.
            slot = choose_purple_slot(purple_slots)
            if slot is not None:
                self._purple_target_slot = slot
            if value["present"] is True:
                self._purple_search_hint = value["hint"]
                self._purple_center_pickup_pending = value["hint"] == "center"
            route_input = VisionRouteInput(
                purple_prescan_present=value["present"],
                purple_slots=purple_slots,
                camera_fault=camera_fault,
            )
            diagnostics["prescan"] = {
                "frames": value["frames"],
                "present": value["present"],
                "hint": value["hint"],
                "region_counts": value["region_counts"],
                "candidate_centers": [list(center) for center in value["candidate_centers"]],
            }
        elif task in {VisionTask.PURPLE_CLOSE, VisionTask.ORANGE_CLOSE}:
            target = self._pickup_controller.select_target(value.accepted) \
                if self._pickup_controller is not None and value is not None else None
            if target is not None:
                diagnostics["block"] = target.to_dict()
            if self._pickup_controller is not None and absolute_lateral_cm is not None:
                pickup = self._pickup_controller.step(
                    now=now, lateral_cm=absolute_lateral_cm, target=target,
                    contact=wall_contact, stopped=stopped,
                    frame_id=None if result is None else result.frame_id,
                )
                diagnostics["pickup_phase"] = pickup.phase.value
                diagnostics["pickup_reason"] = pickup.reason
                diagnostics["pickup_result"] = pickup.result
                # Latch the direction the car drives to REACH the block; the
                # post-grab line hunt goes the opposite way (_last_pickup_lateral).
                # Only the commands that move TOWARD a target count: strafe_left /
                # strafe_right are the bounded search sweep and align_left /
                # align_right are the final approach.  `return_baseline` is
                # deliberately excluded -- it repositions the car to the search
                # origin and says nothing about which side the block was on.
                if (pickup.kind in {"strafe_left", "strafe_right",
                                    "align_left", "align_right"} and pickup.speed):
                    self._last_pickup_lateral = 1 if pickup.speed > 0 else -1
                route_input = VisionRouteInput(
                    pickup_kind=pickup.kind,
                    pickup_speed=pickup.speed,
                    action_done=False,
                    camera_fault=camera_fault,
                )
        elif task is VisionTask.BUILD_OCCUPANCY and value is not None:
            # The biggest blob is the structure in front: BlockDetectionResult sorts
            # `accepted` by area, descending.
            observation = value.accepted[0] if value.accepted else None
            center_error = None
            if observation is not None:
                snapshot = self.frames.snapshot(max_age_s=365 * 24 * 3600)
                width = 1280 if snapshot.image is None else snapshot.image.shape[1]
                window = self._build_profile.capture_window
                window_center = (window.left + window.right) / 2
                # Normalised like the pickup's own alignment error: negative means
                # the blob sits left of centre, and the car then strafes LEFT.
                center_error = observation.center_px[0] / width - window_center
            route_input = VisionRouteInput(
                build_block_visible=bool(value.accepted),
                build_center_error=center_error,
                camera_fault=camera_fault,
            )

        if state in {RouteState.PICKUP_RETURN_TO_LINE, RouteState.PICKUP_2_RETURN_TO_LINE,
                     RouteState.PURPLE_RETURN_TO_LINE}:
            if absolute_lateral_cm is None or self._pickup_baseline_cm is None:
                return VisionRouteInput(pickup_kind="fault", camera_fault=camera_fault), diagnostics
            if self._return_controller is None:
                area_cfg = self.vision.pickup_areas[self._pickup_area]
                # Which way to reacquire the line is decided at J3, together with
                # the block itself -- operator, 2026-09-22 night: "在j3左旋完毕
                # 那一刻判定（如果就只有左边 就立马去左边取 取完后一直往右边找
                # 线）" and "中间没有两边有 优先右边 取完后一直左走找线".  The hunt
                # goes AWAY from the block that was taken; positive strafes LEFT.
                # Centre or no purple at all keeps the original sweep, and the
                # orange area has no purple slot of its own.
                one_way = 0
                if self._pickup_area == "purple":
                    one_way = {1: -1, 3: 1}.get(self._purple_target_slot, 0)
                elif self._pickup_area == "orange" and self._last_pickup_lateral:
                    # The orange area has no prescan slot, so the direction comes
                    # from the pickup itself: the hunt goes back the way the car
                    # came (see _last_pickup_lateral).  Operator, 2026-09-24.
                    one_way = -self._last_pickup_lateral
                # The orange one-way hunt is bounded by the area's OWN measured
                # safe strafe distance in that direction (75 cm both ways,
                # independently measured -- see search_left/search_right).  Those
                # are the operator's numbers for exactly this question: how far may
                # this car slide sideways from this pose before it hits something.
                # The purple area keeps seek_line_max_cm, unchanged.
                seek_max_cm = self.config.seek_line_max_cm
                if one_way and self._pickup_area == "orange":
                    guard = area_cfg.search_left if one_way > 0 else area_cfg.search_right
                    seek_max_cm = guard.max_distance_cm
                self._return_controller = PickupReturnController(
                    baseline_cm=self._pickup_baseline_cm,
                    tolerance_cm=area_cfg.return_tolerance_cm,
                    # return_speed, not fine_speed: the operator asked for the
                    # line SEARCH to be twice as fast (2026-09-24), and
                    # fine_speed is shared with the pickup's final approach,
                    # which must stay slow.  return_speed defaults to fine_speed
                    # when the area does not set it.
                    speed=area_cfg.return_speed or area_cfg.fine_speed,
                    seek_max_cm=seek_max_cm,
                    timeout_s=self.config.seek_line_timeout_s,
                    confirm_frames=self.config.seek_line_confirm_frames,
                    one_way_direction=one_way,
                    swing_cm=self.config.pickup_return_line_swing_cm,
                )
            returning = self._return_controller.step(
                now=now, absolute_lateral_cm=absolute_lateral_cm,
                line_found=self._line_found(sensor_mask, self.config),
            )
            diagnostics["pickup_phase"] = returning.phase.value
            route_input = VisionRouteInput(
                pickup_kind=returning.kind,
                pickup_speed=returning.speed,
                return_line_done=returning.kind == "return_line_done",
                camera_fault=camera_fault,
            )
        return route_input, diagnostics

    def suspend(self, *, state: RouteState, **_kwargs):
        """Pause detector work after visual alignment hands off to an action."""
        self.start()
        self._select(VisionTask.NONE, state, None)
        return VisionRouteInput(pickup_kind="pickup_ready"), {
            "vision": {
                "task": VisionTask.NONE.value,
                "pending": False,
                "ready": False,
                "hold": False,
                "fault_reason": None,
                "suspended": "pickup_action",
            }
        }

    def restart_pickup_search(self, *, absolute_lateral_cm: float | None = None) -> None:
        """Start a fresh bounded search in the current pickup area.

        The orange area can contain two or three ordered blocks.  After a
        placeholder action completes, the car remains in the same area and
        the controller must forget the previous locked target before searching
        for the next block.
        """
        if self._pickup_area is None or self._pickup_controller is None:
            return
        area_cfg = self.vision.pickup_areas[self._pickup_area]
        profile = self.vision.block_profiles[area_cfg.profile]
        frame = self.frames.snapshot(max_age_s=365 * 24 * 3600)
        frame_size = (1280, 720) if frame.image is None else (
            int(frame.image.shape[1]), int(frame.image.shape[0])
        )
        self._pickup_controller = PickupVisionController(
            self._pickup_area,
            area_cfg,
            profile,
            frame_size=frame_size,
            initial_search="right",
        )
        if absolute_lateral_cm is not None:
            self._pickup_baseline_cm = absolute_lateral_cm

    def close(self) -> None:
        self.worker.close()
        self.capture.close()


# Forward projection of the four encoder counts, calibrated on 2026-09-14 with
# D 20 0 0 20.  The wheels are mounted mirrored, so the raw counts and their mean
# mean nothing on their own; see route_v2.md section 5.
FORWARD_COUNTS_PER_CM = 58.8
# Lateral projection of the same four counts, (LF + RF - LR - RR) / 4, positive =
# LEFT.  Calibrated the same way as the forward constant, from commanded D
# distances, so it absorbs that command's overshoot; used here for a V strafe,
# where it has never been verified.  See route_v2.md section 5.1.
LATERAL_COUNTS_PER_CM = 56.8

# How long the chassis is held quiet before a turn's D command is sent, so the D
# does not land inside the V burst of the leg that preceded it.  See the d branch
# of the intent dispatch.  0.3 s covers at least one STOP re-send (the runner
# re-sends STOP every 200 ms).
D_SETTLE_S = 0.3


# Frames discarded before judging the stream.  The first frames after opening are
# usually dark or garbage, but their SHAPE is valid either way, so this only has
# to outlast the driver's warm-up.
_CAMERA_WARMUP_FRAMES = 10


def _load_route_camera_config(path: Path, loader, *, required: bool):
    try:
        return loader(path)
    except Exception as exc:
        if required:
            raise RuntimeError(
                f"visual route requires a valid camera calibration config at {path}: {exc}"
            ) from exc
        return None


def _configure_camera(
    camera,
    config,
    cv2_module,
    *,
    require_calibrated_size: bool = False,
) -> list[str]:
    """Ask the capture for the calibrated stream, and prove it actually delivers.

    `cv2.VideoCapture(0)` on its own opens at the driver default -- measured
    640x480 on this Pi -- while config/camera_config.yaml calibrates 1280x720.
    Feeding a 1280x720 intrinsic matrix to a 640x480 frame puts the principal
    point (660.75, 363.38) and the focal lengths (1107.7, 1102.9) off by roughly
    a factor of two, so any pose solved from it is wrong: the distance by about
    2x, and the lateral offset by a depth-proportional bias of about -0.6 * Z.
    Tag *identity* detection survives that, so nothing looked broken; tag range
    and bearing do not, and range is what decides how far the car strafes before
    tag 2 enters the frame.

    WHICH PROPERTIES TO SET, measured 2026-09-15 on this Pi with
    _pi_cam_config_probe.py -- six configurations, each opened, then asked for
    fifteen real frames:

        FOURCC, WIDTH, HEIGHT       15/15 frames, 1280x720
        WIDTH, HEIGHT               15/15 frames, 1280x720
        FOURCC, WIDTH, HEIGHT, FPS  capture did not open
        WIDTH, HEIGHT, FPS          capture did not open
        FOURCC only                 15/15 frames,  640x480
        nothing set                 15/15 frames,  640x480

    CAP_PROP_FPS is what kills it.  The two configurations carrying it are the
    only two that failed, and they failed in the middle of the sweep with the two
    after them succeeding -- so it is the property, not a device left busy.
    CAP_PROP_BUFFERSIZE is left out as well: it was only ever exercised together
    with FPS, so it is unmeasured here, and 1280x720 does not need it.  FOURCC is
    innocent and its order (before the size, or V4L2 clamps) is correct.

    The cost of getting this wrong is the 2026-09-15 field run: with FPS set, the
    pipeline died at startup ("v4l2src0 reported: Internal data stream error",
    "unable to start pipeline", "no pipeline") and tag 2 was never detected in
    2309 ticks -- including while the car sat exactly where a standalone probe had
    just seen it in 10/10 frames.  Nothing said so; the car simply strafed to its
    ceiling and waited.

    So this no longer trusts capture.get() either.  Measured the same day: both
    working configurations report 640x480 through get() while delivering genuine
    1280x720 frames, so a get()-based check reports a fault that is not there.
    Only a decoded frame is honest, and only a decoded frame is checked.

    Returns warnings for a stream that works but is not what the calibration
    describes, unless require_calibrated_size is true.  The visual route uses
    absolute-pixel pickup gates as well as calibrated tag pose, so it must fail
    closed on the wrong decoded size.  Raises RuntimeError on a stream that
    delivers nothing at all -- tag 2 is what ends the strafe, so a dead camera
    cannot finish this route, and refusing to start is strictly better than
    driving 91 cm to discover it.  Tolerates a capture object with no set/read,
    which is what the tests inject.
    """
    setter = getattr(camera, "set", None)
    if setter is None:
        return []
    # FOURCC first: on V4L2 the pixel format has to be set before the size, or
    # the driver clamps the size to what the current format supports.
    for prop, value in (
        (getattr(cv2_module, "CAP_PROP_FOURCC", 6),
         cv2_module.VideoWriter_fourcc(*config.pixel_format)),
        (getattr(cv2_module, "CAP_PROP_FRAME_WIDTH", 3), config.width),
        (getattr(cv2_module, "CAP_PROP_FRAME_HEIGHT", 4), config.height),
    ):
        setter(prop, value)
    reader = getattr(camera, "read", None)
    if reader is None:
        return []
    for _ in range(_CAMERA_WARMUP_FRAMES):
        ok, frame = reader()
        if ok and frame is not None and getattr(frame, "shape", None):
            height, width = int(frame.shape[0]), int(frame.shape[1])
            if (width, height) != (config.width, config.height):
                warning = (
                    f"camera delivered {width}x{height} but the calibration is "
                    f"{config.width}x{config.height}; tag range and bearing from this "
                    f"run are not trustworthy"
                )
                if require_calibrated_size:
                    raise RuntimeError(
                        f"visual route requires the calibrated "
                        f"{config.width}x{config.height} camera stream; {warning}"
                    )
                return [warning]
            return []
    raise RuntimeError(
        f"camera opened but delivered no frames in {_CAMERA_WARMUP_FRAMES} reads -- "
        "tag 2 can never be detected, so the strafe would run to its ceiling and "
        "hold.  Refusing to start the route; check the capture pipeline."
    )


def _forward_counts(raw: tuple[int, int, int, int]) -> float:
    """(LF, RF, LR, RR) counters -> the forward projection, in raw counts.

    The plain mean says nothing on this chassis: the wheels are mounted
    mirrored, so a straight drive reads as four wheels fighting each other and
    their sum barely moves.  Only the projections separate forward, lateral and
    yaw (see route_v2.md section 5).

    Positional, like the `lf, rf, lr, rr = self._last_encoder_raw` unpack it
    shares its assumption with: the ENC reply is parsed by label but the labels
    are only checked for presence, not for order.
    """
    lf, rf, lr, rr = raw
    return (-lf + rf - lr + rr) / 4.0


@dataclass
class EncoderContactDetector:
    """Arrival detection: the odometer stops while the leg is still running.

    This used to also require `SPD`'s OUT to have collapsed, and that half of
    the test is what broke it.  OUT is not a measurement of anything -- it is
    the duty the firmware is commanding -- so through the stall it went on
    reading 47 while `pickup_speed` was 15, and `47 <= 15 * 0.15` can never
    hold.  Run 6 (2026-09-15) is the proof: the encoders sat frozen at one
    count for 62 s on the first approach leg with the detector silent the
    whole time, and the arrival was only declared once the firmware happened
    to report OUT as 0 -- sixty-two seconds after the car had stopped.

    So the wheels DO stall; it is the skid that never happens.  The odometer
    is the only real measurement on this link, so the odometer is the whole
    test.  It is armed only once the leg has been seen moving, because before
    the first ENC reply lands every delta is zero and an unarmed detector
    would call the wall on the spot -- the same "leave before arming" rule the
    junction debouncer uses.

    `encoder_delta` must be the FORWARD PROJECTION, not the mean of the four
    counters.  Run 11 (2026-09-18) is the proof: driving straight up the ramp,
    the four counters summed to a constant 21749..21761 and the mean delta sat
    at 0.25..0.75 -- below `counts_epsilon` -- while the forward projection
    advanced 852 counts (14.5 cm).  The mean-based detector called the wall at
    54 cm of a 280 cm leg and faulted the run.  See _forward_counts.

    `commanded=False` means the route is deliberately stopped this tick.  This
    is NOT the old OUT test coming back: it reads the intent the runner
    dispatched, never a firmware echo.
    """

    stationary_s: float = 0.5
    min_approach_s: float = 1.5
    counts_epsilon: float = 1.0
    started_at: float | None = None
    stationary_since: float | None = None
    seen_motion: bool = False

    def reset(self, now: float) -> None:
        self.started_at = now
        self.stationary_since = None
        self.seen_motion = False

    def update(self, now: float, *, encoder_delta: float, commanded: bool = True) -> bool:
        if self.started_at is None:
            self.reset(now)
        if not commanded:
            # The route is deliberately stopped this tick -- a car it stopped
            # itself is not a wall.  seen_motion is left alone: the leg may have
            # moved earlier and must stay armed for the real wall.
            self.stationary_since = None
            return False
        if abs(encoder_delta) > self.counts_epsilon:
            self.seen_motion = True
            self.stationary_since = None
            return False
        if not self.seen_motion or now - self.started_at < self.min_approach_s:
            return False
        if self.stationary_since is None:
            self.stationary_since = now
        return now - self.stationary_since >= self.stationary_s


# How hard the exit path tries to actually stop the chassis.  A single STOP is
# known not to stop it (_command_stop has the measurement), so letting go after
# one frame is the difference between a controlled stop and a runaway -- which
# is exactly what happened at the pickup wall on 2026-09-15.  Ten frames at the
# route's own 200 ms cadence is the same effort _pi_halt.py makes by hand.
_STOP_SETTLE_FRAMES = 10
_STOP_SETTLE_INTERVAL_S = 0.2


def _state_by_name(name: str) -> RouteState:
    """A RouteState by name, with a clean error instead of a traceback.

    Shared by --only, --from/--to and --until.  --from NOPE used to raise an
    uncaught KeyError, which main() does not catch, so a typo produced a
    traceback where every other bad argument produces a FAULT_SAFE line.
    """
    try:
        return RouteState[name]
    except KeyError as exc:
        raise RouteSelectionError(f"unknown state: {name}") from exc


def _selected_states(config: RouteV2Config, start: str | None, end: str | None, only: str | None) -> tuple[RouteState, ...]:
    states = tuple(RouteState)
    if only and (start or end):
        raise RouteSelectionError("--only cannot be combined with --from/--to")
    if only:
        return (_state_by_name(only),)
    first = 0 if start is None else states.index(_state_by_name(start))
    last = len(states) - 1 if end is None else states.index(_state_by_name(end))
    if first > last:
        raise RouteSelectionError("--from must not be after --to")
    return states[first:last + 1]


class _DryChassis:
    def __init__(self):
        self.commands: list[tuple] = []

    def set_velocity(self, vx: int, vy: int, wz: int): self.commands.append(("V", vx, vy, wz))
    def run_distance(self, forward_cm: int, right_cm: int, rotate_deg: int, speed: int): self.commands.append(("D", forward_cm, right_cm, rotate_deg, speed))
    def stop(self): self.commands.append(("STOP",))


class RouteRunner:
    """Drive the v2 state machine through narrow, injectable device adapters."""

    def _capture_orange_pickup_photo(self, diagnostics: dict) -> None:
        """Save the frame the orange "recognised -> stop three seconds" decision used.

        Called once per orange pickup, on the tick where the controller reports
        `pickup_ready` and the route arms the placeholder action -- the moment
        the operator sees the car stop for three seconds.  Enqueueing is all this
        does: the JPEG encode and the write happen on the recorder's own thread.
        A missing or stale frame is not an error; it just means no photo.
        """
        if self._evidence is None or self.vision_runtime is None:
            return
        try:
            snapshot = self.vision_runtime.frames.snapshot(
                max_age_s=self.config.vision.frame_timeout_s
            )
        except Exception:
            return
        if snapshot.image is None or not snapshot.fresh:
            return
        self._evidence.offer(
            snapshot.image,
            frame_id=snapshot.frame_id,
            captured_at=snapshot.captured_at if snapshot.captured_at is not None else 0.0,
            record={
                "trigger": "orange_pickup_ready",
                "state": self.machine.state.value,
                "pending_action": self._pending_pickup_action,
                "pickup_phase": diagnostics.get("pickup_phase"),
                "pickup_reason": diagnostics.get("pickup_reason"),
                "block": diagnostics.get("block"),
            },
        )

    def __init__(self, config: RouteV2Config, chassis, line_source, *, camera=None,
                 tag_detector=None, encoder_source=None, initial_state: RouteState | None = None,
                 clock=None, sleeper=None, telemetry=None, tag_tracker=None,
                 stop_at: RouteState | None = None, vision_runtime=None,
                 purple_action=None, orange_action=None,
                 build_action=None, evidence=None):
        self.config = config
        self.chassis = chassis
        self.line_source = line_source
        self.camera = camera
        self.tag_detector = tag_detector
        self.tag_tracker = tag_tracker or TagTracker(target_id=2)
        self.vision_runtime = vision_runtime
        if self.vision_runtime is None and config.vision is not None and camera is not None:
            self.vision_runtime = RouteVisionRuntime(
                config, camera, tag_detector, clock=clock or time.monotonic
            )
        action_wait_s = config.vision.action_wait_s if config.vision is not None else 3.0
        self.purple_action = purple_action or PlaceholderActionExecutor(
            "PICK_PURPLE", wait_s=action_wait_s
        )
        self.orange_action = orange_action or PlaceholderActionExecutor(
            "PICK_ORANGE_1", wait_s=action_wait_s
        )
        self.build_action = build_action or PlaceholderActionExecutor(
            "BUILD_2", wait_s=action_wait_s
        )
        self._pickup_action_state: RouteState | None = None
        self._pickup_action_triggered = False
        self._pickup_reset_phase = False
        self._pending_pickup_action: str | None = None
        self._pending_orange_slot: str | None = None
        # Photo evidence for the orange "recognised -> stop three seconds"
        # decision.  None unless --capture-orange was passed.
        self._evidence = evidence
        # --until: come to a controlled stop the moment the machine reaches this
        # state.  A field test of one leg needs an end that is a state, not a
        # stopwatch -- a timeout that fires late runs the car into the next leg.
        self.stop_at = stop_at
        self._reached_target = False
        self._tag2_result = TagTrackingResult(False, None, 0, 0)
        self._tag_frame_index = 0
        self.encoder_source = encoder_source
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        # Optional per-tick recording hook.  The field runs produced no
        # telemetry at all, which made a crash impossible to diagnose after
        # the fact.
        self.telemetry = telemetry
        self.machine = RouteV2StateMachine(config)
        # States whose line detection stutters while the car is on the line, so
        # a lost frame must not stop it.  Resolved once from the config's names
        # rather than compared as strings every tick.
        self._hold_course_states = frozenset(
            RouteState(name) for name in config.hold_course_on_line_loss
        )
        if initial_state is not None:
            self.machine.state = initial_state
            self.machine._state_started = self.clock()
        self.pid = LinePidController(kp=config.kp, ki=config.ki, kd=config.kd,
                                     integral_limit=config.integral_limit,
                                     lateral_limit=config.lateral_limit,
                                     yaw_limit=config.yaw_limit)
        self.contact = EncoderContactDetector(stationary_s=config.contact_stationary_s)
        self._contact_state: RouteState | None = None
        self._last_tick = self.clock()
        self._last_v_at = -float("inf")
        self._issued_d: tuple | None = None
        # Settle window for a newly requested D: the key waiting to be sent, and
        # the time before which it must not be sent (see the d branch).
        self._d_pending_key: tuple | None = None
        self._d_settle_at = 0.0
        self._d_done = False
        self._actual_speed = 0.0
        self._encoder_delta = 0.0
        self._encoder_delta_forward = 0.0
        self._last_encoder: float | None = None
        self._last_forward: float | None = None
        self._last_encoder_raw: list[int] | None = None
        self._last_motion_query_at = -float("inf")
        self._query_encoder_next = False
        self._last_stop_at = -float("inf")
        self._last_intent_kind: str | None = None
        self._travel_state: RouteState | None = None
        self._travel_base: float | None = None
        self._lateral_base: float | None = None

    def _read_line(self):
        """Latest mask and line error, with "no reading" kept distinct from black.

        A missing mask used to be coerced to 0 here.  That is the all-black
        pattern, so a single dropped sensor frame -- or a sensor that stops
        streaming entirely -- was delivered to the state machine as a junction
        and could advance the route.  Report no-reading as 0xFF instead: every
        probe off the line, which no junction pattern may be (the config
        validation rejects 0xFF for exactly this reason).
        """
        value = self.line_source.poll_once() if hasattr(self.line_source, "poll_once") else self.line_source()
        if isinstance(value, dict):
            mask = value.get("sensor_mask")
            return (0xFF if mask is None else int(mask)), value.get("line_error")
        mask = getattr(value, "sensor_mask", None)
        return (0xFF if mask is None else int(mask)), getattr(value, "line_error", None)

    def tick(self, now: float | None = None, *, wall_contact: bool | None = None) -> RouteIntent:
        now = self.clock() if now is None else now
        mask, line_error = self._read_line()
        if (self.vision_runtime is None and self.camera is not None
                and self.tag_detector is not None):
            ok, frame = self.camera.read()
            if ok:
                observations = self.tag_detector.detect(
                    frame,
                    timestamp_ns=int(now * 1e9),
                    frame_index=self._tag_frame_index,
                )
                self._tag_frame_index += 1
                self._tag2_result = self.tag_tracker.update(observations)
        replies = self.chassis.poll() if hasattr(self.chassis, "poll") else []
        self._d_done = any(getattr(reply, "kind", None) == "done" for reply in replies)
        for reply in replies:
            value = str(getattr(reply, "value", ""))
            if getattr(reply, "kind", None) == "speed":
                match = re.search(r"OUT\s+(-?\d+(?:\.\d+)?(?:\s+-?\d+(?:\.\d+)?){3})", value)
                if match:
                    self._actual_speed = max(abs(float(item)) for item in match.group(1).split())
            elif getattr(reply, "kind", None) == "encoder":
                numbers = [float(item) for item in re.findall(r"(?:LF|RF|LR|RR)\s+(-?\d+(?:\.\d+)?)", value)]
                if numbers:
                    # Keep the four raw counts as well as their mean.  The mean
                    # is meaningless on this chassis -- the wheels are mounted
                    # mirrored, so an in-place translation reads as wheels
                    # fighting each other -- while the three orthogonal
                    # projections of the raw counts give forward, lateral and
                    # yaw separately (see route_v2.md section 5).
                    self._last_encoder_raw = [int(item) for item in numbers]
                    # The contact detector runs on the FORWARD PROJECTION, not
                    # on this mean -- see EncoderContactDetector's docstring.
                    # The mean delta is kept for telemetry continuity with the
                    # runs before 2026-09-18, where it was the only delta there
                    # was; it is NOT what wall_contact is decided on.
                    forward = _forward_counts(numbers)
                    self._encoder_delta_forward = (
                        0.0 if self._last_forward is None else abs(forward - self._last_forward)
                    )
                    self._last_forward = forward
                    current = sum(numbers) / len(numbers)
                    self._encoder_delta = 0.0 if self._last_encoder is None else abs(current - self._last_encoder)
                    self._last_encoder = current
        # A car driving perfectly well and a car stalled against an obstacle
        # used to produce identical telemetry: SPD and ENC were only requested
        # in the pickup approach, so actual_speed and encoder_delta stayed 0 for the
        # whole line-following run.  The 2026-09-14 field run proved it -- ten
        # seconds of driving logged zero motion, and the only evidence the car
        # moved was the operator watching it.  Request both at a low rate while
        # the route is moving under V.  The replies are picked up by a later
        # tick's poll(), so this costs two short writes, not two round trips.
        # Distance travelled since this state began, taken from the encoder's
        # forward projection.  Only the delta matters, so the counters never
        # need resetting: the baseline is re-taken whenever the state changes.
        # The sensor cannot identify a junction -- 10000000 means both "branch to
        # the right" and "one probe off the line, slightly yawed" -- so this is
        # what START_TO_JUNCTION_1 ends on.
        travel_cm = None
        lateral_cm = None
        absolute_lateral_cm = None
        if self._last_encoder_raw is not None:
            lf, rf, lr, rr = self._last_encoder_raw
            forward = _forward_counts((lf, rf, lr, rr))
            # The lateral projection, tracked alongside.  The strafe state needs
            # this one specifically: it moves the car SIDEWAYS, so its forward
            # projection stays at zero and an odometer window measured on
            # travel_cm would never open.
            lateral = (lf + rf - lr - rr) / 4.0
            absolute_lateral_cm = lateral / LATERAL_COUNTS_PER_CM
            if self._travel_state is not self.machine.state or self._travel_base is None:
                self._travel_state = self.machine.state
                self._travel_base = forward
                self._lateral_base = lateral
            travel_cm = (forward - self._travel_base) / FORWARD_COUNTS_PER_CM
            lateral_cm = (lateral - self._lateral_base) / LATERAL_COUNTS_PER_CM
        if self.machine.state in _CONTACT_STATES and self.machine.state is not self._contact_state:
            # The arrival test learns from the leg it is watching, so it has to
            # start when the leg does.  It used to reset only at construction,
            # which meant the "approach for a moment before believing anything"
            # guard was measured from process start and was already satisfied by
            # the time the car reached the second area.
            self._contact_state = self.machine.state
            self.contact.reset(now)
        if wall_contact is None:
            wall_contact = False
            if self.machine.state in _CONTACT_STATES and self.encoder_source is not None:
                sample = self.encoder_source()
                wall_contact = self.contact.update(
                    now,
                    encoder_delta=float(sample.get("encoder_delta", 0.0)),
                )
            elif self.machine.state in _CONTACT_STATES:
                # NO QUERY HERE -- deliberately, and do not put one back.
                #
                # This branch used to write SPD and ENC back to back, and the
                # firmware answers only the FIRST command of a burst (handoff
                # section 6.1, measured).  So ENC was the one it dropped, on
                # exactly the three legs whose arrival test reads the odometer.
                # Measured 2026-09-15 on JUNCTION_3_TO_PICKUP: SPD produced 25
                # distinct values across 377 ticks while the raw encoder tuple
                # never changed once -- (-29076, 24325, -18143, 35276) the whole
                # leg -- so travel_cm sat at 0.0 and _reached_gate(0.0, 90.0)
                # could never fire.  The stall detector died with it, because its
                # arm condition needs abs(delta) > 1.0 and the frozen delta was
                # 0.25.  Both arrival criteria, one dropped reply.
                #
                # A command cannot be issued from here at all: `issued` is not
                # known yet, so any write now would land on the same tick as the
                # V dispatched below.  The single alternating query after the
                # dispatch is the only place that can see the whole tick.
                wall_contact = self.contact.update(
                    now,
                    encoder_delta=self._encoder_delta_forward,
                    commanded=self._last_intent_kind in _MOTION_INTENTS,
                )
        observed_state = self.machine.state
        observed_task = vision_task_for_state(observed_state)
        visual_input = None
        visual_diagnostics = {}
        vision_observe_args = {
            "now": now,
            "absolute_lateral_cm": absolute_lateral_cm,
            "lateral_cm": lateral_cm,
            "wall_contact": wall_contact,
            "stopped": self._last_intent_kind in {"stop", "wait"},
            "sensor_mask": mask,
        }
        action_in_progress = (
            (self._pickup_action_triggered and self._pickup_action_state is observed_state)
            or observed_state is RouteState.BUILD_ACTION
        )
        if self.vision_runtime is not None:
            observe = (
                self.vision_runtime.suspend
                if action_in_progress and hasattr(self.vision_runtime, "suspend")
                else self.vision_runtime.observe
            )
            observed = observe(state=observed_state, **vision_observe_args)
            visual_input, visual_diagnostics = observed
        action_result = None
        pickup_actions = {
            RouteState.PICKUP_VISION_ONLY: self.purple_action,
            RouteState.PICKUP_2_VISION_ONLY: self.orange_action,
            RouteState.BUILD_ACTION: self.build_action,
        }
        action_executor = pickup_actions.get(self.machine.state)
        if action_executor is None:
            self._pickup_action_state = None
            self._pickup_action_triggered = False
            self._pickup_reset_phase = False
            self._pending_pickup_action = None
        else:
            if self._pickup_action_state is not self.machine.state:
                action_executor.reset()
                self._pickup_action_state = self.machine.state
                self._pickup_reset_phase = False
                self._pending_pickup_action = None
                self._pickup_action_triggered = False
                if self.machine.state is RouteState.BUILD_ACTION and hasattr(action_executor, "set_action"):
                    plan = self.machine.loop_context.build_plan
                    target_action = plan[0] if plan else "RESET"
                    action_executor.set_action("RESET")
                    self._pending_pickup_action = target_action
                    self._pickup_reset_phase = True
                    self._pickup_action_triggered = True
            if (visual_input is not None
                    and visual_input.pickup_kind == "pickup_ready"
                    and not self._pickup_action_triggered):
                if self.machine.state is RouteState.PICKUP_VISION_ONLY:
                    self._pending_pickup_action = "PICK_PURPLE"
                elif self.machine.state is RouteState.PICKUP_2_VISION_ONLY:
                    slot = self.machine.loop_context.next_orange_slot()
                    self._pending_orange_slot = slot
                    self._pending_pickup_action = {
                        "right": "PICK_ORANGE_RIGHT",
                        "suction": "PICK_ORANGE_SUCTION",
                        "left": "PICK_ORANGE_LEFT",
                    }.get(slot, "RESET")
                    # This tick is the "orange recognised -> stop three seconds"
                    # decision the operator sees as the car stopping.  Take the
                    # photo of the frame that decision was made on (no-op unless
                    # --capture-orange was passed).
                    self._capture_orange_pickup_photo(visual_diagnostics)
                if (self.machine.state in {
                        RouteState.PICKUP_VISION_ONLY,
                        RouteState.PICKUP_2_VISION_ONLY,
                    } and self._pending_pickup_action is not None
                        and hasattr(action_executor, "set_action")):
                    action_executor.set_action("RESET")
                    self._pickup_reset_phase = True
                self._pickup_action_triggered = True
            if (self._pickup_action_triggered and visual_input is not None
                    and not visual_input.action_done):
                action_result = action_executor.step(
                    now=now,
                    stop_acknowledged=self._last_intent_kind in {"stop", "wait"},
                )
                visual_diagnostics["pickup_action"] = dict(action_result.metadata)
                reset_finished = (
                    action_result.done
                    and self._pickup_reset_phase
                    and self._pending_pickup_action is not None
                )
                if reset_finished and hasattr(action_executor, "set_action"):
                    if self.machine.state is RouteState.BUILD_ACTION:
                        plan = self.machine.loop_context.build_plan
                        if plan and plan[0] == self._pending_pickup_action:
                            self.machine.loop_context.build_plan = plan[1:]
                    action_executor.set_action(self._pending_pickup_action)
                    self._pickup_reset_phase = False
                    action_result = None
                if (action_result is not None and action_result.done
                        and self.machine.state is RouteState.BUILD_ACTION
                        and hasattr(action_executor, "set_action")):
                    remaining = self.machine.loop_context.build_plan
                    completed = self._pending_pickup_action
                    if completed:
                        apply_build_action(self.machine.loop_context, completed)
                    if remaining:
                        self.machine.loop_context.build_plan = remaining[1:]
                        action_executor.set_action("RESET")
                        self._pending_pickup_action = remaining[0]
                        self._pickup_reset_phase = True
                        action_result = None
                visual_input = replace(
                    visual_input,
                    pickup_kind=(
                        "fault" if action_result is not None and action_result.fault
                        else visual_input.pickup_kind
                    ),
                    action_done=(
                        False if reset_finished
                        else action_result is not None and action_result.done
                    ),
                    build_action_done=(
                        action_result is not None and action_result.done
                        if self.machine.state is RouteState.BUILD_ACTION
                        else visual_input.build_action_done
                    ),
                )
                if (action_result is not None and action_result.done
                        and self.machine.state is RouteState.PICKUP_2_VISION_ONLY):
                    # Let the state machine consume this completion once.  The
                    # next tick starts a clean visual search for the next
                    # ordered orange block.
                    self._pickup_action_triggered = False
                    self._pending_orange_slot = None
                    self.orange_action.reset()
                    if hasattr(self.vision_runtime, "restart_pickup_search"):
                        self.vision_runtime.restart_pickup_search(
                            absolute_lateral_cm=absolute_lateral_cm
                        )
        tag2_stable = bool(visual_diagnostics.get("tag2_stable", self._tag2_result.stable))
        intent = self.machine.step(now, sensor_mask=mask, line_error=line_error,
                                   d_done=self._d_done, wall_contact=wall_contact,
                                   travel_cm=travel_cm, lateral_cm=lateral_cm,
                                   absolute_lateral_cm=absolute_lateral_cm,
                                   tag_stable=tag2_stable, vision=visual_input)
        current_task = vision_task_for_state(self.machine.state)
        if (self.vision_runtime is not None
                and current_task is not VisionTask.NONE
                and current_task is not observed_task):
            visual_input, visual_diagnostics = self.vision_runtime.observe(
                state=self.machine.state,
                **vision_observe_args,
            )
            intent = RouteIntent("stop", self.machine.state)
        issued = None
        output = None
        action_owns_chassis = bool(action_result and action_result.chassis_active)
        if action_result is not None and action_result.chassis_velocity is not None:
            vx, vy, wz = action_result.chassis_velocity
            self.chassis.set_velocity(vx, vy, wz)
            self._last_v_at = now
            self._last_stop_at = -float("inf")
            issued = f"ACTION V {vx} {vy} {wz}"
        elif action_result is not None and action_result.chassis_stop:
            self._last_stop_at = -float("inf")
            issued = self._command_stop(now) or issued
        elif action_owns_chassis:
            # The velocity command remains active until its recorded deadline.
            # A normal state-machine STOP here would cancel the pulse early.
            pass
        elif self.stop_at is not None and self.machine.state is self.stop_at:
            # --until reached.  Dispatch NOTHING: the target state's first intent
            # belongs to the leg we are deliberately not running, and for a
            # D-based target issuing it would start that move on the way out --
            # "--until JUNCTION_2_TURN_LEFT" would begin the left turn.  The
            # stopped path below re-sends STOP every 200 ms.
            self._reached_target = True
            issued = self._command_stop(now) or issued
        elif intent.kind == "d":
            key = (self.machine.state, intent.forward_cm, intent.right_cm, intent.rotate_deg, intent.speed)
            if key != self._issued_d:
                if self._d_pending_key != key:
                    # FLUSH STOP BEFORE THE D -- operator, 2026-09-22:
                    # "你完全可以丢线后停止前进 然后执行右旋D 90啊".
                    #
                    # Every turn is entered from a leg that streams V commands
                    # (line following, creep, strafe), and the chassis "answers
                    # only the FIRST command of a burst" (see _command_stop and
                    # section 6.1).  A D arriving inside that stream is dropped,
                    # and a dropped D is indistinguishable from a car that will
                    # not move: the state then waits out turn_timeout_s and holds.
                    #
                    # Measured 2026-09-22: JUNCTION_2_TURN_LEFT sat 196 and 214
                    # ticks on two runs, JUNCTION_2_TURN_LEFT/PICKUP_2_TURN_RIGHT
                    # account for 11 of the 78 turns audited, and the route log
                    # ends with the RFCOMM maintainer rebuilding the node.  A
                    # dropped D is also what leaves the car running its PREVIOUS
                    # command, which is how "the turn is stuck" turns into "the
                    # car is still reversing".
                    #
                    # _command_stop self-rate-limits to one STOP per 200 ms, so
                    # this window costs at most one STOP frame and holds the
                    # chassis quiet long enough for the D to be the first command
                    # it sees.
                    self._d_pending_key = key
                    self._d_settle_at = now + D_SETTLE_S
                    issued = self._command_stop(now) or issued
                elif now >= self._d_settle_at:
                    self.chassis.run_distance(intent.forward_cm, intent.right_cm, intent.rotate_deg, intent.speed)
                    self._issued_d = key
                    self._d_pending_key = None
                    self._last_stop_at = -float("inf")
                    issued = f"D {intent.forward_cm} {intent.right_cm} {intent.rotate_deg} {intent.speed}"
                else:
                    # Still settling: keep the chassis quiet (STOP re-sends
                    # itself every 200 ms) rather than sending the D early.
                    issued = self._command_stop(now) or issued
            # A "re-send the D if the car has not turned" retry was TRIED HERE on
            # 2026-09-22 and REVERTED the same hour: it made turns spin forever.
            # A second D does not resume the move in flight -- it RESTARTS it, so
            # re-issuing every 0.5 s reset the rotation before it could ever
            # complete.  The operator saw it at the first turn on the route, J1.
            # Do NOT reintroduce this without first measuring, on the bench, what
            # the firmware does with a D that arrives mid-move.
        elif intent.kind == "v":
            dt = max(1e-3, now - self._last_tick)
            output = self.pid.update(line_error, dt, vx=intent.speed)
            if output.line_lost and self.machine.state in self._hold_course_states:
                # This leg's line detection stutters while the car is ON the
                # line, so a lost frame is noise rather than news.  Stopping for
                # it turns a 3.2 m straight into stop-start; hold the heading
                # instead.  Straight vx only -- there is no error to steer by,
                # and inventing one is how a stutter becomes a swerve.
                if now - self._last_v_at >= 0.25:
                    self.chassis.set_velocity(intent.speed, 0, 0)
                    self._last_v_at = now
                    self._last_stop_at = -float("inf")
                    issued = f"V {intent.speed} 0 0"
            elif output.line_lost:
                issued = self._command_stop(now) or issued
            elif now - self._last_v_at >= 0.25:
                self.chassis.set_velocity(output.vx, output.vy, output.wz)
                self._last_v_at = now
                self._last_stop_at = -float("inf")
                issued = f"V {output.vx} {output.vy} {output.wz}"
        elif intent.kind == "strafe":
            # Landmark-terminated lateral strafe (J1 -> tag 2).  Not the PID path,
            # for the same reason as the creep below: the bar is off the line for
            # the whole strafe, so line_error is meaningless and a lateral
            # correction term would fight the very motion it is steering.
            #
            # Deliberately V and not D.  D is self-terminating and open loop, and
            # whether STOP interrupts a D in flight is unverified -- a strafe that
            # ends on a landmark has to stop on the tick the tag is seen.  V is a
            # held velocity, STOP is measured to interrupt it, and the re-send
            # below means one lost frame cannot leave the car strafing forever.
            #
            # vy only, never wz: yaw is exactly what accumulates over a long
            # lateral move, and the encoders cannot see the mecanum slip that
            # causes it.
            self.pid.reset()
            if now - self._last_v_at >= 0.25:
                self.chassis.set_velocity(0, intent.speed, 0)
                self._last_v_at = now
                self._last_stop_at = -float("inf")
                issued = f"V 0 {intent.speed} 0"
        elif intent.kind == "creep":
            # Slow reverse creep back onto the junction.  Deliberately not the
            # PID path: the bar is off the line for most of the creep, so
            # line_error is None and the PID path would issue STOP instead of
            # moving at all.  Straight vx only -- a lateral term would fight the
            # alignment this is trying to reach.
            self.pid.reset()
            if now - self._last_v_at >= 0.25:
                self.chassis.set_velocity(intent.speed, 0, 0)
                self._last_v_at = now
                self._last_stop_at = -float("inf")
                issued = f"V {intent.speed} 0 0"
        else:
            self.pid.reset()
            if intent.kind in {"wait", "stop"}:
                issued = self._command_stop(now) or issued
        # Ask the chassis how it is actually moving -- but only on ticks where
        # nothing else was sent.  Measured on the chassis 2026-09-14: the
        # firmware answers only the FIRST command of a burst ("SPD\r\nENC\r\n"
        # returns SPD and swallows ENC, and "ENC\r\nSPD\r\n" does the reverse),
        # so a query issued alongside a V silently loses one of the two.
        # Dispatching first and querying only when nothing was issued keeps every
        # command on its own tick.
        #
        # Stopped states are included on purpose: after the runaway of the same
        # day -- a STOP that did not stop the car for another 2.9 s -- "did it
        # actually stop?" has to be answerable from the hardware.  The rate has to
        # be high enough for the odometer to resolve a few centimetres, because
        # that is what the J1 trigger uses.
        #
        # The `d` intents are excluded from that FAST query -- a query arriving
        # mid-move could disturb a self-terminating move -- but they are no longer
        # left silent.  A D-wait is the one place the route stops talking for
        # seconds at a time, and the JDY-31 tears down a silent SPP session after
        # ~13-20 s, which is fatal to whoever holds the tty; two runs died that
        # way (see config.chassis_keepalive_s).  They now get the cheapest
        # read-only command instead -- SPD, which commands no motion -- on the
        # same 5 s interval the console uses to hold this link open.
        if issued is None and not action_owns_chassis:
            fast = self._last_intent_kind != "d"
            interval = 0.15 if fast else self.config.chassis_keepalive_s
            if now - self._last_motion_query_at >= interval:
                if not fast:
                    # D-wait keepalive.  SPD only, never ENC: the point is traffic
                    # that cannot move the car, and ENC would feed a column no D
                    # state reads.
                    name = "request_speed"
                elif self.machine.state in _CONTACT_STATES:
                    # ENC only while a wall approach is running.  Arrival on these
                    # three legs is the odometer gate, so the encoder is the only
                    # reply worth spending the link on -- alternating with SPD
                    # would halve the encoder rate to feed a column nothing reads
                    # any more (the stall detector stopped using OUT on
                    # 2026-09-15, section 7.1.8).  Accepted cost: `actual_speed`
                    # in the telemetry freezes for the length of these legs, while
                    # `travel_cm` -- the number the gate actually uses -- is
                    # sampled twice as often.
                    name = "request_encoder"
                else:
                    self._query_encoder_next = not self._query_encoder_next
                    name = "request_encoder" if self._query_encoder_next else "request_speed"
                request = getattr(self.chassis, name, None)
                if request:
                    request()
                self._last_motion_query_at = now
        self._last_tick = now
        self._last_intent_kind = "action_velocity" if action_owns_chassis else intent.kind
        if self.telemetry is not None:
            tag2 = self._tag2_result
            tag2_payload = {
                "stable": tag2.stable,
                "id": None if tag2.observation is None else tag2.observation.id,
                "center_px": None if tag2.observation is None else list(tag2.observation.center_px),
                "pose_camera": None if tag2.observation is None or tag2.observation.pose_camera is None
                else tag2.observation.pose_camera.to_dict(),
                "consecutive_frames": tag2.consecutive_frames,
                "missed_frames": tag2.missed_frames,
            }
            record = {
                "t": round(now, 4),
                "state": self.machine.state.value,
                "intent": intent.kind,
                "mask": mask,
                "line_error": line_error,
                "actual_speed": round(self._actual_speed, 3),
                "encoder_delta": round(self._encoder_delta, 3),
                "encoder_delta_forward": round(self._encoder_delta_forward, 3),
                "wall_contact": wall_contact,
                "encoder": self._last_encoder_raw,
                "travel_cm": None if travel_cm is None else round(travel_cm, 2),
                "lateral_cm": None if lateral_cm is None else round(lateral_cm, 2),
                "vy": output.vy if output is not None else None,
                "wz": output.wz if output is not None else None,
                "issued": issued,
                "tag2": tag2_payload,
            }
            record.update(visual_diagnostics)
            record.pop("tag2_stable", None)
            self.telemetry(record)
        return intent

    def run(self, *, timeout_s: float = 420.0) -> RouteState:
        started = self.clock()
        try:
            while self.machine.state not in {RouteState.FINISHED, RouteState.FAULT}:
                now = self.clock()
                if now - started > timeout_s:
                    raise TimeoutError(f"no finish within {timeout_s:.0f}s")
                self.tick(now)
                if self._reached_target:
                    return self.machine.state
                self.sleeper(self.config.poll_period_s)
            return self.machine.state
        except TimeoutError as exc:
            # TimeoutError is an OSError subclass; without this branch a plain
            # route overrun was reported as a chassis serial failure.
            self.machine.state = RouteState.FAULT
            raise RuntimeError(f"route v2 timed out: {exc}") from exc
        except OSError as exc:
            self.machine.state = RouteState.FAULT
            raise RuntimeError(f"chassis I/O failure: {exc}") from exc
        finally:
            self._safe_stop()
            if self.vision_runtime is not None:
                try:
                    self.vision_runtime.close()
                except Exception:
                    pass
            close = getattr(self.line_source, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass
            if self.camera is not None:
                try:
                    self.camera.release()
                except Exception:
                    pass

    def _command_stop(self, now: float) -> str | None:
        """Send STOP, and keep sending it for as long as the route stays stopped.

        Measured 2026-09-14: a STOP issued once -- at the moment J1 was detected
        -- did not stop the car.  It kept moving for another 2.9 s, swept the
        line right off the probe bar, and came to rest well past the junction,
        with the state machine already parked in JUNCTION_1_TO_JUNCTION_2.  V is
        re-sent every 250 ms, so a single lost STOP frame is the whole
        difference between a controlled stop and a runaway.  This chassis
        documents no comms-timeout stop of its own, which leaves repeating the
        command as the only protection available.  STOP is idempotent, so
        repeating it costs nothing.  Returns "STOP" on the ticks it was sent.
        """
        if now - self._last_stop_at < 0.2:
            return None
        self.chassis.stop()
        self._last_stop_at = now
        # Keep the D-dedup key pinned: a stale D must not be re-issued because
        # the route stopped in between.
        self._issued_d = ("STOP",)
        return "STOP"

    def _safe_stop(self) -> None:
        """STOP on the way out, repeatedly -- not once.

        One STOP does not stop this chassis (see _command_stop), and the
        controlled halt on the --until path returned the instant it had sent
        exactly that one frame.  The process then exited and nothing re-sent it.
        Field run 2026-09-15 ended with the car grinding into the pickup wall
        for several seconds *after* the arrival had already been declared, and
        the operator reached for the emergency stop before the car stopped
        itself.  The arrival detection was working; what failed was what came
        after it.

        This is the finally block, so repeating it here covers every exit --
        including a crash, which is the case the single frame was never going
        to survive.  The wait goes through the route's own sleeper, so a test
        with an injected no-op sleeper pays nothing.
        """
        for _ in range(_STOP_SETTLE_FRAMES):
            try:
                self.chassis.stop()
            except Exception:
                # Worth another go rather than giving up: the link may come
                # back, and this is the last thing that will ever run.
                pass
            self.sleeper(_STOP_SETTLE_INTERVAL_S)


# Legs that end by driving into a wall.  The two pickup trips and the
# purple-success build branch share the same contact detector.  The simulator
# ends them on distance rather than on the wall clock, because the wall clock is
# cumulative over the whole run: a time-based test would fire the moment the
# second trip started.
#
# JUNCTION_PICKUP_3_TO_AREA was removed from this set on 2026-09-24, when its
# arrival became a distance gate.  The build-area leg skids against its wall
# rather than stalling on it, so the contact test never fired there: measured on
# run 20260924_192550, wall_contact was false on all 3188 ticks of the run while
# the leg ground 1311 ticks and 2110 cm of odometer into the wall.  See the
# state's own comment in route_v2/state_machine.py.
_AREA_APPROACH_STATES = frozenset({
    RouteState.JUNCTION_3_TO_PICKUP,
    RouteState.JUNCTION_PICKUP_2_TO_AREA,
})

_CONTACT_STATES = _AREA_APPROACH_STATES | frozenset({
    RouteState.PICKUP_VISION_ONLY,
    RouteState.PICKUP_2_VISION_ONLY,
})

# Intents that are actually driving the car.  `stop` and `wait` are deliberate
# halts, and the contact detector must never read a car the route stopped
# itself as a wall -- PICKUP_VISION_ONLY halts to confirm a target, and every
# approach leg halts the instant it arrives.  Note this is the command the
# runner dispatched, NOT the firmware's SPD echo; the old detector compared
# that echo and it is exactly what broke it (see EncoderContactDetector).
_MOTION_INTENTS = frozenset({"v", "creep", "strafe", "d"})


def run_simulation(config: RouteV2Config, states: tuple[RouteState, ...]) -> list[RouteState]:
    machine = RouteV2StateMachine(config)
    machine.state = states[0]
    visited: list[RouteState] = []
    now = 0.0
    d_done = False
    travelled = 0.0
    travel_state = machine.state
    loop_iterations = 0
    build_area_ticks = 0
    previous_state = machine.state
    while machine.state is not RouteState.FINISHED and now < 300:
        if machine.state not in visited:
            visited.append(machine.state)
        if machine.state is not travel_state:
            travel_state = machine.state
            travelled = 0.0
        is_line_state = machine.follows_line(machine.state)
        # Simulated sensor fixtures hold the on-line reading and pulse the
        # junction pattern long enough for the debounce to confirm it.  The
        # pattern is read from the config rather than hard-coded, so this stays
        # honest when the measured junction reading changes -- the old 0x00
        # fixture kept passing while the real detector could never fire.
        if machine.state is RouteState.JUNCTION_1_TO_JUNCTION_2:
            # The alignment phase is reversing onto the junction reading, so it
            # has to be given that reading.  Feeding it the on-line pattern
            # instead leaves it creeping for the whole align timeout and then
            # giving up, which makes a route that works on hardware report
            # STOPPED in the dry run.
            mask = config.junction_mask
        elif machine.state is RouteState.JUNCTION_1_STRAFE_TO_TAG_2:
            # The strafe runs across the all-black area right of J1.
            mask = 0x00
        elif machine.state in (RouteState.JUNCTION_2_SEEK_LINE,
                               RouteState.JUNCTION_3_SEEK_LINE,
                               RouteState.PICKUP_2_SEEK_LINE,
                               ):
            # Off the line after a turn: the car strafes until the line comes
            # back under the bar.
            mask = 0x81 if travelled >= 5.0 else 0x00
        elif machine.state in (RouteState.TAG_2_TURN_RIGHT,
                               RouteState.JUNCTION_2_TURN_LEFT,
                               RouteState.PICKUP_2_TURN_RIGHT,
                               RouteState.PICKUP_3_TURN_RIGHT,
                               ):
            # The mask is chosen from the state at the TOP of the loop, but a
            # turn hands over to a seek *inside* one step() call, so that first
            # seek tick sees whatever mask the turn's tick had -- and the default
            # is 0x81, which the seek reads as "line found", so the seek would be
            # skipped entirely.  A turn reads no mask at all, so feeding it the
            # off-line reading is both harmless and what the car is actually
            # sitting on after turning on the spot.
            mask = 0x00
        elif machine.state is RouteState.JUNCTION_2_LINE_FOLLOW:
            # J2 is an L-corner: the line simply ends.  The generic line-state
            # fixture below pulses the junction pattern, which would never
            # produce the sustained 0xFF this state ends on -- so the dry run
            # would report a route that cannot finish.
            mask = 0xFF if travelled >= config.junction_2_line_loss_gate_cm else 0x81
        elif machine.state is RouteState.PICKUP_1_RETURN:
            # Two phases behind one flag: reverse until the line is lost, then
            # drive forward until the all-black landmark.  travelled is signed
            # now, so this models the real narrative in order rather than
            # approximating it -- and the ORDER is the whole point, because phase
            # 1 only flips on 0xFF:
            #   on the line (0x81) -> reversing, still on it
            #   line lost (0xFF)   -> the flip happens here, mid-reverse
            #   landmark (0x00)    -> only by driving forward again, so it has to
            #                         sit at a travel value phase 2 can reach
            # Getting this wrong is not cosmetic: a fixture that hands out 0xFF at
            # travelled=0 flips the phase on tick one and the leg then creeps
            # forward to its own forward bound and STOPS, which is the dry run
            # reporting a route the car drives as broken.
            mask = (0x00 if travelled >= 10.0
                    else 0xFF if travelled <= -3.0
                    else 0x81)
        elif machine.state is RouteState.PURPLE_RETURN_TO_J3:
            # Same two-phase helper as PICKUP_1_RETURN, but the operator's new
            # branch accepts the first lit probe instead of requiring 0x00.
            mask = (0x81 if machine._return_phase_reversed and travelled >= -2.0
                    else 0xFF if travelled <= -3.0
                    else 0x81)
        # PICKUP_2_RETURN deliberately has no branch here.  It has been
        # sensor-blind since 2026-09-15 -- a fixed 30 cm reverse, then the 180 --
        # so no fixture can make it pass or fail and it takes the default below.
        # It used to have one ("0x81 if travelled < 5.0 else 0x00"), and that
        # fixture is exactly what let the real defect hide: the mask logic it was
        # modelling armed and fired 2.77 cm in on stable2 and never armed at all
        # on seek3.  If this state ever needs a mask again, read
        # _back_off_straight before writing one.
        elif is_line_state and int(now * 10) % 10 >= 7:
            mask = config.junction_mask
        else:
            mask = 0x81
        # Tag 2 stands in for the camera.  This is a WIRING STUB, not a geometry
        # model: the simulator is not claiming tag 2 becomes visible at this
        # distance.  The real distance is a field measurement, which is why
        # run_simulation's caller refuses to call a dry run that reaches the
        # strafe a success.
        tag2_sim_cm = config.tag2_search_min_cm + max(
            1.0, (config.tag2_search_max_cm - config.tag2_search_min_cm) / 2.0)
        tag_stable = (machine.state is RouteState.JUNCTION_1_STRAFE_TO_TAG_2
                      and travelled >= tag2_sim_cm)
        if machine.state is RouteState.PURPLE_PRESCAN:
            purple_present = loop_iterations == 0
        else:
            purple_present = True
        orange_exhausted = loop_iterations >= 2 and machine.state is RouteState.PICKUP_2_VISION_ONLY
        # The build area's blob, modelled as "in view for the first few ticks of the
        # visit, then passed".  That is what the state machine now acts on: it slides
        # right until the blob leaves the view, once per building the route's memory
        # counts, and it centres on the blob before a cap -- so a fixture that always
        # reports nothing (or always reports something) cannot get through the state
        # at all.  A centred blob (error 0.0) lets the cap branch commit immediately.
        if machine.state is RouteState.BUILD_AREA:
            build_area_ticks += 1
        else:
            build_area_ticks = 0
        build_blob_visible = machine.state is RouteState.BUILD_AREA and build_area_ticks <= 5
        simulated_vision = VisionRouteInput(
            tag3_stable=machine.state is RouteState.PICKUP_SEEK_LINE,
            tag4_stable=machine.state is RouteState.PICKUP_2_SEEK_LINE,
            purple_prescan_present=purple_present,
            pickup_kind="no_target" if orange_exhausted else "pickup_ready",
            action_done=not orange_exhausted,
            return_line_done=True,
            build_block_visible=build_blob_visible,
            build_center_error=0.0 if build_blob_visible else None,
            build_action_done=True,
        ) if config.vision is not None else None
        # travelled doubles for both odometers: it is re-baselined on every state
        # change, and no state reads both, so one scalar is enough here.
        contact_at_cm = 20.0
        intent = machine.step(now, sensor_mask=mask, d_done=d_done,
                              wall_contact=(machine.state in _AREA_APPROACH_STATES
                                            and travelled >= contact_at_cm),
                              travel_cm=travelled, lateral_cm=travelled,
                              absolute_lateral_cm=travelled,
                              tag_stable=tag_stable, vision=simulated_vision)
        if previous_state is not RouteState.BUILD_RETURN_REVERSE and machine.state is RouteState.BUILD_RETURN_REVERSE:
            loop_iterations += 1
            # The real route runs until both supplies are absent. The simulator
            # uses a bounded horizon so dry-run remains finite and deterministic.
        previous_state = machine.state
        if loop_iterations >= 2 and machine.state is RouteState.JUNCTION_2_TO_JUNCTION_3:
            visited.append(machine.state)
            break
        d_done = intent.kind == "d"
        if (machine.state not in {
                RouteState.FINISHED, RouteState.FAULT,
                RouteState.PICKUP_VISION_ONLY, RouteState.PICKUP_2_VISION_ONLY,
                RouteState.BUILD_ACTION,
                } and intent.kind == "stop"):
            # The route has come to rest short of the end -- with no measured
            # J1 -> J2 shift distance, for example.  That is a stopping point,
            # not a simulation bug, so report how far it got instead of
            # spinning here until the timeout.
            break
        step_s = max(config.poll_period_s, 0.1) if intent.kind == "wait" else config.poll_period_s
        if intent.kind in {"v", "creep", "strafe"}:
            # Nominal cm/s.  The point of the simulation is the state machine's
            # wiring, not a faithful vehicle model: 15 cm/s is the measured V
            # vx=35, and the strafe is slower because vy=20 measures ~10.8 cm/s.
            #
            # The odometer has to follow the DIRECTION, not just the distance.  A
            # "creep" with a negative speed is a reverse, and the second area's
            # return is now a fixed 30 cm reverse whose whole arrival test is
            # travel_cm <= -30 -- with a travel that only ever grew, that state
            # could not be simulated at all and the dry run reported STOPPED on a
            # route the car drives.
            reverse = intent.kind == "creep" and intent.speed < 0
            travelled += step_s * (10.8 if intent.kind == "strafe" else 15.0) \
                * (-1.0 if reverse else 1.0)
        now += step_s
    if machine.state is not RouteState.FINISHED and now >= 300:
        raise RuntimeError("simulation timed out")
    # Record where it came to rest.  The final transition happens inside a step
    # and the loop exits before the next pass can append it, so without this the
    # caller sees the state *before* the last transition -- a route that reached
    # FINISHED would be reported as stopping in PICKUP_ARRIVED.
    if machine.state not in visited:
        visited.append(machine.state)
    return visited


def main(argv=None, *, config_path: str | Path = "config/route_v2.yaml") -> int:
    parser = argparse.ArgumentParser(description="RoboGame route v2")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--until", help="stop with a controlled halt the moment the route "
                                        "reaches this state.  Unlike --timeout-s this ends on "
                                        "a state, not a stopwatch, so a field test of one leg "
                                        "cannot overshoot into the next one.  The target state "
                                        "issues no command of its own")
    parser.add_argument("--config", default=str(config_path))
    parser.add_argument("--runtime-config", default="config/runtime.yaml")
    parser.add_argument("--log-telemetry", default="logs/route_v2_telemetry.jsonl",
                        help="per-tick JSONL black box written during a hardware run")
    parser.add_argument("--no-telemetry", action="store_true",
                        help="disable the per-tick telemetry log")
    parser.add_argument("--capture-orange", action="store_true",
                        help="save the camera frame at each orange pickup decision -- the "
                             "tick where the detector reports the block as recognised and "
                             "the route stops for three seconds.  One raw JPEG plus one "
                             "overlay JPEG per pickup, and an index.jsonl line.  Off by "
                             "default: with the flag absent no recorder is built and the "
                             "route behaves exactly as before.  Writes happen on a "
                             "separate thread behind a bounded queue, so the control loop "
                             "never waits on the disk")
    parser.add_argument("--capture-orange-dir", default=None,
                        help="output directory for --capture-orange "
                             "(default: logs/orange-evidence/route_<timestamp>)")
    parser.add_argument("--timeout-s", type=float, default=420.0,
                        help="hard stop after this many seconds; --only selects a starting "
                             "state but does not stop at the end of that segment, so a short "
                             "timeout is how a single segment is validated safely")
    args = parser.parse_args(argv)
    if not (args.full or args.dry_run or args.only or args.start):
        parser.print_help()
        return 0
    def _stop_on_signal(_signum, _frame):
        # Default SIGTERM skips the cleanup in _run_hardware: the motors would be
        # left on their last command and the pigpio claim on the line sensor
        # would leak inside the daemon.  Raise so the finally blocks run.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop_on_signal)
    try:
        config = load_route_v2_config(
            args.config, require_visual_calibration=bool(args.full)
        )
        selected = _selected_states(config, args.start, args.end, args.only)
        # Resolve --until here rather than in _run_hardware: a typo must not open
        # the chassis port first.
        until_state = None if getattr(args, "until", None) is None else _state_by_name(args.until)
        if args.dry_run:
            visited = run_simulation(config, selected)
            if until_state is not None:
                if until_state in visited:
                    print(f"DRY_RUN REACHED {until_state.value}",
                          ",".join(state.value for state in visited[:visited.index(until_state) + 1]))
                    return 0
                print(f"DRY_RUN STOPPED {visited[-1].value if visited else selected[0].value}: "
                      f"never reached {until_state.value}")
                return 1
            reached = visited[-1] if visited else selected[0]
            if reached is RouteState.FINISHED:
                # Say plainly that the simulator supplied the tag.  Reporting a
                # bare FINISHED would be promising a route it cannot drive: the
                # tag-2 strafe and the J2 line-loss window are both placeholder
                # numbers until they are measured on the track.
                print("DRY_RUN FINISHED (tag 2 faked by the simulator; the J1 -> J2 "
                      "odometer window is still at its placeholder values)",
                      ",".join(state.value for state in visited))
                return 0
            if (RouteState.BUILD_RETURN_REVERSE in visited
                    and RouteState.JUNCTION_2_TO_JUNCTION_3 in visited):
                print("DRY_RUN LOOPED (bounded simulator horizon; tag 2 faked by the simulator; "
                      "real route continues until fault, manual stop, or timeout)",
                      ",".join(state.value for state in visited))
                return 0
            if reached in (RouteState.JUNCTION_1_STRAFE_TO_TAG_2,
                           RouteState.JUNCTION_2_SEEK_LINE,
                           RouteState.JUNCTION_3_SEEK_LINE,
                           RouteState.JUNCTION_2_LINE_FOLLOW):
                print(f"DRY_RUN STOPPED {reached.value}: the odometer window is still at "
                      f"its placeholder defaults and tag 2 is faked by the simulator, so "
                      f"the route cannot be trusted past J1 until the window is measured "
                      f"on the track.")
            else:
                print(f"DRY_RUN STOPPED {reached.value}")
            return 1
        return _run_hardware(config, args, selected, until_state)
    except KeyboardInterrupt:
        print("FAULT_SAFE: interrupted")
        return 2
    except (RouteSelectionError, ValueError, RuntimeError, OSError) as exc:
        print(f"FAULT_SAFE: {exc}")
        return 2


def _wait_for_chassis_device(path: str, *, timeout_s: float = 40.0) -> None:
    """Block until the RFCOMM node exists, then let the caller open it.

    The maintainer service drops and rebuilds the node every 13-35 s whenever
    the link is idle: JDY-31 hangs up after roughly 20 s of silence, and nothing
    talks to it between runs (seen in the service log on 2026-09-14, and the
    first hardware run of that session died with "could not open port
    /dev/robogame-chassis: No such file or directory" without ever sending a
    command).  Once the route is running it speaks every 250 ms and the link
    holds, so only the opening needs to wait.

    This must happen BEFORE the port lock is taken.  The maintainer only tests
    the lock at the top of its loop, so a lock held while the node is missing
    stops the one service that could rebuild it -- the deadlock that stranded
    the robot earlier the same day.
    """
    if os.name != "posix":
        # The chassis TTY and its maintainer service only exist on the Pi; on a
        # Windows development host this would block for the full timeout and
        # then fail a run that the tests deliberately drive with a fake chassis.
        return
    if os.path.exists(path):
        return
    print(f"waiting for {path} (the RFCOMM maintainer rebuilds it after the link idles out)")
    deadline = time.monotonic() + timeout_s
    while not os.path.exists(path):
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"{path} did not appear within {timeout_s:.0f}s; the RFCOMM maintainer is not "
                "rebuilding it -- check systemctl status robogame-chassis-rfcomm"
            )
        time.sleep(0.5)
    print(f"{path} is back")


def _prepare_route_arm(runtime, *, transport_factory=None):
    """Probe and enable the arm before any route movement can begin."""
    from rg_runtime.arm_tools import ArmSession
    from rg_runtime.devices import ArmDevice
    from rg_runtime.hardware_models import ArmMode
    from rg_runtime.transports import SerialTransport

    factory = transport_factory or SerialTransport
    transport = factory(runtime.arm_device, runtime.arm_baudrate, timeout_s=0.0)
    session = ArmSession(ArmDevice(transport), transport)
    timeout_s = runtime.arm_probe_timeout_ms / 1000.0
    try:
        session.safe_probe(timeout_s)
        if session.arm.state.calibrated is not True:
            raise RuntimeError("arm must report CAL=1 before route start")
        session.enable()
        session.wait_for_mode(ArmMode.READY, timeout_s)
        return session
    except Exception:
        transport.close()
        raise


def _close_route_arm(session, *, keep_suction: bool) -> None:
    if not keep_suction:
        try:
            session.stop()
        except Exception:
            pass
    try:
        session.transport.close()
    except Exception:
        pass


def _run_hardware(config: RouteV2Config, args, selected: tuple[RouteState, ...],
                  until_state: RouteState | None = None) -> int:
    """Open the existing runtime devices and run route v2 with fail-safe cleanup."""
    from control_hub.services.line_service import LineSensorService
    from rg_runtime.app_support import load_runtime_config
    from rg_runtime.chassis_lock import ChassisPortLock
    from rg_runtime.devices import ChassisDevice
    from rg_runtime.transports import SerialTransport

    runtime = load_runtime_config(args.runtime_config)
    # Load the exported action catalog before route movement.  Pickup windows
    # are embedded in the pickup packages; build packages intentionally have
    # no window and are gated by the build vision result in the state machine.
    # Wait for the node BEFORE locking: see _wait_for_chassis_device.
    _wait_for_chassis_device(runtime.chassis_device)
    # Refuse to run while the hub (or another route run) owns the chassis port:
    # two readers would steal each other's replies.
    chassis_lock = ChassisPortLock()
    chassis_lock.acquire()
    transport = SerialTransport(runtime.chassis_device, runtime.chassis_baudrate, timeout_s=0.0)
    chassis = ChassisDevice(transport)
    # Prime RFCOMM before any sensor/camera initialization can delay the first
    # chassis frame. This is a safe command and also establishes the TTY session.
    chassis.stop()
    line = LineSensorService(
        transport=runtime.line_transport,
        device=runtime.line_device,
        rx_gpio=runtime.line_rx_gpio,
        tx_gpio=runtime.line_tx_gpio,
        baudrate=runtime.line_baudrate,
        mode=runtime.line_frame_mode,
        active_level=runtime.line_active_level,
        reverse_order=runtime.line_reverse_order,
        enabled=runtime.line_enabled,
        request_command=runtime.line_request_command,
        startup_delay_s=runtime.line_startup_delay_s,
        request_retry_s=runtime.line_request_retry_s,
    )
    line.start()
    if not line.snapshot.connected:
        try:
            chassis.stop()
        except Exception:
            pass
        raise RuntimeError(f"line sensor unavailable: {line.snapshot.error or line.snapshot.state}")
    camera = None
    detector = None
    arm_session = None
    purple_action = None
    orange_action = None
    build_action = None
    final_state = None
    telemetry_sink = None
    telemetry = None
    # Bound before the try: the finally below closes it, and an exception on the
    # way to the camera (node missing, port busy) must not turn into a
    # NameError that hides the real failure.
    evidence = None
    if not getattr(args, "no_telemetry", False) and getattr(args, "log_telemetry", None):
        telemetry_path = Path(args.log_telemetry)
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        # Line buffered: a killed process must still leave the run readable.
        telemetry_sink = telemetry_path.open("w", encoding="utf-8", buffering=1)

        def telemetry(record: dict) -> None:
            telemetry_sink.write(json.dumps(record, ensure_ascii=False) + "\n")

    try:
        arm_session = _prepare_route_arm(runtime)
        catalog_root = Path(__file__).resolve().parent / "data" / "route_v2_actions"
        catalog = load_action_catalog(catalog_root)
        compiled = {
            role: compile_action(
                package,
                forward_speed_limit=80,
                expected_suction=tuple(
                    step.get("enabled") for step in package["action"]["steps"]
                    if step.get("kind") == "SUCTION"
                ),
            )
            for role, package in catalog.items()
        }
        purple_action = ActionCatalogExecutor(
            {"RESET": compiled["reset"], "PICK_PURPLE": compiled["purple_pickup"]},
            arm_session,
            action_ref_prefix="arm",
        )
        orange_action = ActionCatalogExecutor(
            {
                "RESET": compiled["reset"],
                "PICK_ORANGE_LEFT": compiled["orange_left"],
                "PICK_ORANGE_RIGHT": compiled["orange_right"],
                "PICK_ORANGE_SUCTION": compiled["orange_hold"],
            },
            arm_session,
            action_ref_prefix="arm",
        )
        build_action = ActionCatalogExecutor(
            {
                "RESET": compiled["reset"],
                "BUILD_BASE": compiled["build_base"],
                "BUILD_2": compiled["build_two"],
                "BUILD_3": compiled["build_three"],
                "PLACE_PURPLE": compiled["place_purple"],
                "PLACE_ORANGE": compiled["place_orange"],
                "TOP_SUCTION_ORANGE_PURPLE": compiled["top_suction_purple"],
                "TOP_RIGHT_ORANGE_PURPLE": compiled["top_right_purple"],
            },
            arm_session,
            action_ref_prefix="arm",
        )
        import cv2
        from rg_runtime.apriltag import AprilTagDetector
        from rg_runtime.config import load_camera_config
        camera_config_path = Path(args.runtime_config).with_name("camera_config.yaml")
        camera_config = _load_route_camera_config(
            camera_config_path,
            load_camera_config,
            required=config.vision is not None,
        )
        camera = cv2.VideoCapture(
            camera_config.camera if camera_config is not None else 0
        )
        if not camera.isOpened():
            raise RuntimeError("unable to open camera 0")
        if camera_config is not None:
            # Must happen before the first read, or the whole run measures a
            # stream the calibration does not describe.
            for warning in _configure_camera(
                camera,
                camera_config,
                cv2,
                require_calibrated_size=config.vision is not None,
            ):
                print(f"WARNING: {warning}", flush=True)
        detector = AprilTagDetector(camera_config)
        stop_at = until_state
        # --capture-orange: an off-loop JPEG recorder for the orange close
        # detector.  Built here (camera already open, before the route moves)
        # and closed in the finally below, so even a killed run leaves a
        # complete session.json.
        evidence = None
        if getattr(args, "capture_orange", False):
            evidence_dir = getattr(args, "capture_orange_dir", None) or (
                "logs/orange-evidence/route_" + time.strftime("%Y%m%d_%H%M%S")
            )
            evidence = OrangeEvidenceRecorder(evidence_dir)
            evidence.start()
            print(f"orange evidence -> {evidence.output_dir}", flush=True)
        runner = RouteRunner(config, chassis, line, camera=camera, tag_detector=detector,
                            initial_state=selected[0], telemetry=telemetry, stop_at=stop_at,
                            purple_action=purple_action,
                            orange_action=orange_action,
                            build_action=build_action,
                            evidence=evidence)
        final_state = runner.run(timeout_s=float(getattr(args, "timeout_s", 420.0)))
        if stop_at is not None and final_state is stop_at:
            # Reaching the requested state IS the success condition here.
            return 0
        return 0 if final_state is RouteState.FINISHED else 1
    finally:
        try:
            line.close()
        finally:
            if evidence is not None:
                evidence.close()
            if arm_session is not None:
                _close_route_arm(arm_session, keep_suction=False)
            if camera is not None:
                camera.release()
            if telemetry_sink is not None:
                telemetry_sink.close()
            try:
                transport.close()
            except Exception:
                pass
            chassis_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())

