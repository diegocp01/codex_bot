const state = {
  bots: [],
  selectedId: localStorage.getItem("codex-bots:selected") || "chief-of-staff",
  messages: [],
  runtime: null,
  browser: null,
  routines: [],
  runs: [],
  attachments: [],
  signature: "",
  search: "",
  identity: { color: "#2687e9", shape: "diamond" },
};

const requestToken = document.querySelector('meta[name="codex-bots-request-token"]')?.content || "";

function apiFetch(url, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-Codex-Bots-Token", requestToken);
  }
  return fetch(url, { ...options, headers });
}

const $ = (selector) => document.querySelector(selector);
const list = $("#bot-list");
const messages = $("#messages");
const conversation = $("#conversation");
const input = $("#message-input");
const form = $("#composer");
const sendButton = $("#send-button");
const stopButton = $("#stop-button");
const modal = $("#new-bot-modal");
const routineModal = $("#routine-modal");
const drawer = $("#info-drawer");
const routinesDrawer = $("#routines-drawer");
const sidebar = $(".sidebar");
const chatPanel = $(".chat-panel");
const appShell = $(".app-shell");
let activeModal = null;
let modalReturnFocus = null;

function syncMobileInert() {
  const mobile = window.matchMedia("(max-width: 700px)").matches;
  const chatOpen = document.body.classList.contains("chat-open");
  sidebar.inert = mobile && chatOpen;
  chatPanel.inert = mobile && !chatOpen;
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderMarkdown(value = "") {
  const codeBlocks = [];
  let safe = escapeHtml(value).replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const token = `%%CODEBLOCK${codeBlocks.length}%%`;
    codeBlocks.push(`<pre><code data-language="${escapeHtml(lang)}">${code.trim()}</code></pre>`);
    return token;
  });
  safe = safe
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/^### (.+)$/gm, "<strong>$1</strong>")
    .replace(/^[-*] (.+)$/gm, "<li>$1</li>")
    .replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>")
    .split(/\n{2,}/)
    .map((block) => block.startsWith("<pre") || block.startsWith("<ul") || block.startsWith("%%CODEBLOCK") ? block : `<p>${block.replaceAll("\n", "<br>")}</p>`)
    .join("");
  codeBlocks.forEach((block, index) => { safe = safe.replace(`%%CODEBLOCK${index}%%`, block); });
  return safe;
}

function avatar(bot, small = false) {
  const shape = ["orb", "hex", "squircle", "diamond"].includes(bot?.shape) ? bot.shape : "orb";
  const color = /^#[0-9a-f]{6}$/i.test(bot?.color || "") ? bot.color : "#7957e8";
  return `<span class="bot-avatar ${shape}${small ? " small" : ""}" style="--bot-color:${color}" aria-hidden="true"><span class="bot-face"></span></span>`;
}

