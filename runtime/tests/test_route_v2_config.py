import re
from pathlib import Path
from route_v2.config import load_route_v2_config


def test_new_route_defaults_match_confirmed_strategy():
    cfg = load_route_v2_config(Path(__file__).parents[1] / "config" / "route_v2.yaml")
    # 0x80 = x8, the rightmost probe, joins the black: a branch to the right.
    # Not 0x00, which is any black area wider than the 7 cm probe bar.
    assert cfg.junction_mask == 0x80
    assert cfg.reverse_speed < 0
    # The car drives into the wall at the end; that impact is the arrival signal.
    assert cfg.pickup_speed > 0
    assert cfg.pickup_wait_s == 3.0
    # Measured 2026-09-15: `D 0 0 90 30` turned the car exactly 90 degrees LEFT,
    # so a positive rotate_deg is a left turn on this chassis.
    assert cfg.turn_sign == 1
    # The readings that count as "back on the line" after the right turn.  A
    # don't-care pattern was tried here on 2026-09-15 and withdrawn the same day
    # -- see test_the_seek_accepts_all_three_line_masks and the note on
    # seek_line_masks in config.py.  Do not "widen" this to 0x83/0xC1/0xC3:
    # a strafing car comes onto the line from one side, so x1 is black from the
    # first step of the approach and those readings never occur on the way in.
    assert cfg.seek_line_masks == (0x81, 0x80, 0x01)
    # The measured tag-2 window: the tag first appeared about 49 cm right of J1
    # and was stably tracked by 54.7 cm.
    assert cfg.tag2_search_min_cm < 49 < cfg.tag2_search_max_cm


def test_the_p95_correction_still_fits_inside_the_lateral_clamp():
    """The arithmetic behind both speed rises, pinned so the next one cannot
    quietly leave the clamps behind.

    min250e measured line_error p95 = 0.54 over 626 PID samples, so kp * 0.54 is
    what the 95th-percentile error asks the lateral term for.  It has to fit
    inside lateral_limit: when it did not -- kp=8 against lateral_limit=8, the
    pair before the 2026-09-14 retune -- the car corrected slower than it
    drifted, and the field symptom was "跑的时候歪右了"."""
    cfg = load_route_v2_config(Path(__file__).parents[1] / "config" / "route_v2.yaml")
    assert cfg.kp * 0.54 <= cfg.lateral_limit, (
        "forward_speed and the PID clamps move together; see route_v2.yaml")


def test_hold_course_rejects_an_unknown_state_name(tmp_path):
    """A typo here would silently switch the behaviour off on the one leg that
    needs it, and the field symptom would be "the car stopped for no reason"."""
    source = (Path(__file__).parents[1] / "config" / "route_v2.yaml").read_text(encoding="utf-8")
    patched = tmp_path / "route_bad_hold.yaml"
    patched.write_text(
        source.replace("hold_course_on_line_loss: [JUNCTION_PICKUP_3_TO_AREA]",
                       "hold_course_on_line_loss: [JUNCTION_PICKUP_3_TO_AREA, NOPE]"),
        encoding="utf-8")
    try:
        load_route_v2_config(patched)
    except ValueError as exc:
        assert "NOPE" in str(exc)
        return
    raise AssertionError("an unknown state name in hold_course_on_line_loss must be rejected")


def test_all_black_is_rejected_as_a_line_reading(tmp_path):
    """The seek can start on the all-black area it strafed across, so 0x00 would
    declare success on the first tick; 0xFF is line loss, not an arrival."""
    source = (Path(__file__).parents[1] / "config" / "route_v2.yaml").read_text(encoding="utf-8")
    for bad in ("[0x00, 0x81]", "[0x81, 0xFF]"):
        patched = tmp_path / f"route_{bad[1:3]}.yaml"
        patched.write_text(source.replace("[0x81, 0x80, 0x01]", bad), encoding="utf-8")
        try:
            load_route_v2_config(patched)
        except ValueError:
            continue
        raise AssertionError(f"seek_line_masks {bad} should have been rejected")


def set_yaml_value(source, key, value):
    """Replace one key's value by NAME, whatever it currently is.

    These tests used to patch the YAML by literal string, e.g.
    source.replace("junction_2_seek_line_vy: 20", "...: 0").  That silently
    stopped testing anything the moment the deployed value was tuned -- the
    replace matched nothing, the config loaded unchanged, and the assertion
    that a bad value is rejected failed with a message about the bad value.
    It happened again on 2026-09-15 when the seeks went to +-30.  Keyed on the
    name, these stay honest through a re-tune.
    """
    patched, n = re.subn(rf"^(\s*{key}:\s*).*$", rf"\g<1>{value}",
                         source, count=1, flags=re.MULTILINE)
    assert n == 1, f"{key} not found in the YAML -- the test is not patching anything"
    return patched


