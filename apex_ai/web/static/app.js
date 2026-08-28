"use strict";

const state = {
  currentConversation: null,
  conversations: [],
  messages: [],
  pendingFiles: [],
  memoryCandidates: [],
  generating: false,
  stopping: false,
  requestId: null,
  activeAssistant: null,
  currentView: "chat",
  config: null,
  models: [],
  preferences: {
    theme: localStorage.getItem("apex.theme") || "system",
    enterToSend: localStorage.getItem("apex.enterToSend") !== "false",
    autoScroll: localStorage.getItem("apex.autoScroll") !== "false",
    useMemory: localStorage.getItem("apex.useMemory") !== "false",
  },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const icons = {
  copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>',
  retry: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6v5h-5M4 18v-5h5M6.1 9a7 7 0 0 1 11.3-2.5L20 9M4 15l2.6 2.5A7 7 0 0 0 17.9 15"/></svg>',
  edit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 5 5 5M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/></svg>',
  file: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h8l4 4v16H6zM14 2v5h5"/></svg>',
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...(options.headers || {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
    } catch (_) { /* preserve status */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function escapeHTML(value = "") {
  return String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function safeLink(url) {
  try {
    const parsed = new URL(url, window.location.origin);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "#";
  } catch (_) { return "#"; }
}

function renderMarkdown(source = "") {
  const codeBlocks = [];
  let text = String(source).replace(/```([^\n`]*)\n?([\s\S]*?)```/g, (_, language, code) => {
    const index = codeBlocks.length;
    const lang = (language.trim().match(/^[\w.+#-]{0,24}$/) || [""])[0] || "code";
    codeBlocks.push(`<div class="code-block"><div class="code-header"><span>${escapeHTML(lang)}</span><button class="code-copy" type="button">${icons.copy}<span>Copy code</span></button></div><pre><code>${escapeHTML(code.replace(/\n$/, ""))}</code></pre></div>`);
    return `\nAPEXCODEBLOCK${index}TOKEN\n`;
  });

  text = escapeHTML(text);
  const inlineCodes = [];
  text = text.replace(/`([^`\n]+)`/g, (_, code) => {
    const index = inlineCodes.length;
    inlineCodes.push(`<code>${code}</code>`);
    return `APEXINLINECODE${index}TOKEN`;
  });
  text = text
    .replace(/\[([^\]]+)]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) => `<a href="${escapeHTML(safeLink(url))}" target="_blank" rel="noopener noreferrer">${label}</a>`)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
    .replace(/~~([^~\n]+)~~/g, "<del>$1</del>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  const lines = text.split("\n");
  const output = [];
  let list = null;
  const closeList = () => { if (list) { output.push(`</${list}>`); list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^APEXCODEBLOCK\d+TOKEN$/.test(line.trim())) { closeList(); output.push(line.trim()); continue; }
    if (!line.trim()) { closeList(); continue; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) { closeList(); const level = heading[1].length; output.push(`<h${level}>${heading[2]}</h${level}>`); continue; }
    const unordered = line.match(/^\s*[-+*]\s+(.+)$/);
    if (unordered) { if (list !== "ul") { closeList(); list = "ul"; output.push("<ul>"); } output.push(`<li>${unordered[1]}</li>`); continue; }
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (ordered) { if (list !== "ol") { closeList(); list = "ol"; output.push("<ol>"); } output.push(`<li>${ordered[1]}</li>`); continue; }
    if (/^&gt;\s?/.test(line)) { closeList(); output.push(`<blockquote>${line.replace(/^&gt;\s?/, "")}</blockquote>`); continue; }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { closeList(); output.push("<hr>"); continue; }
    closeList(); output.push(`<p>${line}</p>`);
  }
  closeList();
  let html = output.join("");
  html = html.replace(/APEXCODEBLOCK(\d+)TOKEN/g, (_, index) => codeBlocks[Number(index)] || "");
  html = html.replace(/APEXINLINECODE(\d+)TOKEN/g, (_, index) => inlineCodes[Number(index)] || "");
  return html;
}

function toast(message, type = "") {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  $("#toastRegion").append(item);
  setTimeout(() => item.remove(), 4200);
}

function mergeMemoryCandidates(candidates = []) {
  const byId = new Map(state.memoryCandidates.map(item => [item.id, item]));
  candidates.forEach(item => { if (item && item.id) byId.set(item.id, item); });
  state.memoryCandidates = [...byId.values()];
  renderMemoryCandidates();
}

function renderMemoryCandidates() {
  const region = $("#memoryConfirmationRegion");
  region.replaceChildren();
  region.classList.toggle("has-items", state.memoryCandidates.length > 0);
  state.memoryCandidates.forEach(candidate => {
    const card = document.createElement("section");
    card.className = "memory-confirmation-card";
    card.dataset.candidateId = candidate.id;
    const heading = document.createElement("div"); heading.className = "memory-confirmation-heading";
    const title = document.createElement("strong"); title.textContent = "Save this to long-term memory?";
    const kind = document.createElement("span"); kind.className = "memory-confirmation-kind"; kind.textContent = candidate.kind === "preference" ? "Preference" : "Ongoing context";
    heading.append(title, kind);
    const content = document.createElement("p"); content.className = "memory-confirmation-content"; content.textContent = candidate.content;
    const footer = document.createElement("div"); footer.className = "memory-confirmation-footer";
    const warning = document.createElement("span"); warning.className = "memory-confirmation-warning"; warning.textContent = "Review first · never save secrets";
    const actions = document.createElement("div"); actions.className = "memory-confirmation-actions";
    const reject = document.createElement("button"); reject.type = "button"; reject.className = "memory-confirmation-action reject"; reject.textContent = "Don't save";
    const approve = document.createElement("button"); approve.type = "button"; approve.className = "memory-confirmation-action approve"; approve.textContent = "Remember";
    reject.addEventListener("click", () => decideMemoryCandidate(candidate.id, "reject"));
    approve.addEventListener("click", () => decideMemoryCandidate(candidate.id, "approve"));
    actions.append(reject, approve); footer.append(warning, actions); card.append(heading, content, footer); region.append(card);
  });
}

async function decideMemoryCandidate(candidateId, decision) {
  const card = $(`[data-candidate-id="${CSS.escape(candidateId)}"]`, $("#memoryConfirmationRegion"));
  if (card) $$('button', card).forEach(button => { button.disabled = true; });
  try {
    await api(`/memory/candidates/${encodeURIComponent(candidateId)}/${decision}`, { method: "POST" });
    state.memoryCandidates = state.memoryCandidates.filter(item => item.id !== candidateId);
    renderMemoryCandidates();
    toast(decision === "approve" ? "Saved to long-term memory; prompt use is not enabled yet." : "Memory suggestion dismissed.", "success");
  } catch (error) {
    toast(error.message, "error");
    await loadMemoryCandidates();
  }
}

async function loadMemoryCandidates() {
  try {
    state.memoryCandidates = await api("/memory/candidates");
  } catch (_) {
    state.memoryCandidates = [];
  }
  renderMemoryCandidates();
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: date.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined }).format(date);
}

function setTheme(choice) {
  state.preferences.theme = choice;
  localStorage.setItem("apex.theme", choice);
  const resolved = choice === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : choice;
  document.documentElement.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content = resolved === "dark" ? "#0a0c10" : "#f8f9fc";
  $$('[data-theme-choice]').forEach(button => button.classList.toggle("active", button.dataset.themeChoice === choice));
}

function showView(name) {
  state.currentView = name;
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `${name}View`));
  $$("[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === name));
  if (name === "chat") $("#topbarTitle").textContent = state.currentConversation?.title || "New conversation";
  else $("#topbarTitle").textContent = name[0].toUpperCase() + name.slice(1);
  if (name === "documents") loadDocuments();
  closeMobileSidebar();
}

function openMobileSidebar() { $("#sidebar").classList.add("open"); $("#mobileBackdrop").classList.add("visible"); }
function closeMobileSidebar() { $("#sidebar").classList.remove("open"); $("#mobileBackdrop").classList.remove("visible"); }

async function loadConfig() {
  try {
    state.config = await api("/app-config");
    const status = $("#localStatus");
    status.className = `local-status ${state.config.ready ? "ready" : "error"}`;
    $("strong", status).textContent = state.config.ready ? "Backend ready" : "Backend needs attention";
    $("small", status).textContent = `${state.config.provider} · local`;
    $("#uploadLimit").textContent = `${state.config.max_upload_mb} MB`;
    $("#backendDetails").innerHTML = [
      ["Provider", state.config.provider], ["Active model", state.config.model || "Not selected"],
      ["Embeddings", state.config.embedding_model || "Unavailable"], ["Retrieval", state.config.reranker || "Unavailable"],
      ["Documents", String(state.config.documents || 0)], ["Chunks", String(state.config.chunks || 0)],
    ].map(([label, value]) => `<div class="backend-item"><span>${escapeHTML(label)}</span><strong title="${escapeHTML(value)}">${escapeHTML(value)}</strong></div>`).join("")
      + (!state.config.ready && state.config.startup_error ? `<div class="error-message backend-error">${escapeHTML(state.config.startup_error)}</div>` : "");
    if (!state.config.ready && state.config.startup_error) toast("The AI backend needs configuration. Open Settings for details.", "error");
  } catch (error) {
    $("#localStatus").className = "local-status error";
    $("#backendDetails").innerHTML = `<div class="error-message">${escapeHTML(error.message)}</div>`;
  }
}

async function loadModels() {
  const select = $("#modelSelect");
  try {
    state.models = await api("/models");
    select.innerHTML = "";
    if (!state.models.length) {
      const option = new Option(state.config?.model || `${state.config?.provider || "Local"} · no GGUF detected`, "");
      select.append(option); select.disabled = true; return;
    }
    state.models.forEach(model => {
      const option = new Option(`${model.name} · ${model.size}`, model.name, false, Boolean(model.active));
      select.append(option);
    });
    if (!state.models.some(model => model.active)) select.selectedIndex = 0;
    select.disabled = false;
  } catch (error) {
    select.innerHTML = ""; select.append(new Option("Models unavailable", "")); select.disabled = true;
  }
}

async function selectModel(event) {
  const name = event.target.value;
  if (!name) return;
  event.target.disabled = true;
  try {
    await api("/models/select", { method: "POST", body: JSON.stringify({ name }) });
    toast(`${name} selected. It will load on the next message.`, "success");
    await loadConfig(); await loadModels();
  } catch (error) { toast(error.message, "error"); await loadModels(); }
  finally { event.target.disabled = false; }
}

async function loadConversations(search = "") {
  try {
    state.conversations = await api(`/conversations?search=${encodeURIComponent(search)}`);
    renderConversationList();
  } catch (error) {
    $("#conversationList").innerHTML = `<div class="conversation-empty">Could not load conversations.<br>${escapeHTML(error.message)}</div>`;
  }
}

function renderConversationList() {
  const list = $("#conversationList"); list.replaceChildren();
  $("#historyCount").textContent = state.conversations.length;
  if (!state.conversations.length) {
    const empty = document.createElement("div"); empty.className = "conversation-empty";
    empty.textContent = $("#conversationSearch").value ? "No matching conversations." : "Your real conversations will appear here after you send a message.";
    list.append(empty); return;
  }
  state.conversations.forEach(conversation => {
    const row = document.createElement("div"); row.className = "conversation-item";
    row.classList.toggle("active", state.currentConversation?.id === conversation.id);
    row.dataset.id = conversation.id; row.tabIndex = 0; row.setAttribute("role", "button");
    row.innerHTML = `<span class="conversation-title"></span><span class="conversation-actions"><button class="conversation-action rename-conversation" aria-label="Rename">${icons.edit}</button><button class="conversation-action delete-conversation" aria-label="Delete">${icons.trash}</button></span>`;
    $(".conversation-title", row).textContent = conversation.title;
    row.addEventListener("click", event => {
      if (event.target.closest(".rename-conversation")) { event.stopPropagation(); renameConversation(conversation); return; }
      if (event.target.closest(".delete-conversation")) { event.stopPropagation(); deleteConversation(conversation); return; }
      openConversation(conversation.id);
    });
    row.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key) && !event.target.closest("button")) { event.preventDefault(); openConversation(conversation.id); } });
    list.append(row);
  });
}

