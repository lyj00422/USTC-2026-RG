# Action Package Window and Reset Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Load the latest six arm action packages, reuse pickup trigger windows, run `复位_final` once before every pickup/build action, and let build actions start only when the build view has no blocks.

**Architecture:** Keep route state transitions in `run_route_v2.py` and action validation/execution in `route_v2/pickup_action.py`. Add a filesystem-backed action catalog under `runtime/data/route_v2_actions`, with the latest packages copied from `C:\Users\LJY\Desktop\RG\action`; use the existing pickup vision window for pickup states and the existing build vision signal for build placement, with no build-zone rectangle requirement.

**Tech Stack:** Python 3, JSON action packages, pytest.

---

### Task 1: Import and normalize the latest action packages

**Files:**
- Create/modify: `runtime/data/route_v2_actions/**`
- Create: `runtime/data/route_v2_actions/catalog.json`
- Test: `runtime/tests/test_action_catalog.py`

- [x] Copy all six action directories from `C:\Users\LJY\Desktop\RG\action\包\包` and `复位_final` from `C:\Users\LJY\Desktop\RG\action\复位动作\复位动作\0002_复位_final` into the runtime action data, preserving action/arm/chassis/camera/metadata/zones files.
- [x] Add a catalog mapping pickup/build/reset roles to package directories and source names.
- [x] Add tests that every catalog entry resolves to an action JSON, that pickup entries contain a `capture` zone (using the backup pickup window where missing), and that build entries do not require any zone.

### Task 2: Generalize action loading and execution

**Files:**
- Modify: `runtime/route_v2/pickup_action.py`
- Test: `runtime/tests/test_pickup_action.py`

- [x] Add catalog loading by role/name, expected protocol/name validation, optional zone requirement, and support for arbitrary suction sequences including reset and build actions.
- [x] Preserve chassis velocity/stop pairing and arm acknowledgement behavior.
- [x] Add tests for loading reset/build actions, rejecting malformed packages, and executing a reset followed by a selected action.

### Task 3: Wire real actions and reset sequencing into the route runner

**Files:**
- Modify: `runtime/run_route_v2.py`
- Modify: `runtime/route_v2/pickup_vision.py` or its build decision interface only if needed
- Test: `runtime/tests/test_run_route_v2.py`

- [x] Load the catalog during hardware setup and construct executors for purple pickup, orange pickup variants, build variants, and `复位_final`.
- [x] On each pickup/build state entry, execute exactly one reset executor before the selected action executor; do not reset again while that action remains active.
- [x] Keep pickup trigger windows for pickup states; do not require or consult a build action window.
- [x] In build state, if the vision result says the view contains no block, allow the build action to start immediately; if a block is present, return the existing right-shift intent and retry until the view is clear.
- [x] Add route tests asserting one reset per pickup/build activation and build behavior for clear versus occupied views.

### Task 4: Validate and document deployment

**Files:**
- Modify: `runtime/README.md`
- Modify: `runtime/执行说明.md`

- [x] Document the catalog source, reused pickup window, no-build-window rule, and reset-before-action rule.
- [x] Run focused action/route tests, then the complete runtime test suite.
- [x] Validate every JSON file parses and every catalog action compiles before reporting completion.

