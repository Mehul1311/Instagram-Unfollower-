const state = { status: null, accounts: [], statusTimer: null, accountsTimer: null };

const $ = (selector) => document.querySelector(selector);
const formatNumber = (value) => new Intl.NumberFormat().format(value ?? 0);

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 4200);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.message || "Request failed");
  return data;
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function renderStatus(status) {
  state.status = status;
  const percent = status.total ? Math.round((status.completed / status.total) * 100) : 0;
  $("#progress-ring").style.setProperty("--progress", `${percent * 3.6}deg`);
  $("#progress-percent").textContent = `${percent}%`;
  $("#phase").textContent = status.phase;
  $("#remaining-summary").textContent = `${formatNumber(status.remaining)} remaining · ${formatNumber(status.completed)} cleared`;
  $("#metric-total").textContent = formatNumber(status.total);
  $("#hero-total").textContent = formatNumber(status.total);
  $("#metric-unfollowed").textContent = formatNumber(status.unfollowed);
  $("#metric-skipped").textContent = formatNumber(status.skipped);
  $("#metric-remaining").textContent = formatNumber(status.remaining);
  $("#metric-errors").textContent = formatNumber(status.errors);
  $("#target-file").textContent = status.target_file;

  const running = status.running;
  $("#run-all").disabled = running || status.remaining === 0;
  $("#run-batch").disabled = running || status.remaining === 0;
  $("#pause-run").disabled = !running || status.mode !== "all" || status.pause_requested;
  $("#stop-run").disabled = !running;
  $("#live-badge").textContent = running ? "Live" : "Idle";
  $("#live-badge").classList.toggle("active", running);

  let detail = "No run active";
  if (running) {
    detail = status.cooldown_seconds
      ? `Batch ${status.batch_number} complete · next in ${formatDuration(status.cooldown_seconds)}`
      : `${status.mode === "all" ? "Continuous queue" : "Single batch"} · batch ${status.batch_number || 1}`;
  } else if (status.last_exit_code !== null) {
    detail = status.last_exit_code === 0 ? status.phase : `${status.phase} · review activity`;
  }
  $("#run-detail").textContent = detail;

  const consoleBox = $("#console");
  const logs = status.logs.length ? status.logs.join("\n") : "Waiting for a run…";
  if (consoleBox.textContent !== logs) {
    const pinnedToBottom = consoleBox.scrollHeight - consoleBox.scrollTop - consoleBox.clientHeight < 40;
    consoleBox.textContent = logs;
    if (pinnedToBottom) consoleBox.scrollTop = consoleBox.scrollHeight;
  }

  if (status.target_error) showToast(status.target_error, true);
}

function statusLabel(status) {
  const labels = {
    pending: "Pending",
    success: "Unfollowed",
    not_following: "Already clear",
    failed: "Failed",
    stopped: "Stopped",
    user_not_found: "Not found",
    not_changed: "Not changed",
  };
  return labels[status] || status.replaceAll("_", " ");
}

function isAttention(status) {
  return ["failed", "stopped", "user_not_found", "not_changed"].includes(status);
}

function renderAccounts() {
  const query = $("#account-search").value.trim().toLowerCase().replace(/^@/, "");
  const filter = $("#status-filter").value;
  const filtered = state.accounts.filter((account) => {
    const matchesQuery = !query || account.username.includes(query);
    const matchesFilter = filter === "all"
      || account.status === filter
      || (filter === "attention" && isAttention(account.status));
    return matchesQuery && matchesFilter;
  });

  const visible = filtered.slice(0, 250);
  $("#accounts-body").innerHTML = visible.map((account) => {
    const tagClass = isAttention(account.status) ? "attention" : account.status;
    const updated = account.timestamp
      ? new Date(account.timestamp).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })
      : "—";
    return `<tr>
      <td>@${account.username}</td>
      <td><span class="status-tag status-${tagClass}">${statusLabel(account.status)}</span></td>
      <td>${updated}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="3">No accounts match this view.</td></tr>`;
  $("#table-footer").textContent = `Showing ${formatNumber(visible.length)} of ${formatNumber(filtered.length)} matching accounts`;
}

async function refreshStatus() {
  try { renderStatus(await api("/api/status")); }
  catch (error) { showToast(error.message, true); }
}

async function refreshAccounts() {
  try {
    const data = await api("/api/accounts");
    state.accounts = data.accounts;
    renderAccounts();
  } catch (error) { showToast(error.message, true); }
}

function runConfig(mode) {
  return {
    mode,
    batch_size: Number($("#batch-size").value),
    delay_min: Number($("#delay-min").value),
    delay_max: Number($("#delay-max").value),
    cooldown_minutes: Number($("#cooldown").value),
  };
}

async function startRun(mode) {
  if (mode === "all") {
    const remaining = state.status?.remaining ?? 0;
    const accepted = window.confirm(
      `Queue all ${remaining} remaining targets? The tool will use controlled batches, pause between them, and stop on Instagram warnings.`
    );
    if (!accepted) return;
  }
  try {
    const data = await api("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(runConfig(mode)),
    });
    showToast(data.message);
    await refreshStatus();
  } catch (error) { showToast(error.message, true); }
}

async function controlRun(action) {
  try {
    const data = await api(`/api/${action}`, { method: "POST" });
    showToast(data.message);
    await refreshStatus();
  } catch (error) { showToast(error.message, true); }
}

$("#run-all").addEventListener("click", () => startRun("all"));
$("#run-batch").addEventListener("click", () => startRun("batch"));
$("#pause-run").addEventListener("click", () => controlRun("pause"));
$("#stop-run").addEventListener("click", () => {
  if (window.confirm("Stop the active run now? Completed actions will stay saved.")) controlRun("stop");
});
$("#account-search").addEventListener("input", renderAccounts);
$("#status-filter").addEventListener("change", renderAccounts);

$("#target-upload").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const data = await api("/api/targets", { method: "POST", body: form });
    showToast(data.message);
    await Promise.all([refreshStatus(), refreshAccounts()]);
  } catch (error) { showToast(error.message, true); }
  event.target.value = "";
});

Promise.all([refreshStatus(), refreshAccounts()]);
state.statusTimer = window.setInterval(refreshStatus, 1500);
state.accountsTimer = window.setInterval(refreshAccounts, 7000);