async function openConversation(id) {
  if (state.generating) return toast("Stop the current response before switching conversations.");
  try {
    const conversation = await api(`/conversations/${encodeURIComponent(id)}`);
    state.currentConversation = conversation;
    state.messages = conversation.messages;
    showView("chat");
    renderMessages(); renderConversationList(); closeMobileSidebar();
  } catch (error) { toast(error.message, "error"); }
}

function newChat() {
  if (state.generating) return toast("Stop the current response before starting a new chat.");
  state.currentConversation = null; state.messages = [];
  $("#messageInput").value = ""; autoResizeComposer(); renderMessages(); renderConversationList(); showView("chat");
  setTimeout(() => $("#messageInput").focus(), 50);
}

async function renameConversation(conversation) {
  const title = prompt("Rename conversation", conversation.title);
  if (!title || title.trim() === conversation.title) return;
  try {
    const updated = await api(`/conversations/${conversation.id}`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) });
    if (state.currentConversation?.id === updated.id) state.currentConversation = { ...state.currentConversation, ...updated };
    await loadConversations($("#conversationSearch").value); $("#topbarTitle").textContent = updated.title;
  } catch (error) { toast(error.message, "error"); }
}

async function deleteConversation(conversation) {
  const accepted = await confirmAction("Delete conversation?", `“${conversation.title}” and its messages will be permanently removed. Your documents will not be affected.`, "Delete");
  if (!accepted) return;
  try {
    await api(`/conversations/${conversation.id}`, { method: "DELETE" });
    if (state.currentConversation?.id === conversation.id) newChat();
    await loadConversations($("#conversationSearch").value); toast("Conversation deleted.", "success");
  } catch (error) { toast(error.message, "error"); }
}

