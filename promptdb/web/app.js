let state = {
  q: "",
  sourceKey: "",
  limit: 200,
  offset: 0,
  total: 0,
  items: [],
  selectedId: null,
};

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  return (s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtRange() {
  const start = state.total === 0 ? 0 : state.offset + 1;
  const end = Math.min(state.offset + state.limit, state.total);
  return `${start}-${end} of ${state.total}`;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return await res.json();
}

function hasOfflineBundle() {
  return typeof window !== "undefined" && window.PROMPTDB && Array.isArray(window.PROMPTDB.items);
}

function offlineBundle() {
  return window.PROMPTDB;
}

let OFFLINE = {
  ready: false,
  items: [],
  byId: new Map(),
  sources: [],
  total: 0,
};

function initOffline() {
  const b = offlineBundle();
  const items = (b.items || []).map((p) => ({
    id: Number(p.id),
    title: p.title || "",
    body: p.body || "",
    source: p.source || "",
    source_repo: p.source_repo || "",
    source_path: p.source_path || "",
    _t: (p.title || "").toLowerCase(),
    _b: (p.body || "").toLowerCase(),
  }));

  items.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }));

  const byId = new Map();
  for (const it of items) byId.set(it.id, it);

  let sources = b.sources;
  if (!Array.isArray(sources) || sources.length === 0) {
    const counts = new Map();
    for (const it of items) {
      const key = `${it.source}|${it.source_repo}`;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    sources = Array.from(counts.entries())
      .map(([key, count]) => {
        const parsed = parseSourceKey(key);
        return { source: parsed.source, repo: parsed.repo, count };
      })
      .sort((a, b) => (b.count || 0) - (a.count || 0));
  }

  OFFLINE = {
    ready: true,
    items,
    byId,
    sources,
    total: Number(b.total || items.length),
  };
}

async function loadStats() {
  if (hasOfflineBundle()) {
    $("subtitle").textContent = `${OFFLINE.total} prompts`;
    return;
  }

  try {
    const stats = await fetchJson("/api/stats");
    $("subtitle").textContent = `${stats.total} prompts`;
  } catch {
    $("subtitle").textContent = "db not reachable";
  }
}

function parseSourceKey(key) {
  if (!key) return { source: "", repo: "" };
  const idx = key.indexOf("|");
  if (idx === -1) return { source: key, repo: "" };
  return {
    source: key.slice(0, idx),
    repo: key.slice(idx + 1),
  };
}

function labelForSource(source, repo, count) {
  if (!repo) return `${source} (${count})`;
  return `${repo} • ${source} (${count})`;
}

async function loadSources() {
  const sel = $("source");

  // Keep the first option (All sources)
  sel.querySelectorAll("option").forEach((opt, i) => {
    if (i > 0) opt.remove();
  });

  if (hasOfflineBundle()) {
    for (const it of OFFLINE.sources) {
      const source = it.source || "";
      const repo = it.repo || "";
      const count = it.count || 0;
      const key = `${source}|${repo}`;
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = labelForSource(source, repo, count);
      sel.appendChild(opt);
    }
    return;
  }

  try {
    const data = await fetchJson("/api/sources");
    const items = data.items || [];

    for (const it of items) {
      const source = it.source || "";
      const repo = it.repo || "";
      const count = it.count || 0;
      const key = `${source}|${repo}`;
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = labelForSource(source, repo, count);
      sel.appendChild(opt);
    }
  } catch {
    // If it fails, leave the dropdown as "All sources"
  }
}

function offlineFilterAll() {
  let items = OFFLINE.items;
  if (state.sourceKey) {
    const parsed = parseSourceKey(state.sourceKey);
    items = items.filter((it) => {
      if (parsed.source && it.source !== parsed.source) return false;
      if (parsed.repo && it.source_repo !== parsed.repo) return false;
      return true;
    });
  }

  const q = (state.q || "").trim().toLowerCase();
  if (q) {
    items = items.filter((it) => it._t.includes(q) || it._b.includes(q));
  }
  return items;
}

