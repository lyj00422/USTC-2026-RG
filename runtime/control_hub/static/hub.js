const Hub = (() => {
  let token = sessionStorage.getItem("controlHubToken") || "";
  let heartbeatTimer = null;
  let heartbeatInFlight = false;

  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (token) headers["X-Control-Token"] = token;
    const response = await fetch(path, { ...options, headers });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.message || payload.code || "请求失败");
    return payload;
  }

  async function takeControl() {
    const owner = `browser-${Date.now().toString(36)}`;
    const result = await api("/api/control/acquire", { method: "POST", body: JSON.stringify({ owner }) });
    token = result.token;
    sessionStorage.setItem("controlHubToken", token);
    startHeartbeat();
    document.dispatchEvent(new CustomEvent("hub-control", { detail: { active: true } }));
    return token;
  }

  async function startSession() {
    if (token) {
      try {
        await api("/api/control/heartbeat", { method: "POST", body: "{}" });
        startHeartbeat();
        document.dispatchEvent(new CustomEvent("hub-control", { detail: { active: true } }));
        return token;
      } catch (_error) {
        token = "";
        sessionStorage.removeItem("controlHubToken");
      }
    }
    return takeControl();
  }

  function startHeartbeat() {
    clearTimeout(heartbeatTimer);
    const heartbeatLoop = async () => {
      if (heartbeatInFlight) return;
      heartbeatInFlight = true;
      try {
        await api("/api/control/heartbeat", { method: "POST", body: "{}" });
      } catch (error) {
        token = "";
        sessionStorage.removeItem("controlHubToken");
        document.dispatchEvent(new CustomEvent("hub-control", { detail: { active: false, error: error.message } }));
        heartbeatInFlight = false;
        return;
      }
      heartbeatInFlight = false;
      heartbeatTimer = setTimeout(heartbeatLoop, 1000);
    };
    heartbeatLoop();
  }

  async function globalStop() {
    await api("/api/safety/stop", { method: "POST", body: JSON.stringify({ reason: "browser_operator" }) });
    document.dispatchEvent(new CustomEvent("hub-stopped"));
  }

  async function resetSafety() {
    await api("/api/safety/reset", { method: "POST", body: "{}" });
    document.dispatchEvent(new CustomEvent("hub-safety-reset"));
  }

  function bindShell() {
    document.querySelectorAll("[data-take-control]").forEach(button => {
      button.addEventListener("click", async () => {
        try { await takeControl(); button.textContent = "控制权已取得"; button.disabled = true; }
        catch (error) { alert(error.message); }
      });
    });
    document.querySelectorAll("[data-global-stop]").forEach(button => {
      button.addEventListener("click", async () => {
        try { await globalStop(); }
        catch (error) { alert(error.message); }
      });
    });
    document.querySelectorAll("[data-safety-reset]").forEach(button => {
      button.addEventListener("click", async () => {
        try { await resetSafety(); button.textContent = "安全锁已解除"; }
        catch (error) { alert(error.message); }
      });
    });
    if (token) startHeartbeat();
  }

  document.addEventListener("DOMContentLoaded", bindShell);
  return { api, takeControl, startSession, globalStop, resetSafety, hasControl: () => Boolean(token) };
})();
