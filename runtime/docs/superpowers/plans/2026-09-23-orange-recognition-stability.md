# Orange Recognition Stability Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with tests written before production changes.

**Goal:** Make close-range orange-block recognition stable on the supplied local video while preserving all non-orange automation behavior.

**Architecture:** Extend the existing profile-driven detector with an optional YCrCb color band that is empty by default. Enable it only for `orange_pickup_close`, and lower only that profile's minimum contour area to the evidence-backed threshold; retain every existing geometry, near-field, confirmation, state-machine, and action constraint.

**Tech Stack:** Python 3, OpenCV, NumPy, YAML, pytest.

---

### Task 1: Add optional YCrCb profile and detector support

**Files:**
- Modify: `runtime/route_v2/vision_config.py`
- Modify: `runtime/rg_runtime/blocks.py`
- Test: `runtime/tests/test_blocks.py`

- [x] Add a failing detector test whose target misses HSV/Lab but matches one YCrCb band, and assert the accepted observation reports `("ycrcb",)`.
- [x] Add default-empty `ycrcb_bands` parsing and include it in the profile's at-least-one-color-band validation.
- [x] Convert BGR frames to YCrCb, OR its mask with HSV/Lab masks, and expose YCrCb in accepted-candidate diagnostics.
- [x] Run the focused detector tests and confirm the new test passes without changing centroid behavior.

### Task 2: Calibrate only the orange close profile

**Files:**
- Modify: `runtime/config/route_v2.yaml`
- Modify: `runtime/tests/test_route_v2_vision_config.py`

- [x] Add failing configuration assertions for YCrCb `[150, 131, 115]..[250, 150, 132]`, minimum area `100000`, and default-empty YCrCb on purple profiles.
- [x] Preserve explicit assertions for the existing orange ROI, aspect, height, center, bottom, near-field, fill, rectangularity, capture window, and three-frame confirmation.
- [x] Update only `orange_pickup_close` with the new YCrCb band and area floor, including evidence comments from the local video.
- [x] Run the focused configuration and pickup-vision tests.

### Task 3: Replay media and inspect the exact diff

**Files:**
- Verify: `runtime/route_v2/vision_config.py`
- Verify: `runtime/rg_runtime/blocks.py`
- Verify: `runtime/config/route_v2.yaml`
- Verify: `runtime/tests/test_blocks.py`
- Verify: `runtime/tests/test_route_v2_vision_config.py`

- [x] Replay all 740 orange-video frames with the production detector; require at least 720 accepted frames and no miss run longer than three frames.
- [x] Replay 4431 known-negative purple, Tag3, and route-video frames; require zero accepted frames.
- [x] Replay the 11-frame field sweep; full targets are detected at steps `[5, 6, 7]`. The additional step 9 contour is a left-edge partial whose center is outside the configured capture window, so it cannot enter pickup confirmation.
- [x] Run focused pytest suites and `python -m compileall -q route_v2 rg_runtime`.
- [x] Inspect the scoped diff and worktree status, preserving all unrelated user changes and reporting any pre-existing full-suite failures separately.
