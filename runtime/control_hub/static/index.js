async function refreshModules() {
  try {
    const result = await Hub.api("/api/system/modules");
    result.modules.forEach(module => {
      const row = document.querySelector(`[data-module="${module.key}"]`);
      if (!row) return;
      const detail = row.querySelector(".module-detail");
      if (detail) detail.textContent = module.detail;
      const signal = document.getElementById(`${module.key}-signal`);
      if (signal) { signal.dataset.state = module.state; signal.textContent = `${module.key.toUpperCase()} ${module.state}`; }
    });
  } catch (error) {
    console.error(error);
  }
}
document.addEventListener("DOMContentLoaded", () => { refreshModules(); setInterval(refreshModules, 1000); });
