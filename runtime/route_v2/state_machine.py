from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import RouteV2Config
from .line_control import JunctionDebouncer
from .loop_strategy import (
    CAP_ACTIONS,
    LoopContext,
    build_plan_for_inventory,
    choose_purple_slot,
    prepare_build_plan,
)


class RouteState(str, Enum):
    START_TO_JUNCTION_1 = "START_TO_JUNCTION_1"
    # Creep back onto J1 after the stop overshoots it.  Keeps its name: it is
    # still exactly that, and renaming it would break every existing --only and
    # --from/--to invocation for no functional gain.
    JUNCTION_1_TO_JUNCTION_2 = "JUNCTION_1_TO_JUNCTION_2"
    # Strafe right until AprilTag 2 is seen.  Its own state, not folded into the
    # creep above, because travel_cm is re-baselined whenever the state changes:
    # the creep travels backwards first, so an odometer window measured inside
    # the old state would be offset by the creep distance.
    JUNCTION_1_STRAFE_TO_TAG_2 = "JUNCTION_1_STRAFE_TO_TAG_2"
    # In-place turn between the strafe and the east leg.  Deliberately does not
    # start with JUNCTION: it has an explicit branch in step() that always
    # returns, so it never reaches the name-based speed selection below.
    TAG_2_TURN_RIGHT = "TAG_2_TURN_RIGHT"
    # After the right turn the car is sitting on the all-black area, not on a
    # line: measured 2026-09-15, the bar read 00000000 for the whole strafe and
    # the turn does not change that.  So the turn cannot hand straight over to
    # the line follower -- the car has to strafe LEFT until a line appears.
    JUNCTION_2_SEEK_LINE = "JUNCTION_2_SEEK_LINE"
    # East leg: line following to J2.  The JUNCTION prefix on THIS one is
    # load-bearing -- it is the only new state that falls through to the speed
    # selection at the end of step(), which keys on the state name, and a name
    # without the prefix would silently line-follow BACKWARDS.
    JUNCTION_2_LINE_FOLLOW = "JUNCTION_2_LINE_FOLLOW"
    # J2 is an L-corner, so the car comes to rest PAST the corner: the line-loss
    # debounce costs frames and the stop costs braking distance.  Turning in
    # place from there swings the left turn about a point beyond the corner and
    # puts the whole next leg off by that much.  So the car reverses back onto
    # the line first -- exactly the manoeuvre J1 already uses after its own
    # overshoot, which is why that logic was factored into _creep_back_onto()
    # rather than copied.
    JUNCTION_2_TURN_LEFT = "JUNCTION_2_TURN_LEFT"
    # The same seek, mirrored: the left turn leaves the car off the line again,
    # and this time the line is to its RIGHT.  Same masks, same bounds, opposite
    # direction -- see _seek_line().
    JUNCTION_3_SEEK_LINE = "JUNCTION_3_SEEK_LINE"
    JUNCTION_2_TO_JUNCTION_3 = "JUNCTION_2_TO_JUNCTION_3"
    # Approached from J2, J3 is an L: the line carries straight on and a branch
    # leaves to the LEFT (operator, 2026-09-15).  The route takes the left
    # branch, so the car turns in place and then hunts for the line -- the same
    # manoeuvre as after the J2 turn, which is why it reuses _seek_line() and
    # the same turn helper rather than growing its own copies.
    JUNCTION_3_TURN_LEFT = "JUNCTION_3_TURN_LEFT"
    # The hunt after that turn.  Distinct from JUNCTION_3_SEEK_LINE, which is
    # the hunt after the turn at J2 -- that name is taken and predates this one.
    PICKUP_SEEK_LINE = "PICKUP_SEEK_LINE"
    PICKUP_TAG_LINE_TAKEOVER = "PICKUP_TAG_LINE_TAKEOVER"
    PURPLE_PRESCAN = "PURPLE_PRESCAN"
    # The last leg of the run: line following until the car hits the wall.
    #
    # Hitting it is not a failure, it IS the arrival signal, and it is what will
    # call the vision and arm actions.  The car has a bumper and is undamaged by
    # the impact at any speed (operator, 2026-09-15), but the approach is slow
    # anyway: the detector works by watching the wheels stop while the command
    # is still forward, and a fast approach both reads worse and bounces, which
    # would re-trigger it.
    #
    # The JUNCTION prefix is load-bearing -- without it this state would
    # line-follow BACKWARDS.  See the note on JUNCTION_2_LINE_FOLLOW above.
    JUNCTION_3_TO_PICKUP = "JUNCTION_3_TO_PICKUP"
    # Arrived at the first pickup point.  The arm and the vision are NOT wired up
    # yet: the plan is to finish the line-following program first and then drive
    # the actions from the runtime console, so this holds briefly, marks the
    # arrival, and then reverses back to J3 for the next trip.  This is the hook
    # point for the action package.
    PICKUP_ARRIVED = "PICKUP_ARRIVED"
    PICKUP_VISION_ONLY = "PICKUP_VISION_ONLY"
    PICKUP_RETURN_TO_LINE = "PICKUP_RETURN_TO_LINE"
    # After a SUCCESSFUL purple grab, before the landmark hunt.
    #
    # The grab leaves the car off the line: the alignment strafes it sideways to
    # centre the block, so PICKUP_VISION_ONLY ends with the probe bar over bare
    # floor.  Run 20260918_104541 finished the grab at lateral -34.18 cm with
    # mask 0xFF, and PURPLE_RETURN_TO_J3 was entered in that state.
    #
    # _return_to_j3_by_landmark is a two-phase manoeuvre written for a car
    # PARKED ON THE LINE (it reverses to lose that line, then drives forward to
    # pick up J3's landmark).  Handed a car that is already off the line, its
    # first phase flips on tick one without reversing at all, and the second
    # then creeps forward into pickup_1_return_forward_cm and holds: that run
    # drove 63.55 cm the wrong way and sat for 18.5 s sending STOP, until the
    # operator pushed the car back onto the line.
    #
    # So the line has to be re-acquired FIRST.  This state is that step, and it
    # is deliberately the same PickupReturnController the no-target bypass uses
    # (PICKUP_RETURN_TO_LINE): strafe back to the lateral baseline saved when
    # the vision task was selected, then require the eight-way probe to confirm
    # the line before handing over.  The controller's own bounded seek covers a
    # baseline that is slightly off.
    PURPLE_RETURN_TO_LINE = "PURPLE_RETURN_TO_LINE"
    PURPLE_RETURN_TO_J3 = "PURPLE_RETURN_TO_J3"
    # --- The three trips out of J3 (operator, 2026-09-15) --------------------
    #
    # J3 is where the run stops being a single line.  Three legs leave it,
    # heading for a pickup/build area; the first two end on a wall contact and
    # return to J3 to turn again, and the third ends on the odometer (it is a
    # controlled stop short of its wall, not a collision -- see the state).
    # The arm is attached at the *_ARRIVED states.
    #
    #   trip 1  left 90   seek RIGHT  ->  PICKUP_ARRIVED     (area 2)
    #   trip 2  right 90  seek LEFT   ->  PICKUP_2_ARRIVED   (area 1)
    #   trip 3  right 180 no seek, already on the line, then
    #           line-follow pickup_3_arrived_distance_cm     (build area)
    #
    # The turn after each return is measured from the heading the car is still
    # holding -- reversing does not change it.
    PICKUP_1_RETURN = "PICKUP_1_RETURN"
    PICKUP_2_TURN_RIGHT = "PICKUP_2_TURN_RIGHT"
    PICKUP_2_SEEK_LINE = "PICKUP_2_SEEK_LINE"
    PICKUP_2_TAG_LINE_TAKEOVER = "PICKUP_2_TAG_LINE_TAKEOVER"
    # JUNCTION prefix is load-bearing on both of these: they are line-following
    # states, and the established names are retained for CLI compatibility.
    JUNCTION_PICKUP_2_TO_AREA = "JUNCTION_PICKUP_2_TO_AREA"
    DIRECT_ORANGE_D330 = "DIRECT_ORANGE_D330"
    PICKUP_2_ARRIVED = "PICKUP_2_ARRIVED"
    PICKUP_2_VISION_ONLY = "PICKUP_2_VISION_ONLY"
    # Orange grab -> forward re-seat, entered once per completed PICK_ORANGE_n.
    # Operator, 2026-09-23: "执行完动作后 没有向前顶撞墙回位 ... 你现在在自动化
    PICKUP_2_RETURN_TO_LINE = "PICKUP_2_RETURN_TO_LINE"
    PICKUP_2_RETURN = "PICKUP_2_RETURN"
    PICKUP_3_TURN_RIGHT = "PICKUP_3_TURN_RIGHT"
    JUNCTION_PICKUP_3_TO_AREA = "JUNCTION_PICKUP_3_TO_AREA"
    BUILD_AREA = "BUILD_AREA"
    BUILD_ACTION = "BUILD_ACTION"
    # After a build action the car is off the line -- it slid right to get past the
    # finished buildings, and the cap alignment moves it too.  So it hunts the line
    # back BEFORE the reverse-and-turn: reversing 30 cm and spinning 180 degrees
    # from a pose that is a building's width off the line points the whole next leg
    # the wrong way.  Operator, 2026-09-24: 「避开建筑 执行完动作后 应该左移找线
    # 然后才执行后退等一系列动作」.
    BUILD_BACK_TO_LINE = "BUILD_BACK_TO_LINE"
    BUILD_RETURN_REVERSE = "BUILD_RETURN_REVERSE"
    BUILD_TURN_LEFT = "BUILD_TURN_LEFT"
    FINISHED = "FINISHED"
    FAULT = "FAULT"


def _reached_gate(travel_cm: float | None, gate_cm: float) -> bool:
    """Has the leg's own odometer reached a gate the operator measured by tape?

    The distances from J3 to each pickup area are known (1.1 m and 90 cm), so
    arrival is dead reckoning against a number rather than an inference from
    the motors.  The wall contact stays as the backstop for a tape measurement
    that turns out to be wrong.

    `travel_cm` is None until the first ENC reply lands; not-yet-known must not
    read as arrived.
    """
    return travel_cm is not None and travel_cm >= gate_cm


def _black_probes(sensor_mask: int) -> int:
    """How many of the eight probes are reporting black (bit clear).

    0 means line loss -- no black anywhere under the bar.  8 is the all-black
    reading that covers the wide junctions, which is exactly why the relaxed
    seek still refuses it: a car can enter a post-turn seek already sitting on
    one, and would then call the first tick an arrival.
    """
    return 8 - bin(sensor_mask & 0xFF).count("1")


