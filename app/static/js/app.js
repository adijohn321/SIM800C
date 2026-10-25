(() => {
  const cfg = window.APP_CONFIG || {};
  const apiKey = cfg.apiKeyRequired
    ? (localStorage.getItem("sim800c_api_key") || prompt("Enter API key") || "")
    : "";

  if (cfg.apiKeyRequired && apiKey) {
    localStorage.setItem("sim800c_api_key", apiKey);
  }

  const headers = () => {
    const h = { "Content-Type": "application/json", Accept: "application/json" };
    if (apiKey) h["X-API-Key"] = apiKey;
    return h;
  };

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: { ...headers(), ...(options.headers || {}) },
    });
    let data = null;
    const text = await res.text();
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    if (!res.ok) {
      const detail = data?.detail || res.statusText || "Request failed";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function $(id) {
    return document.getElementById(id);
  }

  let toastTimer = null;
  function toast(message, isError = false) {
    const el = $("toast");
    el.hidden = false;
    el.textContent = message;
    el.classList.toggle("error", isError);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.hidden = true;
    }, 4000);
  }

  function signalLabel(rssi) {
    if (rssi == null || rssi === 99) return "Unknown";
    if (rssi === 0) return "≤ -113 dBm";
    if (rssi === 1) return "-111 dBm";
    if (rssi >= 2 && rssi <= 30) return `${-113 + 2 * rssi} dBm (${rssi}/31)`;
    if (rssi === 31) return "≥ -51 dBm";
    return String(rssi);
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatTime(iso) {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  async function refreshStatus() {
    try {
      const s = await api("/api/modem/status");
      $("st-connected").textContent = s.connected
        ? s.mock
          ? "Connected (mock)"
          : "Connected"
        : "Disconnected";
      $("st-port").textContent = s.port || "—";
      $("st-signal").textContent = signalLabel(s.signal_rssi);
      $("st-network").textContent = s.network_registered ? "Registered" : "Not registered";
      $("st-operator").textContent = s.operator || "—";
      $("st-imei").textContent = s.imei || "—";
      if (s.last_error) {
        $("st-connected").title = s.last_error;
      }
    } catch (err) {
      $("st-connected").textContent = "Error";
      toast(err.message, true);
    }
  }

  async function refreshStats() {
    try {
      const s = await api("/api/stats");
      $("stat-total").textContent = s.total;
      $("stat-inbound").textContent = s.inbound;
      $("stat-outbound").textContent = s.outbound;
      $("stat-unread").textContent = s.unread;
    } catch {
      /* ignore */
    }
  }

  async function refreshMessages() {
    const direction = $("filter-direction").value;
    const phone = $("filter-phone").value.trim();
    const params = new URLSearchParams();
    if (direction) params.set("direction", direction);
    if (phone) params.set("phone", phone);
    params.set("limit", "200");

    const list = $("message-list");
    try {
      const data = await api(`/api/sms?${params}`);
      if (!data.items.length) {
        list.innerHTML = '<p class="empty">No messages yet. Compose one above or sync the SIM inbox.</p>';
        return;
      }
      list.innerHTML = data.items
        .map((m) => {
          const dirClass = m.direction === "inbound" ? "in" : "out";
          const dirLabel = m.direction === "inbound" ? "In" : "Out";
          return `
            <article class="message" data-id="${m.id}">
              <span class="dir-badge ${dirClass}">${dirLabel}</span>
              <div>
                <div class="msg-phone">${escapeHtml(m.phone)}</div>
                <div class="msg-meta">${formatTime(m.created_at)} · <span class="msg-status ${escapeHtml(m.status)}">${escapeHtml(m.status)}</span></div>
                <div class="msg-body">${escapeHtml(m.body)}</div>
                ${m.error ? `<div class="msg-meta" style="color:var(--danger)">${escapeHtml(m.error)}</div>` : ""}
              </div>
              <div class="msg-actions">
                <button type="button" class="btn btn-danger btn-delete" data-id="${m.id}">Delete</button>
              </div>
            </article>`;
        })
        .join("");

      list.querySelectorAll(".btn-delete").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (!confirm("Delete this message?")) return;
          try {
            await api(`/api/sms/${btn.dataset.id}`, { method: "DELETE" });
            toast("Message deleted");
            await Promise.all([refreshMessages(), refreshStats()]);
          } catch (err) {
            toast(err.message, true);
          }
        });
      });
    } catch (err) {
      list.innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`;
    }
  }

  async function refreshAll() {
    await Promise.all([refreshStatus(), refreshStats(), refreshMessages()]);
  }

  $("btn-connect").addEventListener("click", async () => {
    try {
      await api("/api/modem/connect", { method: "POST" });
      toast("Modem connected");
      await refreshStatus();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("btn-disconnect").addEventListener("click", async () => {
    try {
      await api("/api/modem/disconnect", { method: "POST" });
      toast("Modem disconnected");
      await refreshStatus();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("btn-sync").addEventListener("click", async () => {
    try {
      const result = await api("/api/sms/sync", { method: "POST" });
      toast(`Synced: ${result.imported} new of ${result.total_on_modem} on modem`);
      await Promise.all([refreshMessages(), refreshStats()]);
    } catch (err) {
      toast(err.message, true);
    }
  });

  const detectBtn = $("btn-detect");
  if (detectBtn) {
    detectBtn.addEventListener("click", async () => {
      detectBtn.disabled = true;
      try {
        const d = await api("/api/modem/detect", { method: "POST" });
        toast(`Detected ${d.device} via ${d.method} detection`);
        await refreshStatus();
      } catch (err) {
        toast(err.message, true);
      } finally {
        detectBtn.disabled = false;
      }
    });
  }

  $("btn-refresh").addEventListener("click", () => refreshAll());
  $("filter-direction").addEventListener("change", () => refreshMessages());
  $("filter-phone").addEventListener("input", () => {
    clearTimeout($("filter-phone")._t);
    $("filter-phone")._t = setTimeout(refreshMessages, 300);
  });

  const msgBox = $("compose-message");
  const charCount = $("char-count");
  msgBox.addEventListener("input", () => {
    charCount.textContent = `${msgBox.value.length} / 1600`;
  });

  $("compose-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const phone = $("compose-phone").value.trim();
    const message = $("compose-message").value.trim();
    const btn = $("btn-send");
    btn.disabled = true;
    try {
      await api("/api/sms/send", {
        method: "POST",
        body: JSON.stringify({ phone, message }),
      });
      toast("SMS sent");
      $("compose-message").value = "";
      charCount.textContent = "0 / 1600";
      await Promise.all([refreshMessages(), refreshStats()]);
    } catch (err) {
      toast(err.message, true);
      await refreshMessages();
    } finally {
      btn.disabled = false;
    }
  });

  const injectForm = $("inject-form");
  if (injectForm) {
    injectForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/sms/inject", {
          method: "POST",
          body: JSON.stringify({
            phone: $("inject-phone").value.trim(),
            message: $("inject-message").value.trim(),
          }),
        });
        toast("Inbound SMS injected");
        $("inject-message").value = "";
        await Promise.all([refreshMessages(), refreshStats()]);
      } catch (err) {
        toast(err.message, true);
      }
    });
  }

  refreshAll();
  setInterval(refreshStatus, 15000);
  setInterval(() => {
    Promise.all([refreshMessages(), refreshStats()]);
  }, 10000);
})();
