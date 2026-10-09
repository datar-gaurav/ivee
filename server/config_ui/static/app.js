"use strict";

const $ = (id) => document.getElementById(id);
const form = $("settings");
let state = null;

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Ivee": "1" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (response.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("Signed out");
  }
  return response;
}

function showLogin() {
  $("login").hidden = false;
  form.hidden = true;
  $("logout").hidden = true;
  $("password").focus();
}

function formValues() {
  const values = {};
  for (const input of form.querySelectorAll("[name]")) values[input.name] = input.value.trim();
  return values;
}

function showProblems(problems) {
  const box = $("problems");
  box.hidden = !problems || problems.length === 0;
  box.replaceChildren();
  if (box.hidden) return;
  box.append("Fix these before the server can start:");
  const list = document.createElement("ul");
  for (const problem of problems) {
    const item = document.createElement("li");
    item.textContent = problem;
    list.append(item);
  }
  box.append(list);
}

function showStatus(text, kind) {
  const box = $("status");
  box.hidden = !text;
  box.className = `banner ${kind || ""}`;
  box.textContent = text || "";
}

function setMessage(id, text, kind) {
  const el = $(id);
  el.textContent = text;
  el.className = `message ${kind || ""}`;
}

function fill(data) {
  state = data;
  for (const [name, value] of Object.entries(data.values)) {
    const input = form.querySelector(`[name="${name}"]`);
    if (input) input.value = value;
  }
  for (const [name, key] of Object.entries(data.keys)) {
    const input = form.querySelector(`[name="${name}"]`);
    input.value = "";
    input.placeholder = key.set ? `Saved (${key.hint}). Leave empty to keep it.` : "";
    document.querySelector(`[data-key-state="${name}"]`).textContent = key.set ? "A key is saved." : "No key saved.";
  }
  const extraSet = Boolean(data.values.EXTRA_LLM_BASE_URL);
  $("remove-extra").hidden = !extraSet;
  if (extraSet) $("extra").open = true;
  $("identity").value = data.identity;
  $("use-ip").textContent = data.detected_ip || "unknown";
  $("use-ip").disabled = !data.detected_ip;
  $("ota-url").textContent = data.ota_url;
  $("server-ref").textContent = data.server_ref || "unknown";
  showProblems(data.problems);
}

async function load() {
  const response = await api("/api/state");
  const data = await response.json();
  $("login").hidden = true;
  form.hidden = false;
  $("logout").hidden = false;
  fill(data);
  refreshHealth();
}

async function refreshHealth() {
  try {
    const data = await (await api("/api/health")).json();
    const up = (ok) => (ok ? "running" : "not running");
    $("health").textContent = `Server ${up(data.health.server)} · Voice ${up(data.health.voice)}`;
    return data;
  } catch {
    return null;
  }
}

// Wait for ivee-apply to finish (it writes apply.status), then report.
async function waitForApply(request) {
  for (let i = 0; i < 40; i++) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const data = await refreshHealth();
    const status = data && data.apply_status;
    if (status && status.request === request) {
      showStatus(status.ok ? "Saved and applied. The device picks up the changes on its next conversation."
                           : `Saved, but applying failed: ${status.message}`, status.ok ? "ok" : "warn");
      return;
    }
  }
  showStatus("Saved. The server is still restarting; check again in a minute.", "warn");
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("login-message", "");
  const response = await api("/api/login", { password: $("password").value });
  if (response.ok) {
    $("password").value = "";
    await load();
  } else {
    const data = await response.json().catch(() => ({}));
    setMessage("login-message", data.message || "Couldn't sign in.", "bad");
  }
});

$("logout").addEventListener("click", async () => {
  await api("/api/logout", {});
  showLogin();
});

$("use-ip").addEventListener("click", () => {
  form.querySelector('[name="SERVER_LAN_IP"]').value = state.detected_ip;
});

$("reset-identity").addEventListener("click", () => {
  $("identity").value = state.default_identity;
});

$("test-groq").addEventListener("click", async () => {
  const button = $("test-groq");
  button.disabled = true;
  setMessage("groq-message", "Testing…");
  try {
    const apiKey = form.querySelector('[name="GROQ_API_KEY"]').value.trim();
    const data = await (await api("/api/test-groq", { api_key: apiKey, values: formValues() })).json();
    setMessage("groq-message", data.message, data.ok ? "good" : "bad");
  } finally {
    button.disabled = false;
  }
});

$("test-extra").addEventListener("click", async () => {
  const button = $("test-extra");
  button.disabled = true;
  setMessage("extra-message", "Asking it for a reply…");
  try {
    const data = await (await api("/api/test-extra", { values: formValues() })).json();
    setMessage("extra-message", data.message, data.ok ? "good" : "bad");
  } finally {
    button.disabled = false;
  }
});

$("remove-extra").addEventListener("click", async () => {
  const data = await (await api("/api/save", { values: {}, remove_extra: true })).json();
  if (!data.ok) {
    showProblems(data.problems || [data.message]);
    return;
  }
  await load();
  if (data.apply_request) {
    showStatus("Extra provider removed. Restarting the server…");
    await waitForApply(data.apply_request);
  } else {
    showStatus("Extra provider removed. Restart the server to apply.", "ok");
  }
});

$("preview").addEventListener("click", async () => {
  const button = $("preview");
  button.disabled = true;
  setMessage("preview-message", "Speaking…");
  try {
    const response = await api("/api/preview", { text: $("preview-text").value, values: formValues() });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      setMessage("preview-message", data.message || (data.problems || []).join(" ") || "Preview failed.", "bad");
      return;
    }
    const player = $("player");
    if (player.src) URL.revokeObjectURL(player.src);
    player.src = URL.createObjectURL(await response.blob());
    player.hidden = false;
    setMessage("preview-message", "");
    await player.play().catch(() => {});
  } finally {
    button.disabled = false;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("save");
  button.disabled = true;
  showStatus("");
  try {
    const response = await api("/api/save", { values: formValues(), identity: $("identity").value });
    const data = await response.json();
    if (!data.ok) {
      showProblems(data.problems || [data.message]);
      showStatus("Not saved.", "warn");
      return;
    }
    await load();
    if (data.apply_request) {
      showStatus("Saved. Restarting the server…");
      await waitForApply(data.apply_request);
    } else {
      showStatus("Saved. Restart the server to apply (Ctrl-C, then ./server/run-mac.sh).", "ok");
    }
  } finally {
    button.disabled = false;
  }
});

load().catch(() => {});