function renderMessages() {
  const container = $("#messages"); container.replaceChildren();
  if (!state.messages.length) {
    const welcome = buildWelcome(); container.append(welcome); return;
  }
  state.messages.forEach((message, index) => container.append(buildMessage(message, index)));
  requestAnimationFrame(() => scrollToBottom(false));
}

function buildWelcome() {
  const template = document.createElement("template");
  template.innerHTML = `<div class="welcome" id="welcome"><div class="welcome-mark" aria-hidden="true"><span>A</span></div><h1>What can I help you understand?</h1><p>Ask questions across your private documents. Apex AI retrieves the evidence, answers with your local model, and shows exactly where it came from.</p><div class="suggestion-grid"><button class="suggestion" data-prompt="Summarize the key ideas in my documents."><span>Summarize</span><small>Find the most important ideas</small><b>↗</b></button><button class="suggestion" data-prompt="Compare the main recommendations across my documents."><span>Compare sources</span><small>Spot agreement and differences</small><b>↗</b></button><button class="suggestion" data-prompt="What facts in my documents should I verify first?"><span>Verify evidence</span><small>Review claims with citations</small><b>↗</b></button><button class="suggestion" data-view="documents"><span>Add knowledge</span><small>Upload PDF, TXT, MD, or JSON</small><b>＋</b></button></div></div>`;
  const welcome = template.content.firstElementChild;
  $$('[data-prompt]', welcome).forEach(button => button.addEventListener("click", () => { $("#messageInput").value = button.dataset.prompt; autoResizeComposer(); updateSendState(); $("#messageInput").focus(); }));
  $('[data-view="documents"]', welcome).addEventListener("click", () => showView("documents"));
  return welcome;
}

