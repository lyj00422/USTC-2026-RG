from control_hub.services.alignment_controller import AlignmentController, AlignmentState


class Chassis:
    def __init__(self): self.commands = []
    def run_distance(self, f, r, a, s): self.commands.append((f, r, a, s))
    def stop(self): self.commands.append("STOP")

class Arm:
    def __init__(self): self.actions = []
    def run(self, action): self.actions.append(action)

def test_alignment_moves_then_runs_bound_action_after_stable_frames():
    chassis, arm = Chassis(), Arm()
    controller = AlignmentController(chassis, arm, stable_frames=2, step_cm=2, max_travel_cm=10)
    zone = {"rect": {"x": 40, "y": 0, "width": 20, "height": 100}, "color": "purple", "action_id": 3}
    controller.start(zone)
    controller.tick({"color": "purple", "center_px": [20, 50]})
    assert chassis.commands[-1] == (0, 2, 0, 15)
    controller.tick({"color": "purple", "center_px": [50, 50]})
    result = controller.tick({"color": "purple", "center_px": [51, 50]})
    assert result["state"] == AlignmentState.RUNNING_ACTION.value
    assert chassis.commands[-1] == "STOP"
    assert arm.actions == [3]
