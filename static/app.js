const API_BASE =
  location.protocol === "file:" ? "http://localhost:8000" : location.origin;
const LIMITS = { docs: 20, totalWords: 500000, docWords: 100000, pages: 200 };
const state = {
  sources: [],
  activeId: null,
  scope: "all",
  notes: [],
  messages: [],
  model: "openrouter/free",
  pendingType: "pdf",
  pendingFiles: [],
  backend: false,
  demoMode: false,
  allowUserSources: true,
};
let nextId = 1,
  toastTimer,
  saveTimer,
  youtubePlayer;
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const escapeHtml = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const wordCount = (s) => (String(s).trim().match(/\S+/g) || []).length;
const totalWords = () =>
  state.sources.reduce((n, s) => n + wordCount(s.text), 0);
const icon = {
  pdf: "file-text",
  web: "globe",
  youtube: "youtube",
  text: "type",
  note: "sticky-note",
};
if (window.pdfjsLib)
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js";

const DB = {
  db: null,
  async open() {
    if (this.db) return this.db;
    this.db = await new Promise((resolve, reject) => {
      const req = indexedDB.open("quellwerk", 1);
      req.onupgradeneeded = () =>
        req.result.createObjectStore("notebooks", { keyPath: "id" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return this.db;
  },
  async get(id = "default") {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const r = db.transaction("notebooks").objectStore("notebooks").get(id);
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  },
  async put(value) {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("notebooks", "readwrite");
      tx.objectStore("notebooks").put(value);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  },
};

function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}
function openModal(id) {
  $(id).classList.add("open");
}
function closeModal(el) {
  el.closest(".modal-bg").classList.remove("open");
}
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNotebook, 180);
}
async function saveNotebook() {
  const sources = await Promise.all(
    state.sources.map(async (s) => ({
      ...s,
      objectUrl: undefined,
      file: s.file || undefined,
    })),
  );
  await DB.put({
    id: "default",
    updatedAt: Date.now(),
    sources,
    activeId: state.activeId,
    scope: state.scope,
    notes: state.notes,
    messages: state.messages,
    model: state.model,
    nextId,
  });
}
async function restoreNotebook() {
  try {
    const saved = await DB.get();
    if (!saved) return;
    Object.assign(state, {
      sources: saved.sources || [],
      activeId: saved.activeId,
      scope: saved.scope || "all",
      notes: saved.notes || [],
      messages: saved.messages || [],
      model: saved.model || "openrouter/free",
    });
    nextId = saved.nextId || 1;
    state.sources.forEach((s) => {
      if (s.file) s.objectUrl = URL.createObjectURL(s.file);
    });
  } catch (error) {
    toast(`Wiederherstellung fehlgeschlagen: ${error.message}`);
  }
}

function showEmpty() {
  $("#viewer").innerHTML =
    '<div class="viewer-empty"><i data-lucide="library-big"></i><h1>Deine Quelle im Mittelpunkt</h1><p>Links verwaltest du Quellen, in der Mitte siehst du das Original und rechts fragst du die KI.</p></div>';
  lucide.createIcons();
}

async function checkBackend() {
  try {
    const r = await fetch(`${API_BASE}/api/health`, {
      signal: AbortSignal.timeout(2500),
    });
    state.backend = r.ok;
  } catch {
    state.backend = false;
  }
  const el = $("#backendStatus");
  el.classList.toggle("online", state.backend);
  el.querySelector("span").textContent = state.backend
    ? "Backend verbunden"
    : "Backend offline";
}
async function loadModels() {
  const select = $("#modelSelect");
  try {
    const data = await apiJson("/api/models", null, "GET");
    select.innerHTML = data.models
      .map(
        (m) =>
          `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name || m.id)}</option>`,
      )
      .join("");
    select.value = [...select.options].some((o) => o.value === state.model)
      ? state.model
      : "openrouter/free";
  } catch {
    select.innerHTML =
      '<option value="openrouter/free">Free Models Router</option>';
  }
}
function renderSources() {
  $("#sourceList").innerHTML = state.sources.length
    ? state.sources
        .map(
          (s) =>
            `<article class="source-item ${s.id === state.activeId ? "active" : ""}" data-source="${s.id}"><input class="source-check" type="checkbox" data-select="${s.id}" ${s.selected ? "checked" : ""} aria-label="Quelle einbeziehen"><div class="source-icon"><i data-lucide="${icon[s.type]}"></i></div><div class="source-copy"><div class="source-title" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</div><div class="source-meta">${escapeHtml(s.meta)} · ${wordCount(s.text).toLocaleString("de")} Wörter</div></div><button class="source-del" data-delete="${s.id}" aria-label="Quelle löschen"><i data-lucide="trash-2"></i></button></article>`,
        )
        .join("")
    : '<p class="source-empty">Noch keine Quellen.</p>';
  $("#sourceCount").textContent = `${state.sources.length} / ${LIMITS.docs}`;
  $("#modalDocs")?.textContent = `${state.sources.length}/${LIMITS.docs}`;
  $("#wordBudget").textContent =
    `${totalWords().toLocaleString("de")} / ${LIMITS.totalWords.toLocaleString("de")} Wörter`;
  $("#modalWords").textContent = `${Math.round(totalWords() / 1000)}k/500k`;
  lucide.createIcons();
}
const QUICK = {
  summary: "Fasse die aktive Quelle präzise zusammen.",
  keypoints: "Nenne die wichtigsten Kernaussagen der aktiven Quelle.",
  contradictions:
    "Analysiere Widersprüche und Spannungen in der aktiven Quelle.",
};
function toolbar(s) {
  const original = s.url
    ? `<a class="action-chip" href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer"><i data-lucide="external-link"></i><span>Original öffnen</span></a>`
    : "";
  return `<header class="source-toolbar"><div class="toolbar-copy"><div class="toolbar-title">${escapeHtml(s.title)}</div><div class="toolbar-meta">${escapeHtml(s.meta)}</div></div>${original}<button class="action-chip" data-quick="summary"><i data-lucide="align-left"></i><span>Zusammenfassung</span></button><button class="action-chip" data-quick="keypoints"><i data-lucide="list-checks"></i><span>Kernaussagen</span></button><button class="action-chip" data-quick="contradictions"><i data-lucide="git-compare"></i><span>Widersprüche</span></button></header>`;
}
function renderTextSource(s) {
  return `<div class="text-view" id="textScroller"><article class="text-paper">${s.chunks.map((c) => `<section class="source-passage" id="chunk-${c.id}" data-chunk="${c.id}">${escapeHtml(c.text)}</section>`).join("\n")}</article></div>`;
}
function openSource(id, locator = null) {
  const s = state.sources.find((x) => x.id === id);
  if (!s) return;
  state.activeId = id;
  renderSources();
  let view = "";
  if (s.type === "pdf") {
    const page = locator?.page || 1;
    view = s.objectUrl
      ? `<iframe class="pdf-view" src="${s.objectUrl}#page=${page}&view=FitH" title="PDF: ${escapeHtml(s.title)}"></iframe>`
      : '<div class="viewer-empty"><p>Die PDF-Datei ist nicht mehr verfügbar.</p></div>';
  }
  if (s.type === "youtube") {
    const start = Math.floor(locator?.start || 0);
    view = `<div class="video-stage"><div class="video-wrap"><iframe id="youtubeFrame" src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(s.videoId)}?rel=0&start=${start}" title="YouTube: ${escapeHtml(s.title)}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe></div></div>`;
  }
  if (s.type === "web") {
    view = `<iframe class="web-view" src="${escapeHtml(s.url)}" title="Webseite: ${escapeHtml(s.title)}" sandbox="allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts" referrerpolicy="strict-origin-when-cross-origin"></iframe><div class="web-note"><i data-lucide="info"></i><span>Einbettung blockiert? <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">Original extern öffnen</a></span></div>`;
  }
  if (s.type === "text" || s.type === "note") view = renderTextSource(s);
  $("#viewer").innerHTML =
    toolbar(s) + `<div class="source-stage">${view}</div>`;
  lucide.createIcons();
  if (locator?.chunk_id && (s.type === "text" || s.type === "note"))
    requestAnimationFrame(() => highlightChunk(locator.chunk_id));
  scheduleSave();
}
function highlightChunk(chunkId) {
  const el = document.getElementById(`chunk-${chunkId}`);
  if (!el) return;
  $$(".source-passage.highlight").forEach((x) =>
    x.classList.remove("highlight"),
  );
  el.classList.add("highlight");
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => el.classList.remove("highlight"), 5000);
}