function buildMessage(message, index = 0) {
  const article = document.createElement("article");
  article.className = `message ${message.role}`; article.dataset.messageId = message.id || "";
  if (message.role === "user") {
    const content = document.createElement("div"); content.className = "user-content"; content.textContent = message.content; article.append(content); return article;
  }
  article.innerHTML = `<div class="assistant-avatar">A</div><div class="message-body"><div class="message-author">Apex AI <span></span></div><div class="markdown"></div><div class="citations"></div><div class="message-status"></div><div class="message-actions"><button class="message-action copy-response">${icons.copy}<span>Copy</span></button><button class="message-action regenerate-response">${icons.retry}<span>Regenerate</span></button></div></div>`;
  $(".markdown", article).innerHTML = renderMarkdown(message.content || "");
  const status = $(".message-status", article);
  if (message.status === "stopped") status.textContent = "■ Generation stopped"; else status.remove();
  renderCitations($(".citations", article), message.citations || []);
  $(".copy-response", article).addEventListener("click", event => copyResponse(message.content, event.currentTarget));
  $(".regenerate-response", article).addEventListener("click", () => regenerateResponse());
  return article;
}

function createStreamingAssistant() {
  const element = buildMessage({ id: "streaming", role: "assistant", content: "", citations: [] });
  $(".markdown", element).innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  $(".message-actions", element).remove();
  $("#messages").append(element);
  state.activeAssistant = { element, content: "", renderQueued: false };
  scrollToBottom();
}

