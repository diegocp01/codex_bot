const state = {
  bots: [],
  selectedId: localStorage.getItem("codex-bots:selected") || "chief-of-staff",
  messages: [],
  runtime: null,
  attachments: [],
  signature: "",
  search: "",
  identity: { color: "#2687e9", shape: "diamond" },
};

const $ = (selector) => document.querySelector(selector);
const list = $("#bot-list");
const messages = $("#messages");
const conversation = $("#conversation");
const input = $("#message-input");
const form = $("#composer");
const sendButton = $("#send-button");
const stopButton = $("#stop-button");
const modal = $("#new-bot-modal");
const drawer = $("#info-drawer");
const sidebar = $(".sidebar");
const chatPanel = $(".chat-panel");

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
  info.innerHTML = `<div class="info-profile">${avatar(bot)}<div><h3>${escapeHtml(bot.name)}</h3><p>${escapeHtml(bot.title)}</p></div></div>
    <div class="info-description">${escapeHtml(bot.description)}</div>
    <div class="info-meta"><div><span>Runtime</span><strong>Local Codex</strong></div><div><span>Model</span><strong>${escapeHtml(state.runtime?.model || "GPT-5.6")}</strong></div><div><span>Status</span><strong>${escapeHtml(bot.status === "working" ? "Working" : bot.status === "error" ? "Needs attention" : "Ready")}</strong></div></div>`;
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

function render(forceScroll = false) {
  renderBotList();
  renderHeader();
  renderMessages(forceScroll);
  renderRuntime();
}

async function refresh(force = false) {
  try {
    const response = await fetch(`/api/state?bot_id=${encodeURIComponent(state.selectedId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load the local workspace.");
    const payload = await response.json();
    if (!payload.selected && payload.bots.length) state.selectedId = payload.bots[0].id;
    const signature = JSON.stringify([payload.bots, payload.messages]);
    state.bots = payload.bots;
    state.messages = payload.messages;
    state.runtime = payload.runtime;
    if (signature !== state.signature || force) {
      state.signature = signature;
      render(force);
    } else {
      renderRuntime();
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
    const response = await fetch(`/api/bots/${encodeURIComponent(state.selectedId)}/messages`, {
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
    const response = await fetch("/api/uploads", { method: "POST", body: data });
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

function openModal() {
  modal.hidden = false;
  requestAnimationFrame(() => modal.querySelector("input").focus());
}

function closeModal() { modal.hidden = true; }

async function createBot(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget));
  Object.assign(data, state.identity);
  try {
    const response = await fetch("/api/bots", {
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
modal.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", closeModal));
modal.addEventListener("click", (event) => { if (event.target === modal) closeModal(); });
$("#new-bot-form").addEventListener("submit", createBot);
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
  await fetch(`/api/bots/${encodeURIComponent(state.selectedId)}/stop`, { method: "POST" });
  toast("Stop requested.");
  refresh();
});

$("#mobile-back").addEventListener("click", () => { document.body.classList.remove("chat-open"); syncMobileInert(); });
$("#bot-info-button").addEventListener("click", () => { drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); drawer.inert = false; });
$("#close-info").addEventListener("click", () => { drawer.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); drawer.inert = true; });
$("#runtime-card").addEventListener("click", () => toast(state.runtime?.ready ? `${state.runtime.version} is ready with ${state.runtime.model}.` : "Install Codex CLI and run `codex login` in Terminal."));

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault(); $("#bot-search").focus();
  }
  if (event.key === "Escape") { closeModal(); drawer.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); drawer.inert = true; }
});

window.addEventListener("resize", syncMobileInert);
syncMobileInert();
refresh(true);
setInterval(() => refresh(false), 1200);