function splitWithOffsets(text, target = 900, overlap = 120) {
  const out = [];
  let start = 0;
  while (start < text.length) {
    let end = Math.min(text.length, start + target);
    if (end < text.length) {
      const boundary = Math.max(
        text.lastIndexOf(". ", end),
        text.lastIndexOf("\n", end),
      );
      if (boundary > start + target * 0.55) end = boundary + 1;
    }
    const value = text.slice(start, end).trim();
    if (value) out.push({ text: value, start, end });
    if (end >= text.length) break;
    start = Math.max(start + 1, end - overlap);
  }
  return out;
}
function makeChunks(source) {
  if (source.type === "pdf" && source.pagesText)
    return source.pagesText.flatMap((p) =>
      splitWithOffsets(p.text).map((c, i) => ({
        id: `${source.id}-p${p.page}-${i}`,
        source_id: String(source.id),
        source_title: source.title,
        text: c.text,
        locator: {
          type: "pdf",
          page: p.page,
          start: c.start,
          end: c.end,
          label: `S. ${p.page}`,
        },
      })),
    );
  if (source.type === "youtube" && source.segments) {
    const groups = [];
    let current = [];
    let chars = 0;
    for (const seg of source.segments) {
      current.push(seg);
      chars += seg.text.length;
      if (chars >= 900) {
        groups.push(current);
        current = [];
        chars = 0;
      }
    }
    if (current.length) groups.push(current);
    return groups.map((group, i) => ({
      id: `${source.id}-t${i}`,
      source_id: String(source.id),
      source_title: source.title,
      text: group.map((x) => x.text).join(" "),
      locator: {
        type: "youtube",
        start: group[0].start,
        end: group.at(-1).start + group.at(-1).duration,
        label: formatTime(group[0].start),
      },
    }));
  }
  return splitWithOffsets(source.text).map((c, i) => ({
    id: `${source.id}-c${i}`,
    source_id: String(source.id),
    source_title: source.title,
    text: c.text,
    locator: {
      type: source.type,
      start: c.start,
      end: c.end,
      section:
        source.sections?.find((x) => c.start >= x.start && c.start <= x.end)
          ?.heading || null,
      label:
        source.type === "web"
          ? "Abschnitt"
          : source.type === "note"
            ? `Notiz · Absatz ${i + 1}`
            : `Absatz ${i + 1}`,
    },
  }));
}
const formatTime = (seconds) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

