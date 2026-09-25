async function refreshLogs() {
  try {
    const result = await Hub.api("/api/logs/recent");
    const body = document.getElementById("event-log");
    body.innerHTML = "";
    result.events.slice().reverse().forEach(event => {
      const row = document.createElement("tr");
      const time = new Date(event.timestamp_ns / 1000000).toLocaleTimeString();
      [time, event.source, event.type, JSON.stringify(event.payload)].forEach(value => { const cell = document.createElement("td"); cell.textContent = value; cell.className = "mono"; row.appendChild(cell); });
      body.appendChild(row);
    });
  } catch (error) { console.error(error); }
}
document.addEventListener("DOMContentLoaded", () => { refreshLogs(); setInterval(refreshLogs, 1000); });
