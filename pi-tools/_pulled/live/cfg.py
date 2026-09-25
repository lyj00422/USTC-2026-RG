from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml

from .vision_config import RouteVisionConfig


@dataclass(frozen=True)
class RouteV2Config:
    vision: RouteVisionConfig | None = None
    # The vendor module reports 0 for a probe that is on the black line
    # ("X1亮灯（在黑线上）B01111111", 8路巡线模块的使用方法 p.3).  Probe order is
    # x1..x8 left to right across the car, measured on 2026-09-14 by placing the
    # line under each end of the bar in turn.
    #
    # The junction pattern is a *branch*, and a branch shows up as the outer
    # probe on that side flipping to black:
    #     normal, centred on the line      10000001  (0x81)
    #     branch leaving to the right      10000000  (0x80)
    # This is NOT 0x00.  An all-black reading also happens on any black area
    # wider than the 7 cm bar -- there is a 5 cm one a few centimetres after the
    # start -- so keying on it both missed every real junction and fired on that
    # band.  Field-confirmed by the operator for J1, whose branch goes right.
    junction_mask: int = 0x80
    junction_debounce_frames: int = 3
    leave_debounce_frames: int = 3
    # Distance from the start line to J1, in cm, measured on the track.  J1 is
    # triggered by this odometer distance, NOT by the sensor.
    #
    # Reason, measured 2026-09-14: 10000000 is produced both by a real branch to
    # the right AND by a car sitting one probe off the line while slightly
    # yawed.  The two are identical at the sensor, and the ambiguity stopped the
    # route 5 cm out of the start -- the operator's report was "it only came out
    # a little bit, it did not go straight, went a bit crooked, read 10000000
    # and stopped there".  No frame count or debounce can separate them.
    #
    # The odometer has no such ambiguity.  Its ~3% error is cleaned up by the
    # alignment creep in JUNCTION_1_TO_JUNCTION_2, which IS closed loop on the
    # sensor -- the sensor is used for what it is good at (relative positioning)
    # rather than for what it cannot do (absolute identification).
    # J1 is reached when the line runs out.
    #
    # Measured 2026-09-15: the car tracks the line and then reads 11111111 --
    # the line literally ends at J1.  Three runs stopped at 56.6, 62.9 and
    # 68.2 cm for the same command, so a fixed distance cannot express it: the
    # spread is 11 cm, and one of those was 8 cm short of the trigger value and
    # never fired at all.  "The line is gone" is a physical fact the sensor
    # reports directly.
    #
    # The distance is still needed, as a gate: a line loss very early means the
    # car veered off sideways, not that it arrived.  start_to_junction_1_cm is
    # the minimum travel before a loss counts as J1, and
    # junction_1_max_travel_cm is the fallback for a track where the line never
    # ends -- without it the route would sit in START_TO_JUNCTION_1 forever.
    start_to_junction_1_cm: int = 50
    junction_1_max_travel_cm: int = 130
    poll_period_s: float = 0.05
    # Raised 35 -> 45 on 2026-09-15 (operator: "前进速度在pid的范围内安全提高点").
    #
    # "Within the PID's range" is the constraint, and it was measured rather than
    # guessed.  Across min250e's 626 PID-driven samples the correction sat at the
    # lateral_limit twice and the yaw_limit once -- rare, but p95 line_error was
    # 0.54, i.e. kp*0.54 = 18.9 against a limit of 20.  So the 95th-percentile
    # correction was already within 5% of the clamp, and +29% of forward speed
    # would push it to ~24, straight through it.  The limits below therefore rise
    # with the speed (see lateral_limit/yaw_limit) rather than the speed rising
    # into a clamp that cannot answer it.
    #
    # If the car starts oscillating or leaving the line on the long legs, the
    # limits are the lever, not this number.
    forward_speed: int = 55
    reverse_speed: int = -30
    # The approach out of J3 to each pickup area.  Raised from 15 to the normal
    # forward speed on 2026-09-15 (operator: "把 j3 到取物区的速度提高到正常的
    # 别是 15 的安全值了").
    #
    # The 15 belonged to the era when arrival was inferred from the wheels
    # stalling -- a crawl made that reading easier to catch.  Arrival is now the
    # odometer gate against the operator's tape measurement (_reached_gate), and
    # a distance gate does not care how fast the car got there.  The wall contact
    # is only the backstop.  The car has a bumper and is undamaged at any speed
    # (operator, 2026-09-15), so there was nothing left for the slow value to buy.
    pickup_speed: int = 35
    # Line PID.  Raised on 2026-09-14 after the field measurements showed the old
    # values left the chassis with no usable steering authority: a one-probe
    # offset (error 0.143) with kp=8 produced vy=+-1, i.e. about 0.54 cm/s of
    # lateral speed against 15.5 cm/s of forward speed, and the lateral_limit of
    # 8 capped the whole correction at 4.3 cm/s.  A car that drifts faster than
    # it can steer cannot hold a line, and the operator's report was exactly
    # that: "it went crooked to the right while running".
    #
    # The integral was equally dead: ki=0.3 against integral_limit=2.0 could
    # contribute at most 0.6, yet a car pulling steadily to one side is a
    # constant bias, and only the integral term can cancel it.
    kp: float = 35.0
    ki: float = 1.0
    kd: float = 1.2
    integral_limit: float = 5.0
    # Raised with forward_speed 35 -> 45 on 2026-09-15, so the clamp keeps
    # biting at the same line_error it did before the speed went up.  Measured
    # on min250e: 2 of 626 samples pinned at lateral_limit=20, 1 at
    # yaw_limit=12.  Scaling both by the same ~30% as the speed leaves the
    # authority where it was relative to the error the car now generates.
    lateral_limit: int = 32
    yaw_limit: int = 18
    # Turn angle magnitude, shared by every in-place rotation on the route.
    turn_deg: int = 90
    # Raised 30 -> 40 on 2026-09-15 (operator: "加快左移右移 左旋右旋 倒车的速度").
    # D is open loop and self-terminating, so this is the one motion on the route
    # whose overshoot grows directly with speed -- watch the post-turn poses,
    # especially the 180 into the build leg.
    turn_speed: int = 40
    # Which way a POSITIVE D rotate_deg turns the car.
    #
    # MEASURED 2026-09-15 with _pi_turn_probe.py: `D 0 0 90 30` turned the car
    # exactly 90 degrees to the LEFT, confirmed by the operator by eye, and the
    # encoder yaw projection agreed -- all four wheels advanced together
    # (LF +3341 RF +3364 LR +3329 RR +3316) while the forward and lateral
    # projections stayed at +2.5 and +15 counts, i.e. a clean in-place rotation.
    #
    #     +1   a positive rotate_deg turns LEFT     <-- this chassis
    #     -1   a positive rotate_deg turns RIGHT
    #
    # So the route's own plan was right and the older auto_route_v1 spec
    # (docs/superpowers/specs/2026-09-09-auto-route-v1-design.md), which says a
    # positive value turns RIGHT, is wrong.
    #
    # It is deliberately a single multiplier so that if a future chassis or a
    # firmware change inverts it, one value fixes every rotation at once -- both
    # existing turns and both new ones.
    turn_sign: int = 1
    # Yaw encoder counts per degree, for checking a commanded rotation without
    # watching it.  Derived from the same measurement: 3338 counts / 90 deg.
    # Implies the wheelbase half-diagonal sum (a + b) is about 37.3 cm, which is
    # why the probe's first guess of 8-18 cm was wrong -- this is a large chassis.
    # Informational only; nothing in the state machine reads it.
    yaw_counts_per_deg: float = 37.1
    # J1 -> J2: strafe right until AprilTag 2 is seen, turn right, then follow
    # the line to J2 and turn left.
    #
    # This replaces a 3 m open-loop lateral shift.  Two things killed that: the
    # car accumulates yaw over 3 m and the encoders cannot see it (mecanum
    # rollers slip sideways), and the stop criterion depended on the mask
    # sequence 10000000 -> 00000000 -> 00000001, which breaks as soon as the car
    # is crooked -- the all-black area then reads 00000001 / 00000011 and trips
    # early, stopping the car mid-track (operator, 2026-09-15).
    #
    # The strafe is now SHORT and terminated by an absolute landmark, and the
    # segment that follows is closed loop on the line.  That is the whole point:
    # no individual move has to be accurate any more, because the line follower
    # absorbs the error of the turn before it.
    #
    # Why V and not D for the strafe: D is self-terminating and open loop, and
    # whether STOP interrupts a D in flight is UNVERIFIED (see route_v2.md
    # section 4.4).  A landmark-terminated strafe has to stop on the tick the tag
    # is seen, so it must be a held velocity.  V's lateral sign IS verified:
    # V 0 +20 0 strafes left, V 0 -20 0 strafes right, so the state signs
    # tag2_strafe_speed negative.  Chopping D into chunks does not work either --
    # the runner dedups D sends on (state, forward, right, rotate, speed), which
    # is identical for every chunk, so chunk 2 would never be sent.
    #
    # The odometer window is a guard, not the arrival test.  Tag 2 only enters
    # the camera's view after the car has moved: the camera looks along the nose
    # (extrinsics rotation 0) with roughly a 60 degree horizontal field, and from
    # the J1 pose tag 2 is not in it at all.  How far is a question of field
    # geometry, not a distance calibration, so both bounds are placeholders until
    # measured.
    #
    # Note the odometer is only sampled about every 0.3 s during the strafe (the
    # ENC/SPD query alternates at 0.15 s and is skipped on ticks that issue a V),
    # which at vy=20 is about 3 cm between samples -- so both bounds need that
    # much margin.
    tag2_search_min_cm: int = 40
    tag2_search_max_cm: int = 90
    # A positive magnitude; the strafe state signs it negative for rightward.
    #
    # 40, raised from 20 on 2026-09-15 (operator: "对于后退和左右横移 速度可以
    # 提高点").  This strafe is the one lateral move on the route that is NOT a
    # seek: it is terminated by the camera seeing the tag, not by the bar
    # finding a line, so the sampling argument above does not apply and the
    # 55 cm it covers is dead time either way.  It does coarsen the odometer:
    # the ~0.3 s sample gap becomes ~4.8 cm instead of ~2.4 cm, and
    # tag2_search_min_cm/max_cm are the window it has to stop inside.
    #
    # Raised again 40 -> 50 on 2026-09-15 (operator: "加快左移右移").  The
    # sampling cost is paid again with it: the ~0.3 s gap is now ~6 cm, against
    # a 50 cm window (tag2_search_min_cm 40 .. tag2_search_max_cm 90), so the
    # window still holds several samples.  This is the ceiling for that reason --
    # past here the window is under ten samples wide.
    tag2_strafe_speed: int = 50
    # After the right turn the car is sitting on the all-black area, NOT on a
    # line -- the bar reads 00000000.  Measured 2026-09-15: the mask went
    # 10000000 -> 00000000 forty centimetres in and never changed again for the
    # whole 55 cm strafe.  So the turn cannot hand straight over to the line
    # follower; the car has to strafe LEFT until a line appears under the bar.
    #
    # The readings that count as "back on the line", per the operator -- the
    # first cut, restored unchanged on 2026-09-15 after a day on a pattern.
    #
    #     0x81  10000001  x1 and x8 white, the middle six on black -- centred
    #     0x80  10000000  x1 white, x2..x8 on black
    #     0x01  00000001  x1..x7 on black, x8 white
    #
    # 0x80 and 0x01 are mirror images: seven probes on black with the white one
    # at either end.  Together the three cover "the bar is on the black, with at
    # least one probe off it".
    #
    # A whitelist rather than "anything that is not all-black": all-black is
    # where the car can start, so that would declare success on the first tick.
    #
    # WHY THE PATTERN WAS WITHDRAWN -- do not re-try this without reading it.
    # A don't-care pattern (1x0000x1: x1 and x8 white, x3..x6 black) was going
    # to widen the acceptance window and to exclude a black region by
    # construction, since a region wider than the bar turns both ends black.
    # It CANNOT FIRE HERE, and the disproof needs no geometry -- only the masks
    # that were actually recorded.  The car approaches the line by STRAFING, so
    # the black enters at x1 and grows rightward: x1 is black from the first
    # probe step of the approach and never returns to white.  Replaying the run
    # that WORKED, tick for tick:
    #
    #     11111111 x18 -> 00111111 -> 00011111 -> 00001111 -> 00000011 -> EXIT
    #
    # x1 is 0 in every one of those and the pattern matches none of them, so the
    # car would have strafed its whole ceiling and stopped.  The pattern
    # describes the reading a car has once it is ALREADY sitting on the line;
    # a strafing car is never in that pose, it comes up onto the line from one
    # side.
    #
    # The supporting geometry was wrong too, and in a way worth recording: any
    # argument of the form "a 50 mm line on a 70 mm bar can cover at most N
    # probes" is invalid, because the bar is mounted at a HEIGHT above the
    # surface.  Each probe's spot is a disc, not a point, with soft edges, so
    # the black it reports is wider than the tape beneath it.  7 probes on black
    # being the commonest reading on a straight (50% of START_TO_JUNCTION_1) is
    # that effect, not a wider tape.  Do not re-derive a one-dimensional
    # interval model from probe pitch.
    seek_line_masks: tuple[int, ...] = (0x81, 0x80, 0x01)
    # Relaxed acceptance, added 2026-09-16 on operator instruction: "丢线后出现黑
    # 就判定找到线了".
    #
    # The exact masks above all need 7 of 8 probes on the black, which is what the
    # bar reported when the route was tuned (7 probes was the commonest reading on
    # a straight).  On 2026-09-16 the same bar read a much NARROWER black -- the
    # car sat centred on the start line with a symmetric 11000011, i.e. only 4
    # probes on the tape, and the straight ran at 4-5 where it used to run at 7.
    # The seek then swept the full +-38 cm without ever producing a whitelist
    # mask, gave up and held -- recorded in logs/full_20260916_121302.jsonl.
    #
    # With this on, a seek that has seen line loss (0xFF, no black anywhere) ends
    # at the first reading with black under the bar again.  0x00 is still refused,
    # and that exclusion is what keeps the old guard: the car can enter a seek
    # sitting on the wide all-black area right of J1, and accepting 0x00 would
    # declare success on the first tick.  Cost of the exclusion is small -- on the
    # 2026-09-16 run the first black frame was 0x00 for 4 frames (~0.5 s) before
    # the bar settled on the line reading.
    #
    # This does NOT relax seek_line_masks itself; the three exact readings are
    # still accepted unconditionally, so nothing that used to terminate a seek
    # early stops doing so.
    seek_line_accept_black_after_loss: bool = True
    # ...but "black" cannot mean "one probe flickered", and the recording says so.
    #
    # Replaying the 2026-09-16 sweep tick for tick through the rule as literally
    # stated ("any black after a loss") ends the seek at t+1.68 s on 0x9F: TWO
    # probes black, gone three frames later.  The sweep crosses several of these
    # -- 0x9F/0xC7/0xF9 at t+1.68-1.94 s and 0xFE/0xF9/0xF3/0xE7/0xCF/0x9F/0x3F
    # at t+4.10-4.99 s -- and they are thin features being crossed, not a line
    # coming under the bar: their black never reaches 4 probes and wanders from
    # frame to frame.
    #
    # The black the car actually settled on, at t+19.6 s and after, is a
    # different animal: 0x07/0x0F/0x83/0xC1/0xC3, four to five probes, held
    # continuously for over a hundred frames.  4 separates the two populations
    # exactly on that recording -- every crossing peaks at 3, every real reading
    # starts at 4.
    #
    # 4 is also the honest reading of "the bar is on the line" in the regime that
    # made this necessary at all: centred on the line the 2026-09-16 bar read
    # 0xC3, i.e. 4 probes.  A threshold any higher would refuse the very reading
    # the counterexample is made of.
    seek_line_min_black_probes: int = 4
    # The reading must also hold still.  The crossings above last one to three
    # frames and the real find holds for hundreds, so this is cheap insurance
    # against a single bad frame rather than a discriminator in its own right --
    # 2 frames is 0.12 s at poll_period_s.
    seek_line_confirm_frames: int = 2
    # Which way each post-turn seek strafes, as a signed lateral velocity.
    # POSITIVE IS LEFT -- measured on 2026-09-15 (V 0 +20 0 strafes left), so the
    # sign is not a guess the way the D command's lateral field was.
    #
    # The seeks are mirrored, which is the whole reason this is a per-seek value
    # rather than one shared magnitude: after the RIGHT turn at tag 2 the line is
    # to the car's LEFT; after the LEFT turn at J2 it is to its RIGHT; and after
    # the LEFT turn at J3 it is to its RIGHT again.  The last two turn the same
    # way but the line lands on the same side twice -- J3 and J2 are different
    # junctions, so the coincidence is not a rule.  All three were confirmed by
    # the operator on the track, J3 on 2026-09-15.
    # Raised from +-20 to +-30 on 2026-09-15 (operator: "对于后退和左右横移 速度
    # 可以提高点"), as a deliberate compromise.  The +-40 tier it avoided is the
    # one the 2x-speed runs used, where it broke the seeks -- see the note on
    # _seek_line.  +-20 is 16 successes out of 16 across four completed runs.
    #
    # Raised again to +-40 on 2026-09-16 (operator: "提到 ±40").  What changed
    # is the fallback: at +-40 on 2026-09-15 a miss was terminal (J3 died at
    # 42.0 cm, the pickup seek at 44.2 cm), whereas a first sweep that overshoots
    # the line now reverses and re-crosses it at seek_line_fallback_speed.  The
    # fast sweep is worth its higher miss rate only because the second phase
    # exists to pay for it.  On min250e at +-30 both seeks hit first pass, so
    # there is headroom here that was not there before.
    junction_2_seek_line_vy: int = 40      # after the tag-2 right turn: line is LEFT
    junction_3_seek_line_vy: int = -40     # after the J2 left turn:     line is RIGHT
    pickup_seek_line_vy: int = 40          # after the J3 left turn:     line is LEFT
    # Shared bounds.  A line that is never found must stop the car, not send it
    # hunting, so each seek has both a distance and a time ceiling.
    #
    # 35, not 60.  Measured 2026-09-15: on both the working and the failing run
    # the black appeared at a lateral 14.7-15.6 cm and the bar was on the line by
    # 20.2 cm.  There is no case in the data where the line is further out, so 60
    # was 40 cm of pure blind travel -- which is exactly the distance the failing
    # run spent driving deeper into J4.  At the measured 0.92 cm/tick, 35 cm is
    # ~15 s of strafe, inside the timeout below.
    #
    # This is now the bound for ONE SWEEP, not for the whole hunt: a sweep that
    # runs out reverses and covers another 35 cm the other way, for 70 cm and
    # ~4.4 s at the fallback speed.  The bound still has to stay tight -- it is
    # what stops the first sweep driving into whatever is out there -- but it is
    # no longer the point at which the hunt gives up.
    seek_line_max_cm: int = 35
    # The return sweep's speed, as a magnitude; the direction is always the
    # opposite of whichever way the first sweep went, and the phase is owned by
    # _seek_line.  Slow ON PURPOSE, and that is the mechanism, not caution: the
    # measured failure is a fast sweep crossing the line without the bar ever
    # registering it, so a return sweep at the same speed would re-cross it the
    # same way.  At +-20 the same ground is sampled twice as densely as at +-40.
    seek_line_fallback_speed: int = 20
    # Covers BOTH sweeps, so it had to grow when the second phase was added --
    # at 20.0 the timeout, not the bound, would have been what ended the return
    # sweep in the slow case, silently defeating the whole fallback.
    #
    # The arithmetic, from the two measured rates.  Typical: the seeks that
    # succeeded did so at ~7 cm/s lateral (fullrun 25.4 cm / 3.64 s, fullrun3
    # 27.9 cm / 3.79 s), so 35 cm + 70 cm is ~13 s.  Worst observed: stable2's
    # J3 seek crawled 41.1 cm in 13.13 s, i.e. 3.1 cm/s, and the same shape
    # would need ~33 s for the full 105 cm.  45 covers that with ~35% margin.
    #
    # It is safe to be generous because the distance bounds, not this, are what
    # stop the car: the sweep cannot travel more than 2 x seek_line_max_cm no
    # matter how long it runs.  This only catches a sweep that is not progressing
    # at all.
    seek_line_timeout_s: float = 45.0
    # The east leg is line following to J2, which is an L-corner: the line turns
    # and the bar loses it.  So this ends the same way J1 does -- sustained
    # 11111111 gated by an odometer range -- and NOT on junction_mask.  Keying it
    # on 0x80 would reproduce exactly the failure this change exists to remove.
    junction_2_line_loss_gate_cm: int = 20
    # The backstop for that leg, NOT its arrival test.  The 2026-09-15 field run
    # ended the leg on this ceiling at 192 cm with the line never once lost --
    # i.e. the car was still on a straight and had not reached the corner at all
    # -- so it has to sit far enough out that a real corner is reached first.
    # The segment it replaced was 3 m of open-loop strafe, so the east leg
    # cannot plausibly be longer than that: 400 cm is a runaway net, nothing else.
    junction_2_max_travel_cm: int = 400
    # Reversing back onto the line at J2, before the left turn.  The car comes to
    # rest past the L-corner -- line-loss debounce plus braking -- so turning in
    # place from there would pivot about a point beyond the corner.  These bounds
    # are separate from the J1 creep's because the overshoot is a different
    # quantity: at J1 the car overshoots a junction reading it can still see,
    # while here it has driven clean off the end of the line.  UNMEASURED -- the
    # first run to reach this state had not been made when these were written.
    junction_2_back_max_reverse_cm: float = 30.0
    junction_2_back_timeout_s: float = 8.0
    # 2026-09-17 field decision (operator's instruction, four runs at the venue).
    #
    # What the reverse accepts was `seek_line_masks` -- three narrow patterns
    # (0x81 / 0x80 / 0x01) that mean "the bar is on the line and centred".  That
    # is the right test for the post-turn SEEK, which starts beside the line and
    # strafes onto it.  It is the wrong test here, because this state is entered
    # on SUSTAINED line loss and then reverses: the bar arrives on the line from
    # the end and from an unknown lateral offset, so what it actually reads on
    # arrival is whatever slice of the line happens to land under it.  Measured:
    # three runs reported a constant 0x00 for the whole manoeuvre and one
    # reported 0xC0, none of them in the set -- so the reverse never recognised
    # the line, ran out junction_2_back_max_reverse_cm and held STOP for the rest
    # of the run, while the operator watched the bar arrive on the line.
    #
    # The rule the operator set: after the line has genuinely gone (which is what
    # entered this state), ANY probe seeing black means the line is back under
    # the bar.  So the reverse accepts every mask except 0xFF -- and 0xFF is
    # excluded structurally, not by taste: it is the reading that put the car
    # here, so accepting it would declare arrival on the first tick.
    #
    # Set false to restore the narrow set.  J1's creep is unaffected either way:
    # its mark is the junction pattern, which is a genuine exact reading.
    junction_2_back_accept_any_black: bool = True
    # J2 -> J3, and J3 is a T-junction: the line carries straight on and a
    # branch leaves to the left.  That removes BOTH arrival signals this route
    # uses everywhere else.
    #
    #   sustained 11111111   impossible -- the line never stops.  Measured
    #                        2026-09-15: 573 cm past J3 with no line loss.
    #   junction_mask 0x80   this is exactly what J1 was taken OFF, because a
    #                        car one probe off the line with a little yaw reads
    #                        it on a dead straight.  Same field day, same
    #                        failure: the J3 leg tripped it at 124.9 cm with
    #                        line_error 0.14, mid straight, purely from the
    #                        follower's own wobble.
    #
    # Nor does the junction itself have a signature to key on.  The probe run
    # crossed it at travel 81 cm and the bar read 11000000 / 00000001 /
    # 00000011 / 00000111 -- the same readings the wobble produces either side.
    # Demanding 00000000 instead is worse, not better: 10000000 and 00000001 are
    # already SEVEN probes black, so all-black rests the whole judgement on a
    # one-probe margin.
    #
    # So this is the odometer's good case, and the one the rest of the route is
    # built to create: ONE straight, no turns, entered from a sensor-confirmed
    # pose (the seek that precedes it ends with 10000001 under the bar).  Tape
    # measured ~2.1 m on 2026-09-15, and the car held that leg to about 2 cm of
    # lateral drift over 422 cm, so it arrives.  J3 only sets the origin of the
    # 80 cm that follows, so a few cm of odometer error is absorbed rather than
    # accumulated.
    #
    # The odometer reads ~3.5% SHORT (58.8 counts/cm came from commanded D
    # distances, which overshoot), and that direction is the safe one: the
    # trigger fires slightly early, so the fixed 80 cm lands slightly short of
    # the true mark rather than past it.
    junction_3_distance_cm: float = 210.0
    # A D said to be self-terminating that never returns DONE means the link or
    # the firmware is gone; without a ceiling the state would hang for ever.
    turn_timeout_s: float = 15.0
    # Re-finding the junction after the stop overshoots it.  Three debounce
    # frames plus braking at ~15 cm/s carry the car several centimetres past J1,
    # so the bar ends up on bare track reading 11111111 -- measured 2026-09-14
    # and confirmed by eye ("stopped cleanly, but not on 10000000").  The route
    # therefore creeps backwards at a slow speed until the junction pattern is
    # under the bar again.  Closed loop on the sensor, so unlike a distance it
    # cannot drift with battery voltage or surface friction.
    # -24, raised from -12 on 2026-09-15 (operator: "对于后退和左右横移 速度可以
    # 提高点").  This is the ONLY reverse speed the route actually uses: it drives
    # the J1/J2 creeps back onto the line and both reverses back to J3.  The
    # `reverse_speed` field below is validated but never commanded -- see the
    # note there.
    #
    # Measured against the legs it drives, at -12: the reverse from the first
    # area ran 21.2 s and 103.8 cm, and the J3 approach creeps took 4-5 s.  At
    # -24 the first return takes 9.3 s (measured, seek3_20260915.jsonl).
    #
    # The thing to watch is any arrival criterion that reads the sensor while
    # reversing: the faster the creep, the more centimetres each frame covers,
    # so a criterion that fires on "the bar slipped off the line" fires that much
    # further along.  The second area's return used to be exactly that (it
    # declared arrival 2.77 cm in, on stable2) and is now a fixed distance with
    # no sensor at all -- see pickup_2_return_reverse_cm.  The first area's
    # still watches for 0xFF, and is the remaining one to watch.
    # Raised again -24 -> -32 on 2026-09-16 (operator: "加快...倒车的速度").
    #
    # This is where the warning below bites hardest: the first area's return and
    # both J1/J2 creeps fire on a SENSOR reading while reversing, and each 50 ms
    # frame now covers ~0.65 cm instead of ~0.49 cm (at the measured 0.41 cm/s
    # per unit).  That is a 30% coarser arrival edge, not a different criterion.
    # If a reverse leg starts overshooting, this is the number to put back.
    align_speed: int = -32
    align_confirm_frames: int = 2
    align_timeout_s: float = 8.0
    # A second reading that also counts as "back on J1" during the creep.
    #
    # Measured 2026-09-15: on one run the creep saw 11111111 -> 208 -> 00000000
    # and then stayed all-black for 5 cm, never producing 10000000 at all, so a
    # criterion that only accepts the junction mask ran the car 12.24 cm past
    # the junction before giving up.  On the runs that did work the creep saw
    # 254 -> 240 -> 192 -> 128 and never showed 0x00, so accepting it as well
    # costs nothing there.
    #
    # 0x00 is the most common reading on this track -- the strafe measurements
    # put 200 cm of continuous all-black immediately right of J1 -- so on its
    # own it is far too easy to hit.  It is only safe in combination with
    # align_max_reverse_cm, which bounds how far the creep may travel.
    align_alt_mask: int = 0x00
    align_max_reverse_cm: float = 8.0
    # Second phase of the J1 alignment, added 2026-09-16 on operator
    # instruction: "后退找不到全黑就往前找".
    #
    # The reverse phase assumes the car came to rest PAST J1, so the mark is
    # behind it.  When that assumption is wrong -- the arrival test is "sustained
    # line loss past a 50 cm gate", and a spurious loss leaves the car SHORT of
    # the junction -- reversing moves away from the mark for the whole bound and
    # the old behaviour was to latch _align_failed and hold.  This drives forward
    # instead and looks for the same two readings.
    #
    # 0 disables the phase, which is what J2's creep (`JUNCTION_2_BACK_TO_LINE`)
    # passes: there the car really did drive off the end of the line at the
    # L-corner, so the mark is unambiguously behind it and searching forward
    # would head into the corner it just overshot.
    #
    # Bounded on both distance and time, for the same reason the reverse phase
    # is: a mark that cannot be found must stop the car, not send it hunting.
    # The forward bound is deliberately smaller than the reverse one -- 20 cm
    # against 8 cm of reverse is the asymmetry the operator asked for, since the
    # failure being covered is "stopped short", which is a small error, not
    # "stopped miles past".
    align_forward_cm: float = 20.0
    align_forward_speed: int = 20
    align_forward_timeout_s: float = 8.0
    pickup_wait_s: float = 3.0
    # Wall arrival: the odometer stops while the leg is still running.  See
    # EncoderContactDetector in run_route_v2.  There is no speed parameter
    # alongside it: SPD's OUT is the duty the firmware commands, not a
    # measurement, and testing it is what kept the detector silent through a
    # 62 s stall on run 6.
    #
    # ⚠️ "THE WHEELS STALL" IS NOT ALWAYS TRUE -- corrected 2026-09-15 after
    # wall2_20260915.jsonl.  Run 6 showed a stall (encoders frozen 62 s) and
    # that was generalised into "the wheels stall; it is the skid that never
    # happens".  The build leg that night did the opposite: pinned against the
    # wall with encoder_delta reading 40-200 counts per tick for ~27 s, i.e.
    # the wheels were SPINNING, and this detector -- which can only see "the
    # odometer stopped" -- stayed silent until they finally jammed.  So the
    # contact test is reliable when the wheels jam and blind when they slip,
    # and it cannot tell the two apart.  That is the argument for the distance
    # gates: a number the operator measured does not care which one happens.
    contact_stationary_s: float = 0.5
    # How far each trip actually is, measured on the track with a tape by the
    # operator (2026-09-15): J3 -> area 1 is 1.1 m, J3 -> area 2 is 90 cm.
    # These are the primary arrival criterion now, with the wall contact as the
    # backstop -- the wall is a known place, so reaching it is dead reckoning
    # against a number rather than an inference from the motors.  The three
    # approach legs have measured 101 / 120 / 147 cm at the moment the contact
    # fired, which is the spread a stall test alone has to absorb.
    # Named after the STATE that uses them, never after the operator's label:
    # the operator's numbering runs the other way round (2026-09-15 --
    # "目前这个位置是 j2 到 j3 然后左旋到取物区2"), and pairing a number with
    # the wrong leg is exactly the mistake this naming prevents.
    pickup_arrived_distance_cm: float = 90.0      # J3 -> 取物区2, first trip
    pickup_2_arrived_distance_cm: float = 110.0   # J3 -> 取物区1, second trip
    # Trip 3, the build area.  A RUNAWAY NET, not an arrival test -- arrival on
    # this leg is the wall contact and nothing else, as it was before.
    #
    # A distance gate was tried here on 2026-09-15 and withdrawn the same night.
    # The operator measured "j2到搭建区大概是60cm", the full run duly stopped at
    # 62.41 cm -- and the leg is really about **2.6 m**, so the gate parked the
    # car roughly two metres short of the build area.  The measurement was wrong,
    # not the criterion: this leg is long, and its end is the only unambiguous
    # thing on it.
    #
    # The ceiling exists because of what a mis-placed start costs on a leg with
    # no bound at all.  wall2_20260915.jsonl: put down off the line (mask frozen
    # at 11000000 for 33 s, 177 cm of lateral drift) the follower had nothing to
    # follow and the car drove 468 cm until a wheel finally jammed -- and it took
    # that long because the wheels were SPINNING against the wall (encoder_delta
    # 40-200 per tick for ~27 s), so the contact test could not see the collision.
    #
    # 400 -> 500 on 2026-09-15.
    #
    # ⚠ The REASON first given for this change was wrong and is recorded here so
    #   nobody re-derives it: the argument was that shortening the reverse above
    #   to 30 cm would make this leg ~74 cm LONGER and push it past the old 400
    #   ceiling.  It does not.  backoff_20260915.jsonl measured the leg at
    #   179.83 cm -- 147 cm SHORTER, not longer -- and the net never came close
    #   to firing.
    #
    # 500 -> 360 on 2026-09-16, which reverses the paragraph above.  The operator
    # asked for the window to be narrowed ("将右旋180度到搭建区的碰撞窗口收窄点").
    # The case for keeping the net "far past the leg" was written when the
    # arrival test was the only thing between a graze and a false arrival.  It is
    # not any more: the minimum below rejects the graze outright, so the ceiling's
    # job is now only to bound how far a car that is not on the line can run.
    #
    # 360 sits 33 cm past the measured wall (326.78 cm on stable2) -- enough that
    # a correctly-placed run cannot reach it, and 140 cm less runway than 500 gave
    # a runaway.  min250e_20260915 is what that buys: it drove 504.67 cm off the
    # line with the wheels never jamming, and under this value it would have been
    # held 145 cm earlier.
    #
    # On firing it HOLDS rather than declaring an arrival there is no evidence
    # for.  ⚠ Known and accepted (operator, 2026-09-15): holding silences the
    #   odometer, the contact detector reads that silence as a wall, and the
    #   arrival test sits BEFORE this one -- so a breach can still be declared
    #   FINISHED a tick later.  Narrowing the window is the mitigation; do not
    #   re-derive it as a fix.
    pickup_3_max_travel_cm: float = 360.0
    # How far the leg must have travelled before a wall contact may END it.
    #
    # Not the withdrawn distance gate of §7.1.13 -- that one DECLARED an arrival
    # at 60 cm and parked the car two metres short.  This one refuses to accept a
    # contact, which is the opposite failure.  Both exist because the leg's only
    # arrival signal is "the odometer stopped", and that cannot tell the build
    # wall from anything else the car can bump into on the way.
    #
    # The two ends of the range, both measured 2026-09-15:
    #
    #   179.83 cm  backoff_20260915.jsonl -- the car hit something and stopped
    #              dead here (operator: "停在半路了"), while still tracking the
    #              line: lateral stayed within 0-2.29 cm for the whole leg, so it
    #              was not lost.  This is the collision the gate has to REJECT.
    #   326.78 cm  stable2_20260915.jsonl -- the real wall, at the build area.
    #              The operator's tape agrees independently: "取物区1到搭建区
    #              大概长3.2m", and 326.78 cm is 3.2 m.
    #
    # 250 -> 300 on 2026-09-16, with the ceiling coming down to 360 in the same
    # change (operator: "将右旋180度到搭建区的碰撞窗口收窄点").  The window is now
    # [300, 360]: 27 cm of rejection margin below the measured wall and 33 cm of
    # acceptance margin above it.
    #
    # 300 rather than the 250 midpoint is the deliberate half of the narrowing.
    # The cost is stated plainly: a real wall met at 295 cm would be REFUSED and
    # the car would push into it.  The reason to accept that is min250e -- a car
    # off the line can grind a long way, and every centimetre the minimum comes
    # down is another centimetre of runway for it to do so before the arrival
    # test will believe a contact.
    #
    # ⚠ If the 179.83 cm obstruction is a REAL obstacle rather than a graze, the
    #   car will push against it.  §7.1.13 measured what that costs: the wheels
    #   SPIN (encoder_delta 40-200 per tick) rather than jamming, so the contact
    #   test goes blind and the odometer keeps counting -- meaning the car can
    #   grind its way up to the minimum and then declare an arrival on a wall that
    #   is not the build area.  Watch the first run that uses this.
    pickup_3_min_travel_cm: float = 300.0
    # States where a lost line means "hold this course", not "stop".
    #
    # Operator, 2026-09-16: "最后的长直行 单独增加逻辑 即使丢线了 也继续直行",
    # with the cause as they saw it: "因为场地问题 巡线检测最后的直行不是很稳
    # 明明在线上 但是检测一卡一卡地".
    #
    # Measured on the two runs of that day: the final leg is 42% lost frames
    # (run3 124/296, run4 138/327) and the line-following PID answers every one
    # of them with STOP -- 18 and 20 STOPs respectively.  A 3.2 m straight
    # becomes stop-start, which is what stretched it to 37-41 s and left the car
    # wandering.  The car is on the line; the detection is what is stuttering.
    #
    # Only the final leg.  The other two wall legs are 90 and 110 cm and do not
    # show it, and JUNCTION_2_TO_JUNCTION_3 must keep following the line: that
    # leg exists to absorb the previous turn's error, so a lost line there is
    # information, not noise.
    #
    # Names rather than RouteState members because route_v2.state_machine
    # imports THIS module; a tuple of strings is resolved and checked in
    # _validate below, which imports the enum lazily.
    hold_course_on_line_loss: tuple[str, ...] = ()
    # --- The three trips out of J3 (operator, 2026-09-15) --------------------
    #
    # Three legs leave J3, each ending in a wall contact; the first two return to
    # J3 and turn again for the next one.  Turns are applied to the heading the
    # car is still holding, because reversing back to J3 does not change it.
    #
    # Trip 3 needs no seek: the 180 turn puts the car straight back onto a line,
    # so it drives off under the follower with no hunt in between.
    pickup_2_seek_line_vy: int = 40        # trip 2, after the right turn: line is LEFT
    pickup_3_turn_deg: int = 180           # trip 3 turns twice as far as the others
    # Reversing back to J3 from the first area.  Operator: reuse the J1 creep with
    # the direction reversed -- reverse until the line is lost, then drive forward
    # until the all-black landmark shows.  Bounded in both directions for the same
    # reason the J1 creep is: past J1 there is no line for a wrong guess to be
    # discovered against, so a hunt that never finds its landmark must give up and
    # hold rather than keep driving.
    #
    # RAISED 60 -> 150 on 2026-09-15 (operator: "距离太小了 我需要就是一直倒车直到
    # 丢线").  Measured on j3on_20260915.jsonl: the car reversed the full 60 cm
    # without the bar ever reading all-white, stopped on this bound at 61.31 cm,
    # and held for 78 s.  The masks it saw on the way back were 00000011,
    # 10000001, 10000000 and 11000000 -- reversing RETRACES the line, so the line
    # is not lost by reversing along it.  The bound therefore has to be a runaway
    # net rather than a target: the operator's tape says J3 -> area 2 is 90 cm, so
    # 150 is that plus the whole next leg's worth of headroom.  If the line turns
    # out never to be lost even at 150, the criterion is wrong, not the bound.
    #
    # The 15 s timeout was a second, tighter limit on the same move: at the
    # measured 4.9 cm/s of align_speed -12, 15 s is only ~73 cm, so raising the
    # distance alone would have changed nothing.  It covers both phases (it is
    # measured from the state's start), so it has to fit the reverse AND the hunt:
    # 150 cm reverse is ~31 s, plus the forward leg.
    pickup_1_return_reverse_cm: float = 150.0
    pickup_1_return_forward_cm: float = 60.0
    # Reversing out of the SECOND area, which is a different manoeuvre entirely
    # since 2026-09-15: straight back a fixed 30 cm, sensor-blind, then the 180
    # turn.  Operator: "现在取物区1碰撞后不需要识别j3了 直接直线倒车30cm 然后右旋
    # 180度".
    #
    # It replaces a mask hunt for J3 (the pickup_2_return_* keys, since deleted)
    # that was measured broken in BOTH directions on the same leg -- armed and
    # 20 s too short in stable2, never armed and unbounded in seek3.  See
    # _back_off_straight for the two runs.
    #
    # 30 is the operator's number and it is a TARGET here, not a runaway net --
    # unlike pickup_1_return_reverse_cm, which is deliberately 150 against a 90 cm
    # gap.  The car only has to clear the area before turning.  Measured stop
    # accuracy on a reverse bound is good: at the 150 cm bound the car came to
    # rest 3.7 cm later (-152.53 -> -156.19, seek3_20260915.jsonl), so expect
    # ~33 cm of actual travel, not 70.
    pickup_2_return_reverse_cm: float = 30.0
    pickup_return_timeout_s: float = 60.0
    # Loop strategy placeholders.  Arm packages will replace the timed stop
    # once their action definitions are supplied.
    arm_placeholder_stop_s: float = 3.0
    j2_line_recovery_segment_cm: float = 5.0
    # Measured 2026-09-19: build area to the J2 branch is about 60 cm.  Use
    # 65 cm so the reverse clears the branch before the 180 degree left turn.
    loop_reverse_cm: float = 65.0
    # DELETED 2026-09-15: pickup_2_return_masks / _confirm_frames / _armed_mask.
    # They drove a mask hunt for J3 on the second return that was measured broken
    # in both directions -- armed and 20 s short on stable2, never armed and
    # unbounded on seek3.  `0x01` was in BOTH that mask set and seek_line_masks
    # ("this is the line" and "this is J3"), so a car reversing off a line tripped
    # the arrival test within centimetres.  The leg is now a plain 30 cm reverse
    # (pickup_2_return_reverse_cm).  Do not reintroduce a mask criterion here
    # without reading _back_off_straight first.