def test_the_seek_bounds_and_directions_must_be_sane(tmp_path):
    source = (Path(__file__).parents[1] / "config" / "route_v2.yaml").read_text(encoding="utf-8")
    for key, bad in (("seek_line_max_cm", "0"),
                     ("seek_line_timeout_s", "0"),
                     # Zero vy would sit in the seek state without ever moving.
                     ("junction_2_seek_line_vy", "0"),
                     ("junction_3_seek_line_vy", "0")):
        patched = tmp_path / "route_bad.yaml"
        patched.write_text(set_yaml_value(source, key, bad), encoding="utf-8")
        try:
            load_route_v2_config(patched)
        except ValueError:
            continue
        raise AssertionError(f"{key}: {bad} should have been rejected")


def test_the_return_sweep_must_be_slower_than_every_first_sweep(tmp_path):
    """The fallback's whole value is that it is a DIFFERENT attempt.

    A return sweep at the speed that just failed re-crosses the line the same
    way -- see the note on _seek_line.  So "slower than every seek vy" is not a
    style preference, it is the property that makes the second phase worth
    having, and it has to be enforced rather than assumed.

    The rejected values track the seeks: while they sat at +-30 the fallback's
    own value of 20 was fine and 30 was not, and now that they are +-40 a
    fallback of 30 has become legal.  Deriving the threshold from the deployed
    seeks rather than hard-coding a number is deliberate -- a literal here goes
    stale the next time the seeks are tuned, which is exactly what happened on
    2026-09-15 and again on 2026-09-16.
    """
    source = (Path(__file__).parents[1] / "config" / "route_v2.yaml").read_text(encoding="utf-8")
    cfg = load_route_v2_config(Path(__file__).parents[1] / "config" / "route_v2.yaml")
    fastest = max(abs(cfg.junction_2_seek_line_vy), abs(cfg.junction_3_seek_line_vy),
                  abs(cfg.pickup_seek_line_vy), abs(cfg.pickup_2_seek_line_vy))
    # At the fastest seek's own speed, faster than it, and the two non-magnitudes.
    for bad in (str(fastest), str(fastest + 10), "0", "-20"):
        patched = tmp_path / "route_bad_fallback.yaml"
        patched.write_text(set_yaml_value(source, "seek_line_fallback_speed", bad),
                           encoding="utf-8")
        try:
            load_route_v2_config(patched)
        except ValueError:
            continue
        raise AssertionError(f"seek_line_fallback_speed: {bad} should have been rejected")


def test_the_seek_ceiling_sits_where_the_line_actually_is():
    """Measured 2026-09-15, on BOTH runs: the black appears at a lateral
    14.7-15.6 cm and the bar is on the line by 20.2 cm.  The old 60 was 40 cm of
    pure blind travel, and the failing run spent exactly that distance driving
    deeper into J4.  It has to clear 20 with margin without being a free pass."""
    cfg = load_route_v2_config(Path(__file__).parents[1] / "config" / "route_v2.yaml")
    assert 21 <= cfg.seek_line_max_cm <= 40


def test_the_east_leg_ceiling_is_a_backstop_not_the_arrival_test():
    """Field 2026-09-15: the leg ended on a 200 cm ceiling having never lost the
    line.  The car was still on a straight.  A backstop that fires before the
    corner is the arrival test, and it parks the car somewhere arbitrary."""
    cfg = load_route_v2_config(Path(__file__).parents[1] / "config" / "route_v2.yaml")
    assert cfg.junction_2_max_travel_cm >= 300


def test_the_j3_distance_must_be_positive(tmp_path):
    source = (Path(__file__).parents[1] / "config" / "route_v2.yaml").read_text(encoding="utf-8")
    patched = tmp_path / "route_bad_j3.yaml"
    patched.write_text(source.replace("junction_3_distance_cm: 210.0",
                                      "junction_3_distance_cm: 0"), encoding="utf-8")
    try:
        load_route_v2_config(patched)
    except ValueError:
        return
    raise AssertionError("junction_3_distance_cm: 0 should have been rejected")


def test_the_two_seeks_are_mirrored():
    """After the RIGHT turn at tag 2 the line is to the car's left; after the
    LEFT turn at J2 it is to its right.  Both confirmed on the track, and the
    mirroring is the whole reason the direction is per-seek config."""
    cfg = load_route_v2_config(Path(__file__).parents[1] / "config" / "route_v2.yaml")
    assert cfg.junction_2_seek_line_vy > 0, "positive vy strafes left"
    assert cfg.junction_3_seek_line_vy < 0, "negative vy strafes right"