function formatTime(value, compact = false) {
  if (!value) return "";
  const date = new Date(value);
  const now = new Date();
  if (compact && date.toDateString() !== now.toDateString()) {
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
    return date.toLocaleDateString([], { weekday: "short" });
  }
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatSchedule(schedule = {}) {
  const kind = schedule.kind || "manual";
  if (kind === "manual") return "On demand";
  if (kind === "hourly") return "Every hour";
  const time = schedule.time_local || "09:00";
  if (kind === "daily") return `Daily · ${time}`;
  if (kind === "weekdays") return `Weekdays · ${time}`;
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return `${days[Number(schedule.weekday) || 0]} · ${time}`;
}

function formatNext(value) {
  if (!value) return "Ready when you are";
  const date = new Date(value);
  return `Next ${date.toLocaleDateString([], { weekday: "short" })} at ${date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
}

function botById(id) { return state.bots.find((bot) => bot.id === id); }

function renderBotList() {
  const query = state.search.toLowerCase();
  const filtered = state.bots.filter((bot) => `${bot.name} ${bot.title}`.toLowerCase().includes(query));
  if (!filtered.length) {
    list.innerHTML = `<p class="bot-list-empty">No Bots match “${escapeHtml(state.search)}”.</p>`;
    return;
  }
  list.innerHTML = filtered.map((bot) => {
    const working = bot.status === "working";
    const preview = working ? (bot.status_text || "Working") : (bot.latest_message || bot.title);
    return `<button class="bot-row ${bot.id === state.selectedId ? "active" : ""}" type="button" data-bot-id="${escapeHtml(bot.id)}" aria-current="${bot.id === state.selectedId ? "true" : "false"}">
      ${avatar(bot)}
      <span class="bot-row-name">${escapeHtml(bot.name)}</span>
      <time class="bot-row-time">${formatTime(bot.latest_at || bot.updated_at, true)}</time>
      <span class="bot-row-preview">${escapeHtml(preview)}</span>
      ${working ? '<span class="bot-row-status working" aria-label="Working"></span>' : ""}
    </button>`;
  }).join("");
}

function renderHeader() {
  const bot = botById(state.selectedId);
  if (!bot) return;
  $("#header-avatar").innerHTML = avatar(bot, true);
  $("#bot-name").textContent = bot.name;
  $("#bot-title").textContent = bot.status === "working" ? (bot.status_text || "Working") : bot.title;
  const info = $("#info-content");
  const recentRuns = state.runs.slice(0, 3);
  info.innerHTML = `<div class="info-profile">${avatar(bot)}<div><h3>${escapeHtml(bot.name)}</h3><p>${escapeHtml(bot.title)}</p></div></div>
    <div class="info-description">${escapeHtml(bot.description)}</div>
    <div class="info-meta"><div><span>Runtime</span><strong>Local Codex</strong></div><div><span>Model</span><strong>${escapeHtml(state.runtime?.model || "GPT-5.6")}</strong></div><div><span>Status</span><strong>${escapeHtml(bot.status === "working" ? "Working" : bot.status === "error" ? "Needs attention" : "Ready")}</strong></div></div>
    <div class="run-history"><h3>Recent activity</h3>${recentRuns.length ? recentRuns.map((run) => `<div class="run-row"><span class="run-status ${escapeHtml(run.status)}"></span><div><strong>${escapeHtml(run.trigger === "routine" ? "Routine" : run.trigger === "handoff" ? "Handoff" : "Message")}</strong><small>${escapeHtml(run.status)} · ${formatTime(run.created_at)}</small></div></div>`).join("") : '<p class="empty-copy">No runs yet.</p>'}</div>`;
  stopButton.hidden = bot.status !== "working";
  sendButton.hidden = bot.status === "working";
  input.disabled = bot.status === "working";
  conversation.setAttribute("aria-busy", bot.status === "working" ? "true" : "false");
  updateSendButton();
}

function attachmentCards(items = []) {
  if (!items.length) return "";
  return `<div class="attachment-list">${items.map((item) => `<div class="attachment-card"><span class="file-icon">FILE</span><span class="file-copy"><strong>${escapeHtml(item.name)}</strong><small>Local workspace</small></span></div>`).join("")}</div>`;
}

function handoffCard(message) {
  const outgoing = message.kind === "handoff_out";
  const title = outgoing ? "Teammate handoff" : message.kind === "handoff_result" ? `${message.author_name} reported back` : `From ${message.author_name}`;
  return `<article class="message system"><div class="handoff-card"><span class="handoff-icon"><svg viewBox="0 0 24 24"><path d="M7 7h10v10M17 7 7 17"/></svg></span><div class="handoff-copy"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message.content)}</p></div></div></article>`;
}

function renderMessages(forceScroll = false) {
  const selected = botById(state.selectedId);
  const nearBottom = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 120;
  const rows = [];
  let lastDay = "";
  state.messages.forEach((message) => {
    const day = new Date(message.created_at).toDateString();
    if (day !== lastDay) {
      rows.push(`<div class="day-divider">${day === new Date().toDateString() ? "Today" : escapeHtml(new Date(message.created_at).toLocaleDateString([], { month: "short", day: "numeric" }))}</div>`);
      lastDay = day;
    }
    if (["handoff_out", "handoff_in", "handoff_result"].includes(message.kind)) {
      rows.push(handoffCard(message));
      return;
    }
    if (message.kind === "routine_run") {
      rows.push(`<article class="message system"><div class="handoff-card routine-message"><span class="handoff-icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg></span><div class="handoff-copy"><strong>${escapeHtml(message.author_name)}</strong><p>${escapeHtml(message.content)}</p></div></div></article>`);
      return;
    }
    if (message.kind === "stopped") {
      rows.push(`<article class="message system"><div class="handoff-card"><span class="handoff-icon">■</span><div class="handoff-copy"><strong>Run stopped</strong><p>${escapeHtml(message.content)}</p></div></div></article>`);
      return;
    }
    if (message.kind === "error") {
      rows.push(`<article class="message system"><div class="error-card"><span class="handoff-icon">!</span><div class="handoff-copy"><strong>Needs attention</strong><p>${escapeHtml(message.content)}</p></div></div></article>`);
      return;
    }
    const isUser = message.role === "user";
    const classes = `${isUser ? "user" : "assistant"} ${message.kind === "welcome" ? "welcome" : ""}`;
    const author = !isUser && message.author_name !== selected?.name
      ? `<div class="message-author">${avatar(botById(message.metadata?.from_bot) || selected, false)}<span>${escapeHtml(message.author_name)}</span></div>` : "";
    rows.push(`<article class="message ${classes}">${author}<div class="message-bubble">${renderMarkdown(message.content)}${attachmentCards(message.metadata?.attachments)}</div><time class="message-time">${formatTime(message.created_at)}</time></article>`);
  });
  if (selected?.status === "working") {
    rows.push(`<article class="message assistant" aria-label="${escapeHtml(selected.name)} is working"><div class="thinking"><span></span><span></span><span></span></div></article>`);
  }
  messages.innerHTML = rows.join("");
  if (forceScroll || nearBottom) requestAnimationFrame(() => { conversation.scrollTop = conversation.scrollHeight; });
}

