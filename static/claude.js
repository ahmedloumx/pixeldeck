"use strict";

/* The Claude panel: Claude Code as a conversation beside your pull requests, the way the VS Code
   and Windsurf extensions show it, drawn in PixelDeck's own style. The terminal stays the other
   way to run it: Settings, Connected apps, Claude Code picks where a pull request's Claude
   buttons go.

   Everything it shows about Claude comes from the Claude Code installed here, signed in as
   whoever uses this computer (chats.py): the models and their effort levels, the slash commands,
   the account and plan, the five-hour and weekly usage windows, the context a session fills,
   and the sessions Claude Code keeps. Nothing about an account is written in this file.

   Two views share the panel: the sessions (account and usage, then the sessions of a folder)
   and a chat (your messages, Claude's answers as it writes them, each tool with what came back,
   and Allow, Allow for this chat or Deny before a tool runs). The composer below them attaches
   files (+, a drop or a paste), lists the slash commands (/), shows the context and the usage
   left, and picks the model, its effort and the permission mode. Text from Claude goes through
   the page's escape-first Markdown renderer; tool input and output are shown as text only. */

const ClaudePanel = (() => {
  const WORD = { Read: "Read", Write: "Write", Edit: "Edit", MultiEdit: "Edit", NotebookEdit: "Edit", Bash: "Run", PowerShell: "Run",
    Glob: "Find", Grep: "Search", WebFetch: "Fetch", WebSearch: "Search the web for", TodoWrite: "Update the plan", Task: "Agent:", Agent: "Agent:" };
  const STATUS = { new: "New", starting: "Starting", working: "Working", waiting: "Needs you", idle: "Ready",
    stopping: "Ending", stopped: "Ended", error: "Stopped" };
  const BUSY = ["new", "starting", "working", "waiting", "stopping"];
  const MODE_LABEL = { default: "Ask", acceptEdits: "Accept edits", plan: "Plan", auto: "Auto" };
  const MODE_HINT = { default: "Claude asks before it edits a file or runs a command",
    acceptEdits: "Edits go through; commands still ask", plan: "Claude plans first and changes nothing until you agree",
    auto: "Claude Code decides, and asks only when it is unsure" };
  let root = null, chats = [], folders = [], modes = {}, efforts = [], problem = null, listed = false;
  let info = null, infoAt = 0, infoLoading = false, sessions = null, sessionsFolder = null, search = "";
  let current = null, chat = null, after = 0, live = "", loop = 0, watching = 0, sending = false, view = "sessions";
  let context = null, files = [], menu = null, menuIndex = 0;
  const tools = new Map(), asks = new Map();
  const seenAsks = new Set();

  const pref = (key, fallback) => { const v = typeof Prefs !== "undefined" ? Prefs.get(key) : undefined; return v === undefined || v === null ? fallback : v; };
  const setPref = (patch) => { if (typeof Prefs !== "undefined") Prefs.set(patch); };
  const parse = (text) => { try { return JSON.parse(text); } catch { return null; } };
  const base = (p) => String(p || "").split(/[\\/]/).pop();
  const short = (s, n = 80) => { s = String(s ?? "").replace(/\s+/g, " ").trim(); return s.length > n ? `${s.slice(0, n - 1)}…` : s; };
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const markdown = (text) => (typeof md === "function" ? md(text) : `<p>${esc(text)}</p>`);
  const shown = () => typeof Layout !== "undefined" && Layout.isShown("claude");
  function glyph(size = 14) {
    const rows = ["#...#...#", ".#..#..#.", "..#.#.#..", "...###...", "#########", "...###...", "..#.#.#..", ".#..#..#.", "#...#...#"];
    let r = "";
    rows.forEach((row, y) => [...row].forEach((c, x) => { if (c === "#") r += `<rect x="${x}" y="${y}" width="1" height="1"/>`; }));
    return `<svg class="cl-glyph" viewBox="0 0 9 9" width="${size}" height="${size}" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true">${r}</svg>`;
  }
  // A compact age, as the extensions list sessions: now, 4m, 3h, 16d, 2mo, 1y.
  function age(seconds) {
    const s = Math.max(0, Date.now() / 1000 - seconds);
    if (s < 60) return "now";
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${Math.floor(s / 3600)}h`;
    if (s < 30 * 86400) return `${Math.floor(s / 86400)}d`;
    if (s < 365 * 86400) return `${Math.floor(s / (30 * 86400))}mo`;
    return `${Math.floor(s / (365 * 86400))}y`;
  }
  function resets(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    const today = new Date();
    if (d.toDateString() === today.toDateString()) return `Resets at ${time}`;
    return `Resets ${d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}, ${time}`;
  }
  function until(iso) {
    const ms = new Date(iso).getTime() - Date.now();
    if (!iso || Number.isNaN(ms)) return "";
    if (ms <= 0) return "now";
    const m = Math.round(ms / 60000);
    return m < 60 ? `${m}m` : m < 48 * 60 ? `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}` : `${Math.round(m / 1440)}d`;
  }

  function summary(name, input) {
    const i = input && typeof input === "object" ? input : {};
    const what = i.file_path || i.notebook_path ? base(i.file_path || i.notebook_path)
      : i.command ? short(i.command) : i.pattern ? short(i.pattern, 60) : i.url ? short(i.url, 70)
        : i.query ? short(i.query, 70) : i.description ? short(i.description, 70) : "";
    const word = WORD[name] || name || "Tool";
    return what ? `${word} ${what}` : word;
  }

  /* ---- the model, its effort, the mode ---- */

  const models = () => info?.models || [];
  const modelValue = () => (chat ? chat.model || "" : pref("claudeModel", ""));
  const effortValue = () => (chat ? chat.effort || "" : pref("claudeEffort", ""));
  const modeValue = () => (chat ? chat.mode || "default" : pref("claudeMode", "default"));
  const modelOf = (value) => models().find((m) => m.value === (value || "default")) || null;
  function modelLabel(value) {
    const m = modelOf(value);
    if (!m) return value ? cap(value) : "Default model";
    const head = String(m.description || "").split("·")[0].trim();
    return head || m.displayName || m.value;
  }
  function pickLabel() {
    const effort = effortValue();
    return `${modelLabel(modelValue())}${effort ? ` · ${cap(effort)}` : ""}`;
  }

  /* ---- drawing ---- */

  function headHtml() {
    const st = chat?.status;
    const busy = chat && ["working", "waiting", "starting"].includes(st);
    const title = view === "chat" && chat ? chat.title : "Claude Code";
    return `<header class="cl-head">${glyph()}<span class="cl-title" title="${esc(title)}">${esc(title)}</span>
      ${view === "chat" && chat ? `<span class="cl-state is-${esc(st)}">${esc(STATUS[st] || st)}</span>` : ""}
      <span class="cl-spacer"></span>
      ${view === "chat" && busy ? '<button class="cl-tool-btn" type="button" data-cl-interrupt data-tip="Stop what Claude is doing now; the chat stays open">Stop</button>' : ""}
      ${view === "chat" && chat?.alive ? '<button class="cl-tool-btn" type="button" data-cl-end data-tip="End this chat; write again to resume it">End</button>' : ""}
      <button class="cl-tool-btn" type="button" data-cl-view="${view === "chat" ? "sessions" : "chat"}" aria-pressed="${view === "sessions"}"
        data-tip="${view === "chat" ? "Sessions, account and usage" : "Back to the chat"}">${view === "chat" ? "Sessions" : chat ? "Chat" : "Sessions"}</button>
      <button class="cl-tool-btn is-new" type="button" data-cl-new data-tip="A new session">+ New</button></header>`;
  }

  function meter(label, window) {
    if (!window || typeof window.utilization !== "number") return "";
    const pct = Math.max(0, Math.min(100, Math.round(window.utilization)));
    return `<div class="cl-meter${pct >= 90 ? " is-bad" : pct >= 75 ? " is-warn" : ""}"><div class="cl-meter-row"><span>${esc(label)}</span><b>${pct}%</b></div>
      <div class="cl-meter-bar" role="progressbar" aria-label="${esc(label)}" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="--w:${pct / 100}"></span></div>
      <small>${esc(resets(window.resets_at))}</small></div>`;
  }
  function accountHtml() {
    if (problem) {
      return `<p class="cl-error">${esc(problem)}</p><p><button class="btn" type="button" data-cl-settings>How to set it up</button></p>`;
    }
    if (!info) return `<p class="cl-muted">${infoLoading ? "Asking Claude Code…" : ""}</p>`;
    const acc = info.account || {};
    const signedIn = !!acc.email;
    return `<dl class="cl-rows">
        <div><dt>Account</dt><dd>${signedIn ? esc(acc.email) : '<span class="cl-muted">not signed in</span>'}</dd></div>
        ${acc.plan ? `<div><dt>Plan</dt><dd>${esc(acc.plan)}</dd></div>` : ""}</dl>
      ${meter("Session (5h)", info.usage?.five_hour)}${meter("Weekly (7 days)", info.usage?.seven_day)}
      ${info.error ? `<p class="cl-error">${esc(info.error)}</p>` : ""}
      <p class="cl-actions"><button class="btn${signedIn ? " btn--quiet" : " btn--primary"}" type="button" data-cl-login
        data-tip="Opens Claude Code in the Terminal panel: type /login there to sign in with your own account">${signedIn ? "Switch account" : "Sign in"}</button>
        <button class="btn btn--quiet" type="button" data-cl-refresh-info>Refresh</button></p>
      <p class="cl-muted">This is the Claude Code account of whoever uses this computer: each person signs in with their own.</p>`;
  }
  function sessionsHtml() {
    const folder = pref("claudeFolder", "");
    const pick = folders.length ? `<select data-cl-folder data-retro aria-label="Folder">${folders.map((f) => `<option value="${esc(f.id)}"${f.id === folder ? " selected" : ""}>${esc(f.label)}</option>`).join("")}</select>` : "";
    const rows = [];
    const listedIds = new Set();
    const needle = search.trim().toLowerCase();
    for (const s of sessions || []) {
      if (needle && !s.title.toLowerCase().includes(needle)) continue;
      const mine = s.chat ? chats.find((c) => c.id === s.chat) : null;
      if (mine) listedIds.add(mine.id);
      const dot = mine?.pending?.length ? "is-waiting" : s.alive ? "is-live" : "";
      rows.push(`<button class="cl-session${current && mine?.id === current ? " is-current" : ""}" type="button" data-cl-session="${esc(s.id)}">
        <span class="cl-dot ${dot}" aria-hidden="true"></span><span class="cl-session-title">${esc(s.title)}</span><time>${esc(age(s.at))}</time></button>`);
    }
    // PixelDeck's chats that Claude Code has not written a session for yet (just started).
    const extra = chats.filter((c) => !listedIds.has(c.id) && (!needle || c.title.toLowerCase().includes(needle)))
      .filter((c) => !c.session || !(sessions || []).some((s) => s.id === c.session));
    const extraRows = extra.map((c) => `<button class="cl-session${c.id === current ? " is-current" : ""}" type="button" data-cl-chat="${esc(c.id)}">
        <span class="cl-dot ${c.pending?.length ? "is-waiting" : c.alive ? "is-live" : ""}" aria-hidden="true"></span><span class="cl-session-title">${esc(c.title)}</span><time>${esc(age(c.created))}</time></button>`);
    // Two sections that fold, as in the extensions; each remembers whether it was open.
    const fold = (key, title, body) => `<details class="cl-fold" data-cl-fold="${key}"${pref(`claudeFold_${key}`, true) ? " open" : ""}>
      <summary class="cl-sec">${title}</summary>${body}</details>`;
    return `<section class="cl-side" aria-label="Sessions">
      ${fold("account", "Account and usage", accountHtml())}
      ${fold("sessions", "Sessions", `<div class="cl-session-tools">${pick}<label class="field cl-search"><span class="visually-hidden">Search sessions</span>
        <input type="search" data-cl-search placeholder="Search sessions" value="${esc(search)}" autocomplete="off" spellcheck="false"></label></div>
      <button class="cl-session is-add" type="button" data-cl-new><span aria-hidden="true">+</span><span class="cl-session-title">New session</span></button>
      ${extraRows.length ? `<p class="cl-group">In PixelDeck</p>${extraRows.join("")}` : ""}
      ${sessions === null ? '<p class="cl-muted">Reading the sessions…</p>'
        : rows.length ? `<p class="cl-group">Claude Code, in this folder <b>${(sessions || []).length}</b></p>${rows.join("")}`
          : `<p class="cl-muted">${needle ? "No session matches." : "No Claude Code session in this folder yet."}</p>`}`)}
    </section>`;
  }
  function emptyChatHtml() {
    const mascot = typeof Sprites !== "undefined" ? Sprites.svg("buddy", { scale: 4 }) : "";
    return `<div class="cl-empty">${mascot}<h3>Claude, as a chat</h3>
      <p>Claude Code runs here the way the VS Code and Windsurf extensions show it: your messages, its answers, each file it reads or edits, and a yes or no from you before it changes anything.</p>
      <p>Write below to start a session in <b>${esc((folders.find((f) => f.id === pref("claudeFolder", "")) || folders[0] || { label: "your clones" }).label)}</b>, or open one from the sessions.</p></div>`;
  }
  function composeHtml() {
    const ctx = context && typeof context.percentage === "number" ? context : null;
    const five = info?.usage?.five_hour;
    const pct = ctx ? Math.max(0, Math.min(100, ctx.percentage)) : 0;
    const ring = `<span class="cl-ring${pct >= 80 ? " is-warn" : ""}" data-tip="${ctx ? `Context: ${pct}% used (${(ctx.totalTokens || 0).toLocaleString("en-GB")} of ${(ctx.maxTokens || 0).toLocaleString("en-GB")} tokens)` : "Context: shown once the session runs"}">
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><circle cx="10" cy="10" r="8" class="cl-ring-bg"/><circle cx="10" cy="10" r="8" class="cl-ring-fg" style="--p:${pct}"/></svg>${ctx ? `<small>${pct}%</small>` : ""}</span>`;
    const clock = five && five.resets_at ? `<span class="cl-clock${five.utilization >= 90 ? " is-bad" : five.utilization >= 75 ? " is-warn" : ""}"
      data-tip="Session (5h): ${Math.round(five.utilization)}% used. ${esc(resets(five.resets_at))}.${info?.usage?.seven_day ? ` Weekly: ${Math.round(info.usage.seven_day.utilization)}%.` : ""}">⏱ ${esc(until(five.resets_at))}</span>` : "";
    const busy = chat && ["working", "waiting", "starting"].includes(chat.status);
    const mode = modeValue();
    return `<form class="cl-compose" data-cl-compose autocomplete="off">
      ${files.length ? `<div class="cl-files">${files.map((f, i) => `<span class="cl-file-chip" title="${esc(f.path)}">${esc(f.name)}<button type="button" data-cl-unattach="${i}" aria-label="Remove ${esc(f.name)}">×</button></span>`).join("")}</div>` : ""}
      <div class="cl-input"><textarea data-cl-text rows="3" placeholder="${chat ? "Reply to Claude" : "Ask Claude about your code"}  ·  / for commands" aria-label="Message to Claude"></textarea></div>
      <div class="cl-bar">
        <button class="cl-icon" type="button" data-cl-attach data-tip="Attach files (or drop them here, or paste an image)" aria-label="Attach files">+</button>
        <button class="cl-icon" type="button" data-cl-slash data-tip="Slash commands" aria-label="Slash commands">/</button>
        ${ring}${clock}
        <span class="cl-spacer"></span>
        <button class="cl-pick" type="button" data-cl-menu="model" data-tip="Model and effort">${esc(pickLabel())}</button>
        <button class="cl-pick${mode === "auto" ? " is-auto" : ""}" type="button" data-cl-menu="mode" data-tip="${esc(MODE_HINT[mode] || "")}">${mode === "auto" ? "⚡ " : ""}${esc(MODE_LABEL[mode] || mode)}</button>
        ${busy ? '<button class="cl-send is-stop" type="button" data-cl-interrupt aria-label="Stop">■</button>'
          : `<button class="cl-send" type="submit" data-cl-send aria-label="Send"${problem ? " disabled" : ""}>➤</button>`}
      </div>
      <input type="file" data-cl-file multiple hidden>
      <div class="cl-menu" data-cl-menu-box hidden></div></form>`;
  }
  function render() {
    if (!root) return;
    tools.clear(); asks.clear();
    menu = null;
    root.innerHTML = `${headHtml()}<div class="cl-body">${view === "sessions" ? sessionsHtml()
      : `<div class="cl-log" data-cl-log role="log" aria-live="polite">${chat ? "" : emptyChatHtml()}</div><div class="cl-live" data-cl-live hidden></div>`}</div>${composeHtml()}`;
    if (typeof RetroSelect !== "undefined" && RetroSelect.enhanceAll) RetroSelect.enhanceAll(root);
  }
  function paintHead() {
    const head = root?.querySelector(".cl-head");
    if (head) head.outerHTML = headHtml();
  }
  function paintCompose() {
    const form = root?.querySelector("[data-cl-compose]");
    if (!form) return;
    const text = form.querySelector("[data-cl-text]")?.value || "";
    const focused = document.activeElement === form.querySelector("[data-cl-text]");
    form.outerHTML = composeHtml();
    const box = root.querySelector("[data-cl-text]");
    if (box) { box.value = text; if (focused) box.focus(); }
  }
  function paintSessions() {
    if (view !== "sessions") return;
    const side = root?.querySelector(".cl-side");
    if (!side) return;
    const q = side.querySelector("[data-cl-search]");
    const focused = document.activeElement === q, at = q?.selectionStart;
    side.outerHTML = sessionsHtml();
    if (typeof RetroSelect !== "undefined" && RetroSelect.enhanceAll) RetroSelect.enhanceAll(root);
    if (focused) { const again = root.querySelector("[data-cl-search]"); again.focus(); again.setSelectionRange(at, at); }
  }

  const log = () => root?.querySelector("[data-cl-log]");
  const nearBottom = (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 80;

  function inputPreview(name, input) {
    const i = input && typeof input === "object" ? input : null;
    if (!i) return "";
    if (i.command) return `<pre class="cl-code">${esc(i.command)}</pre>`;
    if ("old_string" in i || "new_string" in i) {
      const lines = (text, mark) => String(text || "").split("\n").slice(0, 40).map((l) => `<span class="cl-${mark}">${mark === "del" ? "-" : "+"} ${esc(l)}</span>`).join("\n");
      return `<p class="cl-file">${esc(i.file_path || "")}</p><pre class="cl-code cl-diff">${lines(i.old_string, "del")}\n${lines(i.new_string, "add")}</pre>`;
    }
    if (typeof i.content === "string") {
      const head = i.content.split("\n").slice(0, 30).join("\n");
      return `<p class="cl-file">${esc(i.file_path || "")}</p><pre class="cl-code">${esc(head)}${i.content.split("\n").length > 30 ? "\n…" : ""}</pre>`;
    }
    return `<pre class="cl-code">${esc(JSON.stringify(i, null, 2))}</pre>`;
  }

  function eventHtml(ev) {
    switch (ev.kind) {
      case "system": return `<p class="cl-sys">${esc(ev.text)}</p>`;
      case "user":
        if (ev.brief) return `<details class="cl-msg is-you is-brief"><summary><b class="cl-who">You</b> sent the brief of <em>${esc(ev.title)}</em></summary><pre class="cl-code">${esc(ev.text)}</pre></details>`;
        return `<div class="cl-msg is-you"><b class="cl-who">You</b><div class="cl-text">${esc(ev.text)}</div></div>`;
      case "assistant": return `<div class="cl-msg is-claude"><b class="cl-who">${glyph()} Claude</b><div class="cl-md">${markdown(ev.text)}</div></div>`;
      case "tool": {
        const input = parse(ev.input);
        return `<details class="cl-tool" data-cl-tool="${esc(ev.id)}"><summary><span class="cl-dot is-run" aria-hidden="true"></span>${esc(summary(ev.name, input))}</summary>
          ${inputPreview(ev.name, input) || `<pre class="cl-code">${esc(ev.input)}</pre>`}<pre class="cl-code cl-out" hidden></pre></details>`;
      }
      case "permission": {
        const input = parse(ev.input);
        const open = (chat?.pending || []).includes(ev.request);
        return `<section class="cl-ask${open ? " is-open" : ""}" data-cl-ask="${esc(ev.request)}" aria-label="Claude asks before using a tool">
          <header><b>Claude wants to ${esc(summary(ev.tool, input).replace(/^\w/, (c) => c.toLowerCase()))}</b></header>
          ${ev.description ? `<p class="cl-why">${esc(ev.description)}</p>` : ""}
          ${inputPreview(ev.tool, input)}
          <p class="cl-ask-actions">${open ? `<button class="btn btn--primary" type="button" data-cl-allow>Allow</button>
            ${ev.always ? '<button class="btn" type="button" data-cl-always data-tip="Yes, and the same again for the rest of this chat">Allow for this chat</button>' : ""}
            <button class="btn btn--quiet" type="button" data-cl-deny>Deny</button>` : '<span class="cl-answered">No longer waiting</span>'}</p></section>`;
      }
      case "result": {
        const parts = [ev.ok ? "Done" : `Stopped (${esc(ev.subtype || "error")})`];
        if (ev.turns) parts.push(`${ev.turns} step${ev.turns === 1 ? "" : "s"}`);
        if (ev.ms) parts.push(`${Math.max(1, Math.round(ev.ms / 1000))} s`);
        if (typeof ev.cost === "number") parts.push(`$${ev.cost.toFixed(2)}`);
        return `<p class="cl-turn${ev.ok ? "" : " is-bad"}">${parts.join(" · ")}</p>${ev.text ? `<p class="cl-error">${esc(ev.text)}</p>` : ""}`;
      }
      case "error": return `<p class="cl-error">${esc(ev.text)}</p>`;
      case "stopped": return '<p class="cl-sys">The chat ended. Write again to pick it up where it left off.</p>';
      default: return "";
    }
  }
  function settle(ev) {
    if (ev.kind === "tool_result") {
      const row = tools.get(ev.id);
      if (!row) return;
      row.classList.add(ev.error ? "is-bad" : "is-ok");
      const out = row.querySelector(".cl-out");
      out.textContent = ev.text || "(no output)";
      out.hidden = false;
      return;
    }
    if (ev.kind === "answer") {
      const card = asks.get(ev.request);
      if (!card) return;
      card.classList.remove("is-open");
      card.classList.add(ev.allow ? "is-allowed" : "is-denied");
      card.querySelector(".cl-ask-actions").innerHTML = `<span class="cl-answered">${ev.cancelled ? "Withdrawn by Claude" : ev.allow ? (ev.always ? "Allowed for this chat" : "Allowed") : "Denied"}</span>`;
    }
  }
  function append(list) {
    const box = log();
    if (!box || !list.length) return;
    const stick = nearBottom(box) || !box.childElementCount;
    for (const ev of list) {
      if (ev.kind === "tool_result" || ev.kind === "answer") { settle(ev); continue; }
      const html = eventHtml(ev);
      if (!html) continue;
      box.insertAdjacentHTML("beforeend", html);
      const el = box.lastElementChild;
      if (ev.kind === "tool") tools.set(ev.id, el);
      if (ev.kind === "permission") asks.set(ev.request, el.matches("[data-cl-ask]") ? el : el.querySelector("[data-cl-ask]"));
    }
    if (stick) box.scrollTop = box.scrollHeight;
  }
  function paintLive() {
    const el = root?.querySelector("[data-cl-live]");
    if (!el) return;
    el.hidden = !live;
    if (live) {
      el.innerHTML = `<b class="cl-who">${glyph()} Claude</b><div class="cl-text">${esc(live)}<span class="cl-caret" aria-hidden="true"></span></div>`;
      const box = log();
      if (box && nearBottom(box)) box.scrollTop = box.scrollHeight;
    }
  }

  /* ---- menus: slash commands, model and effort, permission mode ---- */

  function openMenu(kind, filter = "") {
    const box = root.querySelector("[data-cl-menu-box]");
    if (!box) return;
    menu = kind; menuIndex = 0;
    let html = "";
    if (kind === "slash") {
      const needle = filter.toLowerCase();
      const list = (info?.commands || []).filter((c) => !needle || c.name.toLowerCase().includes(needle)).slice(0, 60);
      html = `<p class="cl-menu-title">Slash commands</p>${list.length ? list.map((c, i) => `<button class="cl-menu-item${i === 0 ? " is-active" : ""}" type="button" data-cl-command="${esc(c.name)}">
        <b>/${esc(c.name)}</b>${c.argumentHint ? ` <code>${esc(c.argumentHint)}</code>` : ""}<small>${esc(short(c.description, 90))}</small></button>`).join("")
        : `<p class="cl-muted">${info ? "No command matches." : "Asking Claude Code for its commands…"}</p>`}`;
    } else if (kind === "model") {
      const current_ = modelValue() || "default";
      const m = modelOf(current_);
      const levels = m?.supportsEffort ? (m.supportedEffortLevels || efforts) : [];
      // The effort first, as it changes more often than the model, then the models.
      html = `${levels.length ? `<p class="cl-menu-title">Effort</p><div class="cl-efforts">${["", ...levels].map((e) => `<button class="date-chip" type="button" data-cl-effort="${esc(e)}" aria-pressed="${effortValue() === e}">${e ? esc(cap(e)) : "Default"}</button>`).join("")}</div>` : ""}
        <p class="cl-menu-title">Model</p>${models().length ? models().map((x) => `<button class="cl-menu-item${x.value === current_ ? " is-on" : ""}" type="button" data-cl-model="${esc(x.value)}">
          <b>${esc(x.displayName || x.value)}</b><small>${esc(short(x.description, 90))}</small></button>`).join("")
        : `<p class="cl-muted">${infoLoading ? "Asking Claude Code for its models…" : "Claude Code did not list its models."}</p>`}`;
    } else if (kind === "mode") {
      const auto = !models().length || modelOf(modelValue())?.supportsAutoMode !== false;
      html = `<p class="cl-menu-title">Permissions</p>${Object.keys(modes).filter((k) => k !== "auto" || auto).map((k) => `<button class="cl-menu-item${k === modeValue() ? " is-on" : ""}" type="button" data-cl-mode="${esc(k)}">
        <b>${k === "auto" ? "⚡ " : ""}${esc(modes[k])}</b><small>${esc(MODE_HINT[k] || "")}</small></button>`).join("")}`;
    }
    box.innerHTML = html;
    box.hidden = false;
    box.dataset.kind = kind;
  }
  function closeMenu() {
    const box = root?.querySelector("[data-cl-menu-box]");
    if (box) { box.hidden = true; box.innerHTML = ""; }
    menu = null;
  }
  function moveMenu(step) {
    const items = [...root.querySelectorAll("[data-cl-menu-box] [data-cl-command]")];
    if (!items.length) return;
    menuIndex = (menuIndex + step + items.length) % items.length;
    items.forEach((el, i) => el.classList.toggle("is-active", i === menuIndex));
    items[menuIndex].scrollIntoView({ block: "nearest" });
  }
  function useCommand(name) {
    const box = root.querySelector("[data-cl-text]");
    if (!box) return;
    const rest = box.value.replace(/^\/\S*\s?/, "");
    box.value = `/${name} ${rest}`;
    closeMenu();
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  }

  /* ---- talking to the server ---- */

  async function list() {
    try {
      const r = await api("chats");
      chats = r.chats || []; folders = r.folders || []; modes = r.modes || {}; efforts = r.efforts || [];
      problem = r.problem || null; listed = true;
    } catch (err) { problem = err.message; }
    badge();
    return chats;
  }
  async function loadInfo(force = false) {
    if (infoLoading || problem) return;
    if (!force && info && Date.now() - infoAt < 55000) return;
    infoLoading = true;
    if (view === "sessions") paintSessions();
    try { info = await api(`claude-info${force ? "?refresh=1" : ""}`); infoAt = Date.now(); }
    catch (err) { info = { ...(info || {}), error: err.message }; }
    infoLoading = false;
    if (view === "sessions") paintSessions();
    paintCompose();
  }
  async function loadSessions() {
    const folder = pref("claudeFolder", "");
    if (sessionsFolder !== folder) sessions = null;
    sessionsFolder = folder;
    try { sessions = (await api(`claude-sessions?folder=${encodeURIComponent(folder)}`)).sessions || []; }
    catch (err) { sessions = []; if (view === "sessions") toast("Could not read the sessions.", err.message, true); }
    paintSessions();
  }
  async function loadContext() {
    if (!current || !chat?.alive) return;
    try { context = (await api(`chat-context?id=${encodeURIComponent(current)}`)).context; paintCompose(); } catch { /* the next turn */ }
  }
  function take(r) {
    const was = chat?.status;
    chat = r.chat;
    const i = chats.findIndex((c) => c.id === chat.id);
    if (i >= 0) chats[i] = chat; else chats.unshift(chat);
    append(r.events || []);
    for (const ev of r.events || []) after = Math.max(after, ev.seq);
    live = r.live || "";
    paintLive();
    paintHead();
    if (was !== chat.status) paintCompose();
    if ((r.events || []).some((e) => e.kind === "result")) loadContext();
    badge();
  }
  async function follow(id) {
    const gen = ++loop;
    let first = true;
    while (gen === loop && current === id) {
      let r;
      try { r = await api(`chat?id=${encodeURIComponent(id)}&after=${after}&live=${live.length}&wait=${first ? 0 : 1}`); }
      catch (err) { if (gen === loop) toast("Could not read the chat.", err.message, true); return; }
      if (gen !== loop || current !== id) return;
      if (view !== "chat") { chat = r.chat; return; }
      take(r);
      first = false;
      if (!BUSY.includes(r.chat.status) && !r.live) return;
    }
  }
  async function select(id) {
    current = id || null;
    chat = id ? chats.find((c) => c.id === id) || null : null;
    after = 0; live = ""; context = chat?.context || null;
    view = current ? "chat" : view;
    render();
    if (current) { await follow(current); loadContext(); }
    else loop++;
  }
  async function newChat() {
    current = null; chat = null; context = null; view = "chat";
    loop++;
    render();
    root.querySelector("[data-cl-text]")?.focus();
  }
  async function openSession(id) {
    try {
      const opened = await api("chat-open", { method: "POST", body: { folder: pref("claudeFolder", ""), session: id,
        model: pref("claudeModel", ""), effort: pref("claudeEffort", ""), mode: pref("claudeMode", "default") } });
      await list();
      await select(opened.id);
    } catch (err) { toast("Could not open that session.", err.message, true); }
  }
  function withFiles(text) {
    if (!files.length) return text;
    return `${text}\n\nAttached files:\n${files.map((f) => `- ${f.path}`).join("\n")}`;
  }
  async function send(text) {
    if (sending || !text.trim()) return;
    sending = true;
    const button = root.querySelector("[data-cl-send]");
    if (button) button.disabled = true;
    const message = withFiles(text);
    try {
      if (current) {
        await api("chat-send", { method: "POST", body: { id: current, text: message } });
        files = [];
        const box = root.querySelector("[data-cl-text]");
        if (box) box.value = "";
        paintCompose();
        follow(current);
      } else {
        const started = await api("chat-start", { method: "POST", body: { folder: pref("claudeFolder", ""), text: message,
          mode: pref("claudeMode", "default"), model: pref("claudeModel", ""), effort: pref("claudeEffort", "") } });
        files = [];
        await list();
        await select(started.id);
      }
    } catch (err) {
      toast("Claude did not get it.", err.message, true);
    } finally {
      sending = false;
      const again = root.querySelector("[data-cl-send]");
      if (again) again.disabled = !!problem;
    }
  }
  async function answer(card, allow, always) {
    for (const b of card.querySelectorAll("button")) b.disabled = true;
    try {
      await api("chat-answer", { method: "POST", body: { id: current, request: card.dataset.clAsk, allow, always } });
      follow(current);
    } catch (err) {
      toast("Could not answer Claude.", err.message, true);
      for (const b of card.querySelectorAll("button")) b.disabled = false;
    }
  }
  async function simple(route, body = {}) {
    try { return await api(route, { method: "POST", body: { id: current, ...body } }); }
    catch (err) { toast("Claude did not take it.", err.message, true); return null; }
  }
  async function setModel(model, effort) {
    if (model !== undefined) setPref({ claudeModel: model === "default" ? "" : model });
    if (effort !== undefined) setPref({ claudeEffort: effort });
    if (current) {
      const r = await simple("chat-model", { ...(model !== undefined ? { model } : {}), ...(effort !== undefined ? { effort } : {}) });
      if (r) { chat = r; loadContext(); }
    }
    paintCompose();
  }
  async function setMode(mode) {
    setPref({ claudeMode: mode });
    if (current) { const r = await simple("chat-mode", { mode }); if (r) chat = r; }
    paintCompose();
  }
  // Files go where a terminal's drops go; Claude may read that folder, and the message names them.
  async function attach(list_) {
    for (const file of list_) {
      try {
        const res = await fetch("/api/term-drop", { method: "POST", body: file,
          headers: { "X-Desk-Token": TOKEN, "X-File-Name": encodeURIComponent(file.name || "pasted.png"), "Content-Type": "application/octet-stream" } });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        files.push({ name: file.name || base(data.path), path: data.path });
      } catch (err) { toast(`Could not attach ${file.name || "the file"}.`, err.message, true); }
    }
    paintCompose();
  }
  async function signIn() {
    try {
      const term = await api("terms", { method: "POST", body: { kind: "claude" } });
      if (typeof Term !== "undefined") Term.attach(term.id, term.title || "Claude Code");
      Layout.activate("terminal");
      toast("Claude Code is open in the Terminal panel.", "Type /login there and sign in with your own account, then press Refresh here.", false, null, true);
    } catch (err) { toast("Could not open Claude Code.", err.message, true); }
  }

  /* A question from Claude that waits while the panel is out of sight: the tab says so, and
     a toast offers to open it. The list is read every few seconds while a chat is busy, the
     usage every minute while the panel shows. */
  function badge() {
    const waiting = chats.reduce((n, c) => n + (c.pending?.length || 0), 0);
    if (typeof Layout !== "undefined") Layout.setBadge("claude", waiting ? String(waiting) : "");
    for (const c of chats) {
      for (const rid of c.pending || []) {
        if (seenAsks.has(rid)) continue;
        seenAsks.add(rid);
        if (!(shown() && current === c.id && view === "chat")) {
          toast("Claude is waiting for your yes.", c.title, false, { label: "Open the Claude panel", open: `claude:${c.id}` });
        }
      }
    }
  }
  function watch() {
    clearInterval(watching);
    let tick = 0;
    watching = setInterval(async () => {
      if (document.hidden) return;
      tick++;
      if (chats.some((c) => BUSY.includes(c.status) || c.alive)) {
        await list();
        const mine = chats.find((c) => c.id === current);
        if (mine && view === "chat" && BUSY.includes(mine.status) && !(chat && BUSY.includes(chat.status))) follow(current);
      }
      if (shown() && tick % 15 === 0) { loadInfo(); if (view === "sessions") loadSessions(); }
    }, 4000);
  }

  /* ---- events ---- */

  function bind() {
    root.addEventListener("click", async (e) => {
      const t = e.target;
      if (menu && !t.closest("[data-cl-menu-box]") && !t.closest("[data-cl-menu]") && !t.closest("[data-cl-slash]")) closeMenu();
      const toView = t.closest("[data-cl-view]");
      if (toView) {
        view = toView.dataset.clView === "chat" && !chat ? "chat" : toView.dataset.clView;
        render();
        if (view === "sessions") { loadInfo(); loadSessions(); } else if (current) follow(current);
        return;
      }
      if (t.closest("[data-cl-new]")) return newChat();
      const session = t.closest("[data-cl-session]");
      if (session) return openSession(session.dataset.clSession);
      const own = t.closest("[data-cl-chat]");
      if (own) return select(own.dataset.clChat);
      if (t.closest("[data-cl-settings]")) { if (typeof Settings !== "undefined") Settings.open("connections", { app: "claude" }); return; }
      if (t.closest("[data-cl-login]")) return signIn();
      if (t.closest("[data-cl-refresh-info]")) { await loadInfo(true); return loadSessions(); }
      if (t.closest("[data-cl-interrupt]")) { await simple("chat-interrupt"); return follow(current); }
      if (t.closest("[data-cl-end]")) { await simple("chat-stop"); await list(); return follow(current); }
      if (t.closest("[data-cl-attach]")) return root.querySelector("[data-cl-file]")?.click();
      const un = t.closest("[data-cl-unattach]");
      if (un) { files.splice(Number(un.dataset.clUnattach), 1); return paintCompose(); }
      if (t.closest("[data-cl-slash]")) {
        if (menu === "slash") return closeMenu();
        const box = root.querySelector("[data-cl-text]");
        if (box && !box.value.startsWith("/")) { box.value = `/${box.value}`; }
        loadInfo();
        openMenu("slash", (box?.value || "").slice(1).split(/\s/)[0]);
        box?.focus();
        return;
      }
      const opener = t.closest("[data-cl-menu]");
      if (opener) { if (menu === opener.dataset.clMenu) return closeMenu(); loadInfo(); return openMenu(opener.dataset.clMenu); }
      const command = t.closest("[data-cl-command]");
      if (command) return useCommand(command.dataset.clCommand);
      const model = t.closest("[data-cl-model]");
      if (model) { closeMenu(); return setModel(model.dataset.clModel); }
      const effort = t.closest("[data-cl-effort]");
      if (effort) { closeMenu(); return setModel(undefined, effort.dataset.clEffort); }
      const mode = t.closest("[data-cl-mode]");
      if (mode) { closeMenu(); return setMode(mode.dataset.clMode); }
      const card = t.closest("[data-cl-ask]");
      if (card && t.closest("[data-cl-allow]")) return answer(card, true, false);
      if (card && t.closest("[data-cl-always]")) return answer(card, true, true);
      if (card && t.closest("[data-cl-deny]")) return answer(card, false, false);
    });
    // A section folded or unfolded is remembered (toggle does not bubble: caught on the way down).
    root.addEventListener("toggle", (e) => {
      const fold = e.target.closest?.("[data-cl-fold]");
      if (fold) setPref({ [`claudeFold_${fold.dataset.clFold}`]: fold.open });
    }, true);
    root.addEventListener("change", (e) => {
      const t = e.target;
      if (t.matches("[data-cl-folder]")) { setPref({ claudeFolder: t.value }); loadSessions(); }
      if (t.matches("[data-cl-file]")) { attach([...t.files]); t.value = ""; }
    });
    root.addEventListener("input", (e) => {
      const t = e.target;
      if (t.matches("[data-cl-search]")) { search = t.value; paintSessions(); }
      if (t.matches("[data-cl-text]")) {
        const m = t.value.match(/^\/(\S*)$/);
        if (m) openMenu("slash", m[1]); else if (menu === "slash") closeMenu();
      }
    });
    root.addEventListener("keydown", (e) => {
      if (!e.target.matches("[data-cl-text]")) return;
      if (menu === "slash") {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); return moveMenu(e.key === "ArrowDown" ? 1 : -1); }
        if (e.key === "Enter" || e.key === "Tab") {
          const item = root.querySelectorAll("[data-cl-menu-box] [data-cl-command]")[menuIndex];
          if (item) { e.preventDefault(); return useCommand(item.dataset.clCommand); }
        }
        if (e.key === "Escape") { e.preventDefault(); return closeMenu(); }
      }
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send(e.target.value);
      }
    });
    root.addEventListener("submit", (e) => {
      if (!e.target.matches("[data-cl-compose]")) return;
      e.preventDefault();
      send(root.querySelector("[data-cl-text]")?.value || "");
    });
    // Files dropped on the panel, or an image pasted in the message, are attached.
    root.addEventListener("dragover", (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); root.classList.add("is-drop"); } });
    root.addEventListener("dragleave", (e) => { if (!root.contains(e.relatedTarget)) root.classList.remove("is-drop"); });
    root.addEventListener("drop", (e) => {
      root.classList.remove("is-drop");
      if (!e.dataTransfer?.files?.length) return;
      e.preventDefault();
      attach([...e.dataTransfer.files]);
    });
    root.addEventListener("paste", (e) => {
      if (!e.target.matches?.("[data-cl-text]")) return;
      const pasted = [...(e.clipboardData?.files || [])];
      if (pasted.length) { e.preventDefault(); attach(pasted); }
    });
  }

  /* ---- what the rest of the page calls ---- */

  async function mount(el) {
    root = el;
    root.classList.add("cl-panel");
    render();
    bind();
    if (!(state.features || []).includes("claude-panel")) return;
    await list();
    render();
    watch();
    // The account, usage and sessions are read the first time the panel shows.
    new IntersectionObserver((entries) => {
      if (!entries.some((x) => x.isIntersecting)) return;
      loadInfo();
      if (view === "sessions" && sessions === null) loadSessions();
    }).observe(root);
  }
  // Open the panel, on one chat (a pull request's, just started) or on the sessions.
  async function open(id) {
    if (typeof Layout !== "undefined") { Layout.show("claude"); Layout.activate("claude"); }
    if (!listed) await list();
    if (id) { await list(); return select(id); }
    if (!current) { view = "sessions"; render(); loadInfo(); loadSessions(); }
  }
  // A toast's "Open the Claude panel" link names its chat as claude:<id>.
  document.addEventListener("click", (e) => {
    const go = e.target.closest?.("[data-open^='claude:']");
    if (!go) return;
    e.preventDefault();
    e.stopPropagation();
    open(go.dataset.open.slice("claude:".length));
  }, true);
  // Escape closes an open menu.
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu) closeMenu(); });
  return { mount, open, refresh: list, current: () => current };
})();