async function extractPdf(file) {
  const pdf = await pdfjsLib.getDocument({ data: await file.arrayBuffer() })
    .promise;
  const pagesText = [];
  for (
    let pageNo = 1;
    pageNo <= Math.min(pdf.numPages, LIMITS.pages);
    pageNo++
  ) {
    const page = await pdf.getPage(pageNo);
    const content = await page.getTextContent();
    pagesText.push({
      page: pageNo,
      text: content.items.map((x) => x.str).join(" "),
    });
  }
  return {
    text: pagesText.map((x) => x.text).join("\n\n"),
    pages: pdf.numPages,
    pagesText,
  };
}
async function apiJson(path, body, method = "POST") {
  const options = { method, headers: { "Content-Type": "application/json" } };
  if (body) options.body = JSON.stringify(body);
  const r = await fetch(`${API_BASE}${path}`, options);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || data.error || `HTTP ${r.status}`);
  return data;
}

async function loadRuntimeConfig() {
  try {
    const config = await apiJson("/api/config", null, "GET");
    state.demoMode = Boolean(config.demo_mode);
    state.allowUserSources = config.allow_user_sources !== false;
    if (!state.allowUserSources) {
      $("#addSource").hidden = true;
      $("#sourceModal").remove();
    }
    return config;
  } catch {
    return null;
  }
}

async function loadDemoNotebookIfEmpty() {
  if (!state.demoMode || state.sources.length) return;
  try {
    const demo = await apiJson("/api/demo-notebook", null, "GET");
    state.sources = (demo.sources || []).map((draft) => {
      const source = { ...draft, id: nextId++, selected: true };
      source.chunks = makeChunks(source);
      return source;
    });
    state.activeId = state.sources[0]?.id || null;
    state.messages = [];
    await saveNotebook();
  } catch (error) {
    toast(`Demo konnte nicht geladen werden: ${error.message}`);
  }
}

