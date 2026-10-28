(async function () {
  const health = document.getElementById("health");
  try {
    const res = await fetch("/health", { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    health.classList.add("ok");
    health.textContent = `Online · model ${data.model} · checked ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    health.classList.add("down");
    health.textContent = "Offline. The host PC is off or the tunnel is not running.";
  }

  try {
    const res = await fetch("/downloads/manifest.json", { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    const m = await res.json();
    document.getElementById("installer-meta").textContent =
      `${m.file} · ${(m.size_bytes / 1048576).toFixed(1)} MB · built ${m.built_at}`;
    document.getElementById("installer-sha").textContent = m.sha256;
  } catch (err) {
    document.getElementById("installer-meta").textContent = "Installer metadata unavailable.";
    document.getElementById("installer-sha").textContent = "unavailable";
  }
})();
