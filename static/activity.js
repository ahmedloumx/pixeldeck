"use strict";

/* The Activity panel: one timeline across your team's pull requests, newest first: who
   commented, approved, asked for changes, pushed, merged, declined or opened which pull request,
   and when. Bitbucket only, read from each repository's activity log.

   The server answers from what it keeps (GET, every poll, no call to Bitbucket) and reads the
   logs that are due when asked (POST: on opening the panel, a project change, or Read now), so
   the hourly budget stays the server's to keep. The filters (whose, project, repository, kind,
   dates) are kept per browser. A click on an entry opens its pull request in the queue. */

const TeamActivity = (() => {
  const KEY = "prdesk.activity.v1";
  const FEATURE = "team-activity";
  const STEP = 200;
  const READ_EVERY = 60000;  // ask the server to read at most this often; it decides what is due
  const KINDS = [["", "Every kind"], ["commented", "Comments"], ["approved", "Approvals"], ["changes_requested", "Changes requested"],
    ["updated", "Updates and pushes"], ["merged", "Merges"], ["declined", "Declines"], ["opened", "Opened"]];
  const WORD = { commented: "commented on", approved: "approved", changes_requested: "asked for changes on", updated: "updated",
    merged: "merged", declined: "declined", opened: "opened" };
  const PRESETS = [["today", "Today"], ["7", "7 days"], ["30", "30 days"], ["range", "Dates"]];
  const BLANK = { who: "", project: "", repo: "", kind: "", preset: "7", from: "", to: "" };

  let f = (() => {
    try {
      const v = JSON.parse(localStorage.getItem(KEY));
      return v && typeof v === "object" ? { ...BLANK, ...v } : { ...BLANK };
    } catch { return { ...BLANK }; }
  })();
  let el = null, data = null, error = "", limit = STEP, asked = 0, readAt = 0, reads = 0, shown = false, retired = false;

  const has = () => Array.isArray(state.features) && state.features.includes(FEATURE);
  const known = () => Array.isArray(state.features);
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(f)); } catch { /* storage blocked */ } };
  const part = (sel) => el?.querySelector(sel);

  function range() {
    const now = new Date();
    if (f.preset === "today") return [new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime(), null];
    if (f.preset === "7" || f.preset === "30") return [Date.now() - Number(f.preset) * DAY, null];
    if (f.preset === "range") return [f.from ? Date.parse(`${f.from}T00:00:00`) : null, f.to ? Date.parse(`${f.to}T23:59:59.999`) : null];
    return [null, null];
  }
  function params() {
    const [since, until] = range();
    const p = { limit };
    for (const k of ["who", "project", "repo", "kind"]) if (f[k]) p[k] = f[k];
    if (Number.isFinite(since)) p.since = since / 1000;
    if (Number.isFinite(until)) p.until = until / 1000;
    return p;
  }

  /* An answer to an older set of filters is dropped; a poll and a read with the same filters
     both count, the later one wins. */
  async function fetchFeed({ read = false, force = false } = {}) {
    if (!el || !has()) return;
    const mine = asked;
    const p = params();
    if (read) { reads++; paintStatus(); }
    try {
      const res = read
        ? await api("team-activity", { method: "POST", body: { ...p, force } })
        : await api(`team-activity?${new URLSearchParams(Object.entries(p).map(([k, v]) => [k, String(v)]))}`);
      if (mine !== asked) return;
      data = res;
      error = res.error || "";
      if (read) readAt = Date.now();
    } catch (err) {
      if (mine !== asked || err.message === "reloading") return;
      if (err.status === 400 && f.project && /unknown project/.test(err.message)) { f.project = ""; changed(); return; }
      error = err.message;
    } finally {
      if (read) reads--;
    }
    paint();
  }
  function changed({ read = false } = {}) {
    asked++;
    limit = STEP;
    save();
    paintFilters();
    fetchFeed({ read });
  }

  /* ---------------------------------------------------------- markup */

  function frame() {
    el.innerHTML = `<div class="feed">
      <header class="panel-head-row feed-head"><h2 class="panel-title">Team activity</h2><span class="note" data-feed-scope></span>
        <button class="btn btn--quiet" type="button" data-feed-read data-tip="Read what is new on Bitbucket now">Read now</button></header>
      <p class="feed-status" data-feed-status></p>
      <div class="feed-filters" role="group" aria-label="Filter the activity">
        <label class="field"><span class="visually-hidden">Whose activity</span><select data-feed="who" data-retro></select></label>
        <label class="field"><span class="visually-hidden">Project</span><select data-feed="project" data-retro></select></label>
        <label class="field"><span class="visually-hidden">Repository</span><select data-feed="repo" data-retro></select></label>
        <label class="field"><span class="visually-hidden">Kind of activity</span><select data-feed="kind" data-retro></select></label>
      </div>
      <div class="date-bar" role="group" aria-label="Activity date filter" data-feed-dates></div>
      <div class="feed-notes" data-feed-notes></div>
      <section class="block" data-feed-list></section></div>`;
    paintFilters();
    RetroSelect.enhanceAll(el);
  }
  const option = (value, label, current) => `<option value="${esc(value)}"${value === current ? " selected" : ""}>${esc(label)}</option>`;
  // Options are written only when they change, so an open picker is not rebuilt under the pointer by a poll.
  function fill(box, html) { if (box && box._html !== html) { box.innerHTML = html; box._html = html; } }
  function paintFilters() {
    if (!part(".feed")) return;
    const people = data?.people || [];
    const who = people.some((p) => p.uuid === f.who) ? f.who : "";
    fill(part('[data-feed="who"]'), option("", "You and your team", who)
      + people.map((p) => option(p.uuid, p.is_me ? `${p.name || "You"} (you)` : p.name || "Teammate", who)).join(""));
    const bar = state.project ? state.project.name : "every project";
    fill(part('[data-feed="project"]'), option("", `As in the bar (${bar})`, f.project) + option("*", "Every project", f.project)
      + (state.projects || []).map((p) => option(p.key, p.name, f.project)).join(""));
    const repos = data?.repos || [];
    const extra = f.repo && !repos.includes(f.repo) ? [f.repo] : [];
    fill(part('[data-feed="repo"]'), option("", "Every repository", f.repo) + [...repos, ...extra].map((r) => option(r, r, f.repo)).join(""));
    fill(part('[data-feed="kind"]'), KINDS.map(([v, label]) => option(v, label, f.kind)).join(""));
    const chips = PRESETS.map(([id, label]) => `<button class="date-chip" type="button" data-feed-preset="${id}" aria-pressed="${f.preset === id}">${label}</button>`).join("");
    const dates = f.preset === "range"
      ? `<label class="date-input"><span class="visually-hidden">From</span><input type="date" data-feed-from value="${esc(f.from)}"></label><span class="date-to">to</span>
         <label class="date-input"><span class="visually-hidden">To</span><input type="date" data-feed-to value="${esc(f.to)}"></label>` : "";
    fill(part("[data-feed-dates]"), `<span class="date-basis">When</span>${chips}${dates}`);
  }
  function paintStatus() {
    const box = part("[data-feed-status]");
    if (!box) return;
    const bits = [];
    if (data) {
      bits.push(`${plural(data.repos.length, "repository", "repositories")} where you or your team have pull requests, the last ${data.window_days} days.`);
      bits.push(data.read_at ? `Read ${ago(new Date(data.read_at * 1000).toISOString())}.` : "Not read yet.");
      if (data.budget) bits.push(`${data.budget.used} of ${data.budget.limit} Bitbucket calls used this hour.`);
    }
    if (reads || data?.reading) bits.push("Reading…");
    box.textContent = bits.join(" ");
    const scope = part("[data-feed-scope]");
    if (scope) scope.textContent = data ? (data.project ? data.project.name : "every project") : "";
  }
  function paintNotes() {
    const box = part("[data-feed-notes]");
    if (!box) return;
    const note = (text, bad = false) => `<p class="feed-note${bad ? " is-bad" : ""}">${esc(text)}</p>`;
    const out = [];
    if (error) out.push(note(`Could not read the activity: ${error}`, true));
    if (data) {
      if (!data.scope_known) out.push(note("The project's repositories are not known yet: Read now fetches them."));
      if (data.unread.length) out.push(note(`Not read yet: ${data.unread.join(", ")}.`));
      if (data.partial.length) out.push(note(`More happened at once than one read takes, so older activity is missing in ${data.partial.join(", ")}.`));
      for (const e of data.errors) out.push(note(`${e.repo}: ${e.error}`, true));
      if (data.repos_cut) out.push(note(`${plural(data.repos_cut, "quieter repository", "quieter repositories")} left out, to stay within the hourly calls.`));
    }
    box.innerHTML = out.join("");
  }
  function row(e) {
    const who = isMeUuid(e.who?.uuid) ? "You" : e.who?.name || "Someone";
    const where = e.where ? ` on <code>${esc(String(e.where).split("/").pop())}</code>` : "";
    const id = Number(e.pr_id);
    return `<li class="tl-item is-${esc(e.kind)}"><span class="tl-node" aria-hidden="true"></span>
      <button class="feed-card" type="button" data-feed-open="${esc(e.repo)}" data-feed-id="${id}" data-feed-owner="${esc(e.owner || "")}">
        <span class="tl-line"><b>${esc(who)}</b> ${esc(WORD[e.kind] || e.kind)} <span class="feed-pr">${esc(e.repo)} #${id}</span>${where}<time class="when" datetime="${esc(e.at)}" title="${esc(new Date(e.at).toLocaleString("en-GB"))}">${esc(ago(e.at))}</time></span>
        <span class="feed-title">${esc(e.pr_title)}</span>
        ${e.text ? `<span class="tl-text">${esc(e.text)}</span>` : ""}
      </button></li>`;
  }
  function paintList() {
    const box = part("[data-feed-list]");
    if (!box) return;
    if (!data) { box.innerHTML = error ? "" : skeleton(4); return; }
    if (!data.entries.length) {
      const why = !data.repos.length ? `Neither you nor your team has a pull request in ${data.project ? data.project.name : "any project"} in the last ${data.window_days} days.`
        : data.unread.length === data.repos.length ? "Nothing read yet." : "Nothing matches the filters.";
      box.innerHTML = `<p class="empty">${esc(why)}</p>`;
      return;
    }
    let last = "";
    const rows = data.entries.map((e) => {
      const label = dayLabel(e.at);
      const day = label !== last ? `<li class="tl-day">${esc(label)}</li>` : "";
      last = label;
      return day + row(e);
    }).join("");
    const left = data.total - data.entries.length;
    const more = left > 0 ? `<button class="btn btn--quiet show-all" type="button" data-feed-more>Show ${Math.min(STEP, left)} more of ${data.total}</button>` : "";
    box.innerHTML = `<ol class="tl feed-tl" aria-label="Team activity, newest first">${rows}</ol>${more}`;
  }
  function paint() {
    if (!el) return;
    if (!part(".feed")) frame();
    paintFilters();
    paintStatus();
    paintNotes();
    paintList();
  }
  function unavailable() {
    el.innerHTML = `<p class="empty">The team activity reads Bitbucket's activity logs, so it is there when Bitbucket is the code host.</p>`;
  }

  /* ---------------------------------------------------------- events */

  function onClick(e) {
    const t = e.target;
    const open = t.closest("[data-feed-open]");
    if (open) {
      openFromEvent({ repo: open.dataset.feedOpen, pr_id: Number(open.dataset.feedId), owner: open.dataset.feedOwner || null });
      return;
    }
    const preset = t.closest("[data-feed-preset]");
    if (preset) {
      f.preset = preset.dataset.feedPreset;
      if (f.preset === "range" && !f.from && !f.to) { f.from = isoDay(new Date(Date.now() - 7 * DAY)); f.to = isoDay(new Date()); }
      return changed();
    }
    if (t.closest("[data-feed-read]")) return fetchFeed({ read: true, force: true });
    if (t.closest("[data-feed-more]")) { limit += STEP; return fetchFeed(); }
  }
  function onChange(e) {
    const t = e.target;
    if (t.matches("[data-feed]")) { f[t.dataset.feed] = t.value; return changed({ read: t.dataset.feed === "project" }); }
    if (t.matches("[data-feed-from]")) { f.from = t.value; return changed(); }
    if (t.matches("[data-feed-to]")) { f.to = t.value; return changed(); }
  }

  /* ---------------------------------------------------------- lifecycle */

  function mount(body) {
    el = body;
    if (!el) return;
    el.innerHTML = `<p class="empty">Loading the team activity…</p>`;
    el.addEventListener("click", onClick);
    el.addEventListener("change", onChange);
  }
  // After the bootstrap: a code host without the activity log takes the panel away (the tab,
  // its View menu entry and its key); a window opened for this panel alone says why instead.
  function ready() {
    if (!el || !known()) return;
    if (!has()) {
      if (Layout.isSolo()) unavailable();
      else if (!retired) { retired = true; Layout.retire("activity"); }
      return;
    }
    onLayout();
  }
  // Opening the panel reads what is due; while it stays open the polls answer from the cache.
  function onLayout() {
    const now = !!el && has() && Layout.isShown("activity");
    if (now && !shown) {
      paint();
      fetchFeed({ read: Date.now() - readAt > READ_EVERY });
    }
    shown = now;
  }
  function poll() {
    if (!shown || !has()) return;
    fetchFeed({ read: !!data?.unread?.length && Date.now() - readAt > READ_EVERY });
  }
  return { mount, ready, onLayout, poll };
})();