function updateStreamingMarkdown() {
  const active = state.activeAssistant;
  if (!active || active.renderQueued) return;
  active.renderQueued = true;
  requestAnimationFrame(() => {
    if (!state.activeAssistant) return;
    $(".markdown", active.element).innerHTML = renderMarkdown(active.content) || '<span class="typing"><i></i><i></i><i></i></span>';
    active.renderQueued = false;
    if (state.preferences.autoScroll) scrollToBottom();
  });
}

function finalizeStreaming(message, citations = []) {
  if (!state.activeAssistant) return;
  const element = state.activeAssistant.element;
  const complete = { ...message, citations: citations.length ? citations : (message.citations || []) };
  element.replaceWith(buildMessage(complete, state.messages.length));
  state.messages.push(complete); state.activeAssistant = null;
}

function citationPage(citation) {
  if (citation.page == null) return null;
  return citation.page_end != null && citation.page_end !== citation.page
    ? `${citation.page}–${citation.page_end}` : String(citation.page);
}
function renderCitations(container, citations) {
  container.replaceChildren();
  citations.forEach(citation => {
    const button = document.createElement("button"); button.className = "citation-button";
    const page = citationPage(citation);
    const location = page ? `p. ${page}` : (citation.section || "source");
    button.innerHTML = `<span class="citation-index">${escapeHTML(citation.index)}</span><span></span>`;
    $("span:last-child", button).textContent = `${citation.source} · ${location}`;
    button.title = citation.label || citation.source;
    button.addEventListener("click", () => openSource(citation)); container.append(button);
  });
}

function openSource(citation) {
  $("#sourceTitle").textContent = citation.source || "Source";
  const meta = $("#sourceMeta"); meta.replaceChildren();
  [["Citation", `[${citation.index}]`], ["Page", citationPage(citation) || "Not available"], ["Section", citation.section || "Not labeled"], ["Score", citation.score != null ? Number(citation.score).toFixed(4) : "—"]].forEach(([key, value]) => { const span = document.createElement("span"); span.textContent = `${key}: ${value}`; meta.append(span); });
  $("#sourceText").textContent = citation.text || "The source text is unavailable for this older message.";
  $("#sourceDrawer").classList.add("open"); $("#sourceDrawer").setAttribute("aria-hidden", "false");
}
function closeSource() { $("#sourceDrawer").classList.remove("open"); $("#sourceDrawer").setAttribute("aria-hidden", "true"); }

async function copyResponse(text, button) {
  try {
    await navigator.clipboard.writeText(text || "");
    const old = button.innerHTML; button.innerHTML = `${icons.check}<span>Copied</span>`; setTimeout(() => { button.innerHTML = old; }, 1500);
  } catch (_) { toast("Clipboard access was blocked by the browser.", "error"); }
}

