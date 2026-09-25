from route_v2.pickup_action import ActionCatalogExecutor, ActionPackageExecutor, CompiledActionStep


class Reply:
    def __init__(self, command):
        self.command = command


class Arm:
    def __init__(self):
        self.sent = []
        self.replies = []

    def servo(self, servo_id, position, time_ms):
        self.sent.append(("SERVO", servo_id, position, time_ms))

    def suction(self, enabled):
        self.sent.append(("SUCTION", enabled))

    def poll(self):
        replies, self.replies = self.replies, []
        return replies


def test_suction_stays_on_after_pickup_action_finishes_until_explicit_release():
    arm = Arm()
    pickup = ActionPackageExecutor(
        (CompiledActionStep(kind="suction", enabled=True),), arm
    )
    pickup.step(now=0.0, stop_acknowledged=True)
    arm.replies.append(Reply("SUCTION"))
    assert pickup.step(now=0.1, stop_acknowledged=True).done
    assert pickup.suction_enabled is True

    release = ActionPackageExecutor(
        (CompiledActionStep(kind="suction", enabled=False),), arm
    )
    release.step(now=1.0, stop_acknowledged=True)
    arm.replies.append(Reply("SUCTION"))
    assert release.step(now=1.1, stop_acknowledged=True).done
    assert release.suction_enabled is False
    assert arm.sent == [("SUCTION", True), ("SUCTION", False)]


def test_catalog_switch_does_not_release_suction_without_a_false_step():
    arm = Arm()
    catalog = ActionCatalogExecutor(
        {
            "PICK": (CompiledActionStep(kind="suction", enabled=True),),
            "MOVE_WHILE_HOLDING": (CompiledActionStep(kind="servo", servo_id=1, position=1500, time_ms=100),),
        },
        arm,
    )
    catalog.set_action("PICK")
    catalog.step(now=0.0, stop_acknowledged=True)
    arm.replies.append(Reply("SUCTION"))
    catalog.step(now=0.1, stop_acknowledged=True)
    catalog.set_action("MOVE_WHILE_HOLDING")
    catalog.step(now=1.0, stop_acknowledged=True)

    assert arm.sent[:2] == [("SUCTION", True), ("SERVO", 1, 1500, 100)]