function renderRuntime() {
  const card = $("#runtime-card");
  card.classList.toggle("ready", Boolean(state.runtime?.ready));
  card.classList.toggle("error", state.runtime && !state.runtime.ready);
  $("#runtime-label").textContent = state.runtime?.ready
    ? `${state.runtime.model} · ${state.runtime.version || "Ready"}`
    : (state.runtime?.message || "Codex CLI unavailable");
}

function renderBrowser() {
  const card = $("#browser-ready-card");
  card.classList.toggle("ready", Boolean(state.browser?.ready));
  card.classList.toggle("error", Boolean(state.browser && !state.browser.ready));
  $("#browser-ready-label").textContent = state.browser?.ready
    ? `${state.browser.engine} · isolated per Bot`
    : (state.browser?.message || "Browser unavailable");
}

function renderRoutines() {
  const node = $("#routine-list");
  if (!state.routines.length) {
    node.innerHTML = `<div class="routine-empty"><span class="empty-glyph"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg></span><h3>No routines yet</h3><p>Turn repeat work into a schedule or a one-click run.</p><button class="primary-button" type="button" data-new-routine>Create routine</button></div>`;
    return;
  }
  node.innerHTML = state.routines.map((routine) => `<article class="routine-card" data-routine-id="${escapeHtml(routine.id)}">
    <div class="routine-card-head">${avatar({ color: routine.bot_color, shape: routine.bot_shape }, true)}<div><h3>${escapeHtml(routine.name)}</h3><p>${escapeHtml(routine.bot_name || "Bot")}</p></div><span class="routine-state ${escapeHtml(routine.last_status)}">${escapeHtml(routine.last_status === "never" ? "New" : routine.last_status)}</span></div>
    <p class="routine-prompt">${escapeHtml(routine.prompt)}</p>
    <div class="routine-schedule"><strong>${escapeHtml(formatSchedule(routine.schedule))}</strong><span>${escapeHtml(routine.enabled ? formatNext(routine.next_run_at) : routine.schedule.kind === "manual" ? "Manual routine" : "Paused")}</span></div>
    <div class="routine-actions"><button class="secondary-button compact" type="button" data-run-routine>Run now</button>${routine.schedule.kind !== "manual" ? `<button class="text-button" type="button" data-toggle-routine>${routine.enabled ? "Pause" : "Resume"}</button>` : ""}<button class="text-button danger" type="button" data-delete-routine>Delete</button></div>
  </article>`).join("");
}

function render(forceScroll = false) {
  renderBotList();
  renderHeader();
  renderMessages(forceScroll);
  renderRuntime();
  renderBrowser();
  renderRoutines();
}

