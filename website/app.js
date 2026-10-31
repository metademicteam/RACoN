(function () {
  function detectPlatform() {
    const raw =
      (navigator.userAgentData && navigator.userAgentData.platform) ||
      navigator.platform ||
      navigator.userAgent ||
      "";
    const value = raw.toLowerCase();
    if (value.includes("linux") || value.includes("ubuntu")) return "linux";
    if (value.includes("win")) return "windows";
    return null;
  }

  function formatSize(bytes) {
    if (typeof bytes !== "number" || bytes <= 0) return "size unavailable";
    return `${(bytes / 1048576).toFixed(1)} MB`;
  }

  function prefixFor(key) {
    return key === "windows" ? "win" : key;
  }

  function bindPlatform(key, data) {
    if (!data || !data.file) return;
    const prefix = prefixFor(key);
    const meta = document.getElementById(`${prefix}-meta`);
    const sha = document.getElementById(`${prefix}-sha`);
    const btn = document.getElementById(`${prefix}-btn`);
    if (!meta || !btn) return;

    meta.textContent = `${data.file} · ${formatSize(data.size_bytes)} · built ${data.built_at || "unknown"}`;
    if (sha) sha.textContent = data.sha256 || "unavailable";

    btn.href = `/downloads/${encodeURIComponent(data.file)}`;
    btn.setAttribute("download", data.file);
    btn.textContent = key === "windows" ? "Download for Windows" : "Download for Ubuntu";

    if (key === "linux") {
      const install = document.getElementById("linux-install");
      if (install) {
        const extractDir = data.file.replace(/\.tar\.gz$/, "");
        install.textContent =
          `tar -xzf ${data.file}\n` +
          `cd ${extractDir}\n` +
          "./install.sh          # desktop app, no root\n" +
          "racon                 # launch\n" +
          "racon --headless      # Ubuntu Server worker";
      }

      if (data.deb && data.deb.file) {
        const wrap = document.getElementById("linux-deb-wrap");
        const link = document.getElementById("linux-deb");
        if (wrap && link) {
          link.href = `/downloads/${encodeURIComponent(data.deb.file)}`;
          link.setAttribute("download", data.deb.file);
          link.textContent = data.deb.file;
          wrap.hidden = false;
        }
      }
    }
  }

  function markRecommended(key) {
    const prefix = prefixFor(key);
    const card = document.getElementById(`${prefix}-card`);
    const badge = document.getElementById(`${prefix}-badge`);
    if (card) card.classList.add("recommended");
    if (badge) badge.textContent = `${badge.textContent} · recommended for this PC`;
  }

  async function loadHealth() {
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
  }

  async function loadDownloads() {
    try {
      const res = await fetch("/downloads/manifest.json", { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      const manifest = await res.json();

      const platforms =
        manifest.platforms && typeof manifest.platforms === "object"
          ? manifest.platforms
          : {
              windows: {
                file: manifest.file,
                size_bytes: manifest.size_bytes,
                sha256: manifest.sha256,
                built_at: manifest.built_at,
              },
            };

      Object.keys(platforms).forEach((key) => bindPlatform(key, platforms[key]));

      const detected = detectPlatform();
      if (detected && platforms[detected]) markRecommended(detected);
    } catch (err) {
      for (const prefix of ["win", "linux"]) {
        const meta = document.getElementById(`${prefix}-meta`);
        const sha = document.getElementById(`${prefix}-sha`);
        if (meta) meta.textContent = "Download metadata unavailable.";
        if (sha) sha.textContent = "unavailable";
      }
    }
  }

  loadHealth();
  loadDownloads();
})();
