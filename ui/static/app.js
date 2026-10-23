/* Personal AI Agent: front end (no dependencies) */
(() => {
  "use strict";

  // ------------------------------------------------------------ tiny helpers
  const $ = (id) => document.getElementById(id);
  const svg = (name) => `<svg class="ic"><use href="#i-${name}"/></svg>`;
  function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return n;
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };
  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : "t" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8));
  async function api(path, body, method) {
    const opt = body === undefined && !method ? {} : { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
    const r = await fetch(path, opt);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  }
  let toastTimer;
  function toast(msg) {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); }
    catch { toast("Copy failed. Select the text and press Ctrl+C."); }
  }

  // ------------------------------------------------------------ minimal, safe markdown
  function md(src) {
    const blocks = [];
    let text = String(src ?? "").replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      blocks.push(`<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`);
      return `\u0000${blocks.length - 1}\u0000`;
    });
    text = esc(text);
    const inline = (s) => s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    const out = [];
    let list = null;
    const flush = () => { if (list) { out.push(`</${list}>`); list = null; } };
    const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const rows = text.split("\n");
    for (let i = 0; i < rows.length; i++) {
      const line = rows[i];
      let m;
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < rows.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(rows[i + 1])) {
        flush();
        let t = "<table class=\"mdtbl\"><thead><tr>" + cells(line).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
        i += 2;
        while (i < rows.length && /^\s*\|.*\|\s*$/.test(rows[i])) { t += "<tr>" + cells(rows[i]).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>"; i++; }
        i--; out.push(t + "</tbody></table>");
        continue;
      }
      if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { if (list !== "ul") { flush(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); }
      else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { flush(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); }
      else if ((m = line.match(/^(#{1,4})\s+(.*)/))) { flush(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); }
      else if ((m = line.match(/^&gt;\s?(.*)/))) { flush(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); }
      else if (!line.trim()) { flush(); }
      else { flush(); out.push(`<p>${inline(line)}</p>`); }
    }
    flush();
    return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
  }

  // ------------------------------------------------------------ result renderers (mirror the Streamlit ones)
  const MODIFY = new Set(["create_file", "write_file", "append_file", "replace_text", "rename_file", "copy_file", "move_file", "create_directory", "delete_file", "delete_directory"]);
  const OUTPUT = new Set(["create_note", "convert_document", "generate_report", "research_report"]);
  const isOutput = (tool) => OUTPUT.has(tool) || tool.includes("pdf");  // every PDF tool creates a new file
  const dash = (a) => a.filter(Boolean).join(" · ");

  function alertBox(kind, content) {
    const box = el("div", { class: `alert ${kind}` });
    const inner = el("div"); inner.append(...[].concat(content));
    box.append(inner);
    return box;
  }

  function pathBar(path) {
    return el("div", { class: "pathbar" },
      el("code", {}, path),
      el("button", { class: "btn", type: "button", onclick: () => copy(path), html: `${svg("copy")}Copy` }),
      el("button", {
        class: "btn", type: "button", html: `${svg("folder")}Open in Explorer`,
        onclick: async () => { const r = await api("/api/open", { path }); toast(r.ok ? "Opened in Explorer" : r.error); },
      }),
      /\.pdf$/i.test(path) ? el("a", { class: "btn", href: `/api/download?path=${encodeURIComponent(path)}`, download: "", html: `${svg("download")}Download` }) : null);
  }

  function table(rows, onPick) {
    rows = rows || [];
    if (!rows.length) return el("p", { class: "hint" }, "No rows.");
    const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
    const head = el("tr", {}, cols.map((c) => el("th", {}, c)));
    const body = rows.map((r) => {
      const tr = el("tr", {}, cols.map((c) => {
        const v = r[c] == null ? "" : typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c]);
        if (c === "url" && /^https?:\/\//.test(v)) return el("td", {}, el("a", { href: v, target: "_blank", rel: "noopener noreferrer" }, v));
        return el("td", { class: c === "path" || c === "full_path" ? "path" : "" }, v);
      }));
      if (onPick && (r.path || r.full_path)) {
        tr.style.cursor = "pointer";
        tr.addEventListener("click", () => { tr.parentNode.querySelectorAll("tr.sel").forEach((x) => x.classList.remove("sel")); tr.classList.add("sel"); onPick(r.path || r.full_path); });
      }
      return tr;
    });
    return el("div", { class: "tbl-wrap" }, el("table", { class: "tbl" }, el("thead", {}, head), el("tbody", {}, body)));
  }

  function pathTable(rows) {
    const holder = el("div", { class: "res" });
    const slot = el("div");
    const hasPath = (rows || []).some((r) => r.path);
    holder.append(table(rows, hasPath ? (p) => slot.replaceChildren(pathBar(p)) : null), slot);
    if (hasPath) holder.append(el("p", { class: "hint" }, "Click a row to copy it or open its location in Explorer."));
    return holder;
  }

  function kvTable(obj) {
    const g = el("div", { class: "kv" });
    for (const [k, v] of Object.entries(obj)) g.append(el("div", { class: "k" }, k), el("div", {}, String(v)));
    return g;
  }

  function textBox(value) {
    const t = el("textarea", { class: "textbox", readonly: true }); t.value = value || "";
    return t;
  }

  function renderPending(res) {
    const pid = res.pending_id;
    const wrap = el("div", { class: "res" });
    const paint = (info) => {
      wrap.replaceChildren();
      if (!info || info.status === "gone") { wrap.append(alertBox("warn", `⏱️ ${res.description || ""}\nThis request expired (or the app was restarted). Ask again.`)); return; }
      const st = info.status;
      if (st === "pending") {
        const ok = el("button", { class: "btn go", type: "button", html: `${svg("check")}Approve` });
        const no = el("button", { class: "btn stop", type: "button", html: `${svg("x")}Reject` });
        const act = el("div", { class: "actions" }, ok, no);
        wrap.append(alertBox("warn", [el("b", {}, "Approval needed: "), info.description, act]));
        ok.onclick = async () => { ok.disabled = no.disabled = true; paint(await api(`/api/approve/${pid}`, {})); };
        no.onclick = async () => { ok.disabled = no.disabled = true; paint(await api(`/api/reject/${pid}`, {})); };
      } else if (st === "done" || st === "failed") {
        wrap.append(renderResult(info.result, { nested: true }));
      } else if (st === "rejected") wrap.append(alertBox("info", `Rejected, nothing was changed: ${info.description}`));
      else if (st === "expired") wrap.append(alertBox("warn", `⏱️ Expired, nothing was changed: ${info.description}`));
      else wrap.append(alertBox("info", `${st}: ${info.description}`));
    };
    api(`/api/pending/${pid}`).then(paint).catch(() => paint({ status: "gone" }));
    return wrap;
  }

  function renderResult(res, opts = {}) {
    const root = el("div", { class: "res" });
    if (!res || typeof res !== "object") { root.append(el("p", {}, String(res ?? ""))); return root; }
    if (res.ok === false || (res.ok === undefined && res.error)) { root.append(alertBox("err", res.error || "Tool failed.")); return root; }
    if (res.pending_id) return renderPending(res);

    const tool = res.tool, rows = res.rows || [], notes = res.notes || [];

    if (MODIFY.has(tool)) {
      root.append(alertBox("ok", dash(notes.length ? notes : ["Done."])), table(rows));
      if (rows[0] && rows[0].action !== "deleted" && rows[0].path) root.append(pathBar(rows[0].path));
      return root;
    }
    if (isOutput(tool) && rows.length) {
      root.append(alertBox("ok", dash(notes.length ? notes : ["Done."])), table(rows));
      if (rows[0].path) root.append(pathBar(rows[0].path));
      if (tool === "generate_report" && res.preview) {
        const d = el("details", { class: "exp" }, el("summary", {}, "Report preview"));
        d.append(el("div", { class: "exp-body" }, res.format === "html" ? textBox(res.preview) : el("div", { class: "md", html: md(res.preview) })));
        root.append(d);
      }
      return root;
    }
    if ((tool === "extract_text" || tool === "read_clipboard" || tool === "get_approved_result") && "content" in res) {
      const meta = rows[0] || {};
      if (meta.name) root.append(el("p", { class: "cap" }, `${meta.name} · ${meta.size || ""} · engine: ${res.engine || "local"}`));
      root.append(textBox(res.content));
      if (meta.path) root.append(pathBar(meta.path));
      if (notes.length) root.append(el("p", { class: "cap" }, dash(notes)));
      return root;
    }
    if (tool === "file_operation_history") {
      root.append(table(rows), el("p", { class: "cap" }, dash([`${res.count || 0} entr(ies)`, ...notes])));
      return root;
    }
    if (tool === "find_duplicates") {
      const groups = res.groups || [];
      if (!groups.length) root.append(alertBox("info", "No duplicate files found."));
      groups.forEach((g, i) => {
        const d = el("details", { class: "exp", open: i < 3 }, el("summary", {}, `Group ${i + 1} · ${g.copies} copies · ${g.size} each · ${g.wasted} reclaimable`));
        d.append(el("div", { class: "exp-body" }, el("p", { class: "cap" }, `SHA-256: ${g.hash}`), pathTable(g.files)));
        root.append(d);
      });
    } else if (tool === "read_file" && rows.length) {
      const m = rows[0];
      root.append(el("p", { class: "cap" }, `${m.name} · ${m.size} · modified ${m.modified}`));
      if (res.table) root.append(table(res.table.rows.map((r) => Object.fromEntries(res.table.columns.map((c, i) => [c, r[i]])))));
      else root.append(textBox(res.content));
      root.append(pathBar(m.path));
    } else if (tool === "get_file_info" && rows.length) {
      root.append(kvTable(rows[0]), pathBar(rows[0].full_path));
    } else if (tool === "get_system_info" && rows.length) {
      root.append(kvTable(rows[0]));
    } else if (tool === "get_disk_usage") {
      root.append(table(rows));
    } else if (tool === "run_shell_command" && rows.length) {
      const m = rows[0];
      root.append(el("div", { class: "md", html: `<pre><code>${esc(m.command)}</code></pre>` }),
        el("div", { class: "metrics" },
          el("div", { class: "metric" }, el("small", {}, "Exit code"), el("b", {}, m.exit_code)),
          el("div", { class: "metric" }, el("small", {}, "Duration"), el("b", {}, m.duration)),
          el("div", { class: "metric" }, el("small", {}, "Ran in"), el("b", { style: "font-size:12px;font-weight:600" }, m.working_directory))),
        textBox(res.stdout));
      if (res.stderr) root.append(textBox(res.stderr));
    } else if ((tool === "open_file_location" || tool === "open_file") && rows.length) {
      root.append(alertBox("ok", dash(notes.length ? notes : ["Done."])), el("div", { class: "pathbar" }, el("code", {}, rows[0].path)));
    } else if (tool === "web_search" && rows.length) {
      root.append(table(rows));
    } else if (rows.length) {
      root.append(pathTable(rows));
    } else {
      root.append(alertBox("info", "No results."));
    }
    if (notes.length || res.count != null) root.append(el("p", { class: "cap" }, dash([`${res.count ?? rows.length} row(s)`, ...notes])));
    return root;
  }

  // ------------------------------------------------------------ state
  const state = {
    cfg: null,
    chats: store.get("chats", []),       // [{id, title, ts}]
    current: store.get("current", null),
    view: "chat",
    busy: false,
    drive: store.get("drive", "all"),
    tool: null, group: null,
    zones: {},                           // the upload boxes' elements, by field name
    picks: {},                           // upload boxes of the open tool: {fieldName: [File, ...]}
  };
  const saveChats = () => { store.set("chats", state.chats); store.set("current", state.current); };

  function ensureChat() {
    if (!state.current || !state.chats.find((c) => c.id === state.current)) {
      const c = { id: uid(), title: "New chat", ts: Date.now() };
      state.chats.unshift(c); state.current = c.id; saveChats();
    }
  }

  // ------------------------------------------------------------ sidebar
  function renderFeatureNav() {
    const host = $("featureNav"); host.replaceChildren();
    state.cfg.groups.forEach((g) => {
      const b = el("button", { class: "nav-item", "data-view": g.key, html:
        `<span class="chip c-${g.accent}">${svg(g.icon)}</span><span class="nav-text"><b>${esc(g.label)}</b><small>${esc(g.blurb)}</small></span>` });
      b.addEventListener("click", () => { openFeature(g.key); closeMenu(); });
      host.append(b);
    });
    const sb = el("button", { class: "nav-item", "data-view": "schedules", html:
      `<span class="chip c-blue">${svg("clock")}</span><span class="nav-text"><b>Scheduled tasks</b><small>Run the agent at set times</small></span>` });
    sb.addEventListener("click", () => { openSchedules(); closeMenu(); });
    host.append(sb);
  }
  function markNav(view) {
    document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === view));
  }
  function renderRecent() {
    const host = $("recentChats"); host.replaceChildren();
    const q = $("chatSearch").value.trim().toLowerCase();
    const list = state.chats.filter((c) => !q || c.title.toLowerCase().includes(q));
    if (!list.length) { host.append(el("div", { class: "empty-note" }, q ? "No chats match." : "No chats yet.")); return; }
    list.forEach((c) => {
      const item = el("div", { class: "recent-item" + (c.id === state.current && state.view === "chat" ? " active" : ""), role: "button", tabindex: "0" },
        el("span", { title: c.title }, c.title),
        el("button", { class: "del", type: "button", "aria-label": "Delete chat", html: svg("x"), onclick: (e) => { e.stopPropagation(); deleteChat(c.id); } }));
      const open = () => { switchChat(c.id); closeMenu(); };
      item.addEventListener("click", open);
      item.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      host.append(item);
    });
  }

  async function refreshStatus() {
    try {
      const s = await api("/api/status");
      $("stModel").textContent = s.model;
      $("topMemory").textContent = s.memory.startsWith("Postgres") ? "Memory: Postgres" : s.memory.startsWith("SQLite") ? "Memory: saved" : "Memory: in-session";
      $("topMemory").title = s.memory;
      const pills = [];
      pills.push([s.allow_all_reads ? "Read: all of C:/D:" : `Read: ${s.read_dirs.length} folder(s)`, s.allow_all_reads ? "warn" : "ok"]);
      pills.push([!s.modify_enabled ? "Changes: off" : s.allow_all_writes ? "Write: anywhere" : `Write: ${s.write_dirs.length} folder(s)`, !s.modify_enabled ? "info" : s.allow_all_writes ? "err" : "ok"]);
      pills.push([s.confirm_changes ? "Approval on" : "Approval OFF", s.confirm_changes ? "ok" : "warn"]);
      pills.push([s.tavily ? "Web: ready" : "Web: no key", s.tavily ? "ok" : "warn"]);
      if (!s.computer_enabled) pills.push(["Computer: off", "info"]);
      $("stPills").replaceChildren(...pills.map(([t, k]) => el("span", { class: `pill ${k}` }, t)));
      const notes = [];
      if (!s.tavily) notes.push("Add TAVILY_API_KEY to .env for web search.");
      if (s.whisperer && !s.whisperer_key) notes.push("LLMWhisperer is on but its key is missing.");
      $("stNote").textContent = notes.join(" ");
    } catch { $("stModel").textContent = "server unreachable"; }
  }

  // ------------------------------------------------------------ views
  function setView(view, title, sub) {
    state.view = view;
    $("viewChat").hidden = view !== "chat";
    $("viewFeature").hidden = view === "chat" || view === "schedules";
    $("viewSchedules").hidden = view !== "schedules";
    $("viewTitle").textContent = title;
    $("viewSub").textContent = sub || "";
    markNav(view);
    renderRecent();
  }

  async function switchChat(id) {
    state.current = id; saveChats();
    setView("chat", "Chat", chatTitle());
    await loadHistory();
  }
  const chatTitle = () => (state.chats.find((c) => c.id === state.current) || {}).title || "";

  function newChat() {
    const c = { id: uid(), title: "New chat", ts: Date.now() };
    state.chats.unshift(c); state.current = c.id; saveChats();
    setView("chat", "Chat", "");
    paintMessages([]);
    $("prompt").focus();
  }
  async function deleteChat(id) {
    try { await api("/api/clear", { thread: id }); } catch { /* ignore */ }
    state.chats = state.chats.filter((c) => c.id !== id);
    if (state.current === id) { state.current = null; ensureChat(); saveChats(); await switchChat(state.current); }
    else { saveChats(); renderRecent(); }
  }

  // ------------------------------------------------------------ chat
  function messageNode(item, i) {
    const isUser = item.role === "user";
    const bubble = el("div", { class: "bubble" });
    bubble.append(el("div", { class: "md", html: md(item.content) }));
    if (!isUser) {
      (item.results || []).forEach((r) => bubble.append(renderResult(r)));
      if (item.elapsed != null) bubble.append(el("div", { class: "msg-meta" }, `⏱️ Completed in ${Number(item.elapsed).toFixed(1)} s`));
    }
    return el("div", { class: `msg ${item.role}` },
      el("div", { class: "avatar", html: isUser ? "You" : svg("sparkle").replace('class="ic"', 'class="ic" style="stroke:#fff;width:16px;height:16px"') }), bubble);
  }
  function paintMessages(history) {
    const view = $("viewChat");
    view.classList.toggle("empty", history.length === 0);
    const thread = el("div", { class: "thread" }, history.map(messageNode));
    $("messages").replaceChildren(thread);
    scrollDown(true);
  }
  function scrollDown(instant) {
    const m = $("messages");
    if (instant) { m.style.scrollBehavior = "auto"; m.scrollTop = m.scrollHeight; m.style.scrollBehavior = ""; }
    else m.scrollTop = m.scrollHeight;
  }
  async function loadHistory() {
    paintMessages([]);
    try {
      const { history } = await api(`/api/history?thread=${encodeURIComponent(state.current)}`);
      paintMessages(history || []);
    } catch { /* leave empty */ }
  }

  function setBusy(b) {
    state.busy = b; $("sendBtn").disabled = b; $("prompt").disabled = b;
    if (!b) $("prompt").focus();
  }

  async function send(text) {
    text = text.trim();
    if (!text || state.busy) return;
    ensureChat();
    const chat = state.chats.find((c) => c.id === state.current);
    if (chat.title === "New chat") { chat.title = text.length > 42 ? text.slice(0, 42) + "…" : text; $("viewSub").textContent = chat.title; }
    chat.ts = Date.now(); saveChats(); renderRecent();

    $("viewChat").classList.remove("empty");
    let thread = $("messages").querySelector(".thread");
    if (!thread) { thread = el("div", { class: "thread" }); $("messages").replaceChildren(thread); }
    thread.append(messageNode({ role: "user", content: text }));

    const clock = el("span", {}, "Working… 0.0 s");
    const typing = el("div", { class: "msg assistant" },
      el("div", { class: "avatar", html: svg("sparkle").replace('class="ic"', 'class="ic" style="stroke:#fff;width:16px;height:16px"') }),
      el("div", { class: "bubble" }, el("div", { class: "typing" }, el("span", { class: "dots", html: "<i></i><i></i><i></i>" }), clock)));
    thread.append(typing); scrollDown();
    $("prompt").value = ""; autosize();
    setBusy(true);
    const t0 = performance.now();
    const tick = setInterval(() => { clock.textContent = `Working… ${((performance.now() - t0) / 1000).toFixed(1)} s`; }, 200);
    try {
      const r = await api("/api/chat", { message: text, thread: state.current, drive: state.drive });
      typing.replaceWith(messageNode({ role: "assistant", content: r.text, results: r.results, elapsed: r.elapsed }));
    } catch (e) {
      typing.replaceWith(messageNode({ role: "assistant", content: `⚠️ Could not reach the server: ${e.message}`, results: [] }));
    } finally {
      clearInterval(tick); setBusy(false); scrollDown();
    }
  }

  function autosize() { const p = $("prompt"); p.style.height = "auto"; p.style.height = Math.min(p.scrollHeight, 180) + "px"; }

  // ------------------------------------------------------------ upload box (replaces typed file paths)
  const fmtSize = (n) => n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
  const extOf = (name) => (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();

  function uploadBox(f) {
    const exts = f.accept.split(",");
    const nice = f.kind === "pdf" ? "PDF" : "image";
    state.picks[f.name] = [];
    const input = el("input", { type: "file", accept: f.accept, multiple: f.multiple, hidden: true });
    const list = el("div", { class: "file-list" });
    const zone = el("div", { class: "dropzone", role: "button", tabindex: "0", "aria-label": `Choose ${nice} file` },
      el("span", { class: "dz-ic", html: svg("upload") }),
      el("span", { class: "dz-text" },
        el("b", {}, f.multiple ? `Drop your ${nice} files here` : `Drop your ${nice} here`),
        el("span", {}, " or click to browse")),
      el("small", {}, `${f.kind === "pdf" ? "PDF" : "Image"} only · up to ${f.max_mb} MB each`));

    const paint = () => {
      const picks = state.picks[f.name];
      zone.classList.toggle("has-file", picks.length > 0 && !f.multiple);
      list.replaceChildren(...picks.map((file, i) => {
        const move = (d) => { const a = picks; [a[i], a[i + d]] = [a[i + d], a[i]]; paint(); };
        return el("div", { class: "file-row" },
          el("span", { class: "file-ic", html: svg("pdf") }),
          el("span", { class: "file-meta" }, el("b", { title: file.name }, file.name), el("small", {}, fmtSize(file.size))),
          f.multiple && picks.length > 1 ? el("span", { class: "file-move" },
            el("button", { class: "icon-btn sm", type: "button", title: "Move up", disabled: i === 0, onclick: () => move(-1), html: `<svg class="ic" style="transform:rotate(-90deg)"><use href="#i-chevron"/></svg>` }),
            el("button", { class: "icon-btn sm", type: "button", title: "Move down", disabled: i === picks.length - 1, onclick: () => move(1), html: `<svg class="ic" style="transform:rotate(90deg)"><use href="#i-chevron"/></svg>` })) : null,
          el("button", { class: "icon-btn sm", type: "button", title: "Remove", onclick: () => { picks.splice(i, 1); paint(); }, html: svg("x") }));
      }));
    };

    const add = (files) => {
      const picks = state.picks[f.name];
      for (const file of files) {
        if (!exts.includes(extOf(file.name))) { toast(`${file.name}: only ${nice} files are allowed.`); continue; }
        if (file.size > f.max_mb * 1024 * 1024) { toast(`${file.name} is over ${f.max_mb} MB.`); continue; }
        if (!f.multiple) { picks.splice(0, picks.length, file); break; }
        if (!picks.some((p) => p.name === file.name && p.size === file.size && p.lastModified === file.lastModified)) picks.push(file);
      }
      zone.classList.remove("err"); paint();
    };

    zone.addEventListener("click", () => input.click());
    zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    input.addEventListener("change", () => { add([...input.files]); input.value = ""; });
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
    zone.addEventListener("drop", (e) => add([...e.dataTransfer.files]));
    state.zones[f.name] = zone;
    return el("div", { class: "upload" }, zone, input, list);
  }

  async function uploadPicks(f) {
    const fd = new FormData();
    fd.append("kind", f.kind);
    state.picks[f.name].forEach((file) => fd.append("files", file));
    let data;
    try { data = await (await fetch("/api/upload", { method: "POST", body: fd })).json(); }
    catch { throw new Error("Upload failed. Is the app still running?"); }
    if (!data.ok) throw new Error(data.error || "Upload failed.");
    return data.files.map((x) => x.path);
  }

  // ------------------------------------------------------------ feature (manual tools) view
  function openFeature(key) {
    const g = state.cfg.groups.find((x) => x.key === key);
    state.group = g;
    setView(key, g.label, g.blurb);
    $("fIcon").className = "chip big c-purple"; $("fIcon").innerHTML = svg(g.icon);
    $("fTitle").textContent = g.label; $("fBlurb").textContent = g.blurb;
    const tabs = $("toolTabs"); tabs.replaceChildren();
    g.tools.forEach((t) => {
      tabs.append(el("button", { class: "tab", role: "tab", type: "button", "data-tool": t.name, onclick: () => selectTool(t.name) }, t.label || t.name));
    });
    selectTool(g.tools[0].name);
  }

  function selectTool(name) {
    const t = state.group.tools.find((x) => x.name === name);
    state.tool = t;
    document.querySelectorAll("#toolTabs .tab").forEach((b) => b.setAttribute("aria-selected", b.dataset.tool === name));
    $("toolDesc").textContent = t.description;
    $("toolResult").replaceChildren(); $("toolTime").textContent = "";
    state.picks = {}; state.zones = {};
    const form = $("toolForm"); form.replaceChildren();
    if (!t.fields.length) form.append(el("p", { class: "no-input field wide" }, name === "get_disk_usage" ? "Uses the drive picked in the sidebar." : "No input needed."));
    t.fields.forEach((f) => {
      const id = `f_${t.name}_${f.name}`;
      if (f.type === "file") {
        form.append(el("div", { class: "field wide" }, el("label", {}, f.label), uploadBox(f), f.help ? el("small", {}, f.help) : null));
        return;
      }
      let input;
      if (f.type === "textarea") input = el("textarea", { id, name: f.name, placeholder: f.help || "" });
      else if (f.type === "select") input = el("select", { id, name: f.name }, f.options.map((o) => el("option", { value: o }, o === "" ? "(any)" : (f.labels && f.labels[o]) || o)));
      else if (f.type === "number") input = el("input", { id, name: f.name, type: "number", min: f.min, max: f.max });
      else if (f.type === "checkbox") input = el("input", { id, name: f.name, type: "checkbox" });
      else input = el("input", { id, name: f.name, type: f.type === "password" ? "password" : "text", placeholder: f.type === "password" ? "" : f.placeholder || f.help || "", spellcheck: "false", autocomplete: f.type === "password" ? "new-password" : null });
      if (f.type === "checkbox") { input.checked = !!f.default; form.append(el("div", { class: "field" }, el("label", { class: "check", for: id }, input, f.label))); }
      else {
        input.value = f.default ?? "";
        form.append(el("div", { class: "field" + (f.type === "textarea" ? " wide" : "") }, el("label", { for: id }, f.label), input, f.help && f.type !== "textarea" ? el("small", {}, f.help) : null));
      }
    });
  }

  async function runTool(e) {
    e.preventDefault();
    const t = state.tool, args = {};
    const fileFields = t.fields.filter((f) => f.type === "file");
    const empty = fileFields.find((f) => !state.picks[f.name] || !state.picks[f.name].length);
    if (empty) {
      (state.zones[empty.name] || document.querySelector("#toolForm .dropzone")).classList.add("err");
      toast(empty.multiple ? "Add your files first." : "Choose a file first.");
      return;
    }
    t.fields.filter((f) => f.type !== "file").forEach((f) => {
      const i = $(`f_${t.name}_${f.name}`);
      args[f.name] = f.type === "checkbox" ? i.checked : f.type === "number" ? Number(i.value) : i.value;
    });
    const btn = $("runTool"); btn.disabled = true; btn.textContent = fileFields.length ? "Uploading…" : "Running…";
    $("toolResult").replaceChildren(el("div", { class: "typing" }, el("span", { class: "dots", html: "<i></i><i></i><i></i>" }), fileFields.length ? "Uploading your file…" : `Running ${t.name}…`));
    try {
      for (const f of fileFields) {
        const paths = await uploadPicks(f);
        args[f.name] = f.multiple ? paths.join("\n") : paths[0];
      }
      if (fileFields.length) { btn.textContent = "Running…"; $("toolResult").querySelector(".typing").lastChild.textContent = `Running ${t.label || t.name}…`; }
      const r = await api(`/api/tool/${t.name}`, { args, drive: state.drive });
      $("toolTime").textContent = `⏱️ ${r.elapsed.toFixed(2)} s`;
      $("toolResult").replaceChildren(renderResult(r.result));
    } catch (err) {
      $("toolResult").replaceChildren(alertBox("err", `Request failed: ${err.message}`));
    } finally { btn.disabled = false; btn.textContent = "Run tool"; }
  }

  // ------------------------------------------------------------ scheduled tasks
  const DAY_NAMES = { 0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday" };

  function readSchedule() {
    const f = $("sFreq").value, [hh, mm] = ($("sTime").value || "09:00").split(":").map(Number);
    if (f === "interval") return { kind: "interval", value: Number($("sMinutes").value) };
    if (f === "daily") return { kind: "cron", value: `${mm} ${hh} * * *` };
    if (f === "weekdays") return { kind: "cron", value: `${mm} ${hh} * * 1-5` };
    if (f === "weekly") return { kind: "cron", value: `${mm} ${hh} * * ${$("sDay").value}` };
    return { kind: "cron", value: $("sCron").value.trim() };
  }

  function describeSchedule(j) {
    if (j.kind === "interval") return `Every ${j.value} minutes`;
    const m = String(j.value).match(/^(\d+) (\d+) \* \* (\*|1-5|\d)$/);
    if (!m) return `Cron: ${j.value}`;
    const t = `${m[2].padStart(2, "0")}:${m[1].padStart(2, "0")}`;
    return m[3] === "*" ? `Every day at ${t}` : m[3] === "1-5" ? `Weekdays at ${t}` : `Every ${DAY_NAMES[m[3]]} at ${t}`;
  }

  const fmtWhen = (iso) => iso ? iso.replace("T", " ").slice(0, 16) : "never";

  async function loadSchedules() {
    const host = $("schedList");
    let jobs;
    try { jobs = (await api("/api/schedules")).jobs; }
    catch (e) { host.replaceChildren(alertBox("err", `Could not load schedules: ${e.message}`)); return; }
    if (!jobs.length) { host.replaceChildren(el("p", { class: "hint" }, "No scheduled tasks yet. Add one above.")); return; }
    host.replaceChildren(...jobs.map((j) => {
      const act = async (fn, msg) => { try { await fn(); if (msg) toast(msg); } catch (e) { toast(`Failed: ${e.message}`); } loadSchedules(); };
      const status = j.last_status ? el("span", { class: `badge ${j.last_status}` }, j.last_status.replace("_", " ")) : null;
      return el("div", { class: "sched-card" + (j.enabled ? "" : " off") },
        el("div", { class: "sched-main" },
          el("div", {}, el("b", {}, j.name), status),
          el("div", { class: "when" }, `${describeSchedule(j)} · ${j.enabled ? `next: ${fmtWhen(j.next_run)}` : "paused"}`),
          el("div", { class: "when" }, `Last run: ${fmtWhen(j.last_run)}`),
          el("p", { class: "task" }, j.prompt),
          j.last_summary ? el("div", { class: "last" }, el("b", {}, "Result"), el("div", { class: "md last-md", html: md(j.last_summary) })) : null),
        el("div", { class: "sched-actions" },
          el("button", { class: "btn", type: "button", html: `${svg("send")}Run now`,
            onclick: () => act(() => api(`/api/schedules/${j.id}/run`, {}), "Started. Approvals (if any) appear at the top.") }),
          el("button", { class: "btn", type: "button", onclick: () => act(() => api(`/api/schedules/${j.id}`, { enabled: !j.enabled }, "PATCH")) },
            j.enabled ? "Pause" : "Resume"),
          el("button", { class: "btn stop", type: "button", html: `${svg("trash")}Delete`,
            onclick: () => { if (confirm(`Delete "${j.name}"?`)) act(() => api(`/api/schedules/${j.id}`, {}, "DELETE"), "Deleted"); } })));
    }));
  }

  function openSchedules() {
    setView("schedules", "Scheduled tasks", "Runs the agent at set times");
    syncFreq(); loadSchedules(); pollInbox();
  }

  function syncFreq() {
    const f = $("sFreq").value;
    $("sMinutesBox").hidden = f !== "interval";
    $("sTimeBox").hidden = !["daily", "weekdays", "weekly"].includes(f);
    $("sDayBox").hidden = f !== "weekly";
    $("sCronBox").hidden = f !== "cron";
  }

  async function addSchedule(e) {
    e.preventDefault();
    const body = { name: $("sName").value, prompt: $("sPrompt").value, ...readSchedule() };
    const btn = $("sAdd"); btn.disabled = true;
    try {
      const r = await fetch("/api/schedules", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await r.json();
      if (!data.ok) { $("sMsg").textContent = "⚠️ " + (data.error || "Could not add."); return; }
      $("sMsg").textContent = ""; $("sName").value = ""; $("sPrompt").value = ""; toast("Schedule added");
      loadSchedules();
    } catch (err) { $("sMsg").textContent = "⚠️ " + err.message; }
    finally { btn.disabled = false; }
  }

  // ------------------------------------------------------------ mobile menu
  const openMenu = () => $("app").classList.add("sb-open");
  const closeMenu = () => $("app").classList.remove("sb-open");

  // ------------------------------------------------------------ approval inbox (actions from scheduled runs)
  const inboxSeen = new Set();
  async function pollInbox() {
    try {
      const { items } = await api("/api/pending");
      for (const it of items) {
        if (inboxSeen.has(it.pending_id)) continue;
        inboxSeen.add(it.pending_id);
        // renderPending shows Approve / Reject and later the result; approval always goes through the guard.
        $("inbox").append(el("div", {}, el("div", { class: "origin" }, it.origin),
          renderPending({ pending_id: it.pending_id, description: it.description })));
      }
    } catch { /* offline or restarting: try again next time */ }
  }

  // ------------------------------------------------------------ boot
  async function boot() {
    state.cfg = await api("/api/config");
    renderFeatureNav();
    $("suggest").replaceChildren(...state.cfg.suggestions.map((s) =>
      el("button", { class: "sg-card", type: "button", onclick: () => send(s.text) },
        el("span", { class: "sg-ic", html: svg(s.icon) }), el("b", {}, s.title), el("span", {}, s.text))));

    ensureChat();
    $("drive").value = state.drive; $("topDrive").textContent = "Drive: " + (state.drive === "all" ? "All" : state.drive + ":");
    $("drive").addEventListener("change", (e) => {
      state.drive = e.target.value; store.set("drive", state.drive);
      $("topDrive").textContent = "Drive: " + (state.drive === "all" ? "All" : state.drive + ":");
    });
    $("newChat").addEventListener("click", () => { newChat(); closeMenu(); });
    document.querySelector('[data-view="chat"]').addEventListener("click", () => { switchChat(state.current); closeMenu(); });
    $("chatSearch").addEventListener("input", renderRecent);
    $("composer").addEventListener("submit", (e) => { e.preventDefault(); send($("prompt").value); });
    $("prompt").addEventListener("input", autosize);
    $("prompt").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("prompt").value); } });
    $("toolForm").addEventListener("submit", runTool);
    $("schedForm").addEventListener("submit", addSchedule);
    $("sFreq").addEventListener("change", syncFreq);
    $("menuBtn").addEventListener("click", openMenu);
    $("sbClose").addEventListener("click", closeMenu);
    $("scrim").addEventListener("click", closeMenu);
    $("clearChat").addEventListener("click", async () => {
      if (!confirm("Clear this chat's history?")) return;
      await api("/api/clear", { thread: state.current });
      const c = state.chats.find((x) => x.id === state.current); if (c) c.title = "New chat";
      saveChats(); setView("chat", "Chat", ""); paintMessages([]); toast("Chat history cleared");
    });
    $("checkModel").addEventListener("click", async () => {
      const dot = $("modelDot"); dot.className = "dot";
      $("stNote").textContent = "Checking…";
      try { const r = await api("/api/model-check", {}); dot.className = `dot ${r.level}`; $("stNote").textContent = r.message; }
      catch { dot.className = "dot error"; $("stNote").textContent = "Could not reach the server."; }
    });

    setView("chat", "Chat", chatTitle() === "New chat" ? "" : chatTitle());
    refreshStatus();
    await loadHistory();
    $("prompt").focus();
  }
  setInterval(pollInbox, 15000); pollInbox();
  boot().catch((e) => { document.body.prepend(el("p", { style: "padding:20px;color:#a53347" }, `Could not start the UI: ${e.message}`)); });
})();