@dataclass(frozen=True)
class RouteIntent:
    kind: str
    state: RouteState
    forward_cm: int = 0
    right_cm: int = 0
    rotate_deg: int = 0
    speed: int = 0
    wait_s: float = 0.0


@dataclass(frozen=True)
class VisionRouteInput:
    tag3_stable: bool = False
    tag4_stable: bool = False
    purple_prescan_present: bool | None = None
    purple_slots: tuple[bool, bool, bool] | None = None
    pickup_kind: str | None = None
    pickup_speed: int = 0
    action_done: bool = False
    return_line_done: bool = False
    camera_fault: bool = False
    vision_pending: bool = False
    vision_hold: bool = False
    build_block_visible: bool | None = None
    build_action_done: bool = False
    # Where the build-area blob's centre sits relative to the build capture
    # window's centre, normalised to the frame width.  Negative = the blob is left
    # of centre, and the car then strafes LEFT to bring it in -- the same sign
    # convention the pickup's alignment uses.  None = no blob in view.
    #
    # The route only needs to know a blob EXISTS to place; to CAP it has to be
    # centred on it, which needs this.  Operator, 2026-09-24: 「你应该用视觉找到
    # 方块正中 然后执行动作封顶」.
    build_center_error: float | None = None


class RouteV2StateMachine:
    _LINE_STATES = frozenset({
        RouteState.START_TO_JUNCTION_1, RouteState.JUNCTION_1_TO_JUNCTION_2,
        RouteState.JUNCTION_2_LINE_FOLLOW,
        RouteState.JUNCTION_2_TO_JUNCTION_3,
        RouteState.JUNCTION_3_TO_PICKUP,
        RouteState.JUNCTION_PICKUP_2_TO_AREA,
        RouteState.JUNCTION_PICKUP_3_TO_AREA,
    })

    # States that end when the sensor shows the junction pattern.  EMPTY, and
    # that is the finished state of a deliberate retreat: every junction on this
    # route used to be watched here and every one has now been measured and
    # moved off it, for the same reason each time.
    #
    #   10000000 also appears whenever the car sits one probe off the line with
    #   a little yaw, so it cannot tell a branch from a straight.
    #
    #   J1  -> odometer (start_to_junction_1_cm)
    #   J2  -> sustained line loss; it is a T, so the line really does stop
    #          (measured 2026-09-15: 00000000 for one frame -- the crossbar --
    #          then 11111111; see JUNCTION_2_LINE_FOLLOW)
    #   J3  -> odometer (junction_3_distance_cm); the line carries straight on
    #
    # The map and the debouncer behind it are kept rather than deleted because
    # the mechanism is tested and the config keys are still live: junction_mask
    # is what the J1 creep aligns to, and junction_debounce_frames is what
    # _line_lost_and_travelled() uses to confirm a line loss.  A junction that
    # ever needs watching again plugs in here.
    _JUNCTION_WATCH_STATES: dict[RouteState, RouteState] = {}

    def __init__(self, config: RouteV2Config):
        self.config = config
        self.state = RouteState.START_TO_JUNCTION_1
        self._state_started = 0.0
        self._wait_started: float | None = None
        self._action_pending = False
        self.loop_context = LoopContext()
        self.purple_target_slot: int | None = None
        # The J3 return leg: reverse off the line, STOP, turn, then hunt sideways.
        self._return_turned = False
        self._return_turn_issued = False
        self._return_phase_started = 0.0
        # Sideways line hunt (see _strafe_line_seek).  dir -1 strafes right,
        # +1 strafes left; it starts right because that is the operator's rule.
        self._strafe_seek_origin_cm: float | None = None
        self._strafe_seek_dir = -1
        self._strafe_seek_settled = False
        self._strafe_seek_started = 0.0
        # J1 alignment: whether the car has been brought back onto the junction
        # reading, how many consecutive frames it has held that reading, and
        # whether the creep gave up.
        self._aligned = False
        self._align_count = 0
        self._align_failed = False
        # Consecutive 11111111 frames.  J1 is where the line ends, so this is
        # the arrival signal, gated by the odometer.  J2's east leg reuses it.
        self._line_lost_frames = 0
        # The tag-2 strafe ran to its odometer ceiling without ever seeing the
        # tag.  Latched so a caller (and the tests) can tell "held on purpose"
        # from "still moving".
        self._tag2_search_failed = False
        # The same, for the strafe that hunts for the line after the right turn.
        self._seek_line_failed = False
        # The post-turn line seek is a two-phase sweep: out in the configured
        # direction, then back the other way if that found nothing.  True once
        # the first sweep has run out its bound and the return sweep is on.
        self._seek_reversed = False
        # The relaxed seek acceptance has seen line loss during THIS seek, so a
        # later reading with black under the bar counts as finding the line.  A
        # flag rather than a bare predicate because the relaxation is scoped to
        # "black came back after it went away" -- a seek that starts with black
        # under the bar must not end on its first tick.
        self._seek_line_saw_loss = False
        # Consecutive frames of the relaxed test's "black enough to be the line"
        # reading.  Reset by any frame that is not one.
        self._seek_black_frames = 0
        # The J1 alignment's reverse phase is over without finding the mark, so
        # the forward phase is on.  See _creep_back_onto.
        self._creep_forward = False
        # travel_cm and the clock when the forward phase began, so both of its
        # bounds are measured from the turn-around rather than from the state's
        # start.
        self._creep_forward_from_cm = None
        self._creep_forward_started = None
        # State for the two-phase reverse back to J3 from the first area: the
        # reverse half is done, so the hunt is now driving forward for the
        # landmark.  A flag rather than two states because the phases differ only
        # in which reading they are waiting for.
        self._return_phase_reversed = False
        # The reverse out of the second area ran its timeout without covering
        # its distance.  Latched so a caller (and the tests) can tell "held on
        # purpose" from "still moving".
        self._back_off_stalled = False
        self._visual_line_frames = 0
        # BUILD_AREA's slide and cap alignment (see the state's own comment).
        #   _build_blob_seen / _build_blob_absent_frames detect a blob LEAVING the
        #     view, confirmed over build_slide_clear_frames -- that is one "blob
        #     passed", and the car must pass loop_context.cap_count of them.
        #   _build_visit_origin_cm is the lateral pose on arrival: BUILD_BACK_TO_LINE
        #     reads it to know which way the line is and how far it may hunt.
        #   _build_slide_exhausted latches the ceiling -- hold, never build.
        self._build_blob_seen = False
        self._build_blob_absent_frames = 0
        self._build_blobs_passed = 0
        self._build_visit_origin_cm: float | None = None
        self._build_slide_exhausted = False
        self._build_align_frames = 0
        self._build_line_frames = 0
        # BUILD_BACK_TO_LINE's hunt: its own origin (absolute) and how far it may
        # travel, which is the offset the visit created plus the configured margin.
        self._build_line_seek_origin: float | None = None
        self._build_line_seek_budget = 0.0
        self._build_line_seek_exhausted = False
        # The absolute lateral pose the last time the bar was ON the line.  A line
        # hunt goes the OPPOSITE way from wherever the car has drifted since then.
        #
        # Operator, 2026-09-24: 「希望有一个累计量 记录距离上一次回中 累计往哪里偏移了
        # （不需要具体数值 只需要数字）然后一直往那边找线」and 「相反方向找线」.
        #
        # Only the SIGN is used to pick the direction; the magnitude only bounds how
        # far the hunt may go.  It is NOT re-taken per state: that is the whole point
        # of an accumulated quantity -- a visit's own origin forgets the drift that
        # happened before it.
        self._line_reference_cm: float | None = None
        self._junction = JunctionDebouncer(
            junction_mask=config.junction_mask,
            confirm_frames=config.junction_debounce_frames,
            leave_frames=config.leave_debounce_frames,
        )

    def follows_line(self, state: RouteState) -> bool:
        """Whether this state drives under V and watches for the next junction.

        JUNCTION_1_TO_JUNCTION_2 never does -- it creeps, then the strafe state
        follows it, and neither is line following.

        Field measurement 2026-09-14: past J1 the straight-ahead line is gone --
        the branch leaves to the right -- so a car that keeps line following
        drives straight off the track.  The run that proved it read 10000000 at
        J1 (state advanced at t=4.7 s) and 11111111 within 0.3 s, then sat
        stopped for the rest of the timeout.

        JUNCTION_2_LINE_FOLLOW, by contrast, IS line following: the segment after
        the right turn has a line again, and following it is the whole point.
        """
        return state in self._LINE_STATES and state is not RouteState.JUNCTION_1_TO_JUNCTION_2

    def _enter(self, state: RouteState, now: float) -> None:
        self.state = state
        self._state_started = now
        self._wait_started = None
        self._action_pending = False
        self._junction.reset()
        self._aligned = False
        self._align_count = 0
        self._align_failed = False
        self._line_lost_frames = 0
        self._tag2_search_failed = False
        self._seek_line_failed = False
        self._seek_reversed = False
        self._seek_line_saw_loss = False
        self._seek_black_frames = 0
        self._creep_forward = False
        self._creep_forward_from_cm = None
        self._creep_forward_started = None
        self._return_phase_reversed = False
        self._return_turned = False
        self._return_turn_issued = False
        self._return_phase_started = now
        self._back_off_stalled = False
        self._visual_line_frames = 0
        # The build visit is per-arrival: the blob count, the arrival pose and both
        # latches must not survive into the next time the car comes here.
        self._build_blob_seen = False
        self._build_blob_absent_frames = 0
        self._build_blobs_passed = 0
        self._build_visit_origin_cm = None
        self._build_slide_exhausted = False
        self._build_align_frames = 0
        self._build_line_frames = 0
        self._build_line_seek_origin = None
        self._build_line_seek_budget = 0.0
        self._build_line_seek_exhausted = False
        # Every sideways line hunt starts fresh: right first, origin re-taken.
        self._strafe_seek_origin_cm = None
        self._strafe_seek_dir = -1
        self._strafe_seek_settled = False
        self._strafe_seek_started = now

    def _tag_line_takeover(self, now: float, sensor_mask: int,
                           lateral_cm: float | None, destination: RouteState, *,
                           vy: int) -> RouteIntent | None:
        """Run the Tag-enabled fallback as a bounded lateral re-sweep.

        The caller only enters this state after the probe-first outbound sweep
        reaches its limit.  Tag permits the fallback; only the probes can finish
        it.  Its odometer and command therefore both stay on the lateral axis.
        """
        vision = self.config.vision
        if vision is None:
            return RouteIntent("stop", self.state)
        probes = _black_probes(sensor_mask)
        on_line = sensor_mask in self.config.seek_line_masks or (
            self.config.seek_line_min_black_probes <= probes < 8
        )
        self._visual_line_frames = self._visual_line_frames + 1 if on_line else 0
        if self._visual_line_frames >= self.config.seek_line_confirm_frames:
            self._enter(destination, now)
            return None
        if ((lateral_cm is not None and abs(lateral_cm) >= vision.tag_takeover_max_cm)
                or now - self._state_started >= vision.tag_takeover_timeout_s):
            self._seek_line_failed = True
            return RouteIntent("stop", self.state)
        return RouteIntent("strafe", self.state, speed=vy)

    @property
    def back_off_stalled(self) -> bool:
        """The straight reverse out of the second area timed out before it had
        covered its distance.  Latched until the next state change."""
        return self._back_off_stalled

    @property
    def tag2_search_failed(self) -> bool:
        """The tag-2 strafe ran out its odometer window without seeing the tag.

        Latched until the next state change, so a caller can tell a deliberate
        hold from a car that is still moving.
        """
        return self._tag2_search_failed

    @property
    def seek_line_failed(self) -> bool:
        """The post-turn strafe ran out its bounds without finding a line."""
        return self._seek_line_failed

    def _line_lost_and_travelled(self, sensor_mask: int, travel_cm: float | None, *,
                                 gate_cm: float, ceiling_cm: float) -> bool:
        """Whether the line has run out, gated by the odometer.

        J1 and J2 both end this way: the line simply stops -- J1 is the end of
        the straight, J2 is an L-corner -- and a sustained 11111111 IS the
        arrival signal.  The odometer only gates it:

          gate_cm     below this, losing the line means the car veered off
                      sideways, not that it arrived.  Three field runs lost the
                      line at 56.6, 62.9 and 68.2 cm, so something has to
                      separate the two cases.
          ceiling_cm  the fallback for a track whose line never ends; without it
                      the route would sit in the leg for ever.

        Shared by both legs so they cannot drift apart -- they are the same
        manoeuvre at different places on the track.
        """
        self._line_lost_frames = self._line_lost_frames + 1 if sensor_mask == 0xFF else 0
        if travel_cm is None:
            # No encoder reply yet: an unknown distance may not declare one.
            return False
        if travel_cm >= ceiling_cm:
            return True
        return (travel_cm >= gate_cm
                and self._line_lost_frames >= self.config.junction_debounce_frames)

    def _reached_junction_3(self, travel_cm: float | None) -> bool:
        """Whether the car has driven the measured J2 -> J3 distance.

        J3 is a T-junction -- the line carries straight on and a branch leaves to
        the left -- so neither arrival signal this route uses elsewhere is
        available, and the junction has no signature of its own.  Both halves of
        that are measured, not assumed; the evidence is written up at
        junction_3_distance_cm in config.py.

        The short version: the leg ends on the odometer because the odometer is
        the only instrument here that is not ambiguous.  It is also the case the
        odometer is best at -- one straight, no turns, entered from the
        sensor-confirmed pose the seek leaves the car in.

        A missing encoder reply may not declare an arrival, the same rule every
        other gate here follows: an unknown distance is not a satisfied one.
        """
        if travel_cm is None:
            return False
        return travel_cm >= self.config.junction_3_distance_cm

    def _seek_speed(self, vy: int) -> int:
        """The lateral velocity for the phase of the sweep now running.

        The first sweep uses the configured `vy`.  The return sweep is
        deliberately SLOWER, which is the whole point of having one: the
        measured failure mode for a fast sweep is that it crosses the line
        without the bar ever registering it (see _seek_line), and a return
        sweep at the same speed would re-cross it exactly the same way.  At a
        crawl the same ground is sampled twice as densely.
        """
        if not self._seek_reversed:
            return vy
        return -self.config.seek_line_fallback_speed if vy > 0 \
            else self.config.seek_line_fallback_speed

    def _seek_line(self, now: float, sensor_mask: int, lateral_cm: float | None, *,
                   vy: int) -> RouteIntent | None:
        """Sweep sideways until the bar is back on the black, or give up.

        A turn leaves the car off the line -- it turned on the spot, on whatever
        it was sitting on -- so every leg after a turn starts by hunting for the
        line.  The only difference between the hunts is direction, which is why
        this takes `vy` rather than owning a side.

        The sweep is TWO-PHASE.  Out in the configured direction to
        seek_line_max_cm; if the line never showed, turn round and sweep back
        the other way at seek_line_fallback_speed, out to the same distance on
        the far side.  Both bounds are measured from the state's own lateral
        baseline -- run_route_v2 re-takes it on every state change -- so the
        return sweep needs no second origin: it simply runs from +max back
        through the start and out to -max, covering 2x max in total.

        Why a return sweep rather than a hold.  Measured 2026-09-15, four
        completed runs at vy=+-20: 16 of 16 seeks found the line, at 6.5-27.9 cm
        laterally.  Measured on the 2x-speed runs at vy=+-40: two of four seeks
        (J3 at 42.0 cm, the pickup seek at 44.2 cm) ran out the bound having
        never seen a line reading, and a third was stopped at 34.28 cm on its
        way to the same failure.  So the sweep does not always find the line,
        and the old response -- stop and hold wherever it happened to be -- is
        the one outcome that cannot recover.  Sweeping back can, and it is also
        the only cover for the case the old bound could never handle: a line
        that is on the OTHER side of the turn's resting pose.

        The acceptance set is exact readings, not a shape.  A don't-care pattern
        was tried on 2026-09-15 and cannot work here: the car strafes up onto the
        line from one side, so x1 is black from the first step of the approach
        and a pattern requiring both END probes white never matches.  See
        seek_line_masks -- the disproof is in the recorded masks and needs no
        geometry.

        Returns None once the line is under the bar, meaning "found it -- carry
        on with what this state does next".  Returning a stop instead would cost
        a tick and be indistinguishable from a real stop.
        """
        if sensor_mask in self.config.seek_line_masks:
            return None
        if self.config.seek_line_accept_black_after_loss:
            black = _black_probes(sensor_mask)
            if black == 0:
                # No black anywhere under the bar: the line is lost.  Arm the
                # relaxed test.  This is the resting pose the operator described
                # -- after the turn the car sits off the line -- and it is also
                # what the bar reports for the whole outbound sweep.
                self._seek_line_saw_loss = True
                self._seek_black_frames = 0
            elif (self._seek_line_saw_loss
                  and self.config.seek_line_min_black_probes <= black < 8):
                # Black is back under the bar, and there is enough of it to be
                # the line rather than a thin feature being crossed.  0x00 is
                # excluded at the top of the range: see _black_probes.
                self._seek_black_frames += 1
                if self._seek_black_frames >= self.config.seek_line_confirm_frames:
                    return None
            else:
                # Either no loss has been seen yet, or this is a crossing.  Both
                # mean "do not count this frame"; restart rather than accumulate,
                # so the confirmation counts CONSECUTIVE frames.
                self._seek_black_frames = 0
        if lateral_cm is None:
            # No odometry yet, so there is no phase to be in: hold the first
            # direction rather than flip on a bound that cannot be measured.
            return RouteIntent("strafe", self.state, speed=vy)

        # Distance out in the FIRST sweep's direction.  Positive means "further
        # into the first sweep", negative "back past the start and beyond".
        sign = 1 if vy > 0 else -1
        reached_cm = lateral_cm * sign

        if self._seek_reversed:
            if reached_cm <= -self.config.seek_line_max_cm:
                self._seek_line_failed = True
                return RouteIntent("stop", self.state)
        elif reached_cm >= self.config.seek_line_max_cm:
            # Out of road in this direction.  Flip and keep hunting -- note this
            # is NOT a fall-through to the return below, so the sweep reverses
            # on this tick and covers the ground it just crossed.
            self._seek_reversed = True
        if now - self._state_started >= self.config.seek_line_timeout_s:
            self._seek_line_failed = True
            return RouteIntent("stop", self.state)
        return RouteIntent("strafe", self.state, speed=self._seek_speed(vy))

    def _turn_deg(self, *, left: bool, deg: int | None = None) -> int:
        """Signed rotate_deg for an in-place turn.

        Every rotation on the route goes through here so the sign convention
        (config.turn_sign) lives in a single place: if a different chassis or
        firmware ever flips it, one value corrects all of them together.

        Verified on hardware 2026-09-15: `D 0 0 90 30` turned exactly 90 degrees
        left, operator-confirmed, with the encoder yaw projection agreeing
        (3338 counts / 90 deg).  +1 means a positive rotate_deg turns left.
        """
        return self.config.turn_sign * (self.config.turn_deg if deg is None else deg) * (1 if left else -1)

    def _strafe_line_seek(self, now: float, sensor_mask: int, lateral_cm: float | None,
                          *, speed: int, timeout_s: float,
                          accept_any_black: bool = False,
                          segment_cm: float | None = None,
                          first_dir: int = -1) -> RouteIntent | None:
        """Sideways five-centimetre alternating line hunt: right, then left, repeat.

        Operator's rule (2026-09-22): strafe RIGHT one segment and look; if the
        line shows, carry on.  If not, strafe LEFT one segment and look; if it
        shows, carry on.  If not, keep alternating until it does.

        Written for `_return_to_j3_by_landmark` -- the leg that picks the car up
        after a pickup and takes it back to J3.  That leg used to reverse until
        the line was lost and then drive forward until the all-black landmark
        appeared, which in the field was the operator's "倒车丢线，然后前进，却
        一直前进": the forward phase runs with no line under the bar, so nothing
        in it can stop the car but its own distance ceiling.

        Signs are the route's usual ones: negative vy strafes RIGHT, positive
        LEFT (see `junction_3_seek_line_vy` and PickupVision's `strafe_right`).

        Returns None once the bar is back on the line, meaning "carry on with
        what this state does next".
        """
        probes = _black_probes(sensor_mask)
        if sensor_mask in self.config.seek_line_masks:
            return None
        if accept_any_black:
            # On the return leg, any black probe short of line loss confirms
            # the landmark.  The all-black reading is intentionally excluded
            # because it is the line-loss value that starts this search.
            if sensor_mask != 0xFF and probes > 0:
                return None
        elif self.config.seek_line_min_black_probes <= probes < 8:
            # The route's standard acceptance, same as _line_found: enough black
            # to be the line, with the all-black reading excluded at the top of
            # the range.  Used by the seek states, which can start out sitting
            # on a junction's black area -- there "any black" would call the
            # first tick an arrival.
            return None
        if now - self._strafe_seek_started >= timeout_s:
            return RouteIntent("stop", self.state)
        reach = 5.0 if segment_cm is None else segment_cm
        if self._strafe_seek_origin_cm is None:
            # first_dir is applied here rather than in _enter because it belongs
            # to the caller, not to the state: J2 hunts LEFT first (operator,
            # 2026-09-22), the post-J2-turn hunt goes RIGHT first.
            self._strafe_seek_dir = -1 if first_dir < 0 else 1
            if not self._strafe_seek_settled:
                # lateral_cm is re-based after a state change, so take the origin
                # from the next settled sample.
                self._strafe_seek_settled = True
                return RouteIntent("strafe", self.state, speed=self._strafe_seek_dir * speed)
            self._strafe_seek_origin_cm = lateral_cm
            return RouteIntent("strafe", self.state, speed=self._strafe_seek_dir * speed)
        if lateral_cm is not None and self._strafe_seek_origin_cm is not None:
            # Measured on |delta| so the segment ends after the configured
            # distance in whichever direction is running, without depending on
            # which way lateral_cm happens to be signed.
            if abs(lateral_cm - self._strafe_seek_origin_cm) >= reach:
                self._strafe_seek_origin_cm = lateral_cm
                self._strafe_seek_dir = -self._strafe_seek_dir
        return RouteIntent("strafe", self.state, speed=self._strafe_seek_dir * speed)

    def _creep_back_onto(self, now: float, sensor_mask: int, travel_cm: float | None, *,
                         accept: tuple[int, ...], max_reverse_cm: float,
                         timeout_s: float, forward_cm: float = 0.0,
                         accept_any_black: bool = False) -> RouteIntent | None:
        """Creep backwards until the bar is back on the mark.

        Takes its target and its bounds rather than owning them, because it runs
        twice: at J1 the mark is the junction reading the car overshot, and at J2
        it is the line the car drove off the end of at the L-corner.

        Detecting J1 takes three debounce frames, and stopping adds braking
        distance, so the car always comes to rest past the junction -- measured
        2026-09-14 and confirmed by eye ("stopped cleanly, but not on
        10000000").  Closing that gap with a distance would need its own
        calibration and would drift with battery voltage and surface; the sensor
        is already looking at the answer, so use it.

        Two readings count as arrived.  0x80 is the junction pattern itself.
        0x00 is the all-black area at J1, and it is not optional: field run
        2026-09-15 read 11111111 -> 208 -> 00000000 and then stayed all-black
        for five centimetres, never producing 0x80 at all, so a criterion that
        only accepted the junction mask drove the car 12.24 cm past J1 before
        giving up.  On the runs where 0x80 did appear the creep went
        254 -> 240 -> 192 -> 128 and never showed 0x00, so accepting it too
        costs nothing there.

        Returns "creep" while reversing.  On success it returns None, meaning
        "aligned -- carry on with whatever this state does next"; returning a
        stop here instead would cost a tick and, worse, would be
        indistinguishable from the route genuinely coming to rest.

        Gives up, and holds, on either bound: timeout_s, or max_reverse_cm of
        reverse travel.  The distance bound matters because 0x00 is the most
        common reading on this track -- the strafe measurements put 200 cm of
        continuous all-black immediately right of J1 -- so a creep that started
        unusually far past the mark could otherwise stop on the first black it
        met and call it the mark.

        accept_any_black widens "arrived" from the caller's `accept` set to any
        probe on black at all.  The return-to-J3 landmark uses this because the
        bar can meet an arbitrary slice of the line.

        forward_cm adds a second phase.  The reverse phase rests
        on one assumption: that the car came to rest PAST the mark, so the mark is
        behind it.  At J1 that assumption can be wrong -- arrival is "sustained
        line loss past a 50 cm gate", and a spurious loss leaves the car SHORT of
        the junction -- and then reversing travels away from the mark for the
        whole bound.  With forward_cm set, running out the reverse turns the car
        round and hunts forward instead, on its own distance and time bounds,
        before it finally holds.  Both phases look for the same readings; nothing
        about what counts as arrival changes.
        """
        if accept_any_black:
            # Any probe on black.  0xFF is line loss and None is "no reading" --
            # neither is evidence that the bar has found anything.
            matched = sensor_mask is not None and sensor_mask != 0xFF
        else:
            matched = sensor_mask in accept
        self._align_count = self._align_count + 1 if matched else 0
        if self._align_count >= self.config.align_confirm_frames:
            self._aligned = True
            return None

        def _start_forward() -> RouteIntent:
            """Give up on the reverse and hunt the other way instead.

            Only reachable when the caller passed a forward_cm.  The bounds of
            the new phase are taken from HERE, not from the state's start, so the
            reverse it just spent does not eat the forward phase's budget.
            """
            self._creep_forward = True
            self._creep_forward_from_cm = travel_cm
            self._creep_forward_started = now
            return RouteIntent("creep", self.state, speed=self.config.align_forward_speed)

        if self._creep_forward:
            covered_cm = None
            if travel_cm is not None and self._creep_forward_from_cm is not None:
                covered_cm = travel_cm - self._creep_forward_from_cm
            if covered_cm is not None and covered_cm >= forward_cm:
                # Ran out of road going forward too.  Now it holds: at this point
                # the car has hunted both ways and found no mark, so the one
                # response left that cannot make things worse is to stop.
                self._aligned = True
                self._align_failed = True
                return RouteIntent("stop", self.state)
            if now - self._creep_forward_started >= self.config.align_forward_timeout_s:
                self._aligned = True
                self._align_failed = True
                return RouteIntent("stop", self.state)
            return RouteIntent("creep", self.state, speed=self.config.align_forward_speed)

        def _reverse_ran_out() -> RouteIntent:
            if forward_cm > 0:
                return _start_forward()
            self._aligned = True
            self._align_failed = True
            return RouteIntent("stop", self.state)

        if travel_cm is not None and travel_cm <= -max_reverse_cm:
            # Reversed further than the mark can plausibly be.  Reversing
            # indefinitely is the worst possible response to a mark that cannot
            # be re-found, so either turn round or stop and hold.
            return _reverse_ran_out()
        if now - self._state_started >= timeout_s:
            return _reverse_ran_out()
        return RouteIntent("creep", self.state, speed=self.config.align_speed)

    def _return_to_j3_by_landmark(
        self,
        now: float,
        sensor_mask: int,
        travel_cm: float | None,
        *,
        lateral_cm: float | None = None,
        accept_any_black: bool = False,
        d_done: bool = False,
    ) -> RouteIntent | None:
        """Reverse back to J3 from the first area, the J1 creep with the sign flipped.

        Operator, 2026-09-15: the car is parked on a line at the area, so
        reversing loses that line and driving forward again picks up the
        all-black landmark that marks J3.  Two phases, one flag.

        Bounded in both directions and on time for the same reason the J1 creep
        is: past J1 there is no line for a wrong guess to be discovered against,
        so a hunt that cannot find its landmark must give up and hold rather
        than keep driving into whatever is out there.

        Returns None on arrival, a "creep" intent while hunting, and a "stop"
        intent when a bound is reached -- holding is the safe failure.

        RESTORED 2026-09-22 after a same-day rewrite was proved wrong on the
        field: routing this through `_strafe_line_seek` with `accept_any_black`
        made it accept the very first tick whenever the car happened to be
        parked on black, so the whole reverse-and-hunt vanished and the route
        jumped straight to PICKUP_2_TURN_RIGHT.  The run log shows it plainly --
        PURPLE_RETURN_TO_J3 (133 ticks at vx55) disappeared from the state list
        entirely in route_v2_full_transit80_20260922_193504.jsonl.
        """
        if not self._return_phase_reversed:
            if sensor_mask == 0xFF:
                # The line is gone: start the left turn, then hunt sideways.
                self._return_phase_reversed = True
                self._return_phase_started = now
            elif (travel_cm is not None
                  and travel_cm <= -self.config.pickup_1_return_reverse_cm):
                return RouteIntent("stop", self.state)
            elif now - self._state_started >= self.config.pickup_return_timeout_s:
                return RouteIntent("stop", self.state)
            else:
                # "creep", NOT "v" -- and the difference is the whole manoeuvre.
                #
                # The runner routes a "v" intent through the line-following PID,
                # and that controller issues STOP the moment line_error goes
                # away.  This leg exists to drive the car OFF the line, so the
                # PID stops it on the first tick it succeeds.  Measured
                # 2026-09-15 (full3_20260915.jsonl): the car reversed 4.98 cm,
                # the bar left the line, line_lost fired, and the run then sat
                # re-sending STOP for the rest of the leg until it was stopped
                # by hand.  "creep" is the straight-vx path with the PID reset,
                # which is exactly what _creep_back_onto uses at J1 -- this is
                # the same manoeuvre with the sign flipped, so it takes the same
                # intent kind.
                return RouteIntent("creep", self.state, speed=self.config.align_speed)
        # The line is gone.  TURN FIRST, and only then hunt for the line sideways
        # -- operator, 2026-09-22: "现在丢线后直接左旋 然后左移15cm再右移15cm找线",
        # later corrected on the same day to turn RIGHT instead of left:
        # "把j3倒车丢线后的旋转改成右旋".  The sideways hunt that follows is
        # unchanged: LEFT 15 cm, look, RIGHT 15 cm, look, alternating.
        #
        # The forward phase this replaces is withdrawn.  It crept FORWARD until
        # the all-black landmark appeared, and in the field it never ended: the
        # phase runs with no line under the bar, so nothing in it can stop the
        # car except its own ceiling (`pickup_1_return_forward_cm`, 60 cm) and
        # the timeout -- the operator's "前进找线还是一直前进".
        if not self._return_turned:
            if self._return_turn_issued and d_done:
                self._return_turned = True
            elif now - self._return_phase_started >= self.config.turn_timeout_s:
                return RouteIntent("stop", self.state)
            else:
                # NOTE: the "stop first, then turn" settle this used to hold is
                # now done for EVERY turn in one place -- the d branch of the
                # runner's intent dispatch, via D_SETTLE_S.  Keeping it here too
                # would just stack two 0.3 s windows.
                # One "d" intent per tick until the firmware reports DONE.  The
                # runner dedups it, so this is issued once -- do NOT try to
                # re-send it, see the warning on the d branch in run_route_v2.py.
                #
                # RIGHT, not left: operator, 2026-09-22, after watching the left
                # version -- "把j3倒车丢线后的旋转改成右旋".
                self._return_turn_issued = True
                return RouteIntent("d", self.state,
                                   rotate_deg=self._turn_deg(left=False),
                                   speed=self.config.turn_speed)
        # LEFT first, and NOT from the purple slot.
        #
        # Operator, 2026-09-22 night, watching this leg: "j3右旋结束是先左移找线！"
        # An earlier version of this call site read the slot here and was wrong:
        # the slot rule ("取左边 就往右边一直找线") belongs to the pickup's own
        # return-to-line -- PURPLE_RETURN_TO_LINE, the one_way direction in
        # run_route_v2 -- and not to this post-turn hunt, which always goes LEFT
        # first and alternates from there.
        return self._strafe_line_seek(
            now, sensor_mask, lateral_cm,
            speed=self.config.seek_line_fallback_speed,
            timeout_s=self.config.pickup_return_timeout_s,
            accept_any_black=accept_any_black,
            # Operator, 2026-09-22: 15 -> 20 -> 30 -> 40 -> 60 cm per segment, one
            # step at a time.  LEFT first, then RIGHT, alternating.
            segment_cm=60.0,
            first_dir=1,
        )
    def _back_off_straight(self, now: float, travel_cm: float | None, *,
                           distance_cm: float) -> RouteIntent | None:
        """Reverse in a straight line for a fixed distance.  No sensor at all.

        Operator, 2026-09-15: "现在取物区1碰撞后不需要识别j3了 直接直线倒车30cm
        然后右旋180度".  This replaces a hunt for J3 that was measured broken in
        BOTH directions on the same leg:

          - stable2_20260915.jsonl armed normally (started on 0x81) and then
            declared arrival after 2.77 cm, because 0x01 is BOTH a "the bar is
            on the line, one end off it" reading (seek_line_masks) and an
            "arrived at J3" reading (pickup_2_return_masks).  The car reversing
            off a line produces exactly that reading.
          - seek3_20260915.jsonl started on 0xC0 instead, never saw 0x81, so
            never armed -- and the arrival test could not fire at all.  The leg
            ran to the 150 cm bound and held there for the rest of the run.

        So the criterion was 20 s too short or unboundedly too long depending on
        which mask the car happened to be sitting on when the leg began.  A fixed
        distance has neither failure mode, and the operator's geometry does not
        need the landmark: the car only has to clear the area before the 180 turn.

        Returns None once the distance is covered, "stop" if the car never gets
        there (the timeout is the only thing that can fire, and a car that has
        not reversed 30 cm in that long is not going to).
        """
        if travel_cm is not None and travel_cm <= -distance_cm:
            return None
        if now - self._state_started >= self.config.pickup_return_timeout_s:
            self._back_off_stalled = True
            return RouteIntent("stop", self.state)
        # "creep", not "v": same trap as the landmark return above.  This leg
        # reverses off the line by definition, so a "v" intent would be handed
        # to the PID and stopped on the first tick the line goes away.
        return RouteIntent("creep", self.state, speed=self.config.align_speed)

    def _build_slide_right(self, absolute_lateral_cm: float | None) -> RouteIntent:
        """One tick of BUILD_AREA's slide right, bounded by the ceiling.

        Right is NEGATIVE vy (positive strafes LEFT).  The bound is measured on the
        ABSOLUTE lateral projection from the arrival pose, because the leg-relative
        one is re-baselined on every state change.

        A missing odometer holds the car: a slide bounded only by vision is exactly
        the runaway the ceiling exists to prevent.
        """
        if absolute_lateral_cm is None or self._build_visit_origin_cm is None:
            return RouteIntent("stop", self.state)
        if abs(absolute_lateral_cm - self._build_visit_origin_cm) >= self.config.build_slide_max_cm:
            self._build_slide_exhausted = True
            return RouteIntent("stop", self.state)
        return RouteIntent("strafe", self.state, speed=-abs(self.config.build_slide_speed))

    def step(self, now: float, *, sensor_mask: int = 0, line_error: float | None = 0.0,
             d_done: bool = False, wall_contact: bool = False,
             travel_cm: float | None = None, lateral_cm: float | None = None,
             absolute_lateral_cm: float | None = None,
             tag_stable: bool = False, vision: VisionRouteInput | None = None) -> RouteIntent:
        if now < self._state_started:
            raise ValueError("time must be monotonic")
        if self.state is RouteState.FINISHED:
            return RouteIntent("stop", self.state)
        if self.state is RouteState.FAULT:
            return RouteIntent("stop", self.state)
        visual = vision or VisionRouteInput()
        if absolute_lateral_cm is not None and line_error is not None:
            # The bar is on the line right now, so this pose IS "centred".  A later
            # line hunt measures its accumulated offset from here (see
            # _line_reference_cm).  line_error is the signal rather than a mask
            # threshold: ordinary line following reads 0x81, two probes, which the
            # ≥4-probe ACCEPTANCE would reject -- the bar reports an error whenever
            # it can see the line at all, which is exactly the question here.
            self._line_reference_cm = absolute_lateral_cm
        if self.config.vision is not None and visual.camera_fault:
            self._enter(RouteState.FAULT, now)
            return RouteIntent("stop", self.state)
        if self.config.vision is not None and (visual.vision_pending or visual.vision_hold):
            return RouteIntent("stop", self.state)

        if self.state is RouteState.START_TO_JUNCTION_1:
            if self._line_lost_and_travelled(
                    sensor_mask, travel_cm,
                    gate_cm=self.config.start_to_junction_1_cm,
                    ceiling_cm=self.config.junction_1_max_travel_cm):
                self._enter(RouteState.JUNCTION_1_TO_JUNCTION_2, now)
        elif self.state is RouteState.JUNCTION_2_TO_JUNCTION_3:
            # Not in _JUNCTION_WATCH_STATES: approached from J2, J3 is an L whose
            # line carries straight on, so 0x80 fires on the follower's own
            # wobble long before the car gets there (it did, at 124.9 cm, on
            # 2026-09-15).  See _reached_junction_3().
            if self._reached_junction_3(travel_cm):
                self._enter(RouteState.JUNCTION_3_TURN_LEFT, now)
        elif self.state in self._JUNCTION_WATCH_STATES:
            if self._junction.update(sensor_mask):
                self._enter(self._JUNCTION_WATCH_STATES[self.state], now)

        if self.state is RouteState.JUNCTION_1_TO_JUNCTION_2:
            if not self._aligned:
                aligning = self._creep_back_onto(
                    now, sensor_mask, travel_cm,
                    accept=(self.config.junction_mask, self.config.align_alt_mask),
                    max_reverse_cm=self.config.align_max_reverse_cm,
                    timeout_s=self.config.align_timeout_s,
                    forward_cm=self.config.align_forward_cm)
                if aligning is not None:
                    return aligning
                # Aligned on this tick: fall through rather than burning a tick
                # on a stop that means nothing.
            if self._align_failed:
                # The creep gave up without ever finding the junction.  Do NOT
                # strafe from here: the car can be up to align_max_reverse_cm
                # past J1 and yawed by an unknown amount, so the tag-2 search
                # window would start from the wrong place.  Hold instead.
                #
                # This flag was written at four sites and never read, so a failed
                # alignment used to fall straight through to the shift on the
                # very next tick.
                return RouteIntent("stop", self.state)
            self._enter(RouteState.JUNCTION_1_STRAFE_TO_TAG_2, now)
            # Fall through into the strafe below, same tick.

        if self.state is RouteState.JUNCTION_1_STRAFE_TO_TAG_2:
            # The window is measured on the LATERAL odometer, not the forward one.
            # The car is moving sideways, so its forward projection stays at zero
            # and a window keyed on travel_cm would never open.  lateral_cm is
            # positive to the LEFT and this strafe goes right, so take the
            # magnitude.
            strafed_cm = None if lateral_cm is None else abs(lateral_cm)
            if tag_stable and strafed_cm is not None and strafed_cm >= self.config.tag2_search_min_cm:
                # Decided on this tick rather than latched: a level check cannot
                # be tripped by a single dropped frame, because once TagTracker
                # sets _stable it holds it through up to max_missed_frames misses.
                self._enter(RouteState.TAG_2_TURN_RIGHT, now)
            elif strafed_cm is not None and strafed_cm >= self.config.tag2_search_max_cm:
                # Ceiling reached with no confirmed tag: turn anyway.
                #
                # 2026-09-15, operator decision -- this reverses an earlier
                # deliberate hold.  That hold reasoned that a turn from a pose the
                # tag never confirmed gets no sensor confirmation, so a wrong
                # guess is only discovered at the far end of the route.  Two
                # field runs showed the cost: per-frame detection recall is poor
                # enough that _stable is often unreachable, so the car stood at
                # the ceiling instead, and the stationary chassis dropped the
                # JDY-31 link before the turn was ever issued -- the turn went
                # into a dead TTY.  The point of this leg is the right turn onto
                # the J1 -> J2 line, and that line IS closed-loop after the turn:
                # JUNCTION_2_SEEK_LINE must find it on the sensor within
                # seek_line_max_cm or stop and hold.  A turn from an unconfirmed
                # pose is recoverable; a car parked in an all-black region is not.
                self._enter(RouteState.TAG_2_TURN_RIGHT, now)
                # _enter() clears this with the rest of the per-state latch, so
                # set it after the transition -- otherwise it is set and wiped in
                # the same tick and nothing can tell a tag-confirmed turn from a
                # ceiling turn.
                self._tag2_search_failed = True
            else:
                # Negative vy strafes RIGHT; V's lateral sign is measured, unlike
                # D's rotation sign.
                return RouteIntent("strafe", self.state,
                                   speed=-self.config.tag2_strafe_speed)

        if self.state is RouteState.TAG_2_TURN_RIGHT:
            # Ignore a stale 'done' from an earlier D: the turn must actually be
            # issued before its completion can advance the route.
            if self._action_pending and d_done:
                self._enter(RouteState.JUNCTION_2_SEEK_LINE, now)
            else:
                if now - self._state_started >= self.config.turn_timeout_s:
                    return RouteIntent("stop", self.state)
                self._action_pending = True
                return RouteIntent("d", self.state, rotate_deg=self._turn_deg(left=False),
                                   speed=self.config.turn_speed)

        if self.state is RouteState.JUNCTION_2_SEEK_LINE:
            # After the RIGHT turn at tag 2 the line is to the car's LEFT.
            seeking = self._seek_line(now, sensor_mask, lateral_cm,
                                      vy=self.config.junction_2_seek_line_vy)
            if seeking is None:
                self._enter(RouteState.JUNCTION_2_LINE_FOLLOW, now)
                # Fall through: the line follower drives on this same tick.
            else:
                return seeking

        if self.state is RouteState.JUNCTION_2_LINE_FOLLOW:
            # The same arrival test as J1, shared rather than copied: J2 is an
            # L-corner, so the line simply stops under the bar.  The car comes to
            # rest PAST the corner, though, so this hands over to the reverse
            # rather than straight to the turn.
            if self._line_lost_and_travelled(
                    sensor_mask, travel_cm,
                    gate_cm=self.config.junction_2_line_loss_gate_cm,
                    ceiling_cm=self.config.junction_2_max_travel_cm):
                self._enter(RouteState.JUNCTION_2_TURN_LEFT, now)
            # Falls through to the line-following return at the end of step().


        if self.state is RouteState.JUNCTION_2_TURN_LEFT:
            if self._action_pending and d_done:
                self._enter(RouteState.JUNCTION_3_SEEK_LINE, now)
            else:
                if now - self._state_started >= self.config.turn_timeout_s:
                    return RouteIntent("stop", self.state)
                self._action_pending = True
                return RouteIntent("d", self.state, rotate_deg=self._turn_deg(left=True),
                                   speed=self.config.turn_speed)

        if self.state is RouteState.JUNCTION_3_SEEK_LINE:
            # Mirrored: after the LEFT turn at J2 the line is to the car's RIGHT.
            #
            # Operator, 2026-09-22: "前进丢线后 不需要倒车找线 然后直接左旋90
            # 向右15cm然后循环".  The J2 corner is handled upstream -- the car
            # loses the line, does NOT hunt, turns left 90, and arrives here.
            # This is where the hunt lives: RIGHT fifteen centimetres, look; if
            # the line is not there, LEFT fifteen, look; keep alternating.
            #
            # The sweep this replaces committed to one direction out to
            # seek_line_max_cm before it ever turned round, which reads on the
            # track as "it went the wrong way and gave up", and it had no notion
            # of re-checking ground it had already crossed.
            seeking = self._strafe_line_seek(
                now, sensor_mask, lateral_cm,
                speed=self.config.seek_line_fallback_speed,
                timeout_s=self.config.seek_line_timeout_s,
                segment_cm=15.0,
                first_dir=-1)
            if seeking is None:
                self._enter(RouteState.JUNCTION_2_TO_JUNCTION_3, now)
            else:
                return seeking

        if self.state is RouteState.JUNCTION_3_TURN_LEFT:
            if self._action_pending and d_done:
                self._enter(
                    RouteState.DIRECT_ORANGE_D330
                    if self.loop_context.has_purple
                    else RouteState.PICKUP_SEEK_LINE,
                    now,
                )
            else:
                if now - self._state_started >= self.config.turn_timeout_s:
                    return RouteIntent("stop", self.state)
                self._action_pending = True
                return RouteIntent("d", self.state, rotate_deg=self._turn_deg(left=True),
                                   speed=self.config.turn_speed)

        if self.state is RouteState.PICKUP_SEEK_LINE:
            # The line is to the car's RIGHT after the left turn at J3 (operator,
            # 2026-09-15) -- the OPPOSITE side from the hunt after the J2 turn.
            # That asymmetry is the whole reason the direction is per-seek config
            # and not a shared magnitude; see _seek_line().
            seeking = self._seek_line(now, sensor_mask, lateral_cm,
                                      vy=self.config.pickup_seek_line_vy)
            if seeking is None:
                self._enter(
                    RouteState.PURPLE_PRESCAN
                    if self.config.vision is not None
                    else RouteState.JUNCTION_3_TO_PICKUP,
                    now,
                )
                # Fall through: the line follower drives on this same tick.
            elif (self.config.vision is not None and visual.tag3_stable
                  and self._seek_reversed):
                self._enter(RouteState.PICKUP_TAG_LINE_TAKEOVER, now)
                return RouteIntent(
                    "strafe",
                    self.state,
                    speed=-self.config.seek_line_fallback_speed
                    if self.config.pickup_seek_line_vy > 0
                    else self.config.seek_line_fallback_speed,
                )
            else:
                return seeking

        if self.state is RouteState.PICKUP_TAG_LINE_TAKEOVER:
            takeover = self._tag_line_takeover(
                now,
                sensor_mask,
                lateral_cm,
                RouteState.PURPLE_PRESCAN,
                vy=-self.config.seek_line_fallback_speed
                if self.config.pickup_seek_line_vy > 0
                else self.config.seek_line_fallback_speed,
            )
            if takeover is not None:
                return takeover

        if self.state is RouteState.PURPLE_PRESCAN:
            if visual.purple_prescan_present is None and visual.purple_slots is None:
                # The scan is STILL RUNNING -- wait for it before deciding.
                #
                # `purple_slots` exists from the first frame the worker answers
                # and is all-False until something has been detected, so an
                # all-False tuple here does NOT mean "no purple": it means "no
                # frame has seen one yet".  Acting on it is what skipped a
                # right-hand block on 2026-09-22 night (run 20260922_215250:
                # the payload that decided the state was frames=2, counts all
                # zero, candidate_centers empty, present=None).  A LEFT block was
                # picked up on the first frame, which is why this only ever bit
                # the right-hand slot.
                #
                # This clause used to sit BELOW the purple_slots test, where it
                # could never be reached -- the slots test always fired first.
                return RouteIntent("stop", self.state)
            elif visual.purple_slots is not None:
                self.purple_target_slot = choose_purple_slot(visual.purple_slots)
                if self.purple_target_slot is None:
                    self.loop_context.purple_absent = True
                    self._enter(RouteState.PICKUP_2_TURN_RIGHT, now)
                else:
                    self._enter(RouteState.JUNCTION_3_TO_PICKUP, now)
            elif visual.purple_prescan_present:
                self._enter(RouteState.JUNCTION_3_TO_PICKUP, now)
            else:
                self.loop_context.purple_absent = True
                self._enter(RouteState.PICKUP_2_TURN_RIGHT, now)

        if self.state is RouteState.JUNCTION_3_TO_PICKUP:
            if wall_contact or _reached_gate(travel_cm, self.config.pickup_arrived_distance_cm):
                self._enter(RouteState.PICKUP_ARRIVED, now)
            else:
                return RouteIntent("v", self.state, speed=self.config.pickup_speed)

        if self.state is RouteState.PICKUP_ARRIVED:
            if self.config.vision is not None:
                self._enter(RouteState.PICKUP_VISION_ONLY, now)
            else:
                # Preserve the already field-tested non-visual route for direct
                # RouteV2Config() callers and old segmented tests.
                if self._wait_started is None:
                    self._wait_started = now
                if now - self._wait_started >= self.config.pickup_wait_s:
                    self._enter(RouteState.PICKUP_1_RETURN, now)
                else:
                    return RouteIntent("wait", self.state, wait_s=self.config.pickup_wait_s)

        if self.state is RouteState.PICKUP_VISION_ONLY:
            if visual.pickup_kind == "fault":
                # A purple pickup failure is a competition outcome (the block
                # may have been taken), not a reason to abort the whole run.
                # Disable purple permanently and continue through the normal
                # return-to-line path to orange area 1.
                self.loop_context.purple_absent = True
                self._enter(RouteState.PURPLE_RETURN_TO_LINE, now)
                return RouteIntent("stop", self.state)
            if visual.action_done:
                self.loop_context.record_purple()
                # Hand over to the line first, not straight to the reverse/forward
                # landmark hunt -- that hunt assumes a car sitting on the line,
                # and the alignment has just strafed this one off it.
                self._enter(RouteState.PURPLE_RETURN_TO_LINE, now)
            elif visual.pickup_kind == "no_target":
                self.loop_context.purple_absent = True
                self._enter(RouteState.PICKUP_RETURN_TO_LINE, now)
            elif visual.pickup_kind in {"strafe_right", "strafe_left", "return_baseline",
                                        "align_left", "align_right"}:
                return RouteIntent("strafe", self.state, speed=visual.pickup_speed)
            else:
                return RouteIntent("stop", self.state)

        if self.state is RouteState.PURPLE_RETURN_TO_LINE:
            # The controller reports "fault" when it has run out of bounded seek
            # in both directions.  Fault rather than hold: a silent hold is
            # indistinguishable from a hang on the field, and today's failure
            # (18.5 s of STOP with nothing in the telemetry to explain it) is
            # exactly what that looks like.
            if visual.pickup_kind == "fault":
                self._enter(RouteState.FAULT, now)
                return RouteIntent("stop", self.state)
            if visual.return_line_done:
                # On the line again.  Fall through this tick into the landmark
                # hunt below -- it starts by reversing, which is now correct
                # because there is a line under the car to lose.
                self._enter(RouteState.PURPLE_RETURN_TO_J3, now)
            elif visual.pickup_kind in {"strafe_right", "strafe_left", "return_baseline",
                                        "seek_line"}:
                return RouteIntent("strafe", self.state, speed=visual.pickup_speed)
            else:
                return RouteIntent("stop", self.state)

        if self.state is RouteState.PURPLE_RETURN_TO_J3:
            returning = self._return_to_j3_by_landmark(
                now, sensor_mask, travel_cm, lateral_cm=lateral_cm,
                accept_any_black=True, d_done=d_done,
            )
            if returning is None:
                # Purple is carried through J3 and the orange trip follows in
                # the same round.  Placement is deferred to BUILD_AREA.
                #
                # STRAIGHT INTO THE SEEK, not through PICKUP_2_TURN_RIGHT.
                # Operator, 2026-09-22, after watching the car turn right twice
                # and then crash on the way to the second area: "把（两个）反正
                # 合并成一个右旋就行了".  The return leg already turns right 90
                # (the hunt cannot work without it, see _return_to_j3_by_landmark),
                # and PICKUP_2_TURN_RIGHT was written to supply that same 90 back
                # when this leg was a reverse + forward creep with NO rotation of
                # its own.  Both together are 180, which is the trip-3
                # (build-area) heading, not the trip-2 one -- see the three-trip
                # table at the RouteState definitions.
                self._enter(RouteState.PICKUP_2_SEEK_LINE, now)
            else:
                return returning

        if self.state is RouteState.PICKUP_RETURN_TO_LINE:
            if visual.return_line_done:
                self._enter(RouteState.PICKUP_1_RETURN, now)
            elif visual.pickup_kind in {"strafe_right", "strafe_left", "return_baseline",
                                        "seek_line"}:
                return RouteIntent("strafe", self.state, speed=visual.pickup_speed)
            else:
                return RouteIntent("stop", self.state)

        if self.state is RouteState.PICKUP_1_RETURN:
            returning = self._return_to_j3_by_landmark(
                now, sensor_mask, travel_cm, lateral_cm=lateral_cm, d_done=d_done,
            )
            if returning is None:
                # Same merge as PURPLE_RETURN_TO_J3 above: this leg turned right
                # already, so the PICKUP_2_TURN_RIGHT 90 would make it 180.
                self._enter(RouteState.PICKUP_2_SEEK_LINE, now)
            else:
                return returning

        if self.state is RouteState.PICKUP_2_TURN_RIGHT:
            if self._action_pending and d_done:
                self._enter(RouteState.PICKUP_2_SEEK_LINE, now)
            else:
                if now - self._state_started >= self.config.turn_timeout_s:
                    return RouteIntent("stop", self.state)
                self._action_pending = True
                return RouteIntent("d", self.state, rotate_deg=self._turn_deg(left=False),
                                   speed=self.config.turn_speed)

        if self.state is RouteState.PICKUP_2_SEEK_LINE:
            # Which way to look first depends on which route got here.
            #
            #   Operator, 2026-09-22 night: "判定没有紫色 旋转后是先右移找线
            #   有的话 取完物体后是先左移找线"
            #
            # With NO purple seen at J3 the car turned right here straight from the
            # heading it arrived with, so the line lies to its RIGHT.  With a
            # purple taken it comes back from the area and that same right turn
            # leaves the line to its LEFT -- which is the configured direction
            # (positive strafes left).
            #
            # A missing purple at J3 uses the conservative right-first search;
            # a purple already carried through the loop uses the left-first
            # return geometry.
            vy = self.config.pickup_2_seek_line_vy
            if self.loop_context.purple_absent:
                # ...and SLOWLY.  Operator, 2026-09-22 night: "右移太快了 速度放慢点
                # 对于没有紫色的情况".  At 50 the car crossed the line in three
                # ticks, 0.18 s (run 20260922_220527), so stopping ON the line was
                # luck rather than detection.  The fallback speed is the route's
                # existing slow seek speed, NOT a change to any speed profile.
                vy = -self.config.seek_line_fallback_speed
            # Already ON the line when the turn finished?  Then there is nothing
            # to seek -- drive on.
            #
            #   Operator, 2026-09-22 night: "如果旋转完后还在线上就巡线直跑!"
            #
            # Measured on run 20260922_220155: the right turn ended with the bar
            # on the junction black (mask 0x00) and two ticks into this state it
            # read 0x03 / 0x0F -- six and four probes on black, i.e. THE LINE --
            # while _seek_line refused it twice over: its exact acceptance set is
            # (0x81, 0x80, 0x01), and its relaxed "black after a loss" path can
            # never arm for a car that STARTED on black.  At seek speed the car
            # was off the line again 0.2 s later, so the rest of the leg ran with
            # nothing under the bar and the state ended in the tag takeover.
            #
            # 4..7 probes black is the line.  8 probes black (0x00) is the wide
            # junction area this state is entered sitting on, and is still NOT
            # accepted -- that exclusion is the reason the relaxed seek exists.
            black = _black_probes(sensor_mask)
            if self.config.seek_line_min_black_probes <= black < 8:
                self._enter(RouteState.JUNCTION_PICKUP_2_TO_AREA, now)
                # Fall through: the follower drives on this same tick.
            else:
                seeking = self._seek_line(now, sensor_mask, lateral_cm, vy=vy)
                if seeking is None:
                    self._enter(RouteState.JUNCTION_PICKUP_2_TO_AREA, now)
                    # Fall through: the follower drives on this same tick.
                elif (self.config.vision is not None and visual.tag4_stable
                      and self._seek_reversed):
                    self._enter(RouteState.PICKUP_2_TAG_LINE_TAKEOVER, now)
                    return RouteIntent(
                        "strafe",
                        self.state,
                        speed=-self.config.seek_line_fallback_speed
                        if vy > 0
                        else self.config.seek_line_fallback_speed,
                    )
                else:
                    return seeking

        if self.state is RouteState.PICKUP_2_TAG_LINE_TAKEOVER:
            takeover = self._tag_line_takeover(
                now,
                sensor_mask,
                lateral_cm,
                RouteState.JUNCTION_PICKUP_2_TO_AREA,
                vy=-self.config.seek_line_fallback_speed
                if self.config.pickup_2_seek_line_vy > 0
                else self.config.seek_line_fallback_speed,
            )
            if takeover is not None:
                return takeover

        if self.state is RouteState.JUNCTION_PICKUP_2_TO_AREA:
            if wall_contact or _reached_gate(travel_cm, self.config.pickup_2_arrived_distance_cm):
                self._enter(RouteState.PICKUP_2_ARRIVED, now)
            else:
                return RouteIntent("v", self.state, speed=self.config.pickup_speed)

        if self.state is RouteState.PICKUP_2_ARRIVED:
            if self.config.vision is not None:
                self._enter(RouteState.PICKUP_2_VISION_ONLY, now)
            else:
                if self._wait_started is None:
                    self._wait_started = now
                if now - self._wait_started >= self.config.pickup_wait_s:
                    self._enter(RouteState.PICKUP_2_RETURN, now)
                else:
                    return RouteIntent("wait", self.state, wait_s=self.config.pickup_wait_s)

        if self.state is RouteState.PICKUP_2_VISION_ONLY:
            if visual.pickup_kind == "fault":
                self._enter(RouteState.FAULT, now)
                return RouteIntent("stop", self.state)
            if visual.action_done:
                self.loop_context.record_orange()
                # The action package is the complete pickup motion. Continue
                # with the next visual search or return directly; no extra D
                # correction is issued after an orange grab.
                if self.loop_context.total_count < 3 and not self.loop_context.orange_absent:
                    self._enter(RouteState.PICKUP_2_VISION_ONLY, now)
                else:
                    self._enter(RouteState.PICKUP_2_RETURN_TO_LINE, now)
            elif visual.pickup_kind == "no_target":
                self.loop_context.orange_absent = True
                if self.loop_context.supply_exhausted and self.loop_context.total_count == 0:
                    self._enter(RouteState.FINISHED, now)
                    return RouteIntent("stop", self.state)
                self._enter(RouteState.PICKUP_2_RETURN_TO_LINE, now)
            elif visual.pickup_kind in {"strafe_right", "strafe_left", "return_baseline",
                                        "align_left", "align_right"}:
                return RouteIntent("strafe", self.state, speed=visual.pickup_speed)
            else:
                return RouteIntent("stop", self.state)

        if self.state is RouteState.PICKUP_2_RETURN_TO_LINE:
            if visual.return_line_done:
                self._enter(RouteState.PICKUP_2_RETURN, now)
            elif visual.pickup_kind in {"strafe_right", "strafe_left", "return_baseline",
                                        "seek_line"}:
                return RouteIntent("strafe", self.state, speed=visual.pickup_speed)
            else:
                return RouteIntent("stop", self.state)

        if self.state is RouteState.PICKUP_2_RETURN:
            # Straight back, fixed distance, sensor-blind on purpose -- see
            # _back_off_straight for why the mask hunt was withdrawn.
            returning = self._back_off_straight(
                now, travel_cm, distance_cm=self.config.pickup_2_return_reverse_cm)
            if returning is None:
                self._enter(RouteState.PICKUP_3_TURN_RIGHT, now)
            else:
                return returning

        if self.state is RouteState.PICKUP_3_TURN_RIGHT:
            # The 180 is why this is a separate state rather than a reuse of the
            # 90 turn helpers: the angle comes from config, not from turn_deg.
            if self._action_pending and d_done:
                self._enter(RouteState.JUNCTION_PICKUP_3_TO_AREA, now)
            else:
                if now - self._state_started >= self.config.turn_timeout_s:
                    return RouteIntent("stop", self.state)
                self._action_pending = True
                return RouteIntent("d", self.state,
                                   rotate_deg=self._turn_deg(left=False,
                                                             deg=self.config.pickup_3_turn_deg),
                                   speed=self.config.turn_speed)

        if self.state is RouteState.JUNCTION_PICKUP_3_TO_AREA:
            # No seek before this one: the 180 leaves the car already on the
            # line (operator, 2026-09-15), so it drives off under the follower.
            #
            # ARRIVAL IS THE ODOMETER, and nothing else.  Operator, 2026-09-24:
            # 「倒车30cm后 右旋180 巡线D330cm 然后执行搭建动作」-- the leg ends
            # after pickup_3_arrived_distance_cm of line following, and the build
            # action runs from wherever that puts the car.
            #
            # This replaces arrival-on-wall-contact, which was measured blind on
            # 2026-09-24 (run 20260924_192550).  Pressed against the build wall the
            # wheels SKID rather than jam, and the encoder counts wheel rotation,
            # not ground speed -- so the odometer kept advancing while the car was
            # pinned.  encoder_delta bounced between 0.5 and 10.8, resetting
            # EncoderContactDetector's 0.5 s stationary window every few ticks, and
            # wall_contact never once became true: 1311 ticks / 65 s on this leg,
            # travel_cm out to 2110 cm, with the route commanding V 80 0 0 into the
            # wall the whole time.  It only stopped when the run was killed by
            # hand.  The detector's own docstring had it backwards ("the wheels DO
            # stall; it is the skid that never happens"); the note that had it
            # right was the warning in config.py next to the old minimum.
            #
            # 330 is the operator's number, not a tape reading of the wall: the
            # wall itself measured 370.06 cm (run 20260923_214138), so the car
            # stops about 40 cm short of it and builds from there.  This is NOT
            # the distance gate that was withdrawn on 2026-09-15 -- that one
            # declared an arrival ~2 m short of the build area.
            #
            # The residual risk, stated plainly: this leg now depends entirely on
            # the odometer.  If ENC stops replying, travel_cm stops advancing and
            # nothing but the physical wall -- and the process-wide --timeout-s --
            # ends the leg.  There is deliberately no distance ceiling; see the
            # note on the deleted pickup_3_max_travel_cm in config.py.
            if _reached_gate(travel_cm, self.config.pickup_3_arrived_distance_cm):
                self._enter(RouteState.BUILD_AREA, now)
            else:
                # Deliberately an else, not a fall-through: after
                # _enter(BUILD_AREA) this must reach the tail of step(), which
                # returns "stop" for a state that neither follows a line nor is
                # watched.  A bare return here would command a V.
                return RouteIntent("v", self.state, speed=self.config.pickup_speed)

        if self.state is RouteState.BUILD_AREA:
            # The tick BUILD_AREA is ENTERED is a fall-through from the 330 cm leg:
            # _enter() ran a few lines up and step() kept going, so BOTH inputs on
            # that tick belong to the leg, not to this state.  lateral_cm still
            # carries the leg's odometer (330, measured), and `visual` was sampled
            # for the leg's vision task rather than BUILD_OCCUPANCY.
            #
            # Both would poison the slide.  The stale lateral made the ceiling fire
            # on the second slide tick (the dry run reported STOPPED BUILD_AREA:
            # origin captured at 330, then re-baselined to 0, so |0 - 330| >= 140),
            # and an occupancy reading taken for a different task is not evidence
            # about the build view at all.  So this tick does nothing; the runner
            # re-baselines the lateral odometer and switches the vision task before
            # the next one.  Checked FIRST, so nothing is acted on before that.
            #
            # `wait`, not `stop`: the simulator treats a stop as "the route has come
            # to rest short of the end" and gives up there, which is right for a
            # settled car and wrong for a one-tick hold -- with BUILD_AREA excluded
            # from that test instead, a config with no vision (build_block_visible
            # stays None, so the state can only ever hold) span until the
            # simulation timed out.  On the chassis the two are the same command.
            if now <= self._state_started:
                return RouteIntent("wait", self.state,
                                   wait_s=self.config.poll_period_s)
            if visual.build_block_visible is None:
                return RouteIntent("stop", self.state)
            if absolute_lateral_cm is not None and self._build_visit_origin_cm is None:
                # ABSOLUTE, not lateral_cm: the runner re-baselines the leg-relative
                # odometer on every state change, so only the absolute projection
                # survives from this arrival to BUILD_BACK_TO_LINE, which has to know
                # how far the visit moved the car sideways.
                self._build_visit_origin_cm = absolute_lateral_cm

            # One "building passed" = a blob that WAS in view has left it, held
            # absent over build_slide_clear_frames so detector flicker cannot count
            # as a building.  The car passes exactly loop_context.building_count of
            # them: the buildings stand to the left of the free space and the car
            # arrives from the left.
            if visual.build_block_visible:
                self._build_blob_seen = True
                self._build_blob_absent_frames = 0
            elif self._build_blob_seen:
                self._build_blob_absent_frames += 1
                if self._build_blob_absent_frames >= self.config.build_slide_clear_frames:
                    self._build_blob_seen = False
                    self._build_blob_absent_frames = 0
                    self._build_blobs_passed += 1

            # The ceiling latches, and a latched ceiling HOLDS: reaching it means the
            # detector never let the car past the buildings the memory counts, which
            # is a fault to look at on the field, not a place to drop two blocks.
            if self._build_slide_exhausted:
                return RouteIntent("stop", self.state)

            plan = build_plan_for_inventory(self.loop_context)
            is_cap = bool(plan) and plan[0] in CAP_ACTIONS
            # With only orange (a placement) the car gets past EVERY building, so it
            # ends up in the empty space beyond them.  With a purple (a cap) it only
            # gets past the ones already capped, because the cap goes on the next
            # stack that is still waiting.  Operator, 2026-09-24: 「只有橙色 要跳过
            # 所有建筑」/「有紫色才要封顶 跳过已经封顶的」.
            skip = (self.loop_context.cap_count if is_cap
                    else self.loop_context.building_count)

            if is_cap:
                # CAPPING.  Get past the finished buildings, then centre on the
                # stack that is still waiting for its cap -- the cap goes on top of
                # what the car is looking at, so it has to line up with it first.
                #
                # `build_center_error is None` with a blob in view means the centre
                # is not known; sliding is the only way to change what the car is
                # looking at, and the ceiling bounds that.
                if (self._build_blobs_passed < skip
                        or not visual.build_block_visible
                        or visual.build_center_error is None):
                    return self._build_slide_right(absolute_lateral_cm)
                error = visual.build_center_error
                if abs(error) > self.config.build_align_tolerance:
                    self._build_align_frames = 0
                    # Negative error = the blob sits LEFT of centre, and the car
                    # then strafes LEFT (+vy) to bring it in -- the pickup's own
                    # sign convention, which is field-proven.
                    return RouteIntent(
                        "strafe", self.state,
                        speed=(abs(self.config.build_slide_speed) if error < 0
                               else -abs(self.config.build_slide_speed)))
                self._build_align_frames += 1
                if self._build_align_frames >= self.config.build_align_confirm_frames:
                    prepare_build_plan(self.loop_context)
                    self._enter(RouteState.BUILD_ACTION, now)
                else:
                    # Centred, but not for long enough yet.  `wait` rather than
                    # `stop`: on the chassis both are the same STOP, but the dry
                    # run reads a stop in a state that is not a known hold as "the
                    # route has come to rest" and gives up there.
                    return RouteIntent("wait", self.state,
                                       wait_s=self.config.poll_period_s)
            elif self._build_blobs_passed < skip or visual.build_block_visible:
                # PLACING.  A blob still in view after the memory's count means there
                # are more buildings here than the route knows about, so keep going:
                # building into a structure is the one outcome worth avoiding.
                return self._build_slide_right(absolute_lateral_cm)
            else:
                prepare_build_plan(self.loop_context)
                self._enter(RouteState.BUILD_ACTION, now)

        if self.state is RouteState.BUILD_ACTION:
            if visual.pickup_kind == "fault":
                self._enter(RouteState.FAULT, now)
            elif visual.build_action_done:
                if self.loop_context.supply_exhausted and self.loop_context.total_count == 0:
                    self._enter(RouteState.FINISHED, now)
                else:
                    # Find the line again BEFORE reversing and turning: the visit
                    # slid the car sideways to get past the buildings, so a blind
                    # 30 cm reverse and a 180 from here points the next leg wrong.
                    self._enter(RouteState.BUILD_BACK_TO_LINE, now)
            else:
                return RouteIntent("stop", self.state, wait_s=self.config.arm_placeholder_stop_s)

        if self.state is RouteState.BUILD_BACK_TO_LINE:
            # The entry tick is a fall-through from BUILD_ACTION, so it carries that
            # state's readings; hold one tick as BUILD_AREA does.
            if now <= self._state_started:
                return RouteIntent("wait", self.state,
                                   wait_s=self.config.poll_period_s)
            probes = _black_probes(sensor_mask)
            # The route's standard line acceptance, the same one the return
            # controller uses: enough black probes to be the line, with the
            # all-black reading (line loss) excluded at the top of the range.
            if (sensor_mask in self.config.seek_line_masks
                    or self.config.seek_line_min_black_probes <= probes < 8):
                self._build_line_frames += 1
                if self._build_line_frames >= self.config.seek_line_confirm_frames:
                    self._enter(RouteState.BUILD_RETURN_REVERSE, now)
                else:
                    # Confirming, same `wait`-not-`stop` reason as the align above.
                    return RouteIntent("wait", self.state,
                                       wait_s=self.config.poll_period_s)
            else:
                self._build_line_frames = 0
                # Accumulated offset -- how far the car has drifted sideways since it
                # was last ON the line.  The line reference is the accumulation; the
                # visit origin is only the fallback for a run that has not seen the
                # line yet.
                reference = (self._line_reference_cm
                             if self._line_reference_cm is not None
                             else self._build_visit_origin_cm)
                offset = 0.0
                if absolute_lateral_cm is not None and reference is not None:
                    offset = absolute_lateral_cm - reference
                if self._build_line_seek_exhausted or absolute_lateral_cm is None:
                    return RouteIntent("stop", self.state)
                if self._build_line_seek_origin is None:
                    self._build_line_seek_origin = absolute_lateral_cm
                    # Only the SIGN of the offset chooses the direction; this is the
                    # magnitude, and it only bounds how far the hunt may run.
                    self._build_line_seek_budget = (
                        abs(offset) + self.config.build_line_seek_margin_cm)
                elif abs(absolute_lateral_cm - self._build_line_seek_origin) >= self._build_line_seek_budget:
                    # Hunted its whole budget without the bar seeing the line.  Hold
                    # and let the operator look, like every other failure here.
                    self._build_line_seek_exhausted = True
                    return RouteIntent("stop", self.state)
                # OPPOSITE the accumulated offset.  A positive offset means the car
                # has drifted RIGHT (a right strafe raises this projection), so the
                # line it left is to the LEFT, and positive vy strafes LEFT.
                # Operator, 2026-09-24: 「相反方向找线」.
                speed = abs(self.config.build_slide_speed)
                return RouteIntent("strafe", self.state,
                                   speed=speed if offset >= 0 else -speed)

        if self.state is RouteState.BUILD_RETURN_REVERSE:
            returning = self._back_off_straight(
                now, travel_cm, distance_cm=self.config.loop_reverse_cm
            )
            if returning is None:
                self._enter(RouteState.BUILD_TURN_LEFT, now)
            else:
                return returning

        if self.state is RouteState.BUILD_TURN_LEFT:
            if self._action_pending and d_done:
                destination = (RouteState.DIRECT_ORANGE_D330
                               if self.loop_context.has_purple
                               else RouteState.JUNCTION_2_TO_JUNCTION_3)
                self._enter(destination, now)
            else:
                if now - self._state_started >= self.config.turn_timeout_s:
                    return RouteIntent("stop", self.state)
                self._action_pending = True
                return RouteIntent(
                    "d", self.state,
                    rotate_deg=self._turn_deg(left=True, deg=180),
                    speed=self.config.turn_speed,
                )

        if self.state is RouteState.DIRECT_ORANGE_D330:
            # LINE FOLLOWING, not a blind D.  Operator, 2026-09-24:
            # 「搭建区执行完动作 倒车30cm 旋转180后 没有直线巡线pid调整走直线」.
            #
            # This state used to fire one `D 330 0 0 80` and wait for d_done: a
            # blind move with the line sensor unused, so it could not correct its
            # heading at all.  Same reason a stall here was undiagnosable --
            # measured on run 20260924_201623, 352 ticks in this state with
            # travel_cm 0.0 for every one of them, because the blind path never
            # sampled the odometer.
            #
            # Arrival is the same odometer gate the build-area leg uses, on the
            # operator's 330 cm (config.direct_orange_distance_cm).  The timeout
            # stays as a backstop: if ENC stops replying the gate can never fire,
            # and without it this leg would drive until the process-wide
            # --timeout-s.  It is roughly 3x the travel time at pickup_speed.
            #
            # NOTE: the state name still says DIRECT/D330 from the blind version.
            # It is kept because the loop strategy and the tests reference the
            # name; do not read it as a description of what the state does now.
            if _reached_gate(travel_cm, self.config.direct_orange_distance_cm):
                self._enter(RouteState.PICKUP_2_ARRIVED, now)
            elif now - self._state_started >= self.config.orange_direct_timeout_s:
                return RouteIntent("stop", self.state)
            else:
                return RouteIntent("v", self.state, speed=self.config.pickup_speed)

        if self.follows_line(self.state):
            # Already inside follows_line(), so the old name-prefix test here
            # could only ever pick reverse_speed for a state that really does
            # follow a line -- i.e. it could only drive the car backwards.  Use
            # the predicate that was just checked.
            return RouteIntent("v", self.state, speed=self.config.forward_speed)
        return RouteIntent("stop", self.state)
