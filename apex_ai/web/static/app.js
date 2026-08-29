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
  collections: [],
  // Documents page filter/upload target: null = all documents, "" =
  // uncategorized only, a real ID = that one collection (Phase 66).
  activeCollectionId: null,
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
  thumbUp: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 22V11l5-8 1.5 1L12 11h7a2 2 0 0 1 2 2.4l-1.6 7A2 2 0 0 1 17.4 22H7Z"/><path d="M7 22H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h3"/></svg>',
  thumbDown: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 2v11l-5 8-1.5-1L12 13H5a2 2 0 0 1-2-2.4l1.6-7A2 2 0 0 1 6.6 2H17Z"/><path d="M17 2h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-3"/></svg>',
};

const GENERIC_ERROR_MESSAGE = "Apex AI could not complete that action. Try again.";
const STATUS_ERROR_MESSAGES = Object.freeze({
  400: "Check the request and try again.",
  404: "The requested item was not found.",
  409: "That action conflicts with the current application state.",
  413: "The selected file is too large.",
  415: "That file type is not supported.",
  422: "Check the submitted fields and try again.",
  429: "Too many requests were received. Try again later.",
  500: GENERIC_ERROR_MESSAGE,
  502: "The configured AI provider could not complete the request. Try again.",
  503: "Apex AI is temporarily unavailable. Try again or review Settings.",
});

class ApexAPIError extends Error {
  constructor(problem = {}, status = 0) {
    const message = typeof problem.message === "string" && problem.message.trim()
      ? problem.message : GENERIC_ERROR_MESSAGE;
    super(message);
    this.name = "ApexAPIError";
    this.code = typeof problem.code === "string" ? problem.code : "request_failed";
    this.status = status;
    this.retryable = Boolean(problem.retryable);
    this.fields = Array.isArray(problem.fields) ? problem.fields : [];
  }
}

function errorMessage(error, fallback = GENERIC_ERROR_MESSAGE) {
  return error instanceof ApexAPIError ? error.message : fallback;
}