async function buildPdfSource(file, index, total) {
  const button = $("#saveSource");
  button.textContent =
    total > 1
      ? `PDF ${index + 1}/${total} wird verarbeitet…`
      : "PDF wird verarbeitet…";
  const result = await extractPdf(file);
  return {
    type: "pdf",
    title: file.name.replace(/\.pdf$/i, ""),
    meta: `PDF · ${result.pages} Seiten`,
    text: result.text,
    pagesText: result.pagesText,
    file,
    objectUrl: URL.createObjectURL(file),
  };
}

function validateNewSources(sources) {
  if (state.sources.length + sources.length > LIMITS.docs)
    throw new Error(
      `Dokumentenlimit überschritten. Es sind noch ${Math.max(0, LIMITS.docs - state.sources.length)} Quellen frei.`,
    );
  let words = totalWords();
  for (const source of sources) {
    const count = wordCount(source.text);
    if (count > LIMITS.docWords)
      throw new Error(
        `„${source.title}“ überschreitet das Wortlimit pro Quelle.`,
      );
    words += count;
    if (words > LIMITS.totalWords)
      throw new Error("Gesamt-Wortlimit überschritten.");
  }
}

async function addSource() {
  const button = $("#saveSource"),
    type = state.pendingType;
  button.disabled = true;
  button.textContent = "Wird verarbeitet…";
  const prepared = [];
  try {
    if (type === "pdf") {
      if (!state.pendingFiles.length)
        throw new Error("Bitte mindestens eine PDF auswählen.");
      if (state.sources.length + state.pendingFiles.length > LIMITS.docs)
        throw new Error(
          `Dokumentenlimit überschritten. Es sind noch ${Math.max(0, LIMITS.docs - state.sources.length)} Quellen frei.`,
        );
      for (let i = 0; i < state.pendingFiles.length; i++)
        prepared.push(
          await buildPdfSource(
            state.pendingFiles[i],
            i,
            state.pendingFiles.length,
          ),
        );
    }
    if (type === "web") {
      const url = $("#webUrl").value.trim();
      if (!url) throw new Error("Bitte eine URL eingeben.");
      const result = await apiJson("/api/scrape", { url });
      prepared.push({
        type,
        title: result.title,
        meta: "Webseite · Originalansicht",
        text: result.text,
        url: result.url,
        sections: result.sections,
      });
    }
    if (type === "youtube") {
      const url = $("#youtubeUrl").value.trim();
      if (!url) throw new Error("Bitte einen YouTube-Link eingeben.");
      const result = await apiJson("/api/youtube", { url });
      prepared.push({
        type,
        title: result.title,
        meta: `YouTube · ${result.language}`,
        text: result.text,
        url,
        videoId: result.video_id,
        segments: result.segments,
      });
    }
    if (type === "text") {
      const text = $("#textBody").value.trim();
      if (!text) throw new Error("Bitte Text einfügen.");
      prepared.push({
        type,
        title: $("#textTitle").value.trim() || "Textquelle",
        meta: "Text",
        text,
      });
    }
    validateNewSources(prepared);
    for (const draft of prepared) {
      const source = { ...draft, id: nextId++, selected: true };
      source.chunks = makeChunks(source);
      state.sources.push(source);
    }
    renderSources();
    openSource(state.sources.at(-1).id);
    $("#sourceModal").classList.remove("open");
    resetSourceForm();
    await saveNotebook();
    toast(
      prepared.length > 1
        ? `${prepared.length} PDFs hinzugefügt`
        : "Quelle hinzugefügt",
    );
  } catch (error) {
    prepared.forEach(
      (source) => source.objectUrl && URL.revokeObjectURL(source.objectUrl),
    );
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Hinzufügen";
  }
}
function resetSourceForm() {
  state.pendingFiles = [];
  $("#fileInput").value = "";
  $("#fileLabel").textContent = "PDFs hierher ziehen oder auswählen";
  ["#webUrl", "#youtubeUrl", "#textTitle", "#textBody"].forEach(
    (selector) => ($(selector).value = ""),
  );
}