async function refresh(force = false) {
  try {
    const response = await fetch(`/api/state?bot_id=${encodeURIComponent(state.selectedId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load the local workspace.");
    const payload = await response.json();
    if (!payload.selected && payload.bots.length) state.selectedId = payload.bots[0].id;
    const signature = JSON.stringify([payload.bots, payload.messages, payload.routines, payload.runs]);
    state.bots = payload.bots;
    state.messages = payload.messages;
    state.runtime = payload.runtime;
    state.browser = payload.browser;
    state.routines = payload.routines || [];
    state.runs = payload.runs || [];
    if (signature !== state.signature || force) {
      state.signature = signature;
      render(force);
    } else {
      renderRuntime();
      renderBrowser();
    }
  } catch (error) {
    toast(error.message, true);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const content = input.value.trim();
  if (!content && !state.attachments.length) return;
  const payload = { content, attachments: state.attachments };
  input.value = "";
  input.style.height = "auto";
  state.attachments = [];
  renderAttachmentTray();
  updateSendButton();
  try {
    const response = await apiFetch(`/api/bots/${encodeURIComponent(state.selectedId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not send the message.");
    await refresh(true);
  } catch (error) {
    input.value = content;
    toast(error.message, true);
  }
}

async function uploadFile(file) {
  if (!file) return;
  const data = new FormData();
  data.append("file", file);
  toast(`Attaching ${file.name}…`);
  try {
    const response = await apiFetch("/api/uploads", { method: "POST", body: data });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Upload failed.");
    state.attachments.push(result);
    renderAttachmentTray();
    updateSendButton();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderAttachmentTray() {
  const tray = $("#attachment-tray");
  tray.hidden = !state.attachments.length;
  tray.innerHTML = state.attachments.map((item, index) => `<div class="attachment-chip"><span>${escapeHtml(item.name)}</span><button type="button" data-remove-attachment="${index}" aria-label="Remove ${escapeHtml(item.name)}">×</button></div>`).join("");
}

function updateSendButton() {
  const working = botById(state.selectedId)?.status === "working";
  sendButton.disabled = working || (!input.value.trim() && !state.attachments.length);
}

function toast(message, error = false) {
  const node = document.createElement("div");
  node.className = `toast${error ? " error" : ""}`;
  node.textContent = message;
  $("#toast-region").append(node);
  setTimeout(() => node.remove(), 4200);
}

function syncOverlayInert() {
  appShell.inert = Boolean(activeModal);
  drawer.inert = Boolean(activeModal) || !drawer.classList.contains("open");
  routinesDrawer.inert = Boolean(activeModal) || !routinesDrawer.classList.contains("open");
}

function openDialog(node, focusSelector = "input") {
  if (activeModal && activeModal !== node) activeModal.hidden = true;
  modalReturnFocus = document.activeElement;
  activeModal = node;
  node.hidden = false;
  syncOverlayInert();
  requestAnimationFrame(() => node.querySelector(focusSelector)?.focus());
}

function closeModal(node = activeModal) {
  if (!node || node.hidden) return;
  node.hidden = true;
  if (activeModal === node) activeModal = null;
  syncOverlayInert();
  syncMobileInert();
  if (modalReturnFocus?.isConnected) modalReturnFocus.focus();
  modalReturnFocus = null;
}

function openModal() { openDialog(modal); }

function openDrawer(node) {
  [drawer, routinesDrawer].forEach((candidate) => {
    const open = candidate === node;
    candidate.classList.toggle("open", open);
    candidate.setAttribute("aria-hidden", open ? "false" : "true");
  });
  syncOverlayInert();
  requestAnimationFrame(() => node.querySelector("button")?.focus());
}

function closeDrawer(node) {
  node.classList.remove("open");
  node.setAttribute("aria-hidden", "true");
  syncOverlayInert();
}

function updateScheduleFields() {
  const kind = $("#routine-schedule").value;
  const fields = $("#schedule-fields");
  fields.hidden = ["manual", "hourly"].includes(kind);
  $("#routine-weekday-label").hidden = kind !== "weekly";
}

function openRoutineModal() {
  const select = $("#routine-bot");
  select.innerHTML = state.bots.map((bot) => `<option value="${escapeHtml(bot.id)}"${bot.id === state.selectedId ? " selected" : ""}>${escapeHtml(bot.name)}</option>`).join("");
  updateScheduleFields();
  openDialog(routineModal);
}

async function createBot(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  Object.assign(data, state.identity);
  try {
    const response = await apiFetch("/api/bots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not create the Bot.");
    state.selectedId = result.id;
    localStorage.setItem("codex-bots:selected", result.id);
    event.currentTarget.reset();
    closeModal();
    document.body.classList.add("chat-open");
    syncMobileInert();
    await refresh(true);
  } catch (error) { toast(error.message, true); }
}

async function createRoutine(event) {
  event.preventDefault();
  const fields = Object.fromEntries(new FormData(event.currentTarget));
  const schedule = { kind: fields.schedule_kind };
  if (["daily", "weekdays", "weekly"].includes(schedule.kind)) schedule.time_local = fields.time_local || "09:00";
  if (schedule.kind === "weekly") schedule.weekday = Number(fields.weekday || 0);
  try {
    const response = await apiFetch("/api/routines", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: fields.name,
        bot_id: fields.bot_id,
        prompt: fields.prompt,
        schedule_kind: schedule.kind,
        time_local: schedule.time_local,
        weekday: schedule.weekday,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not create the routine.");
    event.currentTarget.reset();
    closeModal(routineModal);
    await refresh(true);
    openDrawer(routinesDrawer);
    toast("Routine created.");
  } catch (error) { toast(error.message, true); }
}

list.addEventListener("click", (event) => {
  const row = event.target.closest("[data-bot-id]");
  if (!row) return;
  state.selectedId = row.dataset.botId;
  state.attachments = [];
  localStorage.setItem("codex-bots:selected", state.selectedId);
  document.body.classList.add("chat-open");
  syncMobileInert();
  renderAttachmentTray();
  refresh(true);
});

form.addEventListener("submit", sendMessage);
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
  updateSendButton();
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (!sendButton.disabled) form.requestSubmit();
  }
});

$("#bot-search").addEventListener("input", (event) => { state.search = event.target.value; renderBotList(); });
$("#new-bot-button").addEventListener("click", openModal);
[modal, routineModal].forEach((node) => {
  node.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", () => closeModal(node)));
  node.addEventListener("click", (event) => { if (event.target === node) closeModal(node); });
});
$("#new-bot-form").addEventListener("submit", createBot);
$("#routine-form").addEventListener("submit", createRoutine);
$("#routine-schedule").addEventListener("change", updateScheduleFields);
$("#identity-picker").addEventListener("click", (event) => {
  const option = event.target.closest(".identity-option");
  if (!option) return;
  $("#identity-picker").querySelectorAll(".identity-option").forEach((item) => item.classList.toggle("selected", item === option));
  state.identity = { color: option.dataset.color, shape: option.dataset.shape };
});

$("#attach-button").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", (event) => { uploadFile(event.target.files[0]); event.target.value = ""; });
$("#attachment-tray").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-attachment]");
  if (!button) return;
  state.attachments.splice(Number(button.dataset.removeAttachment), 1);
  renderAttachmentTray();
  updateSendButton();
});

