"""Static contract for the console pages.

The operator console is one frontend: /operate gathers the camera, the arm, the
chassis and the line telemetry.  /arm, /camera and /chassis are kept only as
redirects so old bookmarks keep working.
"""

from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_hub_home_links_the_operator_console_and_the_log_viewer():
    html = read("index.html")
    assert 'href="/operate"' in html
    assert 'href="/logs"' in html
    assert 'data-global-stop' in html
    # The deprecated pages must not be advertised as destinations any more.
    assert 'href="/arm"' not in html
    assert 'href="/camera"' not in html
    assert 'href="/chassis"' not in html


def test_deprecated_pages_redirect_to_the_operator_console():
    for name in ("arm.html", "camera.html", "chassis.html"):
        html = read(name)
        assert 'http-equiv="refresh"' in html, name
        assert 'content="0; url=/operate"' in html, name


def test_operator_page_owns_the_camera_surface():
    html = read("operate.html")
    js = read("operate.js")
    assert 'data-operate-action="camera-start" type="button" disabled' in html
    assert 'data-operate-action="camera-stop"' in html
    assert 'data-operate-action="record-start"' in html
    assert 'data-operate-action="record-stop"' in html
    assert 'data-operate-action="snapshot"' in html
    assert 'id="operate-record-label"' in html
    assert "/api/camera/snapshot/latest" in html
    assert 'data-stream-src="/api/camera/stream.mjpg"' in html
    assert 'aria-label="摄像头实时画面"' in html
    for endpoint in ("/api/camera/record/start", "/api/camera/record/stop", "/api/camera/snapshot"):
        assert endpoint in js


def test_arm_panel_runs_base_last_with_speed_above_every_axis():
    html = read("operate.html")
    assert html.count('data-servo-axis="') == 5
    assert 'id="operate-servo-time"' in html
    # Bottom of the arm is the base, so the list reads top-to-bottom as
    # end-effector -> base, and the shared speed field sits above all of them.
    positions = [html.index(f'data-servo-axis="{axis}"') for axis in (4, 3, 2, 1, 0)]
    assert positions == sorted(positions)
    assert html.index('id="operate-servo-time"') < min(positions)
    # Save is the last arm control.
    assert max(positions) < html.index('data-operate-action="record-toggle"')
    assert html.count('data-operate-action="arm-run"') == 6


def test_save_flow_starts_on_first_press_and_names_on_the_second():
    html = read("operate.html")
    js = read("operate.js")
    assert "开始保存" in html
    assert "结束并命名" in js
    assert "record-toggle" in html
    assert 'data-operate-action="draft-name"' in html
    assert 'data-operate-action="draft-discard"' in html
    assert "window.prompt" in js
    assert "/api/arm/actions/confirm" in js
    assert "/api/arm/actions/discard" in js
    assert "/api/arm/recording" in js


def test_operator_page_exports_action_packages_and_history():
    html = read("operate.html")
    js = read("operate.js")
    assert 'data-operate-action="packages-export"' in html
    assert 'data-operate-action="packages-export-all"' in html
    assert "导出本次动作包" in html
    assert "导出全部历史" in html
    assert "/api/arm/packages/export?scope=session" in js
    assert "/api/arm/packages/export?scope=all" in js
    assert '"/api/arm/packages"' in js


def test_operator_page_offers_release_controls_for_automation_runs():
    html = read("operate.html")
    js = read("operate.js")
    assert 'data-operate-action="chassis-release"' in html
    assert 'data-operate-action="line-release"' in html
    assert 'data-operate-action="line-reclaim"' in html
    assert "/api/chassis/disconnect" in js
    assert "/api/line/release" in js
    assert "/api/line/start" in js


def test_pickup_window_supports_dragging_on_the_camera_frame():
    html = read("operate.html")
    js = read("operate.js")
    assert 'id="operate-camera-canvas"' in html
    assert 'id="operate-zone-drag-rect"' in html
    assert 'id="pickup-window"' in html
    for token in ("pointerdown", "pointermove", "pointerup", "clientToImagePoint", "normalizeImageRect"):
        assert token in js
    assert "/api/arm/actions/zone" in js


def test_operator_page_keeps_line_telemetry_and_manual_calibration_zones():
    html = read("operate.html")
    js = read("operate.js")
    for token in ("operate-zone-color", "operate-zone-x", "operate-zone-y", "operate-zone-w", "operate-zone-h", "operate-zone-action"):
        assert token in html
    assert "/api/calibration/zones" in js
    assert 'id="operate-line-sensors"' in html
    assert "/api/system/status" in js


def test_operator_page_exposes_dual_device_controls():
    html = read("operate.html")
    js = read("operate.js")
    assert 'data-module="operate"' in read("index.html")
    for action in ("stop", "run", "suction", "chassis-motion"):
        assert action in html
    for token in ("/api/devices/auto-connect", "/api/arm/run", "/api/arm/suction", "/api/system/status", "/api/chassis/velocity"):
        assert token in js


def test_logs_page_has_the_event_table():
    logs = read("logs.html")
    assert 'id="event-log"' in logs
    assert 'data-global-stop' in logs


def test_frontend_never_exposes_raw_serial_command_input():
    combined = "\n".join(read(name) for name in ("index.html", "operate.html", "operate.js", "hub.js"))
    assert "raw-command" not in combined
    assert "ARM,SERVO" not in combined
    assert "ARM,MOVE" not in combined
    assert "/api/arm/servo" in combined
    assert "record-toggle" in read("operate.html")
    assert "poseInitialized" not in combined
    assert "cancelPlayback" not in combined
    assert "playing" not in combined


def test_operator_scripts_use_single_flight_refresh_and_velocity_hold():
    js = read("operate.js")
    assert "refreshInFlight" in js
    assert "setInterval(refreshStatus" not in js
    assert "/api/chassis/velocity" in js
    assert "pointerup" in js and "pointercancel" in js
    assert "localStorage" not in js
