"""READ-ONLY: reproduce the route's vision start-up without the chassis.

Round 2 of the purple bench test died at t=0.54 s with `camera_fault`, because
the vision worker produced exactly ONE result (frame 1) and then nothing.
Round 1 started identically and kept producing.  This builds the same
RouteVisionRuntime the route builds, ticks observe() for a few seconds, and
prints what the runner would have seen -- plus whether the worker and capture
threads are actually alive.

Opens the camera and nothing else.  No serial port, no chassis command.
"""
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

from pathlib import Path  # noqa: E402

ROOT = Path("/home/pi/robogame-runtime")
SECONDS = 3.0


def main():
    import cv2
    from rg_runtime.apriltag import AprilTagDetector
    from rg_runtime.config import load_camera_config
    from route_v2.config import load_route_v2_config
    from route_v2.state_machine import RouteState
    from run_route_v2 import RouteVisionRuntime, _configure_camera

    config = load_route_v2_config(str(ROOT / "config/route_v2.yaml"))
    camera_config = load_camera_config(ROOT / "config/camera_config.yaml")
    camera = cv2.VideoCapture(camera_config.camera)
    if not camera.isOpened():
        print("camera did not open")
        return 1
    for warning in _configure_camera(camera, camera_config, cv2):
        print(f"WARNING: {warning}")
    detector = AprilTagDetector(camera_config)
    runtime = RouteVisionRuntime(config, camera, detector)

    started = time.monotonic()
    print(f"{'t':>6} {'cam.fresh':>9} {'cam.fid':>7} {'cam.age':>8} "
          f"{'vis.fresh':>9} {'vis.rfid':>8} {'vis.pend':>8} {'vis.rdy':>7} "
          f"{'fault_reason':>20} {'wk.run':>6} {'wk.err':>8} "
          f"{'wk.fid':>6} {'wk.gen':>6} {'cam_thr':>7} {'vis_thr':>7} {'phase':<14} {'reason'}")
    try:
        while True:
            now = time.monotonic()
            if now - started > SECONDS:
                break
            observed, diagnostics = runtime.observe(
                state=RouteState.PICKUP_VISION_ONLY,
                now=now,
                absolute_lateral_cm=0.0,
                wall_contact=False,
                stopped=True,
                sensor_mask=192,
            )
            cam = diagnostics.get("camera", {})
            vis = diagnostics.get("vision", {})
            status = runtime.worker.status()
            capture_thread = runtime.capture._thread
            worker_thread = runtime.worker._thread
            print(f"{now - started:6.2f} {str(cam.get('fresh')):>9} {str(cam.get('frame_id')):>7} "
                  f"{str(cam.get('age_s')):>8} {str(vis.get('fresh')):>9} "
                  f"{str(vis.get('result_frame_id')):>8} {str(vis.get('pending')):>8} "
                  f"{str(vis.get('ready')):>7} {str(vis.get('fault_reason')):>20} "
                  f"{str(status.running):>6} "
                  f"{str(status.error)[:8]:>8} {str(status.frame_id):>6} "
                  f"{str(status.generation):>6} "
                  f"{str(capture_thread is not None and capture_thread.is_alive()):>7} "
                  f"{str(worker_thread is not None and worker_thread.is_alive()):>7} "
                  f"{str(observed.pickup_kind):<14} {str(diagnostics.get('pickup_reason'))}"
                  f"  camera_fault={observed.camera_fault}")
            time.sleep(0.05)
    finally:
        runtime.close()
        camera.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