async function loadList() {
  if (hasOfflineBundle()) {
    const filtered = offlineFilterAll();
    state.total = filtered.length;

    const start = Math.max(0, state.offset);
    const end = Math.min(start + state.limit, filtered.length);
    state.items = filtered.slice(start, end).map((it) => ({
      id: it.id,
      title: it.title,
      source: it.source,
      source_repo: it.source_repo,
    }));

    renderList();
    renderPager();

    if (state.items.length > 0) {
      const visibleIds = new Set(state.items.map((x) => x.id));
      if (state.selectedId == null || !visibleIds.has(state.selectedId)) {
        await selectPrompt(state.items[0].id);
      }
    } else {
      renderEmptyDetail();
    }

    return;
  }

  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.sourceKey) {
    const parsed = parseSourceKey(state.sourceKey);
    if (parsed.source) params.set("source", parsed.source);
    // repo can be empty; when empty we don't filter by repo
    if (parsed.repo) params.set("repo", parsed.repo);
  }
  params.set("limit", String(state.limit));
  params.set("offset", String(state.offset));

  const data = await fetchJson(`/api/prompts?${params.toString()}`);
  state.items = data.items;
  state.total = data.total;
  state.limit = data.limit;
  state.offset = data.offset;

  renderList();
  renderPager();

  // Auto-select first item if nothing selected or selection not visible
  if (state.items.length > 0) {
    const visibleIds = new Set(state.items.map((x) => x.id));
    if (state.selectedId == null || !visibleIds.has(state.selectedId)) {
      selectPrompt(state.items[0].id);
    }
  } else {
    renderEmptyDetail();
  }
}

function renderList() {
  const el = $("items");
  if (state.items.length === 0) {
    el.innerHTML = `<div class="item"><div class="itemTitle">No matches</div><div class="itemMeta">Try a different search.</div></div>`;
    $("count").textContent = "";
    return;
  }

  $("count").textContent = fmtRange();

  el.innerHTML = state.items
    .map((item) => {
      const active = item.id === state.selectedId ? "active" : "";
      const meta = [item.source, item.source_repo].filter(Boolean).join(" • ");
      return `
        <div class="item ${active}" data-id="${item.id}">
          <div class="itemTitle">${escapeHtml(item.title)}</div>
          <div class="itemMeta">${escapeHtml(meta)}</div>
        </div>
      `;
    })
    .join("");

  el.querySelectorAll(".item").forEach((node) => {
    node.addEventListener("click", () => {
      const id = Number(node.getAttribute("data-id"));
      selectPrompt(id);
    });
  });
}

function renderPager() {
  const prev = $("prev");
  const next = $("next");
  const page = $("page");

  prev.disabled = state.offset <= 0;
  next.disabled = state.offset + state.limit >= state.total;

  const currentPage = Math.floor(state.offset / state.limit) + 1;
  const totalPages = Math.max(1, Math.ceil(state.total / state.limit));
  page.textContent = `page ${currentPage} / ${totalPages}`;
}

function renderEmptyDetail() {
  state.selectedId = null;
  $("promptTitle").textContent = "No prompt selected";
  $("promptMeta").textContent = "";
  $("promptBody").textContent = "No results.";
  renderList();
}

async function selectPrompt(id) {
  state.selectedId = id;
  renderList();

  if (hasOfflineBundle()) {
    const p = OFFLINE.byId.get(id);
    if (!p) {
      $("promptTitle").textContent = "Not found";
      $("promptMeta").textContent = "";
      $("promptBody").textContent = "This prompt id is not in the offline bundle.";
      return;
    }
    $("promptTitle").textContent = p.title;
    const meta = [p.source, p.source_repo, p.source_path].filter(Boolean).join(" • ");
    $("promptMeta").textContent = meta;
    $("promptBody").textContent = p.body;
    return;
  }

  const p = await fetchJson(`/api/prompts/${id}`);
  $("promptTitle").textContent = p.title;

  const meta = [p.source, p.source_repo, p.source_path].filter(Boolean).join(" • ");
  $("promptMeta").textContent = meta;
  $("promptBody").textContent = p.body;
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function wireEvents() {
  const q = $("q");
  const source = $("source");
  const clear = $("clear");
  const prev = $("prev");
  const next = $("next");

  const onChange = debounce(async () => {
    state.q = q.value.trim();
    state.offset = 0;
    await loadList();
  }, 200);

  q.addEventListener("input", onChange);

  source.addEventListener("change", async () => {
    state.sourceKey = source.value;
    state.offset = 0;
    await loadList();
  });

  q.addEventListener("keydown", async (e) => {
    if (e.key === "Enter") {
      state.q = q.value.trim();
      state.offset = 0;
      await loadList();
    }
  });

  clear.addEventListener("click", async () => {
    q.value = "";
    state.q = "";
    state.offset = 0;
    await loadList();
    q.focus();
  });

  prev.addEventListener("click", async () => {
    state.offset = Math.max(0, state.offset - state.limit);
    await loadList();
  });

  next.addEventListener("click", async () => {
    state.offset = state.offset + state.limit;
    await loadList();
  });
}

async function main() {
  if (hasOfflineBundle()) {
    initOffline();
  } else {
    // Helpful hint when running index.html directly without generating data.js first
    $("subtitle").textContent = "offline bundle missing";
  }
  wireEvents();
  await loadSources();
  await loadStats();
  await loadList();
}

main().catch((e) => {
  $("subtitle").textContent = "error";
  $("promptBody").textContent = String(e?.message || e);
});