function autoResizeComposer() {
  const input = $("#messageInput"); input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 190)}px`;
}
function updateSendState() { $("#sendButton").disabled = state.generating ? false : (!$("#messageInput").value.trim() && !state.pendingFiles.length); }
function scrollToBottom(smooth = true) { const scroll = $("#messagesScroll"); scroll.scrollTo({ top: scroll.scrollHeight, behavior: smooth ? "smooth" : "auto" }); }

async function sendMessage({ regenerate = false } = {}) {
  if (state.generating) { await stopGeneration(); return; }
  let question = $("#messageInput").value.trim();
  if (!regenerate && !question && state.pendingFiles.length) question = state.pendingFiles.length === 1 ? "Summarize the document I just uploaded." : "Summarize the documents I just uploaded.";
  if (!regenerate && !question) return;

  if (state.pendingFiles.length) {
    const uploaded = await uploadPendingFiles();
    if (!uploaded) return;
    await loadDocuments(); await loadConfig();
  }

  showView("chat");
  if (!regenerate) {
    const optimistic = { id: `local-${Date.now()}`, role: "user", content: question, citations: [], status: "complete" };
    state.messages.push(optimistic);
    if (state.messages.length === 1) renderMessages(); else $("#messages").append(buildMessage(optimistic));
    $("#messageInput").value = ""; autoResizeComposer();
  } else {
    const assistants = $$(".message.assistant", $("#messages"));
    if (assistants.length) assistants.at(-1).remove();
    const lastAssistantIndex = [...state.messages].map(item => item.role).lastIndexOf("assistant");
    if (lastAssistantIndex >= 0) state.messages.splice(lastAssistantIndex, 1);
  }

  state.generating = true; state.stopping = false;
  state.requestId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  $("#sendButton").classList.add("generating"); $("#sendButton").disabled = false; $("#messageInput").disabled = true; $("#attachButton").disabled = true;
  createStreamingAssistant();

  try {
    const response = await fetch("/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, conversation_id: state.currentConversation?.id || null, request_id: state.requestId, regenerate, use_memory: state.preferences.useMemory }),
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n"); buffer = lines.pop() || "";
      for (const line of lines) if (line.trim()) handleStreamEvent(JSON.parse(line));
      if (done) break;
    }
    if (buffer.trim()) handleStreamEvent(JSON.parse(buffer));
  } catch (error) {
    if (state.activeAssistant) {
      $(".markdown", state.activeAssistant.element).innerHTML = `<div class="error-message">${escapeHTML(error.message)}</div>`;
      state.activeAssistant = null;
    }
    toast("The response could not be completed.", "error");
  } finally {
    state.generating = false; state.stopping = false; state.requestId = null;
    $("#sendButton").classList.remove("generating"); $("#messageInput").disabled = false; $("#attachButton").disabled = false; updateSendState();
    await loadConversations($("#conversationSearch").value);
    $("#messageInput").focus();
  }
}

function handleStreamEvent(event) {
  if (event.type === "meta") {
    state.currentConversation = event.conversation;
    $("#topbarTitle").textContent = event.conversation.title;
    mergeMemoryCandidates(event.memory_candidates || []);
    return;
  }
  if (event.type === "token") { state.activeAssistant.content += event.text; updateStreamingMarkdown(); return; }
  if (event.type === "final") {
    finalizeStreaming(event.message, event.citations || []);
    state.currentConversation = event.conversation;
    if (event.insufficient_evidence) toast("Apex AI did not find enough evidence in the indexed documents.");
    return;
  }
  if (event.type === "stopped") {
    if (event.message) finalizeStreaming(event.message, []);
    else if (state.activeAssistant) { state.activeAssistant.element.remove(); state.activeAssistant = null; }
    toast("Generation stopped."); return;
  }
  if (event.type === "error") {
    if (state.activeAssistant) {
      $(".markdown", state.activeAssistant.element).innerHTML = `<div class="error-message">${escapeHTML(event.message)}</div>`;
      state.activeAssistant = null;
    }
    toast("Apex AI reported an error. See the response for details.", "error");
  }
}

async function stopGeneration() {
  if (!state.generating || state.stopping) return;
  state.stopping = true; $("#sendButton").disabled = true;
  try { await api("/chat/stop", { method: "POST", body: JSON.stringify({ request_id: state.requestId }) }); }
  catch (error) { state.stopping = false; $("#sendButton").disabled = false; toast(error.message, "error"); }
}
function regenerateResponse() {
  if (!state.currentConversation || state.generating) return;
  sendMessage({ regenerate: true });
}

function queueFiles(files) {
  const allowed = ["pdf", "txt", "md", "markdown", "json"];
  [...files].forEach(file => {
    const extension = file.name.split(".").pop().toLowerCase();
    if (!allowed.includes(extension)) { toast(`${file.name}: unsupported file type.`, "error"); return; }
    if (state.config && file.size > state.config.max_upload_mb * 1024 * 1024) { toast(`${file.name} exceeds the upload limit.`, "error"); return; }
    if (!state.pendingFiles.some(item => item.file.name === file.name && item.file.size === file.size)) state.pendingFiles.push({ id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()), file, status: "ready" });
  });
  renderAttachmentTray(); updateSendState();
}

function renderAttachmentTray() {
  const tray = $("#attachmentTray"); tray.classList.toggle("has-files", state.pendingFiles.length > 0); tray.replaceChildren();
  state.pendingFiles.forEach(item => {
    const chip = document.createElement("div"); chip.className = `attachment-chip ${item.status}`; chip.dataset.id = item.id;
    const ext = item.file.name.split(".").pop(); chip.innerHTML = `<span class="file-icon">${escapeHTML(ext)}</span><span class="file-name"></span><button class="remove-file" aria-label="Remove file"><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg></button>`;
    $(".file-name", chip).textContent = item.status === "uploading" ? `Processing ${item.file.name}…` : item.file.name;
    $(".remove-file", chip).disabled = item.status === "uploading";
    $(".remove-file", chip).addEventListener("click", () => { state.pendingFiles = state.pendingFiles.filter(file => file.id !== item.id); renderAttachmentTray(); updateSendState(); });
    tray.append(chip);
  });
}

async function uploadOne(item) {
  item.status = "uploading"; renderAttachmentTray();
  const form = new FormData(); form.append("file", item.file, item.file.name);
  try {
    const result = await api("/documents/upload", { method: "POST", body: form });
    item.status = "done"; item.result = result; renderAttachmentTray(); toast(result.message, "success"); return true;
  } catch (error) { item.status = "error"; item.error = error.message; renderAttachmentTray(); toast(`${item.file.name}: ${error.message}`, "error"); return false; }
}

async function uploadPendingFiles() {
  let success = true;
  for (const item of state.pendingFiles.filter(item => item.status !== "done")) if (!await uploadOne(item)) success = false;
  if (success) { state.pendingFiles = []; renderAttachmentTray(); updateSendState(); }
  return success;
}

async function uploadDocumentPage(files) {
  queueFiles(files);
  const success = await uploadPendingFiles();
  if (success) { await loadDocuments(); await loadConfig(); }
}

async function loadDocuments() {
  try {
    const documents = await api("/documents");
    $("#documentCount").textContent = documents.length;
    $("#librarySummary").textContent = `${documents.length} document${documents.length === 1 ? "" : "s"}`;
    const library = $("#documentLibrary"); library.replaceChildren();
    if (!documents.length) { library.innerHTML = '<div class="library-empty"><div><strong>No documents indexed yet</strong>Drop a file above to give Apex AI grounded knowledge.</div></div>'; return; }
    documents.forEach(item => {
      const row = document.createElement("div"); row.className = "document-row";
      row.innerHTML = `<span class="document-type">${escapeHTML(item.file_type)}</span><div class="document-name"><strong></strong><span>${item.looks_medical ? "Medical content detected" : "General document"}</span></div><div class="document-stat"><b>${item.pages}</b><span>Pages</span></div><div class="document-stat"><b>${item.chunks}</b><span>Chunks</span></div><div class="document-stat"><b>${escapeHTML(formatDate(item.created_at))}</b><span>Added</span></div><div class="document-actions"><button class="reindex-doc" title="Re-index">${icons.retry}</button><button class="delete-doc" title="Delete">${icons.trash}</button></div>`;
      $(".document-name strong", row).textContent = item.name;
      $(".reindex-doc", row).addEventListener("click", () => reindexDocument(item));
      $(".delete-doc", row).addEventListener("click", () => deleteDocument(item)); library.append(row);
    });
  } catch (error) { $("#documentLibrary").innerHTML = `<div class="library-empty"><div><strong>Documents unavailable</strong>${escapeHTML(error.message)}</div></div>`; }
}

async function reindexDocument(document) {
  try { toast(`Re-indexing ${document.name}…`); const result = await api(`/documents/${document.document_id}/reindex`, { method: "POST" }); toast(result.message, "success"); await loadDocuments(); }
  catch (error) { toast(error.message, "error"); }
}
async function deleteDocument(document) {
  const accepted = await confirmAction("Delete document?", `“${document.name}” and all of its indexed vectors will be removed. Existing conversation text will remain.`, "Delete document");
  if (!accepted) return;
  try { await api(`/documents/${document.document_id}`, { method: "DELETE" }); toast(`${document.name} deleted.`, "success"); await loadDocuments(); await loadConfig(); }
  catch (error) { toast(error.message, "error"); }
}

function confirmAction(title, text, actionLabel) {
  return new Promise(resolve => {
    const modal = $("#confirmModal"); $("#confirmTitle").textContent = title; $("#confirmText").textContent = text; $("#confirmAccept").textContent = actionLabel;
    modal.classList.add("open"); modal.setAttribute("aria-hidden", "false");
    const finish = value => { modal.classList.remove("open"); modal.setAttribute("aria-hidden", "true"); $("#confirmAccept").onclick = null; $("#confirmCancel").onclick = null; resolve(value); };
    $("#confirmAccept").onclick = () => finish(true); $("#confirmCancel").onclick = () => finish(false);
  });
}

async function deleteAllConversations() {
  const accepted = await confirmAction("Delete all conversations?", "Every saved conversation and message will be permanently removed. Indexed documents are not affected.", "Delete all");
  if (!accepted) return;
  try { await api("/conversations", { method: "DELETE" }); newChat(); await loadConversations(); toast("All conversations deleted.", "success"); }
  catch (error) { toast(error.message, "error"); }
}

function bindEvents() {
  $("#openSidebar").addEventListener("click", openMobileSidebar); $("#closeSidebar").addEventListener("click", closeMobileSidebar); $("#mobileBackdrop").addEventListener("click", closeMobileSidebar);
  $("#newChatButton").addEventListener("click", newChat);
  $$('[data-view]').forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
  $("#themeQuickToggle").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  $$('[data-theme-choice]').forEach(button => button.addEventListener("click", () => setTheme(button.dataset.themeChoice)));
  $("#modelSelect").addEventListener("change", selectModel);
  $("#messageInput").addEventListener("input", () => { autoResizeComposer(); updateSendState(); });
  $("#messageInput").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey && state.preferences.enterToSend) { event.preventDefault(); sendMessage(); } });
  $("#sendButton").addEventListener("click", () => sendMessage());
  $("#attachButton").addEventListener("click", () => { $("#fileInput").dataset.mode = "composer"; $("#fileInput").click(); });
  $("#pageUploadButton").addEventListener("click", () => { $("#fileInput").dataset.mode = "documents"; $("#fileInput").click(); });
  $("#documentDropZone").addEventListener("click", () => { $("#fileInput").dataset.mode = "documents"; $("#fileInput").click(); });
  $("#documentDropZone").addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); $("#pageUploadButton").click(); } });
  $("#fileInput").addEventListener("change", event => { const files = [...event.target.files]; event.target.value = ""; if (event.target.dataset.mode === "documents") uploadDocumentPage(files); else queueFiles(files); });
  $("#refreshDocuments").addEventListener("click", loadDocuments); $("#closeSource").addEventListener("click", closeSource);
  $("#deleteAllConversations").addEventListener("click", deleteAllConversations);
  $("#enterToSend").checked = state.preferences.enterToSend; $("#autoScroll").checked = state.preferences.autoScroll; $("#useMemory").checked = state.preferences.useMemory;
  [["enterToSend", "enterToSend"], ["autoScroll", "autoScroll"], ["useMemory", "useMemory"]].forEach(([id, key]) => $("#" + id).addEventListener("change", event => { state.preferences[key] = event.target.checked; localStorage.setItem(`apex.${key}`, String(event.target.checked)); }));
  let searchTimer; $("#conversationSearch").addEventListener("input", event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadConversations(event.target.value), 180); });
  document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); newChat(); }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) { event.preventDefault(); openMobileSidebar(); $("#conversationSearch").focus(); }
    if (event.key === "Escape") { closeSource(); closeMobileSidebar(); }
  });
  $("#messages").addEventListener("click", event => { const button = event.target.closest(".code-copy"); if (!button) return; const code = $("code", button.closest(".code-block")).textContent; navigator.clipboard.writeText(code).then(() => { $("span", button).textContent = "Copied"; setTimeout(() => $("span", button).textContent = "Copy code", 1200); }); });

  let dragDepth = 0;
  window.addEventListener("dragenter", event => { if ([...event.dataTransfer.types].includes("Files")) { event.preventDefault(); dragDepth++; if (state.currentView === "chat") $("#dropOverlay").classList.add("visible"); else if (state.currentView === "documents") $("#documentDropZone").classList.add("dragging"); } });
  window.addEventListener("dragover", event => { if ([...event.dataTransfer.types].includes("Files")) event.preventDefault(); });
  window.addEventListener("dragleave", () => { dragDepth--; if (dragDepth <= 0) { dragDepth = 0; $("#dropOverlay").classList.remove("visible"); $("#documentDropZone").classList.remove("dragging"); } });
  window.addEventListener("drop", event => { event.preventDefault(); dragDepth = 0; $("#dropOverlay").classList.remove("visible"); $("#documentDropZone").classList.remove("dragging"); if (event.dataTransfer.files.length) { if (state.currentView === "documents") uploadDocumentPage(event.dataTransfer.files); else { queueFiles(event.dataTransfer.files); showView("chat"); } } });
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => { if (state.preferences.theme === "system") setTheme("system"); });
}

async function initialize() {
  setTheme(state.preferences.theme); bindEvents(); renderMessages(); updateSendState();
  await Promise.all([
    loadConfig(),
    loadConversations(),
    loadDocuments(),
    loadMemoryCandidates(),
  ]);
  await loadModels();
}

document.addEventListener("DOMContentLoaded", initialize);
