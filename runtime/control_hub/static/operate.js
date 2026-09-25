const OperatorPage = (() => {
  const directions = { forward: [1, 0, 0], backward: [-1, 0, 0], left: [0, 1, 0], right: [0, -1, 0], "rotate-left": [0, 0, 1], "rotate-right": [0, 0, -1] };
  const $ = selector => document.querySelector(selector);
  const $$ = selector => Array.from(document.querySelectorAll(selector));
  let armStatus = { connected: false, mode: "DISCONNECTED" };
  let chassisStatus = { connected: false, state: "DISCONNECTED" };
  let cameraRunning = false, cameraRecording = false, cameraAvailable = false;
  let recording = false, armBusy = false, armQueue = Promise.resolve();
  let motionHeld = false, refreshInFlight = false;
  let packageCount = 0, historyCount = 0;

  function message(text, error = false) { const element = $("#operate-message"); element.textContent = text; element.classList.toggle("error-notice", error); }
  async function call(path, payload = {}) { return Hub.api(path, { method: "POST", body: JSON.stringify(payload) }); }
  function report(action) { Promise.resolve(action()).catch(error => message(error.message, true)); }

  /* ---------------------------------------------------------------- camera */
  function imageViewport(stage, image) {
    const width = stage.clientWidth, height = stage.clientHeight, naturalWidth = image.naturalWidth || 1280, naturalHeight = image.naturalHeight || 720;
    const scale = Math.min(width / naturalWidth, height / naturalHeight);
    const displayWidth = naturalWidth * scale, displayHeight = naturalHeight * scale;
    return { left: (width - displayWidth) / 2, top: (height - displayHeight) / 2, width: displayWidth, height: displayHeight, naturalWidth, naturalHeight };
  }
  function clientToImagePoint(event, stage, image) {
    const bounds = stage.getBoundingClientRect(), viewport = imageViewport(stage, image);
    const x = Math.max(viewport.left, Math.min(viewport.left + viewport.width, event.clientX - bounds.left));
    const y = Math.max(viewport.top, Math.min(viewport.top + viewport.height, event.clientY - bounds.top));
    return { x: (x - viewport.left) * viewport.naturalWidth / viewport.width, y: (y - viewport.top) * viewport.naturalHeight / viewport.height };
  }
  function normalizeImageRect(start, end) { return { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) }; }
  // The grab window is taught by dragging the live frame while a package is
  // being recorded, so the rectangle and the motion it belongs to land in the
  // same package.
  function setupZoneDragging() {
    const stage = $(".operator-camera-stage"), image = $("#operate-camera-stream"), canvas = $("#operate-camera-canvas"), marker = $("#operate-zone-drag-rect");
    if (!stage || !image || !canvas || !marker) return;
    let start = null, active = false;
    const resize = () => { canvas.width = stage.clientWidth; canvas.height = stage.clientHeight; };
    const draw = rect => { const context = canvas.getContext("2d"), viewport = imageViewport(stage, image); context.clearRect(0, 0, canvas.width, canvas.height); if (!rect) { marker.style.display = "none"; return; } const left = viewport.left + rect.x * viewport.width / viewport.naturalWidth, top = viewport.top + rect.y * viewport.height / viewport.naturalHeight, width = rect.width * viewport.width / viewport.naturalWidth, height = rect.height * viewport.height / viewport.naturalHeight; context.strokeStyle = "#ffce5c"; context.lineWidth = 2; context.fillStyle = "rgba(255, 206, 92, .16)"; context.fillRect(left, top, width, height); context.strokeRect(left, top, width, height); marker.style.display = "block"; marker.style.left = `${left}px`; marker.style.top = `${top}px`; marker.style.width = `${width}px`; marker.style.height = `${height}px`; };
    canvas.addEventListener("pointerdown", event => { if (!Hub.hasControl() || !cameraRunning || !recording) return; event.preventDefault(); canvas.setPointerCapture(event.pointerId); start = clientToImagePoint(event, stage, image); active = true; draw({ x: start.x, y: start.y, width: 0, height: 0 }); });
    canvas.addEventListener("pointermove", event => { if (!active) return; draw(normalizeImageRect(start, clientToImagePoint(event, stage, image))); });
    canvas.addEventListener("pointerup", async event => { if (!active) return; const rect = normalizeImageRect(start, clientToImagePoint(event, stage, image)); active = false; start = null; if (rect.width < 2 || rect.height < 2) { draw(null); return; } $("#operate-zone-x").value = Math.round(rect.x); $("#operate-zone-y").value = Math.round(rect.y); $("#operate-zone-w").value = Math.round(rect.width); $("#operate-zone-h").value = Math.round(rect.height); draw(rect); try { await call("/api/arm/actions/zone", { name: "capture", rect }); message("抓取窗口已写入当前动作包"); } catch (error) { message(error.message, true); } });
    canvas.addEventListener("pointercancel", () => { active = false; start = null; draw(null); });
    window.addEventListener("resize", resize); image.addEventListener("load", resize); resize();
  }

  /* --------------------------------------------------------- action package */
  function showDraft(draft) { const panel = $("#operate-draft"); $("#operate-draft-name").textContent = `未命名动作包：${draft.name || "未命名"}`; panel.hidden = false; }
  function hideDraft() { $("#operate-draft").hidden = true; }
  function setSaveButton(isRecording) { recording = isRecording; $("[data-operate-action=record-toggle]").textContent = isRecording ? "结束并命名" : "开始保存"; $("#operate-record-state").dataset.recording = String(isRecording); $("#operate-record-hint").textContent = isRecording ? "再按一次结束，然后填包名" : "按下开始记录，再按一次结束并命名"; }
  async function refreshPackages() {
    try {
      const result = await Hub.api("/api/arm/packages");
      packageCount = result.count || 0; historyCount = result.history_count || 0;
      $("#operate-package-count").textContent = `本次 ${packageCount} 个包`;
      $("[data-operate-action=packages-export]").disabled = packageCount === 0;
      $("[data-operate-action=packages-export-all]").disabled = historyCount === 0;
    } catch (error) { message(error.message, true); }
  }
  async function toggleSave() {
    const state = $("#operate-record-state");
    if (!recording) {
      // First press: everything sent from here on joins one package, and the
      // camera frame at save time is taken automatically.
      const result = await call("/api/arm/recording", { enabled: true });
      setSaveButton(true);
      state.textContent = `正在记录：${(result.action && result.action.name) || "动作包"}`;
      // Teaching a grab means dragging on the picture, so it comes back up.
      const moved = streamMirrored;
      setStreamMirrored(false);
      message(moved
        ? "已开始记录：画面已移回上方（拖拽抓取窗口要用上面那块）"
        : "已开始记录：舵机、吸盘、底盘与抓取窗口都会进这个动作包");
      return;
    }
    const result = await call("/api/arm/recording", { enabled: false });
    setSaveButton(false);
    if (result.saved) { state.textContent = `已保存：${result.saved.name}`; message(`动作包已保存：${result.saved.name}`); await refreshPackages(); return; }
    const draft = result.draft;
    if (!draft) { state.textContent = "未保存"; return; }
    state.textContent = "等待命名";
    const name = window.prompt("给这个动作包起个名字", draft.name || "");
    if (name === null || !name.trim()) { showDraft(draft); message("动作包暂存为草稿：可以命名，也可以丢弃", true); return; }
    await confirmDraft(name.trim());
  }
  async function confirmDraft(name) {
    const result = await call("/api/arm/actions/confirm", { title: name });
    hideDraft();
    $("#operate-record-state").textContent = `已保存：${(result.saved && result.saved.name) || name}`;
    message(`动作包已保存：${(result.saved && result.saved.name) || name}`);
    await refreshPackages();
  }
  function renameDraft() { const draft = draftSnapshot(); const name = window.prompt("给这个动作包起个名字", (draft && draft.name) || ""); if (name === null || !name.trim()) return; return confirmDraft(name.trim()); }
  function discardDraft() {
    if (!window.confirm("丢弃这个未命名的动作包？记录的内容会消失。")) return;
    return call("/api/arm/actions/discard").then(() => { hideDraft(); $("#operate-record-state").textContent = "未保存"; message("草稿已丢弃"); });
  }
  let lastDraft = null;

  /* ------------------------------------------------------- recording library */
  // The recordings live on the Pi; the console's job is to get them onto the
  // operator's own machine.  Every row is a plain link, so the browser's own
  // download handling does the transfer -- no base64, no SSH, and no zip unless
  // they want the whole folder at once.
  let library = { count: 0, total_bytes: 0 };
  let librarySignature = "";
  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
    if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
    if (bytes >= 1e3) return `${Math.round(bytes / 1e3)} KB`;
    return `${bytes} B`;
  }
  function applyLibraryButtons() {
    const hasControl = Hub.hasControl(), empty = library.count === 0;
    setDisabled("[data-operate-action=recordings-export]", !hasControl || empty);
    setDisabled("[data-operate-action=recordings-clear]", !hasControl || empty);
  }
  function renderRecordings(payload) {
    const items = payload.recordings || [];
    library = { count: items.length, total_bytes: payload.total_bytes || 0 };
    $("#operate-recording-summary").textContent = items.length ? `${items.length} 个 · ${formatBytes(library.total_bytes)}` : "暂无录像";
    applyLibraryButtons();
    // Rebuild the rows only when something actually changed: this refreshes on a
    // timer, and rebuilding under the pointer would fight the operator's click.
    const signature = `${Hub.hasControl()}|${library.count}|` + items.map(item => `${item.name}:${item.size_bytes}:${item.active}`).join("|");
    if (signature === librarySignature) return;
    librarySignature = signature;
    const list = $("#operate-recording-list");
    list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "recording-empty";
      empty.textContent = "还没有录像。按「开启录像」录一段，停下就会出现在这里。";
      list.appendChild(empty);
      return;
    }
    items.forEach(item => list.appendChild(recordingRow(item)));
  }
  function recordingRow(item) {
    const row = document.createElement("div");
    row.className = "recording-row";
    row.dataset.active = String(Boolean(item.active));
    const label = document.createElement("strong");
    label.className = "recording-label";
    label.textContent = item.label || item.name;
    const meta = document.createElement("span");
    meta.className = "recording-meta";
    meta.textContent = `${(item.recorded_at || "").replace("T", " ")} · ${formatBytes(item.size_bytes)}`;
    row.append(label, meta);
    if (item.active) {
      // Still being written: a download would be a truncated AVI, and deleting
      // the file under the writer would corrupt the recording in progress.
      const chip = document.createElement("span");
      chip.className = "panel-kicker";
      chip.textContent = "录像中";
      row.append(chip, document.createElement("span"));
      return row;
    }
    const download = document.createElement("a");
    download.className = "command-link";
    download.textContent = "下载";
    download.href = `/api/camera/recordings/file?name=${encodeURIComponent(item.name)}`;
    download.setAttribute("download", item.name);
    const remove = document.createElement("button");
    remove.className = "danger-outline";
    remove.type = "button";
    remove.textContent = "删除";
    remove.disabled = !Hub.hasControl();
    remove.addEventListener("click", () => report(() => deleteRecording(item)));
    row.append(download, remove);
    return row;
  }
  async function refreshRecordings() {
    try { renderRecordings(await Hub.api("/api/camera/recordings")); }
    catch (error) { message(error.message, true); }
  }
  async function deleteRecording(item) {
    const size = formatBytes(item.size_bytes);
    if (!window.confirm(`删除录像「${item.label || item.name}」（${size}）？\n本机还没取回去的话，删了就没了。`)) return;
    await call("/api/camera/recordings/delete", { name: item.name });
    message(`已删除：${item.name}（${size}）`);
    librarySignature = "";
    await refreshRecordings();
  }
  async function clearRecordings() {
    const note = library.count ? `${library.count} 个录像，共 ${formatBytes(library.total_bytes)}` : "全部录像";
    if (!window.confirm(`清空${note}？\n正在录的那个会保留，其余直接删掉，删了取不回来。`)) return;
    const result = await call("/api/camera/recordings/delete-all", { confirm: true });
    message(`已删除 ${result.count} 个录像，释放 ${formatBytes(result.size_bytes)}`);
    librarySignature = "";
    await refreshRecordings();
  }

  /* ------------------------------------------------------- camera placement */
  // One MJPEG stream, two windows.  Only the picture moves: the <img> node is
  // re-parented, so the connection stays open and the frame does not restart --
  // pointing a second src at the same endpoint would double the bandwidth and
  // re-pointing one src would blink and re-handshake.
  let streamMirrored = false;
  function setStreamMirrored(mirrored) {
    // The capture window is dragged on the camera panel's stage, so while an
    // action package is being recorded the picture has to be up there.
    streamMirrored = Boolean(mirrored) && !recording;
    const image = $("#operate-camera-stream");
    const stage = streamMirrored ? $("#operate-chassis-stage") : $(".operator-camera-stage");
    if (image && stage && image.parentElement !== stage) stage.prepend(image);
    const button = $("[data-operate-action=stream-toggle]");
    // The label says what the next click does, not what the state currently is.
    if (button) button.textContent = streamMirrored ? "画面移回上方 ↑" : "画面移到下方 ↓";
  }
  function applyCameraOverlays() {
    const live = cameraAvailable, home = $("[data-camera-offline=home]"), away = $("[data-camera-offline=away]");
    if (!home || !away) return;
    // The window holding the picture shows no overlay; the other one says where
    // it went.  Camera off means neither window has it.
    home.hidden = live && !streamMirrored;
    away.hidden = live && streamMirrored;
    home.textContent = live ? "画面已移到下方 ↓" : "摄像头未开启";
    away.textContent = live ? "画面在上方 ↑" : "摄像头未开启";
  }

  /* ----------------------------------------------------------------- render */
  function render(status) {
    armStatus = status.arm || armStatus; chassisStatus = status.chassis || chassisStatus;
    const armMode = armStatus.mode || "DISCONNECTED", chassisState = chassisStatus.state || "DISCONNECTED", camera = status.camera || {}, line = status.line || {};
    cameraRunning = Boolean(camera.running); cameraRecording = Boolean(camera.recording);
    $("#operate-arm-state").textContent = armMode; $("#operate-arm-signal").textContent = `ARM ${armMode}`; $("#operate-arm-signal").dataset.state = armMode;
    $("#operate-arm-routine").textContent = armStatus.routine ?? "--"; $("#operate-arm-step").textContent = armStatus.step ?? "--";
    $("#operate-chassis-state").textContent = chassisState; $("#operate-chassis-signal").textContent = `CHASSIS ${chassisState}`; $("#operate-chassis-signal").dataset.state = chassisState; $("#operate-chassis-device").textContent = chassisStatus.device || "固定底盘未连接";
    const keepalive = chassisStatus.keepalive || {};
    $("#operate-keepalive").textContent = keepalive.enabled ? `${keepalive.period_s}s · 已发 ${keepalive.sends} 次` : "关闭";
    $("#operate-camera-state").textContent = camera.running ? "CAMERA READY" : "CAMERA OFFLINE"; $("#operate-camera-signal").textContent = camera.running ? "CAMERA READY" : "CAMERA OFFLINE";
    cameraAvailable = Boolean(camera.frame_available);
    // Recording moves the picture back to the camera panel and holds it there.
    if (recording) setStreamMirrored(streamMirrored);
    applyCameraOverlays();
    $("#operate-recording-state").textContent = cameraRecording ? "录像中" : "未录像";
    $("#operate-snapshot-download").hidden = !camera.snapshot_available;
    $$("[data-stream-src]").forEach(node => { if (!camera.running) { node.removeAttribute("src"); return; } const source = new URL(node.dataset.streamSrc, window.location.href).href; if (node.src !== source) node.src = node.dataset.streamSrc; });
    const hasControl = Hub.hasControl();
    setDisabled("[data-operate-action=camera-start]", !hasControl || camera.running);
    setDisabled("[data-operate-action=camera-stop]", !hasControl || !camera.running);
    setDisabled("[data-operate-action=record-start]", !hasControl || !camera.running || cameraRecording);
    setDisabled("[data-operate-action=record-stop]", !hasControl || !camera.running || !cameraRecording);
    setDisabled("[data-operate-action=snapshot]", !hasControl || !camera.running);
    setDisabled("[data-operate-action=stream-toggle]", recording);
    const ready = armMode === "READY" && !armBusy;
    $$("[data-operate-action=arm-run], [data-operate-action=arm-suction], [data-operate-action=arm-servo]").forEach(button => { button.disabled = !ready; });
    setDisabled("[data-operate-action=record-toggle]", !hasControl || (!recording && !ready));
    setDisabled("[data-operate-action=chassis-release]", !hasControl || !chassisStatus.connected);
    setDisabled("[data-operate-action=line-release]", !hasControl || !line.connected);
    setDisabled("[data-operate-action=line-reclaim]", !hasControl || Boolean(line.connected));
    $$("[data-operate-action=chassis-motion]").forEach(button => { button.disabled = !chassisStatus.connected; });
    const canDrag = hasControl && camera.running && recording;
    $("#operate-camera-canvas").style.pointerEvents = canDrag ? "auto" : "none";
    $("#operate-line-state").textContent = line.state || "--"; $("#operate-line-signal").textContent = `LINE ${line.state || "--"}`; $("#operate-line-signal").dataset.state = line.state || "DISCONNECTED";
    $("#operate-line-error").textContent = `偏差 ${line.line_error ?? "--"}`;
    $("#operate-line-intersection").textContent = `路口 ${line.intersection || "--"}`;
    $("#operate-line-lost").textContent = `丢线 ${line.line_lost == null ? "--" : (line.line_lost ? "是" : "否")}`;
    $("#operate-line-time").textContent = `时间戳 ${line.timestamp_ms ?? "--"}`;
    applyLibraryButtons();
    const sensors = $("#operate-line-sensors"); sensors.replaceChildren(); (line.sensors || []).forEach((active, index) => { const cell = document.createElement("span"); cell.className = `line-sensor${active ? " active" : ""}`; cell.textContent = `S${index + 1} ${active}`; sensors.appendChild(cell); });
  }
  function setDisabled(selector, disabled) { const element = $(selector); if (element) element.disabled = disabled; }
  async function refreshStatus() { render(await Hub.api("/api/system/status")); const actions = await Hub.api("/api/arm/actions"); lastDraft = actions.draft || null; if (lastDraft && !recording && $("#operate-record-state").textContent !== "已保存") showDraft(lastDraft); else if (!lastDraft) hideDraft(); }
  function draftSnapshot() { return lastDraft; }
  // Status every second; the recordings folder is re-read on every fifth tick --
  // often enough to watch a recording appear, rarely enough to be free.
  let refreshTick = 0;
  async function scheduleRefresh() { if (refreshInFlight) return; refreshInFlight = true; try { await refreshStatus(); if (refreshTick++ % 5 === 4) await refreshRecordings(); } catch (error) { message(error.message, true); } finally { refreshInFlight = false; window.setTimeout(scheduleRefresh, 1000); } }
  function withArm(action) { armQueue = armQueue.then(async () => { armBusy = true; try { return await action(); } finally { armBusy = false; await refreshStatus(); } }, async () => action()); return armQueue; }

  /* ---------------------------------------------------------------- chassis */
  async function beginMotion(name) { if (!directions[name] || !chassisStatus.connected || motionHeld) return; motionHeld = true; const [vx, vy, wz] = directions[name].map(value => value * Number($("#operate-speed").value)); try { await call("/api/chassis/velocity", { vx, vy, wz }); } catch (error) { motionHeld = false; message(error.message, true); } }
  async function stopChassis() { if (!motionHeld && !chassisStatus.connected) return; motionHeld = false; try { await call("/api/chassis/stop"); await refreshStatus(); } catch (error) { message(error.message, true); } }
  async function releaseChassis() {
    if (recording) { message("先结束当前动作包再释放底盘", true); return; }
    if (!window.confirm("断开底盘串口？断开后自动化路线程序才能占用它。")) return;
    await call("/api/chassis/disconnect"); message("底盘已释放，蓝牙保活同时停止"); await refreshStatus();
  }
  async function releaseLine() { await call("/api/line/release"); message("巡线串口已释放，路线程序可以直接占用"); await refreshStatus(); }
  async function reclaimLine() { await call("/api/line/start"); message("巡线串口已重新占用"); await refreshStatus(); }

  /* ----------------------------------------------------------------- camera */
  async function cameraCommand(path, payload = {}) { try { await call(path, payload); await refreshStatus(); } catch (error) { message(error.message, true); } }
  async function autoConnect() {
    await Hub.startSession();
    try { await call("/api/devices/auto-connect"); message("固定设备连接完成"); }
    catch (error) { message(`${error.message}（底盘被自动化程序占用时属正常，跑完路线再连）`, true); }
    await refreshStatus(); await refreshPackages();
  }

  function bind() {
    $("[data-operate-action=camera-start]").addEventListener("click", () => report(() => cameraCommand("/api/camera/start")));
    $("[data-operate-action=camera-stop]").addEventListener("click", () => report(() => cameraCommand("/api/camera/stop")));
    $("[data-operate-action=record-start]").addEventListener("click", () => report(async () => { await cameraCommand("/api/camera/record/start", { label: $("#operate-record-label").value }); librarySignature = ""; await refreshRecordings(); }));
    $("[data-operate-action=record-stop]").addEventListener("click", () => report(async () => { await cameraCommand("/api/camera/record/stop"); librarySignature = ""; await refreshRecordings(); }));
    $("[data-operate-action=recordings-refresh]").addEventListener("click", () => { librarySignature = ""; return report(refreshRecordings); });
    $("[data-operate-action=recordings-export]").addEventListener("click", () => { window.location.href = "/api/camera/recordings/export"; });
    $("[data-operate-action=recordings-clear]").addEventListener("click", () => report(clearRecordings));
    $("[data-operate-action=snapshot]").addEventListener("click", () => report(async () => { await cameraCommand("/api/camera/snapshot"); message("已拍照，可用“下载最新照片”取回"); }));
    $("[data-operate-action=stream-toggle]").addEventListener("click", () => { setStreamMirrored(!streamMirrored); applyCameraOverlays(); message(streamMirrored ? "画面已移到下方，开车时看那一格" : "画面已移回上方"); });
    $$("[data-operate-action=arm-servo]").forEach(button => button.addEventListener("click", () => report(() => { const row = button.closest("[data-servo-axis]"); return withArm(() => call("/api/arm/servo", { id: Number(row.dataset.servoAxis), position: Number(row.querySelector("[data-servo-position]").value), time_ms: Number($("#operate-servo-time").value) })); })));
    $$("[data-operate-action=arm-run]").forEach(button => button.addEventListener("click", () => report(() => withArm(() => call("/api/arm/run", { routine: Number(button.dataset.routine) })))));
    $$("[data-operate-action=arm-suction]").forEach(button => button.addEventListener("click", () => report(() => withArm(() => call("/api/arm/suction", { enabled: button.dataset.enabled === "true" })))));
    $("[data-operate-action=arm-stop]").addEventListener("click", () => report(() => call("/api/arm/stop")));
    $("[data-operate-action=record-toggle]").addEventListener("click", () => report(toggleSave));
    $("[data-operate-action=draft-name]").addEventListener("click", () => report(renameDraft));
    $("[data-operate-action=draft-discard]").addEventListener("click", () => report(discardDraft));
    $("[data-operate-action=packages-export]").addEventListener("click", () => { window.location.href = "/api/arm/packages/export?scope=session"; });
    $("[data-operate-action=packages-export-all]").addEventListener("click", () => { window.location.href = "/api/arm/packages/export?scope=all"; });
    $("[data-operate-action=zone-save]").addEventListener("click", () => report(async () => { await call("/api/calibration/zones", { id: $("#operate-zone-id").value, color: $("#operate-zone-color").value, action_id: $("#operate-zone-action").value, rect: { x: Number($("#operate-zone-x").value), y: Number($("#operate-zone-y").value), width: Number($("#operate-zone-w").value), height: Number($("#operate-zone-h").value) } }); message("标定窗口已保存"); }));
    $("[data-operate-action=chassis-release]").addEventListener("click", () => report(releaseChassis));
    $("[data-operate-action=line-release]").addEventListener("click", () => report(releaseLine));
    $("[data-operate-action=line-reclaim]").addEventListener("click", () => report(reclaimLine));
    $("[data-operate-action=route-record]").addEventListener("click", () => report(async () => { const title = window.prompt("请输入起点标题"); if (!title || !title.trim()) return; if (!cameraRunning) await call("/api/camera/start"); await call("/api/route-capture/start", { title: title.trim() }); await call("/api/route-capture/capture", { title: title.trim(), role: "start" }); message(`已保存起点：${title.trim()}，并已清零下一段里程`); await refreshStatus(); }));
    $("[data-operate-action=route-capture]").addEventListener("click", () => report(async () => { const title = window.prompt("请输入当前位置标题"); if (!title || !title.trim()) return; await call("/api/route-capture/capture", { title: title.trim() }); message(`已保存位置：${title.trim()}，并已清零下一段里程`); await refreshStatus(); }));
    $$("[data-operate-action=chassis-motion]").forEach(button => { button.addEventListener("pointerdown", event => { event.preventDefault(); report(() => beginMotion(button.dataset.motion)); }); ["pointerup", "pointercancel", "pointerleave"].forEach(event => button.addEventListener(event, () => report(stopChassis))); });
    $$("[data-operate-action=chassis-stop]").forEach(button => button.addEventListener("click", () => report(stopChassis)));
    document.addEventListener("visibilitychange", () => { if (document.hidden) report(stopChassis); });
    window.addEventListener("blur", () => report(stopChassis));
    window.addEventListener("beforeunload", () => { fetch("/api/chassis/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", keepalive: true }).catch(() => {}); });
    $("#operate-snapshot-download").hidden = true;
    hideDraft();
    setupZoneDragging();
    refreshRecordings();
    report(autoConnect);
    scheduleRefresh();
  }
  document.addEventListener("DOMContentLoaded", bind);
  return { refreshStatus };
})();