function safeLegacyMessage(value, fallback = GENERIC_ERROR_MESSAGE) {
  if (typeof value !== "string" || !value.trim()) return fallback;
  const diagnostic = /traceback|\b[A-Z][A-Za-z0-9_.]*(?:Error|Exception)\b/i;
  const credential = /\b(?:bearer\s+\S+|(?:api[ _-]?key|token|password|secret)\s*[:=]|sk-[A-Za-z0-9_-]{8,})/i;
  const location = /(?:https?:\/\/|(?:^|[\s`('" ])(?:[A-Za-z]:[\\/]|~?\/|\.\.?\/))/;
  return diagnostic.test(value) || credential.test(value) || location.test(value) ? fallback : value;
}

async function errorFromResponse(response) {
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* use status fallback */ }
  if (payload?.error && typeof payload.error === "object") {
    return new ApexAPIError(payload.error, response.status);
  }
  const fallback = STATUS_ERROR_MESSAGES[response.status] || GENERIC_ERROR_MESSAGE;
  const legacy = response.status < 500
    ? safeLegacyMessage(payload?.detail, fallback) : fallback;
  return new ApexAPIError({ code: `http_${response.status}`, message: legacy }, response.status);
}

function streamErrorFromEvent(event = {}) {
  const problem = event.error && typeof event.error === "object"
    ? event.error
    : { code: "stream_error", message: GENERIC_ERROR_MESSAGE };
  return new ApexAPIError(problem);
}

async function request(path, options = {}) {
  try {
    return await fetch(path, options);
  } catch (_) {
    throw new ApexAPIError({
      code: "network_error",
      message: "Apex AI could not be reached. Check the connection and try again.",
      retryable: true,
    });
  }
}

async function api(path, options = {}) {
  const response = await request(path, {
    ...options,
    headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...(options.headers || {}) },
  });
  if (!response.ok) throw await errorFromResponse(response);
  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch (_) {
    throw new ApexAPIError({
      code: "invalid_response",
      message: "Apex AI returned an unreadable response. Try again.",
      retryable: true,
    }, response.status);
  }
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

// Phase 16: a small, dependency-free syntax highlighter. This is deliberately not a
// full-grammar tokenizer (no CDN library is loaded for the product UI — see
// docs/CHAT_INTERFACE_ARCHITECTURE.md). It recognizes comments/strings/numbers/keywords
// for a handful of common languages via one alternation regex per language and falls
// back to plain (still safely escaped) text for anything it doesn't recognize, which is
// exactly the prior behavior — so an unrecognized language never regresses.
const CSTYLE_COMMENT = /\/\/[^\n]*|\/\*[\s\S]*?\*\//;
const HASH_COMMENT = /#[^\n]*/;
const DASH_COMMENT = /--[^\n]*/;
const LANG_SYNTAX = {
  javascript: { comment: CSTYLE_COMMENT, keywords: new Set(["function", "const", "let", "var", "return", "if", "else", "for", "while", "in", "of", "class", "extends", "new", "this", "try", "catch", "finally", "throw", "import", "export", "default", "from", "async", "await", "yield", "typeof", "instanceof", "null", "true", "false", "undefined", "break", "continue", "switch", "case", "static", "get", "set", "void", "delete", "do"]) },
  python: { comment: HASH_COMMENT, keywords: new Set(["def", "class", "import", "from", "return", "if", "elif", "else", "for", "while", "in", "not", "and", "or", "try", "except", "finally", "with", "as", "pass", "break", "continue", "lambda", "yield", "None", "True", "False", "self", "raise", "async", "await", "global", "nonlocal", "assert", "del", "is"]) },
  bash: { comment: HASH_COMMENT, keywords: new Set(["if", "then", "else", "elif", "fi", "for", "do", "done", "while", "case", "esac", "function", "export", "local", "return", "echo", "in"]) },
  sql: { comment: DASH_COMMENT, caseInsensitiveKeywords: true, keywords: new Set(["select", "from", "where", "insert", "into", "values", "update", "set", "delete", "join", "left", "right", "inner", "outer", "on", "group", "by", "order", "having", "limit", "create", "table", "alter", "drop", "and", "or", "not", "null", "as", "distinct", "union", "all"]) },
  java: { comment: CSTYLE_COMMENT, keywords: new Set(["public", "private", "protected", "class", "interface", "extends", "implements", "static", "final", "void", "new", "return", "if", "else", "for", "while", "do", "try", "catch", "finally", "throw", "throws", "import", "package", "this", "super", "true", "false", "null", "break", "continue", "switch", "case", "default"]) },
  c: { comment: CSTYLE_COMMENT, keywords: new Set(["int", "char", "float", "double", "void", "struct", "typedef", "return", "if", "else", "for", "while", "do", "switch", "case", "default", "break", "continue", "static", "const", "unsigned", "signed", "sizeof", "include", "define", "null", "NULL"]) },
  go: { comment: CSTYLE_COMMENT, keywords: new Set(["func", "package", "import", "var", "const", "type", "struct", "interface", "return", "if", "else", "for", "range", "switch", "case", "default", "go", "chan", "select", "defer", "map", "break", "continue", "true", "false", "nil"]) },
  rust: { comment: CSTYLE_COMMENT, keywords: new Set(["fn", "let", "mut", "const", "struct", "enum", "impl", "trait", "pub", "use", "mod", "return", "if", "else", "for", "while", "loop", "match", "break", "continue", "true", "false", "self", "Self", "async", "await", "move", "ref", "where"]) },
  json: { comment: null, keywords: new Set(["true", "false", "null"]) },
  yaml: { comment: HASH_COMMENT, keywords: new Set(["true", "false", "null"]) },
};
LANG_SYNTAX.typescript = { comment: CSTYLE_COMMENT, keywords: new Set([...LANG_SYNTAX.javascript.keywords, "interface", "type", "enum", "implements", "public", "private", "protected", "readonly", "namespace", "as", "satisfies"]) };
LANG_SYNTAX.cpp = LANG_SYNTAX.c;
const LANG_ALIASES = { js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript", ts: "typescript", tsx: "typescript", py: "python", py3: "python", sh: "bash", shell: "bash", zsh: "bash", yml: "yaml", "c++": "cpp", cc: "cpp", cxx: "cpp", golang: "go", rs: "rust" };

function highlightCode(code, lang) {
  const spec = LANG_SYNTAX[LANG_ALIASES[lang] || lang];
  if (!spec) return escapeHTML(code);
  const parts = [];
  if (spec.comment) parts.push(spec.comment.source);
  parts.push('"(?:[^"\\\\\\n]|\\\\.)*"', "'(?:[^'\\\\\\n]|\\\\.)*'", "`(?:[^`\\\\]|\\\\.)*`", "\\b\\d+(?:\\.\\d+)?\\b", "[A-Za-z_$][\\w$]*");
  const pattern = new RegExp(parts.join("|"), "g");
  const commentPattern = spec.comment ? new RegExp(`^(?:${spec.comment.source})$`) : null;
  let out = ""; let last = 0; let match;
  while ((match = pattern.exec(code)) !== null) {
    out += escapeHTML(code.slice(last, match.index));
    const text = match[0];
    const first = text[0];
    if (commentPattern && commentPattern.test(text)) out += `<span class="tok-comment">${escapeHTML(text)}</span>`;
    else if (first === '"' || first === "'" || first === "`") out += `<span class="tok-string">${escapeHTML(text)}</span>`;
    else if (/^\d/.test(first)) out += `<span class="tok-number">${escapeHTML(text)}</span>`;
    else {
      const word = spec.caseInsensitiveKeywords ? text.toLowerCase() : text;
      out += spec.keywords.has(word) ? `<span class="tok-keyword">${escapeHTML(text)}</span>` : escapeHTML(text);
    }
    last = match.index + text.length;
  }
  out += escapeHTML(code.slice(last));
  return out;
}

function renderMarkdown(source = "") {
  const codeBlocks = [];
  let text = String(source).replace(/```([^\n`]*)\n?([\s\S]*?)```/g, (_, language, code) => {
    const index = codeBlocks.length;
    const lang = (language.trim().match(/^[\w.+#-]{0,24}$/) || [""])[0] || "code";
    const body = code.replace(/\n$/, "");
    codeBlocks.push(`<div class="code-block"><div class="code-header"><span>${escapeHTML(lang)}</span><button class="code-copy" type="button">${icons.copy}<span>Copy code</span></button></div><pre><code>${highlightCode(body, lang.toLowerCase())}</code></pre></div>`);
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
  // Phase 15: GFM-style pipe tables. A table is a header row immediately followed by a
  // separator row of dashes/colons; this needs one line of lookahead, which is why this
  // loop is index-based rather than a plain for..of like the rest of the parser below.
  const isTableSeparator = raw => /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/.test(raw.trim());
  const splitTableRow = raw => {
    let row = raw.trim();
    if (row.startsWith("|")) row = row.slice(1);
    if (row.endsWith("|")) row = row.slice(0, -1);
    return row.split("|").map(cell => cell.trim());
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd();
    if (/^APEXCODEBLOCK\d+TOKEN$/.test(line.trim())) { closeList(); output.push(line.trim()); continue; }
    if (!line.trim()) { closeList(); continue; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) { closeList(); const level = heading[1].length; output.push(`<h${level}>${heading[2]}</h${level}>`); continue; }
    if (line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      closeList();
      const headerCells = splitTableRow(line);
      output.push(`<div class="table-wrap"><table><thead><tr>${headerCells.map(cell => `<th>${cell}</th>`).join("")}</tr></thead><tbody>`);
      i += 1; // consume the separator row
      while (i + 1 < lines.length && lines[i + 1].trim() && lines[i + 1].includes("|") && !isTableSeparator(lines[i + 1])) {
        i += 1;
        const cells = splitTableRow(lines[i]);
        output.push(`<tr>${cells.map(cell => `<td>${cell}</td>`).join("")}</tr>`);
      }
      output.push("</tbody></table></div>");
      continue;
    }
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
    let conflict = null;
    if (candidate.conflicts_with) {
      conflict = document.createElement("p"); conflict.className = "memory-confirmation-conflict";
      conflict.innerHTML = "May conflict with a saved memory: <b></b>";
      $("b", conflict).textContent = candidate.conflicts_with.content;
    }
    const footer = document.createElement("div"); footer.className = "memory-confirmation-footer";
    const warning = document.createElement("span"); warning.className = "memory-confirmation-warning"; warning.textContent = "Review first · never save secrets";
    const actions = document.createElement("div"); actions.className = "memory-confirmation-actions";
    const reject = document.createElement("button"); reject.type = "button"; reject.className = "memory-confirmation-action reject"; reject.textContent = "Don't save";
    const approve = document.createElement("button"); approve.type = "button"; approve.className = "memory-confirmation-action approve"; approve.textContent = "Remember";
    reject.addEventListener("click", () => decideMemoryCandidate(candidate.id, "reject"));
    approve.addEventListener("click", () => decideMemoryCandidate(candidate.id, "approve"));
    actions.append(reject, approve); footer.append(warning, actions); card.append(heading, content); if (conflict) card.append(conflict); card.append(footer); region.append(card);
  });
}

async function decideMemoryCandidate(candidateId, decision) {
  const card = $(`[data-candidate-id="${CSS.escape(candidateId)}"]`, $("#memoryConfirmationRegion"));
  if (card) $$('button', card).forEach(button => { button.disabled = true; });
  try {
    await api(`/memory/candidates/${encodeURIComponent(candidateId)}/${decision}`, { method: "POST" });
    state.memoryCandidates = state.memoryCandidates.filter(item => item.id !== candidateId);
    renderMemoryCandidates();
    toast(decision === "approve" ? "Saved to long-term memory. Relevant items may shape future answers." : "Memory suggestion dismissed.", "success");
  } catch (error) {
    toast(errorMessage(error), "error");
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
  if (name === "chat") { $("#topbarTitle").textContent = state.currentConversation?.title || "New conversation"; syncConversationCollectionSelect(); }
  else $("#topbarTitle").textContent = name[0].toUpperCase() + name.slice(1);
  if (name === "documents") loadDocuments();
  if (name === "settings") { loadMemories(); loadAccount(); }
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
    $("#backendDetails").innerHTML = `<div class="error-message">${escapeHTML(errorMessage(error))}</div>`;
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
  } catch (error) { toast(errorMessage(error), "error"); await loadModels(); }
  finally { event.target.disabled = false; }
}

async function loadConversations(search = "") {
  try {
    state.conversations = await api(`/conversations?search=${encodeURIComponent(search)}`);
    renderConversationList();
  } catch (error) {
    $("#conversationList").innerHTML = `<div class="conversation-empty">Could not load conversations.<br>${escapeHTML(errorMessage(error))}</div>`;
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
  } catch (error) { toast(errorMessage(error), "error"); }
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
  } catch (error) { toast(errorMessage(error), "error"); }
}

async function deleteConversation(conversation) {
  const accepted = await confirmAction("Delete conversation?", `“${conversation.title}” and its messages will be permanently removed. Your documents will not be affected.`, "Delete");
  if (!accepted) return;
  try {
    await api(`/conversations/${conversation.id}`, { method: "DELETE" });
    if (state.currentConversation?.id === conversation.id) newChat();
    await loadConversations($("#conversationSearch").value); toast("Conversation deleted.", "success");
  } catch (error) { toast(errorMessage(error), "error"); }
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
  article.innerHTML = `<div class="assistant-avatar">A</div><div class="message-body"><div class="message-author">Apex AI <span></span></div><div class="markdown"></div><div class="citations"></div><div class="message-status"></div><div class="message-actions"><button class="message-action copy-response">${icons.copy}<span>Copy</span></button><button class="message-action regenerate-response">${icons.retry}<span>Regenerate</span></button><button class="message-action feedback-up" aria-pressed="false">${icons.thumbUp}<span>Good response</span></button><button class="message-action feedback-down" aria-pressed="false">${icons.thumbDown}<span>Bad response</span></button></div></div>`;
  $(".markdown", article).innerHTML = renderMarkdown(message.content || "");
  const status = $(".message-status", article);
  if (message.status === "stopped") status.textContent = "■ Generation stopped"; else status.remove();
  renderCitations($(".citations", article), message.citations || []);
  $(".copy-response", article).addEventListener("click", event => copyResponse(message.content, event.currentTarget));
  $(".regenerate-response", article).addEventListener("click", () => regenerateResponse());
  const upButton = $(".feedback-up", article);
  const downButton = $(".feedback-down", article);
  if (message.id && message.id !== "streaming") {
    setFeedbackButtonState(upButton, downButton, message.feedback || null);
    upButton.addEventListener("click", () => toggleMessageFeedback(message, "up", upButton, downButton));
    downButton.addEventListener("click", () => toggleMessageFeedback(message, "down", upButton, downButton));
  } else {
    upButton.remove(); downButton.remove();
  }
  return article;
}

function setFeedbackButtonState(upButton, downButton, feedback) {
  upButton.classList.toggle("active", feedback === "up"); upButton.setAttribute("aria-pressed", String(feedback === "up"));
  downButton.classList.toggle("active", feedback === "down"); downButton.setAttribute("aria-pressed", String(feedback === "down"));
}

async function toggleMessageFeedback(message, value, upButton, downButton) {
  const next = message.feedback === value ? null : value; // click again to clear
  const conversationId = state.currentConversation?.id;
  if (!conversationId) return;
  try {
    const updated = await api(`/conversations/${conversationId}/messages/${message.id}/feedback`, {
      method: "POST", body: JSON.stringify({ feedback: next }),
    });
    message.feedback = updated.feedback;
    const stored = state.messages.find(item => item.id === message.id);
    if (stored) stored.feedback = updated.feedback;
    setFeedbackButtonState(upButton, downButton, updated.feedback);
  } catch (error) {
    toast(errorMessage(error), "error");
  }
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
    const response = await request("/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, conversation_id: state.currentConversation?.id || null, request_id: state.requestId, regenerate, use_memory: state.preferences.useMemory, collection_id: state.currentConversation?.id ? null : ($("#conversationCollection").value || null) }),
    });
    if (!response.ok) throw await errorFromResponse(response);
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
      $(".markdown", state.activeAssistant.element).innerHTML = `<div class="error-message">${escapeHTML(errorMessage(error))}</div>`;
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
    syncConversationCollectionSelect();
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
    const message = errorMessage(streamErrorFromEvent(event));
    if (state.activeAssistant) {
      $(".markdown", state.activeAssistant.element).innerHTML = `<div class="error-message">${escapeHTML(message)}</div>`;
      state.activeAssistant = null;
    }
    toast("Apex AI reported an error. See the response for details.", "error");
  }
}

async function stopGeneration() {
  if (!state.generating || state.stopping) return;
  state.stopping = true; $("#sendButton").disabled = true;
  try { await api("/chat/stop", { method: "POST", body: JSON.stringify({ request_id: state.requestId }) }); }
  catch (error) { state.stopping = false; $("#sendButton").disabled = false; toast(errorMessage(error), "error"); }
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
  // Documents page: whatever collection filter is active. Chat composer: the
  // current conversation's own knowledge base, so an attachment is
  // immediately retrievable in that same chat.
  const targetCollection = state.currentView === "documents" ? state.activeCollectionId : state.currentConversation?.collection_id;
  if (targetCollection) form.append("collection_id", targetCollection);
  try {
    const result = await api("/documents/upload", { method: "POST", body: form });
    item.status = "done"; item.result = result; renderAttachmentTray(); toast(result.message, "success"); return true;
  } catch (error) { item.status = "error"; item.error = errorMessage(error); renderAttachmentTray(); toast(`${item.file.name}: ${errorMessage(error)}`, "error"); return false; }
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
    const query = state.activeCollectionId !== null ? `?collection_id=${encodeURIComponent(state.activeCollectionId)}` : "";
    const documents = await api(`/documents${query}`);
    $("#documentCount").textContent = documents.length;
    $("#librarySummary").textContent = `${documents.length} document${documents.length === 1 ? "" : "s"}`;
    const library = $("#documentLibrary"); library.replaceChildren();
    if (!documents.length) { library.innerHTML = '<div class="library-empty"><div><strong>No documents indexed yet</strong>Drop a file above to give Apex AI grounded knowledge.</div></div>'; return; }
    const collectionOptions = collectionId => ['<option value="">Uncategorized</option>', ...state.collections.map(c => `<option value="${escapeHTML(c.id)}"${collectionId === c.id ? " selected" : ""}>${escapeHTML(c.name)}</option>`)].join("");
    documents.forEach(item => {
      const row = document.createElement("div"); row.className = "document-row";
      row.innerHTML = `<span class="document-type">${escapeHTML(item.file_type)}</span><div class="document-name"><strong></strong><span>${item.looks_medical ? "Medical content detected" : "General document"}</span></div><div class="document-stat"><b>${item.pages}</b><span>Pages</span></div><div class="document-stat"><b>${item.chunks}</b><span>Chunks</span></div><div class="document-stat"><b>${escapeHTML(formatDate(item.created_at))}</b><span>Added</span></div><select class="document-collection-select" aria-label="Collection for this document">${collectionOptions(item.collection_id)}</select><div class="document-actions"><button class="reindex-doc" title="Re-index">${icons.retry}</button><button class="delete-doc" title="Delete">${icons.trash}</button></div>`;
      $(".document-name strong", row).textContent = item.name;
      $(".document-collection-select", row).addEventListener("change", event => moveDocumentToCollection(item, event.target.value));
      $(".reindex-doc", row).addEventListener("click", () => reindexDocument(item));
      $(".delete-doc", row).addEventListener("click", () => deleteDocument(item)); library.append(row);
    });
  } catch (error) { $("#documentLibrary").innerHTML = `<div class="library-empty"><div><strong>Documents unavailable</strong>${escapeHTML(errorMessage(error))}</div></div>`; }
}

async function moveDocumentToCollection(item, collectionId) {
  try { await api(`/documents/${item.document_id}/collection`, { method: "PATCH", body: JSON.stringify({ collection_id: collectionId || null }) }); toast(`${item.name} moved.`, "success"); }
  catch (error) { toast(errorMessage(error), "error"); }
  if (state.activeCollectionId !== null) await loadDocuments();
}

async function reindexDocument(document) {
  try { toast(`Re-indexing ${document.name}…`); const result = await api(`/documents/${document.document_id}/reindex`, { method: "POST" }); toast(result.message, "success"); await loadDocuments(); }
  catch (error) { toast(errorMessage(error), "error"); }
}
async function deleteDocument(document) {
  const accepted = await confirmAction("Delete document?", `“${document.name}” and all of its indexed vectors will be removed. Existing conversation text will remain.`, "Delete document");
  if (!accepted) return;
  try { await api(`/documents/${document.document_id}`, { method: "DELETE" }); toast(`${document.name} deleted.`, "success"); await loadDocuments(); await loadConfig(); }
  catch (error) { toast(errorMessage(error), "error"); }
}

// ---------------- collections (Phase 66/67) ----------------

async function loadCollections() {
  try { state.collections = await api("/collections"); }
  catch (_) { state.collections = []; }
  renderCollectionFilterRow();
  renderConversationCollectionOptions();
}

function renderCollectionFilterRow() {
  const row = $("#collectionFilterRow"); if (!row) return;
  row.replaceChildren();
  const makeChip = (id, label) => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "collection-chip";
    button.classList.toggle("active", state.activeCollectionId === id);
    button.textContent = label;
    button.addEventListener("click", () => { state.activeCollectionId = id; renderCollectionFilterRow(); updateUploadCollectionNote(); loadDocuments(); });
    return button;
  };
  row.append(makeChip(null, "All documents"), makeChip("", "Uncategorized"));
  state.collections.forEach(collection => {
    const group = document.createElement("div"); group.className = "collection-chip-group";
    group.innerHTML = `<button type="button" class="collection-chip"></button><button type="button" class="collection-chip-action" title="Rename collection">${icons.edit}</button><button type="button" class="collection-chip-action" title="Delete collection">${icons.trash}</button>`;
    const [selectButton, renameButton, deleteButton] = group.children;
    selectButton.textContent = collection.name;
    selectButton.classList.toggle("active", state.activeCollectionId === collection.id);
    selectButton.addEventListener("click", () => { state.activeCollectionId = collection.id; renderCollectionFilterRow(); updateUploadCollectionNote(); loadDocuments(); });
    renameButton.addEventListener("click", () => renameCollection(collection));
    deleteButton.addEventListener("click", () => deleteCollectionAction(collection));
    row.append(group);
  });
  const addButton = document.createElement("button");
  addButton.type = "button"; addButton.className = "collection-chip collection-chip-add"; addButton.textContent = "+ New collection";
  addButton.addEventListener("click", createCollection);
  row.append(addButton);
  updateUploadCollectionNote();
}

function updateUploadCollectionNote() {
  const note = $("#uploadCollectionNote"); if (!note) return;
  const collection = state.activeCollectionId ? state.collections.find(c => c.id === state.activeCollectionId) : null;
  note.textContent = collection ? `New uploads join "${collection.name}"` : "";
}

async function createCollection() {
  const name = prompt("New collection name");
  if (!name || !name.trim()) return;
  try {
    const created = await api("/collections", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
    await loadCollections();
    state.activeCollectionId = created.id; renderCollectionFilterRow(); loadDocuments();
  } catch (error) { toast(errorMessage(error), "error"); }
}

async function renameCollection(collection) {
  const name = prompt("Rename collection", collection.name);
  if (!name || !name.trim() || name.trim() === collection.name) return;
  try { await api(`/collections/${collection.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) }); await loadCollections(); }
  catch (error) { toast(errorMessage(error), "error"); }
}

async function deleteCollectionAction(collection) {
  const accepted = await confirmAction("Delete collection?", `“${collection.name}” will be removed. Its documents are not deleted — they become uncategorized.`, "Delete collection");
  if (!accepted) return;
  try {
    await api(`/collections/${collection.id}`, { method: "DELETE" });
    if (state.activeCollectionId === collection.id) state.activeCollectionId = null;
    await loadCollections(); await loadDocuments();
    toast(`${collection.name} deleted.`, "success");
  } catch (error) { toast(errorMessage(error), "error"); }
}

function renderConversationCollectionOptions() {
  const select = $("#conversationCollection"); if (!select) return;
  select.innerHTML = '<option value="">All documents</option>' + state.collections.map(c => `<option value="${escapeHTML(c.id)}">${escapeHTML(c.name)}</option>`).join("");
  syncConversationCollectionSelect();
}

function syncConversationCollectionSelect() {
  const select = $("#conversationCollection"); if (!select) return;
  select.value = state.currentConversation?.collection_id || "";
}

async function changeConversationCollection(collectionId) {
  if (!state.currentConversation) return;  // a not-yet-created chat: sendMessage() reads the select directly
  try {
    const updated = await api(`/conversations/${state.currentConversation.id}/collection`, { method: "PATCH", body: JSON.stringify({ collection_id: collectionId || null }) });
    state.currentConversation = { ...state.currentConversation, ...updated };
  } catch (error) { toast(errorMessage(error), "error"); syncConversationCollectionSelect(); }
}

function confirmAction(title, text, actionLabel) {
  return new Promise(resolve => {
    const modal = $("#confirmModal"); $("#confirmTitle").textContent = title; $("#confirmText").textContent = text; $("#confirmAccept").textContent = actionLabel;
    modal.classList.add("open"); modal.setAttribute("aria-hidden", "false");
    const finish = value => { modal.classList.remove("open"); modal.setAttribute("aria-hidden", "true"); $("#confirmAccept").onclick = null; $("#confirmCancel").onclick = null; resolve(value); };
    $("#confirmAccept").onclick = () => finish(true); $("#confirmCancel").onclick = () => finish(false);
  });
}

async function loadAccount() {
  try {
    const user = await api("/auth/me");
    $("#accountDetails").innerHTML = [
      ["Signed in as", user.display_name || user.email], ["Email", user.email],
    ].map(([label, value]) => `<div class="backend-item"><span>${escapeHTML(label)}</span><strong title="${escapeHTML(value)}">${escapeHTML(value)}</strong></div>`).join("");
    $("#accountActions").hidden = false;
  } catch (error) {
    $("#accountDetails").innerHTML = `<div class="error-message">${escapeHTML(errorMessage(error))}</div>`;
    $("#accountActions").hidden = true;
  }
}

async function signOut() {
  try { await api("/auth/logout", { method: "POST" }); } catch (_) { /* proceed to /login regardless */ }
  window.location.href = "/login";
}

async function loadMemories() {
  try {
    const memories = await api("/memory");
    const list = $("#memoryList"); list.replaceChildren();
    if (!memories.length) { list.innerHTML = '<div class="memory-empty">No saved memory yet. Approve a “Remember” suggestion from chat to see it here.</div>'; return; }
    memories.forEach(item => {
      const row = document.createElement("div"); row.className = "memory-row";
      row.innerHTML = `<span class="memory-kind">${item.kind === "preference" ? "Preference" : "Context"}</span><span class="memory-content"></span><button class="delete-memory" title="Delete">${icons.trash}</button>`;
      $(".memory-content", row).textContent = item.content;
      $(".delete-memory", row).addEventListener("click", () => deleteMemory(item));
      list.append(row);
    });
  } catch (error) { $("#memoryList").innerHTML = `<div class="memory-empty">${escapeHTML(errorMessage(error))}</div>`; }
}

async function deleteMemory(memory) {
  const accepted = await confirmAction("Delete this memory?", `“${memory.content}” will be permanently removed.`, "Delete memory");
  if (!accepted) return;
  try { await api(`/memory/${memory.id}`, { method: "DELETE" }); toast("Memory deleted.", "success"); await loadMemories(); }
  catch (error) { toast(errorMessage(error), "error"); }
}

async function clearAllMemories() {
  const accepted = await confirmAction("Clear all memory?", "Every saved preference and context item will be permanently removed. Conversations are not affected.", "Clear all");
  if (!accepted) return;
  try { await api("/memory", { method: "DELETE" }); toast("All memory cleared.", "success"); await loadMemories(); }
  catch (error) { toast(errorMessage(error), "error"); }
}

async function deleteAllConversations() {
  const accepted = await confirmAction("Delete all conversations?", "Every saved conversation and message will be permanently removed. Indexed documents are not affected.", "Delete all");
  if (!accepted) return;
  try { await api("/conversations", { method: "DELETE" }); newChat(); await loadConversations(); toast("All conversations deleted.", "success"); }
  catch (error) { toast(errorMessage(error), "error"); }
}

function bindEvents() {
  $("#openSidebar").addEventListener("click", openMobileSidebar); $("#closeSidebar").addEventListener("click", closeMobileSidebar); $("#mobileBackdrop").addEventListener("click", closeMobileSidebar);
  $("#newChatButton").addEventListener("click", newChat);
  $$('[data-view]').forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
  $("#themeQuickToggle").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  $$('[data-theme-choice]').forEach(button => button.addEventListener("click", () => setTheme(button.dataset.themeChoice)));
  $("#modelSelect").addEventListener("change", selectModel);
  $("#conversationCollection").addEventListener("change", event => changeConversationCollection(event.target.value));
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
  $("#clearAllMemories").addEventListener("click", clearAllMemories);
  $("#signOutButton").addEventListener("click", signOut);
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
  await loadCollections();  // documents render collection dropdowns from state.collections
  await Promise.all([
    loadConfig(),
    loadConversations(),
    loadDocuments(),
    loadMemoryCandidates(),
  ]);
  await loadModels();
}

document.addEventListener("DOMContentLoaded", initialize);
