from rg_runtime.protocols import (
    ArmAck,
    ArmEvent,
    ArmStateReply,
    ChassisReply,
    format_arm,
    format_velocity,
    parse_arm_reply,
    parse_chassis_reply,
)


def test_formats_commands_with_crlf():
    assert format_velocity(20, -3, 0) == "V 20 -3 0\r\n"
    assert format_arm("RUN", 2) == "ARM,RUN,2\r\n"


def test_parses_chassis_ack_done_and_error():
    assert parse_chassis_reply("OK STOP") == ChassisReply(kind="ok", value="STOP")
    assert parse_chassis_reply("DONE D") == ChassisReply(kind="done", value="D")
    assert parse_chassis_reply("ERR: bad") == ChassisReply(kind="error", value="bad")
    assert parse_chassis_reply("ERR TIMEOUT POSITION") == ChassisReply(kind="error", value="TIMEOUT POSITION")
    assert parse_chassis_reply("ENC LF -12 RF 13 LR -14 RR 15") == ChassisReply(kind="encoder", value="LF -12 RF 13 LR -14 RR 15")
    assert parse_chassis_reply("SPD LF 480 RF 476 LR 482 RR 479 OUT 20 20 19 20") == ChassisReply(kind="speed", value="LF 480 RF 476 LR 482 RR 479 OUT 20 20 19 20")
    assert parse_chassis_reply("garbage") is None


def test_parses_arm_ack_state_event_and_fault():
    assert parse_arm_reply("ACK,RUN,2") == ArmAck(command="RUN", value="2")
    assert parse_arm_reply("STATE,BUSY,CAL=1,SUCTION=0,ROUTINE=2,STEP=3,RX3=10") == ArmStateReply(
        mode="BUSY", routine=2, step=3, suction=0, calibrated=1
    )
    assert parse_arm_reply("EVENT,DONE,2") == ArmEvent(name="DONE", value="2")
    assert parse_arm_reply("FAULT,BAD_ROUTINE").value == "BAD_ROUTINE"
    assert parse_arm_reply("nope") is None


def test_rejects_non_binary_arm_state_flags():
    assert parse_arm_reply("STATE,LOCKED,CAL=2,SUCTION=0,ROUTINE=255,STEP=0") is None
    assert parse_arm_reply("STATE,LOCKED,CAL=1,SUCTION=-1,ROUTINE=255,STEP=0") is None