def load_route_v2_config(
    path: str | Path, *, require_visual_calibration: bool = False
) -> RouteV2Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    values = raw.get("route_v2", raw)
    if not isinstance(values, dict):
        raise ValueError("route_v2 must be a mapping")
    # YAML gives lists where the dataclass declares tuples.  Normalise both
    # multi-value fields here so a config loaded from a file and one built from
    # the defaults are the same object -- the validation below unpacks them, and
    # the state machine unpacks the patterns further.
    values = dict(values)
    for name in ("seek_line_masks",):
        if name in values:
            values[name] = tuple(int(mask) for mask in values[name])
    vision_raw = values.pop("vision", None)
    vision = RouteVisionConfig.from_mapping(vision_raw) if isinstance(vision_raw, dict) else None
    cfg = RouteV2Config(
        **{k: values[k] for k in RouteV2Config.__dataclass_fields__ if k in values},
        vision=vision,
    )
    if require_visual_calibration and (cfg.vision is None or not cfg.vision.calibrated):
        raise ValueError("visual route is not calibrated; run field calibration before --full")
    if not 0 <= cfg.junction_mask <= 0xFF or cfg.junction_debounce_frames < 1 or cfg.leave_debounce_frames < 1:
        raise ValueError("invalid junction detection configuration")
    if cfg.start_to_junction_1_cm <= 0:
        raise ValueError("start_to_junction_1_cm must be positive")
    if cfg.junction_1_max_travel_cm <= cfg.start_to_junction_1_cm:
        # The fallback must sit beyond the gate, or the route would declare J1
        # the instant it set off.
        raise ValueError("junction_1_max_travel_cm must exceed start_to_junction_1_cm")
    if cfg.junction_mask == 0xFF:
        # 0xFF is every probe OFF the line: a fully lost track, not a junction.
        raise ValueError("junction mask 0xFF means the car has left the track entirely")
    if cfg.forward_speed <= 0 or cfg.reverse_speed >= 0 or cfg.pickup_speed <= 0:
        raise ValueError("invalid V speeds")
    if cfg.turn_sign not in (-1, 1):
        # Single knob for an unmeasured convention: it flips every D rotation on
        # the route at once, so a value that is not a clean sign is a bug.
        raise ValueError("turn_sign must be +1 or -1; it flips every D rotation at once")
    if cfg.turn_timeout_s <= 0:
        raise ValueError("turn_timeout_s must be positive")
    if cfg.contact_stationary_s <= 0:
        raise ValueError("contact_stationary_s must be positive")
    if cfg.pickup_arrived_distance_cm <= 0 or cfg.pickup_2_arrived_distance_cm <= 0:
        # A zero would end the leg on its first tick, before the car had moved.
        raise ValueError("pickup distance gates must be positive")
    if cfg.pickup_3_max_travel_cm <= 0:
        raise ValueError("pickup_3_max_travel_cm must be positive")
    if cfg.tag2_search_min_cm < 0:
        raise ValueError("tag2_search_min_cm must not be negative")
    if cfg.tag2_search_max_cm <= cfg.tag2_search_min_cm:
        # A ceiling at or below the floor would either never open the window or
        # fire the moment it did.
        raise ValueError("tag2_search_max_cm must exceed tag2_search_min_cm")
    if not 1 <= cfg.tag2_strafe_speed <= 100:
        # A magnitude.  The state signs it negative because the J1 branch leaves
        # to the right; a negative value here would double the negation and
        # strafe left.
        raise ValueError("tag2_strafe_speed must be a magnitude between 1 and 100")
    if not cfg.seek_line_masks:
        raise ValueError("seek_line_masks must not be empty")
    for mask in cfg.seek_line_masks:
        if not 0 <= int(mask) <= 0xFF:
            raise ValueError("every seek_line_masks entry must be a byte")
        if int(mask) == 0xFF:
            # Every probe off the line is a lost track, not an arrival.
            raise ValueError("seek_line_masks may not contain 0xFF: that is line loss")
        if int(mask) == 0x00:
            # All-black is where the car can start the seek, so accepting it
            # would declare success on the first tick.
            raise ValueError("seek_line_masks may not contain 0x00")
    for name in ("junction_2_seek_line_vy", "junction_3_seek_line_vy",
                 "pickup_seek_line_vy", "pickup_2_seek_line_vy"):
        # Signed, and zero is the one value that means nothing at all: it would
        # sit in the seek state without ever moving.
        if not -100 <= getattr(cfg, name) <= 100 or getattr(cfg, name) == 0:
            raise ValueError(f"{name} must be a non-zero lateral velocity in -100..100")
    if cfg.seek_line_max_cm <= 0:
        raise ValueError("seek_line_max_cm must be positive")
    if not 1 <= cfg.seek_line_fallback_speed <= 100:
        # A magnitude, like tag2_strafe_speed: _seek_speed derives the sign from
        # the direction of the first sweep.  It must also be slow enough to be a
        # genuinely different attempt rather than a repeat of the sweep that
        # just failed -- at or above the fastest seek vy there is no point
        # having a second phase at all.
        raise ValueError("seek_line_fallback_speed must be a magnitude between 1 and 100")
    fastest_seek = max(abs(getattr(cfg, name)) for name in
                       ("junction_2_seek_line_vy", "junction_3_seek_line_vy",
                        "pickup_seek_line_vy", "pickup_2_seek_line_vy"))
    if cfg.seek_line_fallback_speed >= fastest_seek:
        raise ValueError("seek_line_fallback_speed must be slower than every "
                         "seek_line_vy; a return sweep at the same speed "
                         "re-crosses the line the same way")
    if cfg.seek_line_timeout_s <= 0:
        raise ValueError("seek_line_timeout_s must be positive")
    if cfg.junction_2_line_loss_gate_cm <= 0:
        raise ValueError("junction_2_line_loss_gate_cm must be positive")
    if cfg.junction_2_max_travel_cm <= cfg.junction_2_line_loss_gate_cm:
        raise ValueError("junction_2_max_travel_cm must exceed junction_2_line_loss_gate_cm")
    if cfg.junction_2_back_max_reverse_cm <= 0:
        raise ValueError("junction_2_back_max_reverse_cm must be positive")
    if cfg.j2_line_recovery_segment_cm <= 0:
        raise ValueError("j2_line_recovery_segment_cm must be positive")
    if cfg.junction_2_back_timeout_s <= 0:
        raise ValueError("junction_2_back_timeout_s must be positive")
    if cfg.junction_3_distance_cm <= 0:
        raise ValueError("junction_3_distance_cm must be positive")
    # The alignment creep must run backwards: a positive speed would drive the
    # car further past the junction it is trying to re-find.
    if cfg.align_speed >= 0:
        raise ValueError("align_speed must be negative; the creep runs backwards")
    if cfg.align_confirm_frames < 1:
        raise ValueError("align_confirm_frames must be positive")
    if cfg.align_timeout_s <= 0:
        raise ValueError("align_timeout_s must be positive")
    if not 0 <= cfg.align_alt_mask <= 0xFF:
        raise ValueError("align_alt_mask must be a byte")
    if cfg.align_alt_mask == 0xFF:
        # The same objection as junction_mask: every probe off the line is a
        # lost track, not an arrival.
        raise ValueError("align_alt_mask 0xFF means the car has left the track entirely")
    if cfg.align_max_reverse_cm <= 0:
        raise ValueError("align_max_reverse_cm must be positive")
    if not 1 <= cfg.seek_line_min_black_probes <= 7:
        # 8 would be all-black, which is the blob reading the relaxed test exists
        # to refuse; 0 would make every frame a candidate.
        raise ValueError("seek_line_min_black_probes must be in 1..7")
    if cfg.seek_line_confirm_frames < 1:
        raise ValueError("seek_line_confirm_frames must be positive")
    if cfg.align_forward_cm < 0:
        raise ValueError("align_forward_cm must not be negative; 0 disables the phase")
    if cfg.align_forward_cm > 0:
        # Only meaningful when the phase is on, so a config that disables it may
        # leave the speed and timeout at anything.
        if cfg.align_forward_speed <= 0:
            raise ValueError("align_forward_speed must be positive: the phase drives forward")
        if cfg.align_forward_timeout_s <= 0:
            raise ValueError("align_forward_timeout_s must be positive")
    if not 0 < cfg.pickup_3_turn_deg <= 180:
        raise ValueError("pickup_3_turn_deg must be in 0..180")
    if cfg.pickup_1_return_reverse_cm <= 0 or cfg.pickup_1_return_forward_cm <= 0:
        raise ValueError("pickup_1_return bounds must be positive")
    if cfg.pickup_return_timeout_s <= 0:
        raise ValueError("pickup_return_timeout_s must be positive")
    if cfg.pickup_2_return_reverse_cm <= 0:
        raise ValueError("pickup_2_return_reverse_cm must be positive")
    if cfg.pickup_3_min_travel_cm <= 0:
        raise ValueError("pickup_3_min_travel_cm must be positive")
    if cfg.hold_course_on_line_loss:
        # Lazy import: state_machine imports this module at module level, so
        # importing it at the top of the file would be circular.  By the time a
        # config is loaded both modules are importable.
        from .state_machine import RouteState
        known = {state.value for state in RouteState}
        for name in cfg.hold_course_on_line_loss:
            if name not in known:
                # A typo here would silently disable the hold-course behaviour
                # on the leg that needs it -- exactly the sort of failure that
                # only shows up as "the car stopped for no reason" in the field.
                raise ValueError(f"hold_course_on_line_loss names an unknown state: {name}")
    if cfg.pickup_3_min_travel_cm >= cfg.pickup_3_max_travel_cm:
        # If the minimum met the maximum the leg could never end on a contact at
        # all -- it would drive to the runaway net and hold there, on every run.
        raise ValueError("pickup_3_min_travel_cm must be below pickup_3_max_travel_cm")
    return cfg