stopButton.addEventListener("click", async () => {
  await apiFetch(`/api/bots/${encodeURIComponent(state.selectedId)}/stop`, { method: "POST" });
  toast("Stop requested.");
  refresh();
});

$("#mobile-back").addEventListener("click", () => { document.body.classList.remove("chat-open"); syncMobileInert(); });
$("#bot-info-button").addEventListener("click", () => openDrawer(drawer));
$("#close-info").addEventListener("click", () => closeDrawer(drawer));
$("#routines-button").addEventListener("click", () => openDrawer(routinesDrawer));
$("#close-routines").addEventListener("click", () => closeDrawer(routinesDrawer));
$("#new-routine-button").addEventListener("click", openRoutineModal);
$("#routine-list").addEventListener("click", async (event) => {
  if (event.target.closest("[data-new-routine]")) { openRoutineModal(); return; }
  const card = event.target.closest("[data-routine-id]");
  if (!card) return;
  const routine = state.routines.find((item) => item.id === card.dataset.routineId);
  if (!routine) return;
  try {
    if (event.target.closest("[data-run-routine]")) {
      const response = await apiFetch(`/api/routines/${encodeURIComponent(routine.id)}/run`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not run the routine.");
      toast(`${routine.name} started.`);
    } else if (event.target.closest("[data-toggle-routine]")) {
      const response = await apiFetch(`/api/routines/${encodeURIComponent(routine.id)}/enabled`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !routine.enabled }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not update the routine.");
      toast(routine.enabled ? "Routine paused." : "Routine resumed.");
    } else if (event.target.closest("[data-delete-routine]")) {
      if (!window.confirm(`Delete “${routine.name}”?`)) return;
      const response = await apiFetch(`/api/routines/${encodeURIComponent(routine.id)}`, { method: "DELETE" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not delete the routine.");
      toast("Routine deleted.");
    } else return;
    await refresh(true);
  } catch (error) { toast(error.message, true); }
});
$("#runtime-card").addEventListener("click", () => toast(state.runtime?.ready ? `${state.runtime.version} is ready with ${state.runtime.model}.` : "Install Codex CLI and run `codex login` in Terminal."));

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault(); $("#bot-search").focus();
  }
  if (event.key === "Tab" && activeModal) {
    const focusable = [...activeModal.querySelectorAll("button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])")]
      .filter((node) => node.getClientRects().length);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  if (event.key === "Escape") {
    if (activeModal) closeModal();
    else if (routinesDrawer.classList.contains("open")) closeDrawer(routinesDrawer);
    else if (drawer.classList.contains("open")) closeDrawer(drawer);
  }
});

window.addEventListener("resize", syncMobileInert);
syncMobileInert();
syncOverlayInert();
refresh(true);
setInterval(() => refresh(false), 1200);