function inlineMarkdown(raw) {
  let s = escapeHtml(raw);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  return s;
}
function locatorLabel(hit) {
  return (
    hit?.locator?.section ||
    hit?.locator?.label ||
    hit?.source_title ||
    "Quelle"
  );
}
function renderMarkdown(raw, hits = []) {
  let text = String(raw || "");
  const refs = new Map(hits.map((h, i) => [String(i + 1), h]));
  const blocks = text
    .split(/\n{2,}/)
    .filter(Boolean)
    .map((block) => {
      const lines = block.split("\n");
      if (lines.every((l) => /^[-*]\s+/.test(l.trim())))
        return `<ul>${lines.map((l) => `<li>${inlineMarkdown(l.trim().replace(/^[-*]\s+/, ""))}</li>`).join("")}</ul>`;
      if (/^###\s/.test(block))
        return `<h3>${inlineMarkdown(block.replace(/^###\s/, ""))}</h3>`;
      if (/^##\s/.test(block))
        return `<h2>${inlineMarkdown(block.replace(/^##\s/, ""))}</h2>`;
      return `<p>${inlineMarkdown(block).replace(/\n/g, "<br>")}</p>`;
    })
    .join("");
  return blocks.replace(/\[(\d+)]/g, (match, n) => {
    const hit = refs.get(n);
    if (!hit) return match;
    return `<button class="cite" data-source-id="${escapeHtml(hit.source_id)}" data-chunk-id="${escapeHtml(hit.id)}" data-locator="${encodeURIComponent(JSON.stringify({ ...hit.locator, chunk_id: hit.id }))}" title="${escapeHtml(hit.source_title)} · ${escapeHtml(locatorLabel(hit))}">${n}</button>`;
  });
}
function appendMessage(
  role,
  content,
  hits = [],
  error = false,
  persist = true,
) {
  const el = document.createElement("div");
  el.className = `message ${role}`;
  if (role === "user") el.textContent = content;
  else {
    el.dataset.raw = content;
    el.innerHTML = `<div class="ai-label"><i data-lucide="sparkles"></i> Quellwerk</div><div class="ai-card ${error ? "error" : ""}">${renderMarkdown(content, hits)}</div><div class="message-actions"><button class="message-action" data-save-answer><i data-lucide="bookmark"></i> Notiz</button><button class="message-action" data-copy-answer><i data-lucide="copy"></i> Kopieren</button></div>`;
  }
  $("#chatLog").appendChild(el);
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
  lucide.createIcons();
  if (persist && content) {
    state.messages.push({ role, content, hits: role === "ai" ? hits : [] });
    state.messages = state.messages.slice(-40);
    scheduleSave();
  }
  return el;
}
function renderChat() {
  $("#chatLog").innerHTML = "";
  if (!state.messages.length) {
    const el = appendMessage(
      "ai",
      "Ich antworte ausschließlich anhand deiner Quellen und verweise direkt auf die Fundstellen.",
      [],
      false,
      false,
    );
    el.querySelector(".ai-card").insertAdjacentHTML(
      "beforeend",
      '<div class="suggestions"><button>Was sind die Kernaussagen?</button><button>Welche Widersprüche gibt es?</button><button>Fasse alle Quellen zusammen</button></div>',
    );
    return;
  }
  state.messages.forEach((m) =>
    appendMessage(m.role, m.content, m.hits || [], false, false),
  );
}
function selectedChunks() {
  const ids =
    state.scope === "active" && state.activeId
      ? [state.activeId]
      : state.sources.filter((s) => s.selected).map((s) => s.id);
  return state.sources
    .filter((s) => ids.includes(s.id))
    .flatMap((s) => s.chunks || []);
}
async function ask(query) {
  const chunks = selectedChunks();
  if (!chunks.length)
    return appendMessage(
      "ai",
      "Bitte wähle mindestens eine Quelle aus.",
      [],
      true,
    );
  const previous = state.messages
    .filter((m) => m.content)
    .slice(-12)
    .map((m) => ({
      role: m.role === "ai" ? "assistant" : "user",
      content: m.content,
    }));
  const el = appendMessage("ai", "", [], false, false),
    card = el.querySelector(".ai-card");
  card.innerHTML = '<div class="typing"><i></i><i></i><i></i></div>';
  let answer = "",
    hits = [];
  try {
    const response = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({
        query,
        chunks,
        top_k: 8,
        history: previous,
        model: state.model,
      }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${response.status}`);
    }
    const reader = response.body.getReader(),
      decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const raw of events) {
        let event = "message",
          data = {};
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:")) data = JSON.parse(line.slice(5));
        }
        if (event === "meta") hits = data.hits || [];
        if (event === "token") {
          answer += data.text;
          card.innerHTML = renderMarkdown(answer, hits);
        }
        if (event === "error") throw new Error(data.message);
      }
    }
    el.dataset.raw = answer;
    state.messages.push({ role: "ai", content: answer, hits });
    state.messages = state.messages.slice(-40);
    scheduleSave();
  } catch (error) {
    card.classList.add("error");
    card.textContent = error.message;
    el.dataset.raw = error.message;
  }
}
function renderNotes() {
  $("#noteList").innerHTML = state.notes
    .map((n) => {
      const linked = state.sources.find(
        (s) => s.type === "note" && s.noteId === n.id,
      );
      return `<article class="note-card"><div class="note-text">${escapeHtml(n.text)}</div><div class="note-meta"><span>${escapeHtml(n.time)}</span><div class="note-actions"><button class="note-source" data-note-source="${n.id}"><i data-lucide="${linked ? "external-link" : "library-big"}"></i>${linked ? "Quelle öffnen" : "Als Quelle"}</button><button class="note-delete" data-delete-note="${n.id}" aria-label="Notiz löschen"><i data-lucide="trash-2"></i></button></div></div></article>`;
    })
    .join("");
  lucide.createIcons();
}
function addNoteAsSource(noteId) {
  const note = state.notes.find((n) => n.id === noteId);
  if (!note) return;
  const linked = state.sources.find(
    (s) => s.type === "note" && s.noteId === noteId,
  );
  if (linked) {
    openSource(linked.id);
    toast("Notizquelle geöffnet");
    return;
  }
  const heading = note.text.split(/\n/).find(Boolean)?.trim() || "Notiz";
  const source = {
    id: nextId++,
    type: "note",
    noteId,
    title: heading.length > 64 ? `${heading.slice(0, 61)}…` : heading,
    meta: `Notiz · ${note.time}`,
    text: note.text,
    selected: true,
  };
  try {
    validateNewSources([source]);
    source.chunks = makeChunks(source);
    state.sources.push(source);
    renderSources();
    renderNotes();
    openSource(source.id);
    scheduleSave();
    toast("Notiz als Quelle hinzugefügt");
  } catch (error) {
    toast(error.message);
  }
}

function addNote(text) {
  state.notes.unshift({
    id: nextId++,
    text,
    time: new Date().toLocaleTimeString("de", {
      hour: "2-digit",
      minute: "2-digit",
    }),
  });
  renderNotes();
  scheduleSave();
}

$("#addSource").onclick = () => openModal("#sourceModal");
$("#settingsBtn").onclick = () => openModal("#settingsModal");
$$("[data-close]").forEach((b) => (b.onclick = () => closeModal(b)));
$$(".modal-bg").forEach(
  (m) =>
    (m.onclick = (e) => {
      if (e.target === m) m.classList.remove("open");
    }),
);
$$(".type").forEach(
  (b) =>
    (b.onclick = () => {
      $$(".type").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.pendingType = b.dataset.type;
      $$(".source-input").forEach((x) => (x.hidden = true));
      $(
        `#input${b.dataset.type[0].toUpperCase()}${b.dataset.type.slice(1)}`,
      ).hidden = false;
    }),
);
const fileDrop = $("#fileDrop"),
  fileInput = $("#fileInput");
function isPdf(file) {
  return file?.type === "application/pdf" || /\.pdf$/i.test(file?.name || "");
}
function setPendingFiles(fileList) {
  const files = [...fileList];
  const pdfs = files.filter(isPdf);
  state.pendingFiles = pdfs;
  $("#fileLabel").textContent = !pdfs.length
    ? "PDFs hierher ziehen oder auswählen"
    : pdfs.length === 1
      ? pdfs[0].name
      : `${pdfs.length} PDFs ausgewählt`;
  if (files.length !== pdfs.length)
    toast(`${files.length - pdfs.length} Nicht-PDF-Datei(en) ignoriert`);
}
fileDrop.onclick = () => fileInput.click();
fileInput.onchange = (e) => setPendingFiles(e.target.files);
["dragenter", "dragover"].forEach((evt) =>
  fileDrop.addEventListener(evt, (e) => {
    e.preventDefault();
    fileDrop.classList.add("dragover");
  }),
);
["dragleave", "drop"].forEach((evt) =>
  fileDrop.addEventListener(evt, (e) => {
    e.preventDefault();
    fileDrop.classList.remove("dragover");
  }),
);
fileDrop.addEventListener("drop", (e) => {
  setPendingFiles(e.dataTransfer.files);
});
$("#saveSource").onclick = addSource;
$("#sourceList").onclick = (e) => {
  const del = e.target.closest("[data-delete]");
  if (del) {
    const id = Number(del.dataset.delete),
      source = state.sources.find((x) => x.id === id);
    if (source?.objectUrl) URL.revokeObjectURL(source.objectUrl);
    state.sources = state.sources.filter((x) => x.id !== id);
    if (state.activeId === id) {
      state.activeId = null;
      showEmpty();
    }
    renderSources();
    renderNotes();
    scheduleSave();
    return;
  }
  if (e.target.matches("[data-select]")) return;
  const item = e.target.closest("[data-source]");
  if (item) openSource(Number(item.dataset.source));
};
$("#sourceList").onchange = (e) => {
  if (e.target.matches("[data-select]")) {
    state.sources.find(
      (x) => x.id === Number(e.target.dataset.select),
    ).selected = e.target.checked;
    scheduleSave();
  }
};
$("#viewer").onclick = (e) => {
  const quick = e.target.closest("[data-quick]");
  if (quick) {
    const q = QUICK[quick.dataset.quick];
    appendMessage("user", q);
    ask(q);
  }
};
$("#chatForm").onsubmit = (e) => {
  e.preventDefault();
  const q = $("#chatInput").value.trim();
  if (!q) return;
  appendMessage("user", q);
  $("#chatInput").value = "";
  ask(q);
};
$("#chatInput").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#chatForm").requestSubmit();
  }
};
$("#chatLog").onclick = (e) => {
  const suggestion = e.target.closest(".suggestions button");
  if (suggestion) {
    appendMessage("user", suggestion.textContent);
    ask(suggestion.textContent);
    return;
  }
  const cite = e.target.closest(".cite");
  if (cite) {
    const locator = JSON.parse(decodeURIComponent(cite.dataset.locator));
    openSource(Number(cite.dataset.sourceId), locator);
    return;
  }
  const msg = e.target.closest(".message");
  if (e.target.closest("[data-save-answer]")) {
    addNote(msg.dataset.raw);
    toast("Antwort gespeichert");
  }
  if (e.target.closest("[data-copy-answer]")) {
    navigator.clipboard?.writeText(msg.dataset.raw);
    toast("Kopiert");
  }
};
$$(".tabs button").forEach(
  (b) =>
    (b.onclick = () => {
      $$(".tabs button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const notes = b.dataset.tab === "notes";
      $("#chatLog").style.display = notes ? "none" : "flex";
      $("#composerWrap").style.display = notes ? "none" : "block";
      $("#notes").style.display = notes ? "block" : "none";
    }),
);
$$(".scope button").forEach(
  (b) =>
    (b.onclick = () => {
      $$(".scope button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.scope = b.dataset.scope;
      scheduleSave();
    }),
);
$("#saveNote").onclick = () => {
  const v = $("#noteInput").value.trim();
  if (v) {
    addNote(v);
    $("#noteInput").value = "";
    toast("Notiz gespeichert");
  }
};
$("#noteList").onclick = (e) => {
  const sourceButton = e.target.closest("[data-note-source]");
  if (sourceButton) {
    addNoteAsSource(Number(sourceButton.dataset.noteSource));
    return;
  }
  const b = e.target.closest("[data-delete-note]");
  if (b) {
    state.notes = state.notes.filter(
      (n) => n.id !== Number(b.dataset.deleteNote),
    );
    renderNotes();
    scheduleSave();
  }
};
$("#saveSettings").onclick = () => {
  state.model = $("#modelSelect").value;
  $("#settingsModal").classList.remove("open");
  scheduleSave();
  toast("KI-Einstellungen gespeichert");
};
$("#sourcesToggle").onclick = () => $("#sourcesPanel").classList.toggle("open");
$("#chatToggle").onclick = () => $("#chatPanel").classList.toggle("open");
$("#themeBtn").onclick = () => {
  const dark = document.documentElement.dataset.theme === "dark";
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  $("#themeBtn").innerHTML = `<i data-lucide="${dark ? "moon" : "sun"}"></i>`;
  lucide.createIcons();
};

(async function init() {
  await restoreNotebook();
  await loadRuntimeConfig();
  await loadDemoNotebookIfEmpty();
  renderSources();
  renderNotes();
  renderChat();
  if (state.activeId) openSource(state.activeId);
  else showEmpty();
  await Promise.allSettled([checkBackend(), loadModels()]);
  lucide.createIcons();
})();
