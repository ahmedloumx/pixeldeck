"use strict";

/* PixelDeck. Plain DOM, no build step, every asset served locally.
   Panels (queue, pull request, insights, terminal) live in the Layout workspace. */

const TOKEN = document.querySelector('meta[name="desk-token"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)");

const state = {
  me: null, auth: null, team: [], budget: { used: 0, limit: 1000 },
  project: null, projects: [],
  route: { who: "me", section: "needs", pr: null },
  people: {}, teamData: null, loadedAt: {},
  pr: null,          // { pr, detail, extra, diff, tab }
  filter: "", launches: [], busy: false,
  animate: {},      // panel id -> false once its first paint has animated
  showAll: false, cursor: -1,
  prFolded: false,
};
const FOLD_KEY = "prdesk.prfold.v1";
try { state.prFolded = localStorage.getItem(FOLD_KEY) === "1"; } catch { /* private window */ }

/* ------------------------------------------------------------- icons */

const ICON = {
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  reply: '<path d="M10 8 5 12.5l5 4.5"/><path d="M5.5 12.5H14a5 5 0 0 1 5 5V19"/>',
  task: '<rect x="5" y="5" width="14" height="14" rx="3"/>',
  taskdone: '<rect x="5" y="5" width="14" height="14" rx="3"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  merge: '<circle cx="7" cy="6" r="2.2"/><circle cx="7" cy="18" r="2.2"/><circle cx="17" cy="14" r="2.2"/><path d="M7 8.2v7.6M7 8.5c0 3.5 3 5.5 7.8 5.5"/>',
};
const svg = (name, w = "1.9") =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[name]}</svg>`;

/* ------------------------------------------------------------ helpers */

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function initials(name) {
  const parts = String(name || "?").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return ((parts[0][0] || "") + (parts.length > 1 ? parts[parts.length - 1][0] : parts[0][1] || "")).toUpperCase();
}
const firstName = (name) => String(name || "").trim().split(/\s+/)[0] || name;
const DAY = 86400000;
const HOUR = 3600000;
function ago(iso) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60000) return "now";
  if (diff < HOUR) return `${Math.round(diff / 60000)}m ago`;
  if (diff < DAY) return `${Math.round(diff / HOUR)}h ago`;
  if (diff < 14 * DAY) return `${Math.round(diff / DAY)}d ago`;
  return shortDate(iso);
}
function shortDate(value) {
  const d = value instanceof Date ? value : new Date(value);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
const clock = (seconds) => new Date(seconds * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
function listNames(names) {
  const u = [...new Set(names.filter(Boolean))];
  return u.length <= 1 ? u.join("") : `${u.slice(0, -1).join(", ")} and ${u[u.length - 1]}`;
}
function duration(ms) {
  if (ms == null || Number.isNaN(ms)) return null;
  if (ms < HOUR) return { n: Math.max(1, Math.round(ms / 60000)), unit: "min" };
  if (ms < 2 * DAY) return { n: +(ms / HOUR).toFixed(1), unit: "h" };
  return { n: +(ms / DAY).toFixed(1), unit: "d" };
}

/* ---------------------------------------------------------------- api */

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api/${path}`, {
    method,
    headers: { "X-Desk-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { location.reload(); throw new Error("reloading"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.auth = data.auth;
    err.data = data;
    err.status = res.status;
    throw err;
  }
  return data;
}
async function openExternal(url) {
  try { await api("open", { method: "POST", body: { url } }); }
  catch { window.open(url, "_blank", "noopener,noreferrer"); }
}

/* ------------------------------------------------------------ seen map */

const SEEN_KEY = "prdesk.seen.v1";
const seen = (() => { try { return JSON.parse(localStorage.getItem(SEEN_KEY)); } catch { return null; } })() || { initAt: null, prs: {} };
const saveSeen = () => { try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch { /* private window */ } };
const prKey = (pr) => `${pr.repo}#${pr.id}`;
function isNew(pr) {
  if (!seen.initAt) return false;
  const mark = seen.prs[prKey(pr)];
  return mark ? (pr.updated_on || "") > mark : (pr.created_on || "") > seen.initAt;
}
function markSeen(pr) { seen.prs[prKey(pr)] = pr.updated_on || new Date().toISOString(); saveSeen(); }
function primeSeen(prs) {
  if (seen.initAt) return;
  seen.initAt = new Date().toISOString();
  for (const pr of prs) seen.prs[prKey(pr)] = pr.updated_on;
  saveSeen();
}

/* ------------------------------------------------------------ analysis */

function mondayOf(value) {
  const d = new Date(value);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return d;
}
function weeks(n = 12) {
  const now = mondayOf(new Date());
  return Array.from({ length: n }, (_, i) => { const s = new Date(now); s.setDate(s.getDate() - 7 * (n - 1 - i)); return s; });
}
function dayOf(value) {
  const d = new Date(value);
  d.setHours(0, 0, 0, 0);
  return d;
}
function lastDays(n) {
  const today = dayOf(new Date());
  return Array.from({ length: n }, (_, i) => { const s = new Date(today); s.setDate(s.getDate() - (n - 1 - i)); return s; });
}
function perWeek(items, field, starts, bucketOf = mondayOf) {
  const index = new Map(starts.map((s, i) => [s.getTime(), i]));
  const counts = starts.map(() => 0);
  for (const it of items) {
    if (!it[field]) continue;
    const i = index.get(bucketOf(it[field]).getTime());
    if (i !== undefined) counts[i]++;
  }
  return counts;
}
const median = (v) => { if (!v.length) return null; const s = [...v].sort((a, b) => a - b); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const mean = (v) => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : null);
const tookMs = (pr) => new Date(pr.updated_on).getTime() - new Date(pr.created_on).getTime();

function signals(pr) {
  const reviews = pr.reviews || [];
  const changes = reviews.filter((r) => r.state === "changes_requested");
  const approvals = reviews.filter((r) => r.state === "approved");
  const att = pr.attention || null;
  const awaiting = att ? att.awaiting : 0;
  const tasks = att ? att.tasks_open : 0;
  return {
    changes, approvals, att, awaiting, tasks, unresolved: att ? att.unresolved : 0,
    needs: changes.length > 0 || awaiting > 0 || tasks > 0,
    approved: changes.length === 0 && approvals.length > 0,
  };
}
/* The date filter: open pull requests by when they were opened, merged and declined ones by
   when that happened, pushed branches by the push. Kept per browser. */
const DATES_KEY = "prdesk.datefilter.v1";
const DATE_PRESETS = [["all", "Any time"], ["today", "Today"], ["7", "7 days"], ["30", "30 days"], ["range", "Dates"]];
let dateFilter = (() => { try { return JSON.parse(localStorage.getItem(DATES_KEY)) || { preset: "all", from: "", to: "" }; } catch { return { preset: "all", from: "", to: "" }; } })();
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function dateRange() {
  const now = new Date();
  const dayStart = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  if (dateFilter.preset === "today") return [dayStart(now), Infinity];
  if (dateFilter.preset === "7" || dateFilter.preset === "30") return [Date.now() - Number(dateFilter.preset) * DAY, Infinity];
  if (dateFilter.preset === "range") {
    const from = dateFilter.from ? Date.parse(`${dateFilter.from}T00:00:00`) : -Infinity;
    const to = dateFilter.to ? Date.parse(`${dateFilter.to}T23:59:59.999`) : Infinity;
    return [from, to];
  }
  return null;
}
function inDates(pr) {
  const range = dateRange();
  if (!range) return true;
  const when = pr.pushed_at ? pr.pushed_at * 1000 : Date.parse(!pr.state || pr.state === "OPEN" ? pr.created_on || pr.updated_on : pr.updated_on || pr.created_on);
  return Number.isFinite(when) && when >= range[0] && when <= range[1];
}
function setDates(next) {
  dateFilter = { ...dateFilter, ...next };
  if (dateFilter.preset === "range" && !dateFilter.from && !dateFilter.to) {
    dateFilter.from = isoDay(new Date(Date.now() - 30 * DAY));
    dateFilter.to = isoDay(new Date());
  }
  try { localStorage.setItem(DATES_KEY, JSON.stringify(dateFilter)); } catch { /* storage blocked */ }
  renderQueue();
}
function dateBar(secId, shown, total) {
  const basis = secId === "merged" ? "Merged" : secId === "declined" ? "Declined" : secId === "pushed" ? "Pushed" : "Opened";
  const chips = DATE_PRESETS.map(([id, label]) => `<button class="date-chip" type="button" data-date-preset="${id}" aria-pressed="${dateFilter.preset === id}">${label}</button>`).join("");
  const range = dateFilter.preset === "range"
    ? `<label class="date-input"><span class="visually-hidden">From</span><input type="date" data-date-from value="${esc(dateFilter.from)}"></label><span class="date-to">to</span>
       <label class="date-input"><span class="visually-hidden">To</span><input type="date" data-date-to value="${esc(dateFilter.to)}"></label>` : "";
  const count = dateFilter.preset !== "all" ? `<span class="date-count">${shown} of ${total}</span>` : "";
  return `<div class="date-bar" role="group" aria-label="${basis} date filter"><span class="date-basis">${basis}</span>${chips}${range}${count}</div>`;
}

function matches(pr) {
  if (!inDates(pr)) return false;
  const q = state.filter.trim().toLowerCase();
  if (!q) return true;
  const hay = [pr.repo, pr.title, pr.source, pr.dest, pr.ticket?.key, pr.author?.name, pr.id ? `#${pr.id}` : "", pr.branch, pr.subject].join(" ").toLowerCase();
  return q.split(/\s+/).every((part) => hay.includes(part));
}

const isMeUuid = (uuid) => state.me && uuid === state.me.uuid;
const whoUuid = (view = state.route) => (view.who === "me" ? state.me?.uuid : view.who);

/* A view is what one Queue or Insights panel shows: whose pull requests, which section and
   which time range. The first panel of each follows the address bar; a copy keeps its own
   view in the saved layout, so two people or two ranges can sit side by side. */
const RANGE_KEY = "prdesk.insights.range.v1";
// Merged pull requests are kept for 120 days, so no range reaches further back.
const RANGES = [["7", "7 days", "the last 7 days"], ["30", "30 days", "the last 30 days"], ["84", "12 weeks", "the last 12 weeks"], ["120", "120 days", "the last 120 days"]];
let insightsRange = (() => { try { const r = localStorage.getItem(RANGE_KEY); return RANGES.some(([id]) => id === r) ? r : "84"; } catch { return "84"; } })();
const rangeLabel = (r) => (RANGES.find(([id]) => id === r) || RANGES[2])[1];
const rangeNote = (r) => (RANGES.find(([id]) => id === r) || RANGES[2])[2];
function viewOf(id) {
  const p = Layout.params(id);
  if (!p) return { ...state.route, range: insightsRange };
  const range = RANGES.some(([r]) => r === p.range) ? p.range : insightsRange;
  return { who: p.who || "me", section: p.section || "", pr: null, range };
}
const panelOf = (el) => el?.closest?.("[data-panel]")?.dataset.panel || null;
const youReview = (pr) => (pr.reviews || []).some((r) => isMeUuid(r.uuid) && r.reviewer && (r.state === "pending" || r.state === "commented"));

let teamAgg = { src: null, data: null };
function teamAggregate() {
  if (!state.teamData) return null;
  if (teamAgg.src === state.teamData) return teamAgg.data;
  const m = state.teamData.members || [];
  const data = {
    open: m.flatMap((x) => x.open || []).sort((a, b) => (b.updated_on || "").localeCompare(a.updated_on || "")),
    merged: m.flatMap((x) => x.merged || []).sort((a, b) => (b.updated_on || "").localeCompare(a.updated_on || "")),
    declined: m.flatMap((x) => x.declined || []),
    synced_at: Math.max(0, ...m.map((x) => x.synced_at || 0)) || null,
  };
  teamAgg = { src: state.teamData, data };
  return data;
}
function currentData(view = state.route) {
  if (view.who === "team") return teamAggregate();
  return state.people[whoUuid(view)] || null;
}

function buckets(data, kind) {
  const open = data?.open || [];
  const sig = new Map(open.map((pr) => [prKey(pr), signals(pr)]));
  const s = (pr) => sig.get(prKey(pr));
  const byUrgency = (a, b) => (s(b).changes.length - s(a).changes.length) || (s(b).awaiting - s(a).awaiting) || (s(b).tasks - s(a).tasks) || (a.updated_on || "").localeCompare(b.updated_on || "");
  const oldest = (a, b) => (a.updated_on || "").localeCompare(b.updated_on || "");
  return {
    needs: open.filter((pr) => s(pr).needs).sort(byUrgency),
    changes: open.filter((pr) => s(pr).changes.length).sort(byUrgency),
    open: [...open].sort(byUrgency),
    waiting: open.filter((pr) => !s(pr).needs && !s(pr).approved).sort(oldest),
    // Approved means approved: one with threads to answer is in Needs you as well, not instead.
    approved: open.filter((pr) => s(pr).approved).sort(oldest),
    requests: kind === "me" ? data?.review_requests || [] : open.filter(youReview),
    pushed: kind === "me" ? data?.pushed || [] : [],
    merged: data?.merged || [],
    declined: [...(data?.declined || [])].sort((a, b) => (b.updated_on || "").localeCompare(a.updated_on || "")),
  };
}

// Every figure covers the same range: a month or less is drawn per day, longer per week.
function overviewStats(data, range = "84") {
  const days = Number(range) || 84;
  const daily = days <= 31;
  const starts = daily ? lastDays(days) : weeks(Math.ceil(days / 7));
  const since = daily ? starts[0].getTime() : Math.min(starts[0].getTime(), Date.now() - days * DAY);
  const merged = data.merged || [];
  const created = [...(data.open || []), ...merged, ...(data.declined || [])];
  const mergedIn = merged.filter((p) => new Date(p.updated_on).getTime() >= since);
  const took = mergedIn.map(tookMs).filter((v) => v >= 0);
  const known = mergedIn.filter((p) => Array.isArray(p.changes_requested_by));
  const fixed = known.filter((p) => p.changes_requested_by.length);
  const bucketOf = daily ? dayOf : mondayOf;
  return {
    range, days, unit: daily ? "day" : "week", starts,
    opened: perWeek(created, "created_on", starts, bucketOf),
    merged: perWeek(merged, "updated_on", starts, bucketOf),
    openedN: created.filter((p) => new Date(p.created_on).getTime() >= since).length,
    mergedN: mergedIn.length, mergedIn,
    median: median(took), mean: mean(took), took, known, fixed,
    byRepo: tally(mergedIn.map((p) => p.repo)),
    approvers: tally(mergedIn.flatMap((p) => (p.reviews || []).filter((r) => r.state === "approved").map((r) => r.name))),
    askers: tally(fixed.flatMap((p) => p.changes_requested_by)),
  };
}
function tally(values) {
  const m = new Map();
  for (const v of values) m.set(v, (m.get(v) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
}

/* ------------------------------------------------------------- markup */

function face(user, st, title) {
  const mark = st === "approved" ? `<span class="mark">${svg("check", "3.4")}</span>`
    : st === "changes_requested" ? `<span class="mark">${svg("x", "3.4")}</span>` : "";
  return `<span class="face"${st ? ` data-state="${esc(st)}"` : ""}${title ? ` title="${esc(title)}"` : ""}>${esc(initials(user?.name))}${mark}</span>`;
}
const REVIEW_WORD = { approved: "approved", changes_requested: "asked for changes", pending: "has not reviewed", commented: "commented" };
function faces(reviews) {
  const shown = reviews.slice(0, 4).map((r) => face(r, r.state, `${r.name} ${REVIEW_WORD[r.state] || r.state}`)).join("");
  const more = reviews.length > 4 ? `<span class="more-faces" title="${esc(reviews.slice(4).map((r) => r.name).join(", "))}">+${reviews.length - 4}</span>` : "";
  return `<span class="faces">${shown}${more}</span>`;
}
function stateChip(pr) {
  if (pr.state === "MERGED") return `<span class="chip chip--cool">${svg("merge")}Merged</span>`;
  if (pr.state === "DECLINED") return `<span class="chip chip--bad">${Sprites.svg("tomb", { scale: 1 })}Declined</span>`;
  const s = signals(pr);
  if (s.changes.length) return `<span class="chip chip--bad" title="${esc(listNames(s.changes.map((r) => r.name)))} asked for changes">${svg("x")}Changes requested</span>`;
  if (s.awaiting) return `<span class="chip chip--you">${svg("reply")}Your reply</span>`;
  if (s.tasks) return `<span class="chip chip--you">${svg("task")}Open tasks</span>`;
  if (s.approved) return `<span class="chip chip--ok">${svg("check")}Approved</span>`;
  if (pr.draft) return `<span class="chip">Draft</span>`;
  return `<span class="chip">In review</span>`;
}
function threadsCell(pr) {
  const s = signals(pr);
  if (!s.att) return pr.comment_count ? plural(pr.comment_count, "comment") : "none";
  if (s.awaiting) return `<span class="is-due">${s.awaiting} to answer</span>`;
  if (s.unresolved) return `${s.unresolved} open`;
  return "none";
}
function prRow(pr, opts = {}) {
  const current = state.pr && prKey(state.pr.pr) === prKey(pr) ? " is-open" : "";
  const ticket = pr.ticket ? `<span class="id">${esc(pr.ticket.key)}</span>` : "";
  const author = opts.showAuthor ? `<span>${esc(pr.author.name)}</span>` : "";
  const fresh = isNew(pr) ? `<span class="new-mark">New activity</span>` : "";
  const merged = pr.state === "MERGED";
  const tookD = merged ? duration(tookMs(pr)) : null;
  const last = merged
    ? `<td class="num"><span class="figure-cell">${tookD ? `${tookD.n} ${tookD.unit}` : ""}</span></td><td class="num"><span class="when">${esc(shortDate(pr.updated_on))}</span></td>`
    : `<td class="num"><span class="figure-cell">${threadsCell(pr)}</span></td><td class="num"><span class="when" title="${esc(new Date(pr.updated_on).toLocaleString("en-GB"))}">${esc(ago(pr.updated_on))}</span></td>`;
  return `<tr data-pr="${esc(prKey(pr))}" class="row${current}" style="--i:${Math.min(opts.i || 0, 14)}">
    <td>${stateChip(pr)}</td>
    <td><a class="pr-title" href="${esc(pr.url)}" data-open="${esc(prKey(pr))}">${esc(pr.title)}</a>
      <div class="pr-sub"><span class="id">${esc(pr.repo)} #${pr.id}</span>${ticket}${author}${fresh}</div>
      <div class="branch">${esc(pr.source)} into ${esc(pr.dest)}</div></td>
    <td>${faces(pr.reviews || [])}</td>${last}</tr>`;
}
function queueTable(prs, opts = {}) {
  const list = prs.filter(matches);
  if (!list.length) return `<p class="empty">${esc(state.filter || dateFilter.preset !== "all" ? "Nothing matches the filter." : opts.empty || "Nothing here.")}</p>`;
  const limit = opts.limit && !state.showAll ? opts.limit : list.length;
  const merged = list[0]?.state === "MERGED";
  const head = merged
    ? `<tr><th class="col-state">State</th><th>Pull request</th><th class="col-who">Approved by</th><th class="num">Took</th><th class="num">Merged</th></tr>`
    : `<tr><th class="col-state">State</th><th>Pull request</th><th class="col-who">Reviewers</th><th class="num">Threads</th><th class="num">Updated</th></tr>`;
  const rows = list.slice(0, limit).map((pr, i) => prRow(pr, { ...opts, i })).join("");
  const more = list.length > limit ? `<button class="btn btn--quiet show-all" type="button" data-action="show-all">Show all ${list.length}</button>` : "";
  return `<table class="queue"><thead>${head}</thead><tbody>${rows}</tbody></table>${more}`;
}
function pushedTable(items) {
  const list = items.filter((b) => matches({ repo: b.repo, title: b.subject, source: b.branch, dest: b.dest, pushed_at: b.pushed_at }));
  if (!list.length) return `<p class="empty">${state.filter ? "Nothing matches the filter." : "Every branch you pushed lately has a pull request."}</p>`;
  const rows = list.map((b, i) => `<tr class="row" style="--i:${Math.min(i, 14)}">
      <td><span class="chip">Pushed</span></td>
      <td><span class="pr-title">${esc(b.subject)}</span>
        <div class="pr-sub"><span class="id">${esc(b.repo)} ${esc(b.sha)}</span></div>
        <div class="branch">${esc(b.branch)} into ${esc(b.dest)}</div></td>
      <td class="num"><span class="when">${esc(ago(new Date(b.pushed_at * 1000).toISOString()))}</span></td>
      <td class="num"><span class="row-actions">
        <button class="btn" type="button" data-preview="${esc(b.repo)}" data-branch="${esc(b.branch)}">Preview pull request</button>
        <button class="btn btn--danger" type="button" data-delete="${esc(b.repo)}" data-branch="${esc(b.branch)}" data-sha="${esc(b.sha)}">Delete branch</button>
      </span></td></tr>`).join("");
  return `<table class="queue"><thead><tr><th class="col-state">State</th><th>Branch</th><th class="num">Pushed</th><th class="num"><span class="visually-hidden">Action</span></th></tr></thead><tbody>${rows}</tbody></table>`;
}

/* ------------------------------------------------------------- charts */

function weeklyChart(st, width) {
  // 8-bit bars: each bar is a stack of 6px blocks on an 8px pitch, the top block lit
  const PITCH = 8, BLOCK = 6, height = 168, top = 16, bottom = 22;
  const rows = Math.floor((height - top - bottom) / PITCH);
  const base = top + rows * PITCH;
  const n = st.starts.length, slot = width / n;
  const bw = Math.max(6, Math.floor(Math.min(18, (slot - 12) / 2)));
  const max = Math.max(1, ...st.opened, ...st.merged);
  const nice = max <= 4 ? max : Math.ceil(max / 5) * 5;
  const blocksFor = (v) => (v ? Math.max(1, Math.round((v / nice) * rows)) : 0);
  let out = "";
  for (const f of [0.5, 1]) {
    const y = base - Math.round(rows * f) * PITCH + 0.5;
    out += `<line class="grid" x1="0" x2="${width}" y1="${y}" y2="${y}"/><text class="tick" x="0" y="${y - 4}">${Math.round(nice * f)}</text>`;
  }
  out += `<line class="grid" x1="0" x2="${width}" y1="${base + 0.5}" y2="${base + 0.5}"/>`;
  const stack = (x, count, cls, i) => {
    let s = "";
    for (let b = 0; b < count; b++) {
      s += `<rect class="px ${cls}${b === count - 1 ? " cap" : ""}" x="${x}" y="${base - (b + 1) * PITCH + (PITCH - BLOCK)}" width="${bw}" height="${BLOCK}" style="--i:${i};--b:${b}"/>`;
    }
    return s;
  };
  st.starts.forEach((start, i) => {
    const cx = Math.round(i * slot + slot / 2);
    const o = st.opened[i], m = st.merged[i];
    const when = st.unit === "day" ? (i === n - 1 ? "Today" : start.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })) : i === n - 1 ? "This week" : `Week of ${shortDate(start)}`;
    out += `<g data-tip="${esc(`${when}: ${o} opened, ${m} merged`)}">`;
    out += stack(cx - bw - 1, blocksFor(o), "px-opened", i) + stack(cx + 1, blocksFor(m), "px-merged", i);
    out += `<rect class="hit" x="${i * slot}" y="0" width="${slot}" height="${base}"/></g>`;
    if (i === 0 || i === n - 1 || i === Math.floor(n / 2)) {
      out += `<text class="tick" x="${cx}" y="${height - 5}" text-anchor="middle">${i === n - 1 ? (st.unit === "day" ? "today" : "this week") : esc(shortDate(start))}</text>`;
    }
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="Pull requests opened and merged per ${st.unit}, ${esc(rangeNote(st.range))}">${out}</svg>`;
}

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
function levelOf(v, max) { return v ? Math.min(4, Math.max(1, Math.ceil((v / Math.max(1, max)) * 4))) : 0; }
function dailyHeat(data, weeksBack = 17) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const start = mondayOf(today); start.setDate(start.getDate() - 7 * (weeksBack - 1));
  const merged = new Map(), opened = new Map();
  for (const pr of data.merged || []) { const k = dayKey(new Date(pr.updated_on)); merged.set(k, (merged.get(k) || 0) + 1); }
  for (const pr of [...(data.open || []), ...(data.merged || []), ...(data.declined || [])]) {
    if (!pr.created_on) continue;
    const k = dayKey(new Date(pr.created_on)); opened.set(k, (opened.get(k) || 0) + 1);
  }
  const days = [];
  for (let d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
    const k = dayKey(d);
    days.push({ date: new Date(d), merged: merged.get(k) || 0, opened: opened.get(k) || 0 });
  }
  const max = Math.max(0, ...days.map((d) => d.merged));
  let run = 0, best = 0;
  for (const d of days) { run = d.merged ? run + 1 : 0; best = Math.max(best, run); }
  const busiest = days.reduce((a, d) => (d.merged > (a?.merged || 0) ? d : a), null);
  return { days, max, start, weeksBack, active: days.filter((d) => d.merged).length, longest: best, busiest };
}
function heatmapHtml(h) {
  let lastMonth = -1;
  const months = [];
  for (let c = 0; c < h.weeksBack; c++) {
    const first = new Date(h.start); first.setDate(first.getDate() + c * 7);
    months.push(first.getMonth() !== lastMonth ? first.toLocaleDateString("en-GB", { month: "short" }) : "");
    lastMonth = first.getMonth();
  }
  const cells = h.days.map((d, i) => `<span class="heat-cell" data-l="${levelOf(d.merged, h.max)}" data-tip="${esc(`${d.date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}: ${d.merged} merged, ${d.opened} opened`)}" style="--c:${Math.floor(i / 7)}"></span>`).join("");
  return `<div class="heat-wrap">
    <div class="heat-months" aria-hidden="true">${months.map((m) => `<span>${esc(m)}</span>`).join("")}</div>
    <div class="heat-days" aria-hidden="true">${DAY_NAMES.map((d, i) => `<span>${i % 2 === 0 ? d : ""}</span>`).join("")}</div>
    <div class="heat" role="img" aria-label="Merged pull requests per day, last ${h.weeksBack} weeks">${cells}</div></div>`;
}
const heatLegend = () => `<span class="heat-legend" aria-hidden="true">less ${[0, 1, 2, 3, 4].map((l) => `<span class="heat-cell" data-l="${l}"></span>`).join("")} more</span>`;
function punchcard(merged) {
  const grid = Array.from({ length: 7 }, () => Array(24).fill(0));
  for (const pr of merged || []) { const d = new Date(pr.updated_on); grid[(d.getDay() + 6) % 7][d.getHours()]++; }
  const max = Math.max(0, ...grid.flat());
  let peak = null;
  grid.forEach((row, r) => row.forEach((v, h) => { if (v && (!peak || v > peak.v)) peak = { v, r, h }; }));
  const cells = grid.map((row, r) => row.map((v, h) => `<span class="heat-cell" data-l="${levelOf(v, max)}" data-tip="${esc(`${DAY_NAMES[r]} ${String(h).padStart(2, "0")}:00: ${plural(v, "merge")}`)}" style="--c:${h}"></span>`).join("")).join("");
  const hours = Array.from({ length: 24 }, (_, h) => `<span>${h % 3 === 0 ? String(h).padStart(2, "0") : ""}</span>`).join("");
  return { peak, html: `<div class="punch-wrap"><div class="punch-hours" aria-hidden="true">${hours}</div>
    <div class="heat-days" aria-hidden="true">${DAY_NAMES.map((d) => `<span>${d}</span>`).join("")}</div>
    <div class="punch" role="img" aria-label="Merges by weekday and hour">${cells}</div></div>` };
}
const heatStrip = (counts, max) => `<span class="strip" aria-hidden="true">${counts.map((c, i) => `<span class="heat-cell" data-l="${levelOf(c, max)}" data-tip="${esc(plural(c, "merge"))}" style="--c:${i}"></span>`).join("")}</span>`;
function hbars(rows, opts = {}) {
  if (!rows.length) return `<p class="panel-empty">${esc(opts.empty || "No data yet.")}</p>`;
  const max = Math.max(1, ...rows.map((r) => r[1]));
  return `<ol class="hbars">${rows.map(([label, value], i) => `
    <li class="hbar" data-tip="${esc(`${label}: ${value}`)}"><span class="hbar-label">${esc(label)}</span>
      <span class="hbar-track">${value ? `<span class="hbar-fill${opts.tone ? ` is-${opts.tone}` : ""}" style="--w:${(value / max).toFixed(3)};--i:${i}"></span>` : ""}</span>
      <span class="hbar-value">${value}</span></li>`).join("")}</ol>`;
}
function paintCharts() {
  for (const box of $$(".chart-box")) {
    const view = viewOf(panelOf(box) || "insights");
    const data = currentData(view);
    const width = Math.floor(box.clientWidth);
    if (!width || !data || Number(box.dataset.drawn) === width) continue;
    box.innerHTML = weeklyChart(overviewStats(data, view.range), width);
    box.dataset.drawn = String(width);
  }
}
function countUp(root) {
  if (REDUCED.matches) return;
  for (const el of $$("[data-count]", root)) {
    const target = Number(el.dataset.count);
    if (!Number.isFinite(target) || target === 0) continue;
    const decimals = String(el.dataset.count).includes(".") ? 1 : 0;
    const t0 = performance.now();
    const step = (t) => {
      const p = Math.min(1, (t - t0) / 650);
      el.textContent = (target * (1 - Math.pow(1 - p, 3))).toFixed(decimals);
      if (p < 1) requestAnimationFrame(step);
    };
    el.textContent = (0).toFixed(decimals);
    requestAnimationFrame(step);
  }
}

/* --------------------------------------------------------------- queue */

const SECTIONS = [
  { id: "needs", label: "Needs you", kinds: ["me"], empty: "Nothing to fix or answer. Your reviews are with the team." },
  { id: "changes", label: "Changes requested", short: "Changes", kinds: ["me", "mate", "team"], empty: "No open pull request has changes requested." },
  { id: "open", label: "Open", kinds: ["me", "mate", "team"], empty: "No open pull requests." },
  { id: "waiting", label: "Waiting", kinds: ["me", "mate"], empty: "Nothing is waiting on reviewers." },
  { id: "approved", label: "Approved", kinds: ["me", "mate"], empty: "No approved pull request is waiting to merge." },
  { id: "requests", label: "Your review", kinds: ["me", "mate", "team"], empty: "No pull request is waiting for your review." },
  { id: "pushed", label: "Pushed, no PR", short: "No PR", kinds: ["me"] },
  { id: "merged", label: "Merged", kinds: ["me", "mate", "team"], empty: "No merges in the last 120 days." },
  { id: "declined", label: "Declined", kinds: ["me", "mate", "team"], empty: "Nothing declined in the last 90 days." },
];
const viewKind = (view = state.route) => (view.who === "team" ? "team" : view.who === "me" ? "me" : "mate");
const sectionsFor = (kind) => SECTIONS.filter((s) => s.kinds.includes(kind));
const VISUAL_TITLE = {
  needs: "How long each has waited", changes: "How long each has waited", open: "How long each has waited",
  waiting: "Waiting on reviewers, oldest first", approved: "Approved and waiting to merge",
  requests: "Waiting on your review the longest", pushed: "Branches waiting for a pull request",
  merged: "Merge tree, newest at the top: merged, still open, pushed without a pull request, declined",
  declined: "Declined branches, newest at the top",
};

function personName(uuid) {
  if (isMeUuid(uuid)) return state.me.name;
  return state.team.find((m) => m.uuid === uuid)?.name || "Teammate";
}

function shortWho(view) {
  const kind = viewKind(view);
  return kind === "team" ? "Team" : kind === "me" ? "You" : firstName(personName(whoUuid(view)));
}
function whoOptions(value, { edit = false } = {}) {
  const opt = (v, label) => `<option value="${esc(v)}"${v === value ? " selected" : ""}>${esc(label)}</option>`;
  const out = [];
  if (state.me) out.push(opt("me", `${state.me.name} (you)`));
  if (state.team.length) out.push(`<optgroup label="Team">${state.team.map((m) => opt(m.uuid, m.name)).join("")}</optgroup>`);
  out.push(opt("team", "Whole team"));
  if (edit) out.push(opt("edit-team", "Edit team…"));
  return out.join("");
}
// A copy picks its own person here; the first panel follows the picker in the top bar.
const whoPicker = (id, who) => `<label class="field copy-who"><span class="visually-hidden">Whose pull requests this copy shows</span>
  <select data-copy-who="${esc(id)}" data-retro>${whoOptions(who)}</select></label>`;

function renderQueue() { for (const id of Layout.instances("queue")) renderQueueIn(id); }
function renderQueueIn(id) {
  const el = Layout.body(id);
  if (!el) return;
  const copy = Layout.isCopy(id);
  const view = viewOf(id);
  const kind = viewKind(view);
  const data = currentData(view);
  const list = sectionsFor(kind);
  const sec = list.find((s) => s.id === view.section) || list[0];
  const b = buckets(data, kind);
  const who = kind === "team" ? "Your team" : kind === "me" ? "Your pull requests" : `${personName(whoUuid(view))}`;
  const chips = list.map((s) => {
    const n = data ? b[s.id].length : "";
    const tone = n && s.id === "needs" ? " is-due" : n && s.id === "changes" ? " is-bad" : "";
    const link = copy ? `href="#" data-copy-section="${s.id}"` : `href="${hashFor({ ...state.route, section: s.id, pr: null })}"`;
    return `<a class="sec-chip${s.id === sec.id ? " is-current" : ""}" role="tab" aria-selected="${s.id === sec.id}" ${link}>
      <span class="sec-label"><span class="sec-long">${esc(s.label)}</span><span class="sec-short">${esc(s.short || s.label)}</span></span><span class="sec-n${tone}">${n}</span></a>`;
  }).join("");
  let visual = "", body;
  if (!data) body = skeleton();
  else if (sec.id === "pushed") { visual = pushedChart(b.pushed); body = pushedTable(b.pushed); }
  else if (sec.id === "merged") {
    visual = mergeTree(b.merged.filter(matches), (data.open || []).filter(matches), {
      limit: 40, declined: b.declined.filter(matches), pushed: b.pushed.filter((x) => matches({ repo: x.repo, title: x.subject, source: x.branch, dest: x.dest, pushed_at: x.pushed_at })),
    });
    body = queueTable(b.merged, { empty: sec.empty, limit: 40, showAuthor: kind === "team" });
  } else if (sec.id === "declined") {
    visual = b.declined.length ? mergeTree([], [], { declined: b.declined.filter(matches).slice(0, 40) }) : "";
    body = queueTable(b.declined, { empty: sec.empty, limit: 40, showAuthor: kind === "team" });
  } else {
    visual = ageChart(b[sec.id].filter(matches), { showAuthor: kind !== "me" || sec.id === "requests" });
    body = queueTable(b[sec.id], { empty: sec.empty, showAuthor: kind !== "me" || sec.id === "requests" });
  }
  el.classList.toggle("calm", state.animate[id] === false);
  const title = copy ? `${whoPicker(id, view.who)}` : `<h2 class="panel-title">${esc(who)}</h2>`;
  // The sections sit in a rail beside the list; a narrow panel wraps them above it instead.
  el.innerHTML = `<div class="queue-split">
    <nav class="sec-chips" role="tablist" aria-label="Sections" aria-orientation="vertical">${chips}</nav>
    <div class="queue-main">${authNotice()}
      <header class="panel-head-row">${title}<span class="note">${esc(projectNote())}</span></header>
      ${data ? dateBar(sec.id, sec.id === "pushed" ? b.pushed.filter((x) => matches({ repo: x.repo, title: x.subject, source: x.branch, dest: x.dest, pushed_at: x.pushed_at })).length : b[sec.id].filter(matches).length, b[sec.id].length) : ""}
      ${visual ? `<figure class="section-visual"><figcaption>${esc(VISUAL_TITLE[sec.id] || "")}</figcaption>${visual}</figure>` : ""}
      <section class="block">${body}</section>
    </div></div>`;
  if (data) state.animate[id] = false;
  if (copy) { RetroSelect.enhanceAll(el); Layout.setLabel(id, `Queue: ${shortWho(view)}`); return; }
  Layout.setBadge("queue", data && kind === "me" ? b.needs.length || "" : "");
  const needs = state.me && state.people[state.me.uuid] ? buckets(state.people[state.me.uuid], "me").needs.length : 0;
  document.title = needs ? `(${needs}) PixelDeck` : "PixelDeck";
}
const projectNote = () => (state.project ? state.project.name : "every project");

/* ------------------------------------------------------------ insights */

function stat(label, value, opts = {}) {
  const cls = ["stat", opts.reading ? "stat--reading" : "", opts.due ? "is-due" : ""].filter(Boolean).join(" ");
  const count = typeof value === "number" ? ` data-count="${value}"` : "";
  return `<div class="${cls}" style="--i:${opts.i || 0}"><dt>${esc(label)}</dt><dd><span class="num8"${count}>${esc(value == null ? "none" : value)}</span>${opts.unit ? `<small>${esc(opts.unit)}</small>` : ""}</dd></div>`;
}
function skeleton(rows = 5) {
  return `<table class="queue" aria-busy="true"><tbody>${Array.from({ length: rows }, () =>
    '<tr class="skeleton-row"><td><span class="skeleton-bar"></span></td><td><span class="skeleton-bar"></span><span class="skeleton-bar"></span></td><td><span class="skeleton-bar"></span></td></tr>').join("")}</tbody></table>`;
}
function authNotice() {
  const auth = state.auth || {};
  if (auth.ok !== false) return "";
  const unset = /^No BITBUCKET_/.test(auth.error || "");
  return `<section class="notice" role="alert"><h2>${unset ? "PixelDeck is not set up yet" : "Bitbucket refused the token"}</h2><p>${esc(auth.error || "Unknown error")}</p>
    <p>Put an API token with Bitbucket read scopes (and pull request write, for the actions) in PixelDeck's <code>.env</code> as <code>BITBUCKET_TOKEN=</code>, next to <code>BITBUCKET_EMAIL=</code> and <code>BITBUCKET_WORKSPACE=</code>. See <code>.env.example</code>. It is picked up within a minute.</p>
    <p><button class="btn" type="button" data-action="retry">Try again now</button></p></section>`;
}

function rangeBar(id, range) {
  const chips = RANGES.map(([r, label]) => `<button class="date-chip" type="button" data-range="${r}" data-range-panel="${esc(id)}" aria-pressed="${r === range}">${label}</button>`).join("");
  return `<div class="date-bar range-bar" role="group" aria-label="Time range of these numbers" data-tip="Merged pull requests go back 120 days, declined ones 90."><span class="date-basis">Range</span>${chips}</div>`;
}
function setRange(id, range) {
  if (!RANGES.some(([r]) => r === range)) return;
  if (Layout.isCopy(id)) Layout.setParams(id, { range });
  else { insightsRange = range; try { localStorage.setItem(RANGE_KEY, range); } catch { /* storage blocked */ } }
  state.animate[id] = true;
  renderInsightsIn(id);
}

function renderInsights() { for (const id of Layout.instances("insights")) renderInsightsIn(id); }
function renderInsightsIn(id) {
  const el = Layout.body(id);
  if (!el) return;
  const view = viewOf(id);
  el.classList.toggle("calm", state.animate[id] === false);
  el.innerHTML = viewKind(view) === "team" ? insightsTeam(view, id) : insightsPerson(view, id);
  paintCharts();
  if (state.animate[id] !== false && currentData(view)) { countUp(el); state.animate[id] = false; }
  if (Layout.isCopy(id)) { RetroSelect.enhanceAll(el); Layout.setLabel(id, `Insights: ${shortWho(view)}`); }
}
const insightsTools = (view, id) => `<div class="head-tools">${Layout.isCopy(id) ? whoPicker(id, view.who) : ""}${rangeBar(id, view.range)}</div>`;

function insightsPerson(view, id) {
  const data = currentData(view);
  const kind = viewKind(view);
  const name = kind === "me" ? "Your numbers" : personName(whoUuid(view));
  if (!data) return `<header class="head"><h1>${esc(name)}</h1>${insightsTools(view, id)}</header>${skeleton()}`;
  const b = buckets(data, kind);
  const st = overviewStats(data, view.range);
  const span = rangeLabel(view.range), note = rangeNote(view.range);
  const awaiting = (data.open || []).map(signals).reduce((n, s) => n + s.awaiting, 0);
  const med = duration(st.median), avg = duration(st.mean);
  const asked = listNames(b.changes.flatMap((pr) => pr.reviews.filter((r) => r.state === "changes_requested").map((r) => r.name)));
  const line = [
    kind === "me" ? (b.needs.length ? `${plural(b.needs.length, "pull request")} ${b.needs.length === 1 ? "needs" : "need"} you.` : "Nothing needs you right now.") : `${plural((data.open || []).length, "open pull request")}.`,
    asked ? `${asked} asked for changes.` : "",
    b.requests.length ? `${plural(b.requests.length, "pull request")} ${b.requests.length === 1 ? "waits" : "wait"} for your review.` : "",
  ].filter(Boolean).join(" ");
  const figures = [
    kind === "me" ? stat("Needs you", b.needs.length, { reading: true, due: b.needs.length > 0, i: 0 }) : stat("Open", (data.open || []).length, { reading: true, i: 0 }),
    stat("Changes requested", b.changes.length, { i: 1 }),
    kind === "me" ? stat("Threads to answer", awaiting, { i: 2 }) : stat("Your review", b.requests.length, { i: 2 }),
    stat("Opened", st.openedN, { unit: span, i: 3 }),
    stat("Merged", st.mergedN, { unit: data.merged_total ? `${span}, ${data.merged_total} all time` : span, i: 4 }),
    stat("Median to merge", med ? med.n : null, { unit: med ? med.unit : "", i: 5 }),
    stat("Average to merge", avg ? avg.n : null, { unit: avg ? avg.unit : "", i: 6 }),
  ].join("");
  const bucket = [["under 4 hours", 0, 4 * HOUR], ["4 to 24 hours", 4 * HOUR, DAY], ["1 to 3 days", DAY, 3 * DAY], ["3 to 7 days", 3 * DAY, 7 * DAY], ["over a week", 7 * DAY, Infinity]]
    .map(([label, lo, hi]) => [label, st.took.filter((v) => v >= lo && v < hi).length]);
  const topRepos = st.byRepo.slice(0, 6);
  const other = st.byRepo.slice(6).reduce((n, r) => n + r[1], 0);
  if (other) topRepos.push([`${st.byRepo.length - 6} other repos`, other]);
  const heat = dailyHeat(data, Math.min(18, Math.max(5, Math.ceil(st.days / 7) + 1)));
  const punch = punchcard(st.mergedIn);
  const busy = heat.busiest ? `on ${heat.busiest.date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}` : "";
  // Review history is read for the last 30 days of merges only, so a longer range says so.
  const history = st.mergedN ? `history read for ${st.known.length} of ${st.mergedN}${st.days > 30 ? ", kept for 30 days" : ""}` : note;
  return `${authNotice()}
    <header class="head reveal" style="--i:0"><h1>${esc(name)}</h1><p class="status">${esc(line)}</p>${insightsTools(view, id)}</header>
    <dl class="stats reveal" style="--i:1">${figures}</dl>
    <div class="panels reveal" style="--i:2">
      <figure class="panel panel--lead"><div class="panel-head"><h2>Opened and merged, per ${st.unit}</h2>
        <span class="legend"><span><span class="swatch swatch--opened"></span>Opened</span><span><span class="swatch swatch--merged"></span>Merged</span></span></div>
        <div class="chart-box"></div></figure>
      <figure class="panel panel--lead"><div class="panel-head"><h2>Merge tree, by ticket</h2><span class="panel-note">the last 14 merges and what is still open</span></div>
        ${mergeTree(data.merged || [], data.open || [], { limit: 14, declined: data.declined || [], pushed: data.pushed || [] })}</figure>
      <figure class="panel panel--lead"><div class="panel-head"><h2>Merges per day</h2>${heatLegend()}</div>
        <div class="heat-row">${heatmapHtml(heat)}
          <dl class="heat-facts">
            <div><dt>Days with a merge</dt><dd><span class="num8" data-count="${heat.active}">${heat.active}</span><small>of ${heat.days.length}</small></dd></div>
            <div><dt>Longest run</dt><dd><span class="num8" data-count="${heat.longest}">${heat.longest}</span><small>${heat.longest === 1 ? "day" : "days"} in a row</small></dd></div>
            <div><dt>Busiest day</dt><dd><span class="num8">${heat.busiest ? heat.busiest.merged : 0}</span><small>${esc(busy)}</small></dd></div>
          </dl></div></figure>
      <figure class="panel panel--lead"><div class="panel-head"><h2>When the merges land</h2><span class="panel-note">${esc(punch.peak ? `most often ${DAY_NAMES[punch.peak.r]} around ${String(punch.peak.h).padStart(2, "0")}:00` : note)}</span></div>${punch.html}</figure>
      <figure class="panel"><div class="panel-head"><h2>Time to merge</h2><span class="panel-note">${esc(note)}</span></div>${hbars(st.took.length ? bucket : [], { empty: `No merges in ${note}.` })}</figure>
      <figure class="panel"><div class="panel-head"><h2>Merged by repository</h2><span class="panel-note">${esc(span)}</span></div>${hbars(topRepos, { empty: `No merges in ${note}.` })}</figure>
      <figure class="panel"><div class="panel-head"><h2>Who approved the merges</h2><span class="panel-note">${esc(span)}</span></div>${hbars(st.approvers.slice(0, 7), { empty: "No approvals recorded." })}</figure>
      <figure class="panel"><div class="panel-head"><h2>Changes requested, then merged</h2><span class="panel-note">${esc(history)}</span></div>
        <p class="panel-figure"><span class="reading-s num8" data-count="${st.fixed.length}">${st.fixed.length}</span><span>of ${st.known.length} merges went through a round of requested changes</span></p>
        ${hbars(st.askers.slice(0, 5), { tone: "bad", empty: st.known.length ? "Nobody asked for changes on these merges." : "History is still loading." })}</figure>
    </div>`;
}

function insightsTeam(view, id) {
  const td = state.teamData;
  if (!td) return `<header class="head"><h1>Your team</h1>${insightsTools(view, id)}</header>${skeleton(6)}`;
  const stats = new Map(td.members.map((m) => [m, overviewStats(m, view.range)]));
  const stripMax = Math.max(1, ...td.members.flatMap((m) => stats.get(m).merged));
  const unit = stats.size ? [...stats.values()][0].unit : "week";
  const rows = td.members.map((m, i) => {
    const synced = !!m.synced_at;
    const st = stats.get(m);
    const open = (m.open || []).length;
    const changes = (m.open || []).filter((pr) => signals(pr).changes.length).length;
    const asks = m.user.is_me ? 0 : (m.open || []).filter(youReview).length;
    const med = duration(st.median);
    const cell = (v, cls = "") => synced ? `<span class="reading num8${cls}">${v}</span>` : `<span class="reading is-zero">…</span>`;
    return `<tr class="row" data-person="${esc(m.user.is_me ? "me" : m.user.uuid)}" style="--i:${Math.min(i, 14)}">
      <td><span class="who">${face(m.user)}<span>${esc(m.user.name)}${m.user.is_me ? ' <span class="you-tag">you</span>' : ""}</span></span></td>
      <td class="num">${cell(open, open ? "" : " is-zero")}</td>
      <td class="num">${cell(changes, changes ? " is-bad" : " is-zero")}</td>
      <td class="num">${cell(asks, asks ? " is-you" : " is-zero")}</td>
      <td class="num">${cell(st.mergedN, st.mergedN ? "" : " is-zero")}</td>
      <td class="num"><span class="figure-cell">${synced && med ? `${med.n} ${med.unit}` : "none"}</span></td>
      <td>${synced ? heatStrip(st.merged, stripMax) : ""}</td></tr>`;
  }).join("");
  const total = td.members.reduce((t, m) => t + (m.open || []).length, 0);
  return `${authNotice()}
    <header class="head reveal" style="--i:0"><h1>Your team</h1>
      <p class="status">${esc(`${plural(total, "open pull request")} across ${plural(td.members.length, "person", "people")}. Pick a name to see their pull requests.`)}</p>${insightsTools(view, id)}</header>
    <section class="block reveal" style="--i:1"><table class="queue team">
      <thead><tr><th>Person</th><th class="num">Open</th><th class="num">Changes requested</th><th class="num">Your review</th><th class="num">Merged, ${esc(rangeLabel(view.range))}</th><th class="num">Median to merge</th><th>Merged per ${unit}</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p><button class="btn btn--quiet" type="button" data-action="edit-team">Choose who is on the team</button></p></section>`;
}

/* ------------------------------------------------------- pull request panel */

/* Descriptions and comments are Markdown. Every character of the source is escaped and
   only the tags built here reach the page, so a pull request can never inject its own
   HTML into a window that also drives terminals. Links are http, https or mailto only. */
const MD_FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const MD_HEAD = /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/;
const MD_HR = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const MD_LIST = /^( *)([-*+]|\d{1,9}[.)])(?:( +)(.*))?$/;
const MD_QUOTE = /^ {0,3}> ?/;
const MD_SETEXT = /^ {0,3}(={2,}|-{2,})[ \t]*$/;
const MD_TABLE_SEP = /^ *\|? *:?-+:? *(?:\| *:?-+:? *)*\|? *$/;
const MD_NUM = /^[-+]?\d[\d\s.,]*%?$/;
const mdIndent = (s) => s.match(/^ */)[0].length;
const mdFence = (line) => { const m = line.match(MD_FENCE); return m && !(m[1][0] === "`" && m[2].includes("`")) ? m : null; };
// A marker with nothing after it ("2026." alone on a line) stays text.
const mdList = (line) => { const m = MD_HR.test(line) ? null : line.match(MD_LIST); return m && m[4]?.trim() ? m : null; };
const mdTableAt = (lines, i) => lines[i].includes("|") && i + 1 < lines.length && lines[i + 1].includes("|") && MD_TABLE_SEP.test(lines[i + 1]);
function mdCells(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}
function mdLink(url, label, image) {
  // A URL holding a placeholder would get HTML spliced into its attribute later, so it stays text.
  if (!/^(https?:|mailto:)/i.test(url) || url.includes("\u0000")) return label;
  return `<a href="${esc(url)}" target="_blank" rel="noreferrer"${image ? ' class="md-img"' : ""}>${image ? `image: ${label || "untitled"}` : label}</a>`;
}
function mdInline(text) {
  const kept = [];
  const hold = (html) => `\u0000${kept.push(html) - 1}\u0000`;
  const emph = (s) => s
    .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, "$1<strong>$2</strong>")
    .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, "$1<em>$2</em>")
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");
  let s = String(text)
    .replace(/(`+)(?!`)([\s\S]*?[^`])\1(?!`)/g, (_, _t, code) => hold(`<code>${esc(code.replace(/^ ([\s\S]*) $/, "$1"))}</code>`))
    .replace(/\\([!-/:-@[-`{-~])/g, (_, c) => hold(esc(c)))
    .replace(/<((?:https?:\/\/|mailto:)[^\s<>\u0000]+)>/gi, (_, url) => hold(mdLink(url, esc(url))))
    .replace(/(!?)\[([^\]\n]*)\]\(\s*<?([^\s<>()\u0000]+)>?(?:\s+"[^"\n]*")?\s*\)/g, (_, bang, label, url) => hold(mdLink(url, emph(esc(label)), !!bang)))
    .replace(/(^|[^\w"'=/])(https?:\/\/[^\s<>"'`\u0000]+)/g, (_, pre, url) => {
      // Sentence punctuation after a bare link is not part of it; a closing bracket is when the link opened one.
      const tail = url.match(/[.,;:!?)]+$/)?.[0] || "";
      const keep = tail && url.includes("(") && tail.startsWith(")") ? tail.slice(1) : tail;
      const link = url.slice(0, url.length - keep.length);
      return `${pre}${hold(mdLink(link, esc(link)))}${keep}`;
    });
  s = emph(esc(s).replace(/@\{[^}\s]+\}/g, "@mention"))
    .replace(/(?: {2,}|\\)?\n/g, "<br>");
  for (let n = 0; n < 4 && s.includes("\u0000"); n++) s = s.replace(/\u0000(\d+)\u0000/g, (_, k) => kept[Number(k)]);
  return s;
}
function mdTable(lines, i) {
  const head = mdCells(lines[i]);
  const align = mdCells(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "is-center" : c.endsWith(":") ? "is-right" : ""));
  const rows = [];
  let j = i + 2;
  for (; j < lines.length && lines[j].trim() && lines[j].includes("|"); j++) {
    const cells = mdCells(lines[j]);
    rows.push(head.map((_, k) => cells[k] ?? ""));
  }
  // A column of plain figures reads right aligned, like a spreadsheet, unless the author chose.
  const cls = head.map((_, k) => align[k] || (rows.length && rows.every((r) => !r[k] || MD_NUM.test(r[k])) && rows.some((r) => r[k]) ? "is-num" : ""));
  const cell = (tag, text, k) => `<${tag}${cls[k] ? ` class="${cls[k]}"` : ""}>${mdInline(text)}</${tag}>`;
  const html = `<div class="md-table"><table><thead><tr>${head.map((h, k) => cell("th", h, k)).join("")}</tr></thead>`
    + `<tbody>${rows.map((r) => `<tr>${r.map((c, k) => cell("td", c, k)).join("")}</tr>`).join("")}</tbody></table></div>`;
  return [html, j];
}
function mdListBlock(lines, i) {
  const first = mdList(lines[i]);
  const ordered = /\d/.test(first[2]);
  let base = first[1].length;
  const items = [];
  let cur = null, blank = false, loose = false;
  for (; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) { blank = true; continue; }
    const m = mdList(line);
    const ind = mdIndent(line);
    // Hand-written lists drift by a space or two: any marker left of the item's text is a sibling.
    // A bullet indented under a numbered item (or the reverse) nests even when short of its text.
    const kindChange = m && /\d/.test(m[2]) !== ordered;
    if (m && (!cur || ind < cur.content) && !(kindChange && ind > base)) {
      if (kindChange) break;
      if (cur && blank) loose = true;
      const gap = Math.min(m[3]?.length || 1, 4);
      cur = { lines: [m[4] || ""], content: ind + m[2].length + gap, start: parseInt(m[2], 10) };
      base = Math.min(base, ind);
      items.push(cur);
    } else if (cur && ind > base) {
      if (blank) cur.lines.push("");
      cur.lines.push(line.slice(Math.min(ind, cur.content)));
    } else if (cur && !blank && !m && !mdFence(line) && !MD_HEAD.test(line) && !MD_QUOTE.test(line) && !MD_HR.test(line)) {
      cur.lines.push(line.trim());
    } else break;
    blank = false;
  }
  const li = items.map((it) => {
    const task = it.lines[0].match(/^\[([ xX])\][ \t]+/);
    if (task) it.lines[0] = it.lines[0].slice(task[0].length);
    const box = task ? `<input type="checkbox" disabled${task[1] === " " ? "" : " checked"} aria-label="${task[1] === " " ? "To do" : "Done"}">` : "";
    return `<li${task ? ' class="md-task"' : ""}>${box}${mdBlocks(it.lines)}</li>`;
  }).join("");
  const tag = ordered ? "ol" : "ul";
  const start = ordered && items[0].start !== 1 ? ` start="${items[0].start}"` : "";
  return [`<${tag}${start}${loose ? ' class="is-loose"' : ""}>${li}</${tag}>`, i];
}
function mdBlocks(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if (!line.trim()) { i++; continue; }
    if ((m = mdFence(line))) {
      const close = new RegExp(`^ {0,3}${m[1][0] === "`" ? "`" : "~"}{${m[1].length},}[ \\t]*$`);
      const ind = mdIndent(line), body = [];
      for (i++; i < lines.length && !close.test(lines[i]); i++) body.push(lines[i].slice(Math.min(ind, mdIndent(lines[i]))));
      i++;
      out.push(`<pre><code>${esc(body.join("\n"))}</code></pre>`);
    } else if ((m = line.match(MD_HEAD))) {
      const level = m[1].length;
      out.push(`<h${Math.min(6, level + 3)} class="md-h md-h${level}">${mdInline(m[2] || "")}</h${Math.min(6, level + 3)}>`);
      i++;
    } else if (MD_HR.test(line)) {
      out.push("<hr>");
      i++;
    } else if (MD_QUOTE.test(line)) {
      const body = [];
      for (; i < lines.length && MD_QUOTE.test(lines[i]); i++) body.push(lines[i].replace(MD_QUOTE, ""));
      out.push(`<blockquote>${mdBlocks(body)}</blockquote>`);
    } else if (mdTableAt(lines, i)) {
      [m, i] = mdTable(lines, i);
      out.push(m);
    } else if (mdList(line)) {
      [m, i] = mdListBlock(lines, i);
      out.push(m);
    } else {
      const para = [line.trim()];
      let heading = 0;
      for (i++; i < lines.length; i++) {
        const next = lines[i];
        if (!next.trim()) break;
        if ((m = next.match(MD_SETEXT))) { heading = m[1][0] === "=" ? 1 : 2; i++; break; }
        const list = mdList(next);
        if (mdFence(next) || MD_HEAD.test(next) || MD_HR.test(next) || MD_QUOTE.test(next) || mdTableAt(lines, i)
          || (list && list[4]?.trim() && (!/\d/.test(list[2]) || parseInt(list[2], 10) === 1))) break;
        para.push(next.trim());
      }
      out.push(heading ? `<h${heading + 3} class="md-h md-h${heading}">${mdInline(para.join(" "))}</h${heading + 3}>` : `<p>${mdInline(para.join("\n"))}</p>`);
    }
  }
  return out.join("");
}
function md(raw) {
  return mdBlocks(String(raw || "").replace(/\u0000/g, "").replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n"));
}
function trail(t, { compact = false } = {}) {
  const where = t.path ? `${t.path}${t.line ? `:${t.line}` : ""}` : "General comment";
  const chip = t.resolved ? '<span class="chip chip--ok">Resolved</span>'
    : t.awaiting_author ? '<span class="chip chip--you">Waiting on the author</span>'
    : t.inline ? '<span class="chip">Open</span>' : '<span class="chip">Comment</span>';
  const entries = t.messages.map((m) => `<li class="entry">
      <div class="entry-meta"><span class="person">${esc(m.author.name)}</span><time class="when" datetime="${esc(m.at)}">${esc(ago(m.at))}</time></div>
      <div class="entry-body">${md(m.raw)}</div></li>`).join("");
  return `<article class="trail${t.resolved ? " is-resolved" : ""}${compact ? " is-inline" : ""}">
    ${compact ? `<div class="trail-head">${chip}</div>` : `<div class="trail-head"><button class="trail-where link" type="button" data-goto-file="${esc(t.path || "")}">${esc(where)}</button>${chip}${t.id != null ? `<button class="btn btn--quiet tool-copy is-icon" type="button" data-copy-threads="${esc(t.id)}" data-tip="Copy this thread">${Sprites.svg("copy", { scale: 2 })}<span class="visually-hidden">Copy this thread</span></button>` : ""}</div>`}
    <ol class="entries">${entries}</ol></article>`;
}
const ACTION_LABEL = { summarize: "Read and summarize", fix: "Fix", fix_push: "Fix and push", review: "Review with the rubric" };
// A path under the workspace root reads from there; the server sends the root (older servers do not).
function shortPath(p) {
  const path = String(p || "").replace(/\\/g, "/");
  const root = String(state.root || "").replace(/\\/g, "/").replace(/\/+$/, "");
  if (root && path.startsWith(`${root}/`)) return path.slice(root.length + 1);
  return path.split("/").slice(-3).join("/");
}

function prLaunches(pr) {
  const recent = state.launches.filter((l) => l.repo === pr.repo && l.id === pr.id).slice(0, 3)
    .map((l) => `<li class="launch"><time class="when">${esc(clock(l.at))}</time><span>${esc(ACTION_LABEL[l.action] || l.action)}</span><span class="branch">${esc(shortPath(l.cwd))}</span></li>`).join("");
  return recent ? `<ol class="launches pr-launches" aria-label="Recent Claude runs">${recent}</ol>` : "";
}
// The actions live on the tab row as icons. The Claude critter is the main one and only
// moves while hovered; the label is there for screen readers and shows when a click arms it.
function prTools(pr) {
  const tool = (cls, attrs, sprite, label, tip) =>
    `<button class="btn tool ${cls}" type="button" ${attrs} data-tip="${esc(tip)}"${cls.includes("agent") ? " data-spr-hover" : ""}>${sprite}<span class="tool-label">${esc(label)}</span></button>`;
  const buddy = `${Sprites.svg("buddy", { scale: 2 })}${Sprites.svg("sparkle", { scale: 2 })}`;
  const fold = `<button class="btn tool tool--fold" type="button" data-action="pr-fold" aria-expanded="${!state.prFolded}" data-tip="${state.prFolded ? "Show the header (f)" : "Hide the header (f)"}">${Sprites.svg(state.prFolded ? "down" : "up", { scale: 2 })}<span class="tool-label">${state.prFolded ? "Show the header" : "Hide the header"}</span></button>`;
  if (pr.state !== "OPEN") return fold;
  const shift = "Shift-click opens it in its own window.";
  const mine = isMeUuid(pr.author.uuid);
  const tools = mine
    ? tool("tool--agent", 'data-launch="fix"', buddy, "Fix with Claude", `Fix with Claude: runs in the Terminal panel, briefed with the open threads. ${shift}`)
      + tool("tool--quiet tool--push", 'data-launch="fix_push"', Sprites.svg("push", { scale: 2 }), "Fix and push", "Fix and push: Claude fixes, commits and pushes with a lease. Asks you to confirm.")
      + tool("tool--quiet", 'data-launch="summarize"', Sprites.svg("read", { scale: 2 }), "Read and summarize", `Read and summarize: Claude reads the threads and edits nothing. ${shift}`)
      + `<span class="tool-sep" aria-hidden="true"></span>`
      + tool(`tool--merge${signals(pr).approved ? " is-ready" : ""}`, "data-merge", Sprites.svg("merge", { scale: 2 }), "Merge",
        signals(pr).approved
          ? `Merge into ${pr.dest}: pick the strategy, the message and the ticket's next status first.`
          : `Not approved yet. Bitbucket's merge checks decide whether it can merge into ${pr.dest}. Asks you to confirm.`)
      + tool("tool--danger", "data-decline", Sprites.svg("decline", { scale: 2 }), "Decline", "Decline this pull request. Asks you to confirm.")
    : tool("tool--agent", 'data-launch="review"', buddy, "Review with the rubric", `Review with the rubric: Claude reviews it in the Terminal panel. ${shift}`)
      + tool("tool--quiet", 'data-launch="summarize"', Sprites.svg("read", { scale: 2 }), "Summarize the threads", `Summarize the threads, edit nothing. ${shift}`)
      + reviewTools(pr);
  return `${tools}<span class="tool-sep" aria-hidden="true"></span>${fold}`;
}

// Your verdict on someone else's pull request. A pressed button is the verdict you gave;
// clicking it again takes it back. Both ask for a second click, as Bitbucket tells the author.
function reviewTools(pr) {
  if (!(state.features || []).includes("reviews")) return "";
  const mine = (pr.reviews || []).find((r) => isMeUuid(r.uuid))?.state;
  const approved = mine === "approved", asked = mine === "changes_requested";
  const btn = (cls, action, sprite, label, tip, on) =>
    `<button class="btn tool ${cls}${on ? " is-on" : ""}" type="button" data-review="${action}" aria-pressed="${on}" data-tip="${esc(tip)}">${Sprites.svg(sprite, { scale: 2 })}<span class="tool-label">${esc(label)}</span></button>`;
  return `<span class="tool-sep" aria-hidden="true"></span>`
    + btn("tool--approve", approved ? "unapprove" : "approve", "check", approved ? "Remove your approval" : "Approve",
      approved ? "You approved it. Click to take your approval back." : "Approve this pull request. Asks you to confirm.", approved)
    + btn("tool--changes", asked ? "unrequest_changes" : "request_changes", "changes", asked ? "Withdraw your change request" : "Request changes",
      asked ? "You asked for changes. Click to withdraw the request." : "Request changes on this pull request. Asks you to confirm.", asked);
}
const REVIEW_CONFIRM = { approve: "Confirm: approve", unapprove: "Confirm: remove your approval", request_changes: "Confirm: request changes", unrequest_changes: "Confirm: withdraw your request" };
const REVIEW_DONE = { approve: "Approved", unapprove: "Approval removed on", request_changes: "Changes requested on", unrequest_changes: "Change request withdrawn on" };
async function reviewPr(button) {
  const pr = state.pr?.detail?.pr || state.pr?.pr;
  const action = button.dataset.review;
  if (!pr || !REVIEW_CONFIRM[action] || !armed(button, `${REVIEW_CONFIRM[action]} #${pr.id}`)) return;
  try {
    const res = await busy(button, () => api("review", { method: "POST", body: { repo: pr.repo, id: pr.id, action } }));
    if (state.pr && prKey(state.pr.pr) === prKey(pr)) {
      state.pr.pr = { ...state.pr.pr, ...res.pr };
      if (state.pr.detail?.pr) state.pr.detail.pr = { ...state.pr.detail.pr, ...res.pr };
    }
    renderPr();
    toast(`${REVIEW_DONE[action]} #${pr.id}.`, `${pr.author?.name || "The author"} sees it on Bitbucket.`);
    state.loadedAt = {};
    loadView({ silent: true });
  } catch (err) {
    if (/no route/.test(err.message)) toast("Reviews need the newer PixelDeck server.", NEEDS_RESTART, true);
    else toast("Bitbucket did not take the review.", err.message, true);
  }
}

const VIEWED_KEY = "prdesk.viewed.v1";
function viewedStore() { try { return JSON.parse(localStorage.getItem(VIEWED_KEY)) || {}; } catch { return {}; } }
const viewedKey = (pr) => `${prKey(pr)}@${pr.source_commit || ""}`;
function viewedSet(pr) { return new Set(viewedStore()[viewedKey(pr)] || []); }
function saveViewed(pr, set) {
  const all = viewedStore();
  all[viewedKey(pr)] = [...set];
  const keys = Object.keys(all);
  if (keys.length > 200) for (const k of keys.slice(0, keys.length - 200)) delete all[k];
  try { localStorage.setItem(VIEWED_KEY, JSON.stringify(all)); } catch { /* storage blocked */ }
}

const JOURNEY_WORD = { approved: "approved", changes_requested: "asked for changes", commented: "commented", updated: "updated the branch" };
function journey(pr, extra) {
  const t0 = Date.parse(pr.created_on);
  const t1 = pr.state === "OPEN" ? Date.now() : Date.parse(pr.updated_on);
  const span = Math.max(1, t1 - t0);
  const marks = (extra?.timeline || []).filter((e) => e.at).map((e) => {
    const x = Math.min(1, Math.max(0, (Date.parse(e.at) - t0) / span));
    return `<span class="jmark is-${e.kind}" style="--x:${x.toFixed(4)}" data-tip="${esc(`${e.who} ${JOURNEY_WORD[e.kind] || e.kind}, ${ago(e.at)}`)}"></span>`;
  }).join("");
  return `<div class="journey" aria-label="Pull request timeline"><div class="journey-track">${marks}
      <span class="jmark is-start" style="--x:0" data-tip="${esc(`Opened ${ago(pr.created_on)}`)}"></span>
      <span class="jmark is-end is-${pr.state.toLowerCase()}" style="--x:1" data-tip="${esc(pr.state === "OPEN" ? "Now" : `${pr.state.toLowerCase()} ${ago(pr.updated_on)}`)}"></span></div>
    <div class="journey-ends"><span>opened ${esc(ago(pr.created_on))}</span><span>${pr.state === "OPEN" ? "now" : `${esc(pr.state.toLowerCase())} ${esc(ago(pr.updated_on))}`}</span></div>
    <p class="age-legend"><span class="age-key is-cool"></span>pushed <span class="age-key is-muted"></span>comment <span class="age-key is-ok"></span>approval <span class="age-key is-bad"></span>changes requested</p></div>`;
}

function tabOverview() {
  const { pr, detail, extra, diff } = state.pr;
  const files = diff ? diff.files.length : null;
  const added = diff ? diff.files.reduce((n, f) => n + f.added, 0) : null;
  const removed = diff ? diff.files.reduce((n, f) => n + f.removed, 0) : null;
  const facts = `<dl class="pr-facts">
    <div><dt>Commits</dt><dd class="num8">${extra ? extra.commits.length : "…"}</dd></div>
    <div><dt>Files</dt><dd class="num8">${files ?? "…"}</dd></div>
    <div><dt>Added</dt><dd class="num8 plus">${added == null ? "…" : `+${added}`}</dd></div>
    <div><dt>Removed</dt><dd class="num8 minus">${removed == null ? "…" : `-${removed}`}</dd></div>
    <div><dt>Comments</dt><dd class="num8">${pr.comment_count || 0}</dd></div></dl>`;
  const reviewers = (pr.reviews || []).map((r) => `<li>${face(r, r.state)}<span>${esc(r.name)}</span><em class="is-${r.state}">${esc(REVIEW_WORD[r.state] || r.state)}</em></li>`).join("");
  const activity = (extra?.timeline || []).slice(0, 25).map((e) => `<li class="entry">
      <div class="entry-meta"><span class="person">${esc(e.who)}</span><span>${esc(JOURNEY_WORD[e.kind] || e.kind)}${e.where ? ` on ${esc(e.where)}` : ""}</span><time class="when">${esc(ago(e.at))}</time></div>
      ${e.text ? `<div class="entry-body"><p>${esc(e.text)}</p></div>` : ""}</li>`).join("");
  return `${facts}${extra ? journey(pr, extra) : ""}
    <section class="pr-section"><h3>Reviewers</h3>${reviewers ? `<ul class="reviewer-list">${reviewers}</ul>` : '<p class="empty">No reviewers.</p>'}</section>
    <section class="pr-section"><h3>Description</h3>${detail?.description ? `<div class="entry-body">${md(detail.description)}</div>` : '<p class="empty">No description.</p>'}</section>
    <section class="pr-section"><h3>Activity</h3>${extra ? (activity ? `<ol class="entries">${activity}</ol>` : '<p class="empty">No activity yet.</p>') : '<p class="empty">Loading the activity…</p>'}</section>`;
}

function tabFiles() {
  const { pr, diff, diffError } = state.pr;
  if (diffError) return `<section class="notice" role="alert"><h2>Could not load the diff</h2><p>${esc(diffError)}</p></section>`;
  if (!diff) return `<p class="empty">Loading the diff…</p>`;
  if (!diff.files.length) return `<p class="empty">No file changes.</p>`;
  const viewed = viewedSet(pr);
  const threads = (state.pr.detail?.threads || []).filter((t) => t.inline);
  const perFile = tally(threads.map((t) => t.path));
  const countOf = new Map(perFile);
  const letter = { added: "A", removed: "D", renamed: "R", modified: "M" };
  const list = diff.files.map((f, i) => {
    const slash = f.path.lastIndexOf("/");
    return `<li><button class="file-link${viewed.has(f.path) ? " is-viewed" : ""}" type="button" data-goto="${i}">
      <span class="fstatus is-${f.status}">${letter[f.status] || "M"}</span>
      <span class="fname"><b>${esc(f.path.slice(slash + 1))}</b><small>${esc(slash > 0 ? f.path.slice(0, slash) : "")}</small></span>
      <span class="fnum plus">+${f.added}</span><span class="fnum minus">-${f.removed}</span>${countOf.get(f.path) ? `<span class="fcom" title="comments">${countOf.get(f.path)}</span>` : ""}
    </button></li>`;
  }).join("");
  const done = diff.files.filter((f) => viewed.has(f.path)).length;
  const blocks = diff.files.map((f, i) => {
    const big = f.lines > 700;
    const collapsed = viewed.has(f.path) || big || f.binary;
    return `<section class="file${collapsed ? " is-collapsed" : ""}" id="file-${i}" data-file="${i}">
      <header class="file-head">
        <button class="file-toggle" type="button" data-toggle-file="${i}" aria-expanded="${!collapsed}"><span class="caret" aria-hidden="true"></span><span class="branch">${esc(f.status === "renamed" ? `${f.old_path} to ${f.path}` : f.path)}</span></button>
        <span class="fnum plus">+${f.added}</span><span class="fnum minus">-${f.removed}</span>
        <label class="check"><input type="checkbox" data-viewed="${i}"${viewed.has(f.path) ? " checked" : ""}> Viewed</label>
      </header>
      <div class="file-body" data-body="${i}">${collapsed ? "" : '<p class="empty">…</p>'}</div></section>`;
  }).join("");
  return `<div class="files">
    <aside class="file-list"><div class="viewed-meter"><span>Viewed <b class="num8">${done}</b> of ${diff.files.length}</span>
      <span class="energy viewed-bar">${Array.from({ length: 12 }, (_, i) => `<i class="${i < Math.round((done / diff.files.length) * 12) ? "on" : ""}"></i>`).join("")}</span></div>
      <ol>${list}</ol>${diff.cut ? '<p class="empty">The diff was cut at 4 MB.</p>' : ""}</aside>
    <div class="file-blocks">${blocks}</div></div>`;
}

function fileBody(i) {
  const { diff, detail } = state.pr;
  const f = diff.files[i];
  if (f.binary) return `<p class="empty">Binary file.</p>`;
  const threads = (detail?.threads || []).filter((t) => t.inline && t.path === f.path);
  const anchored = new Map();
  for (const t of threads) {
    const key = `${t.side === "old" ? "o" : "n"}${t.line}`;
    if (!anchored.has(key)) anchored.set(key, []);
    anchored.get(key).push(t);
  }
  const used = new Set();
  let rows = "";
  for (const h of f.hunks) {
    rows += `<tr class="dh"><td colspan="3">${esc(h.header)}</td></tr>`;
    for (const [kind, o, n, text] of h.lines) {
      const cls = kind === "+" ? "da" : kind === "-" ? "dd" : "dc";
      rows += `<tr class="${cls}"><td class="ln">${o ?? ""}</td><td class="ln">${n ?? ""}</td><td class="code"><span class="sg">${kind === " " ? "" : kind}</span>${esc(text)}</td></tr>`;
      for (const key of [n != null ? `n${n}` : null, o != null && kind === "-" ? `o${o}` : null]) {
        if (key && anchored.has(key) && !used.has(key)) {
          used.add(key);
          rows += `<tr class="dthread"><td colspan="3">${anchored.get(key).map((t) => trail(t, { compact: true })).join("")}</td></tr>`;
        }
      }
    }
  }
  const stray = [...anchored.entries()].filter(([k]) => !used.has(k)).flatMap(([, ts]) => ts);
  const strayHtml = stray.length ? `<div class="stray"><p class="note">Comments on lines outside this diff</p>${stray.map((t) => trail(t)).join("")}</div>` : "";
  return `${strayHtml}<div class="diff-wrap"><table class="diff"><tbody>${rows}</tbody></table></div>${f.truncated ? '<p class="empty">This file was cut; open it on Bitbucket for the rest.</p>' : ""}`;
}

function hydrateFiles(root) {
  const scroller = root;
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const body = e.target;
      const i = Number(body.dataset.body);
      if (!body.dataset.done && !body.closest(".file").classList.contains("is-collapsed")) {
        body.innerHTML = fileBody(i);
        body.dataset.done = "1";
      }
      io.unobserve(body);
    }
  }, { root: scroller, rootMargin: "800px 0px" });
  for (const b of $$(".file-body", root)) if (!b.closest(".file").classList.contains("is-collapsed")) io.observe(b);
}

const sortThreads = (threads) => (threads || []).slice().sort((a, b) => Number(a.resolved) - Number(b.resolved) || Number(b.awaiting_author) - Number(a.awaiting_author));
function tabThreads() {
  const { detail } = state.pr;
  if (!detail) return `<p class="empty">Loading the threads…</p>`;
  if (detail.error) return `<section class="notice" role="alert"><h2>Could not load the threads</h2><p>${esc(detail.error)}</p></section>`;
  const list = sortThreads(detail.threads);
  if (!list.length) return `<p class="empty">No comments yet.</p>`;
  const open = list.filter((t) => !t.resolved).length;
  const comments = list.reduce((n, t) => n + t.messages.length, 0);
  const copy = (which, label) => `<button class="btn tool-copy" type="button" data-copy-threads="${which}">${Sprites.svg("copy", { scale: 2 })}<span class="tool-copy-label">${label}</span></button>`;
  const bar = `<div class="threads-bar"><p class="threads-sum">${list.length} ${list.length === 1 ? "thread" : "threads"}, ${open} open, ${comments} ${comments === 1 ? "comment" : "comments"}</p>
    ${copy("all", "Copy all comments")}${open && open < list.length ? copy("open", "Copy open only") : ""}</div>`;
  return bar + list.map((t) => trail(t)).join("");
}

/* Review comments as plain Markdown, ready to paste into a Claude prompt: the pull request,
   then each thread with its file and line, whether it is still open, and every message raw. */
function threadsText(pr, threads) {
  const open = threads.filter((t) => !t.resolved).length;
  const head = [
    `Review comments on pull request #${pr.id} in ${pr.repo}: ${pr.title}`,
    `Branch ${pr.source} into ${pr.dest}${pr.url ? ` (${pr.url})` : ""}`,
    `${threads.length} ${threads.length === 1 ? "thread" : "threads"}, ${open} open`,
  ].join("\n");
  const raw = (text) => String(text || "").replace(/@\{[^}\s]+\}/g, "@mention").trim();
  const body = threads.map((t, i) => {
    const where = t.path ? `${t.path}${t.line ? ` line ${t.line}` : ""}` : "General comment";
    const status = t.resolved ? "resolved" : t.awaiting_author ? "open, waiting on the author" : "open";
    const messages = t.messages.map((m, k) => `${k ? "Reply from " : ""}${m.author.name}:\n${raw(m.raw)}`).join("\n\n");
    return `## ${i + 1}. ${where} (${status})\n\n${messages}`;
  });
  return `${head}\n\n${body.join("\n\n---\n\n")}\n`;
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* no async clipboard here: fall back */ }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
  document.body.append(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  area.remove();
  return ok;
}
async function copyLink(btn) {
  const ok = await copyText(btn.dataset.copyLink);
  if (!ok) return toast("Could not copy the link.", btn.dataset.copyLink, true);
  clearTimeout(btn._reset);
  btn.textContent = "Copied!";
  btn.classList.add("is-copied");
  btn._reset = setTimeout(() => { btn.textContent = "Copy link"; btn.classList.remove("is-copied"); }, 1400);
}
async function copyThreads(btn) {
  const { pr, detail } = state.pr || {};
  if (!detail?.threads) return;
  const all = sortThreads(detail.threads);
  const which = btn.dataset.copyThreads;
  const list = which === "all" ? all : which === "open" ? all.filter((t) => !t.resolved) : all.filter((t) => String(t.id) === which);
  if (!list.length) return toast("Nothing to copy.");
  if (!(await copyText(threadsText(detail.pr || pr, list)))) return toast("Could not copy.", "The clipboard refused the text.", true);
  const comments = list.reduce((n, t) => n + t.messages.length, 0);
  const spr = btn.querySelector(".spr");
  if (spr) spr.outerHTML = Sprites.svg("check", { scale: 2 });
  btn.classList.add("is-copied");
  const label = btn.querySelector(".tool-copy-label");
  const before = label?.textContent;
  if (label) label.textContent = "Copied";
  setTimeout(() => {
    if (!btn.isConnected) return;
    btn.classList.remove("is-copied");
    const done = btn.querySelector(".spr");
    if (done) done.outerHTML = Sprites.svg("copy", { scale: 2 });
    if (label) label.textContent = before;
  }, 1600);
  toast(`Copied ${list.length} ${list.length === 1 ? "thread" : "threads"}, ${comments} ${comments === 1 ? "comment" : "comments"}.`, "Paste it into Claude in the Terminal panel.");
}
function tabCommits() {
  const { extra } = state.pr;
  if (!extra) return `<p class="empty">Loading the commits…</p>`;
  if (!extra.commits.length) return `<p class="empty">No commits.</p>`;
  return `<ol class="commit-list">${extra.commits.map((c) => `<li><span class="id">${esc(c.sha)}</span><span>${esc(c.subject)}</span><span class="person">${esc(c.author)}</span><time class="when">${esc(ago(c.at))}</time></li>`).join("")}</ol>`;
}
function tabTasks() {
  const tasks = state.pr.detail?.tasks || [];
  if (!tasks.length) return `<p class="empty">No tasks on this pull request.</p>`;
  return `<ul class="task-list">${tasks.map((t) => `<li class="task${t.state === "RESOLVED" ? " is-done" : ""}">${svg(t.state === "RESOLVED" ? "taskdone" : "task")}<div>${md(t.raw)}<div class="entry-meta"><span class="person">${esc(t.creator.name)}</span></div></div></li>`).join("")}</ul>`;
}

function renderPr() {
  const el = Layout.body("pr");
  if (!el) return;
  if (!state.pr) {
    el.innerHTML = `<div class="pr-empty"><p class="pr-empty-title">Pick a pull request</p><p>From the queue, or press <kbd>j</kbd> and <kbd>k</kbd> to move and <kbd>Enter</kbd> to open. Press <kbd>?</kbd> for every key.</p></div>`;
    Layout.setBadge("pr", "");
    return;
  }
  const { pr, detail, extra, diff, tab } = state.pr;
  const full = detail?.pr || pr;
  const threads = detail?.threads || [];
  const open = threads.filter((t) => !t.resolved).length;
  const tasks = (detail?.tasks || []).filter((t) => t.state !== "RESOLVED").length;
  const tabBtn = (id, label, count) => `<button class="tab" type="button" role="tab" data-tab="${id}" aria-selected="${tab === id}">${label}${count != null && count !== "" ? `<span class="count">${count}</span>` : ""}</button>`;
  const ticket = full.ticket?.url ? `<button class="link" type="button" data-external="${esc(full.ticket.url)}">${esc(full.ticket.key)} in Jira</button>` : "";
  const mini = `<span class="id">#${full.id}</span>${full.ticket ? `<span class="tree-ticket">${esc(full.ticket.key)}</span>` : ""}`;
  el.innerHTML = `
    <div class="pr-view${state.prFolded ? " is-folded" : ""}">
      <header class="pr-head"${state.prFolded ? " inert" : ""}>
        <div class="pr-top"><span class="id">${esc(full.repo)} #${full.id}</span>${stateChip(full)}<span>${esc(full.author.name)}</span><time class="when">${esc(ago(full.updated_on))}</time>
          <span class="spacer"></span><button class="link" type="button" data-external="${esc(full.url)}">Bitbucket</button><button class="link copy-link" type="button" data-copy-link="${esc(full.url)}" title="Copy the link to this pull request">Copy link</button>${ticket}
          <button class="link" type="button" data-action="reload-pr">Reload</button><button class="btn btn--quiet" type="button" data-action="close-pr">Close</button></div>
        <h2>${esc(full.title)}</h2>
        <div class="branch">${esc(full.source)} into ${esc(full.dest)}</div>
        ${prLaunches(full)}
      </header>
      <div class="pr-stage">
        <div class="pr-bar">
          <div class="tabs" role="tablist">
            ${tabBtn("overview", "Overview")}${tabBtn("files", "Files changed", diff ? diff.files.length : "")}
            ${tabBtn("threads", "Threads", detail ? open : "")}${tabBtn("commits", "Commits", extra ? extra.commits.length : "")}${tabBtn("tasks", "Tasks", detail ? tasks : "")}
          </div>
          <span class="pr-mini" aria-hidden="true">${mini}</span>
          <div class="pr-tools" role="toolbar" aria-label="Pull request actions">${prTools(full)}</div>
        </div>
        <div class="sync-slot" id="sync-slot">${syncBar()}</div>
        <div class="pr-body" id="pr-body">${{ overview: tabOverview, files: tabFiles, threads: tabThreads, commits: tabCommits, tasks: tabTasks }[tab]()}</div>
      </div>
    </div>`;
  measureHead(el);
  if (tab === "files" && diff) hydrateFiles($("#pr-body"));
  Layout.setBadge("pr", `#${full.id}`);
}

// The header folds away under the tab row so the diff gets the height. The flag lives in
// state (and survives a reload); renderPr draws it as is, and only a change animates.
let headObserver = null;
function measureHead(el) {
  const view = el.querySelector(".pr-view");
  const head = el.querySelector(".pr-head");
  if (!view || !head) return;
  // Placing the stage under the header must not animate; only a fold does.
  const set = () => {
    view.classList.add("no-anim");
    view.style.setProperty("--head-h", `${head.offsetHeight}px`);
    void view.querySelector(".pr-stage")?.getBoundingClientRect();
    view.classList.remove("no-anim");
  };
  set();
  headObserver?.disconnect();
  headObserver = new ResizeObserver(set);
  headObserver.observe(head);
}
function applyFold(folded) {
  if (folded === state.prFolded && $(".pr-view")?.classList.contains("is-folded") === folded) return;
  state.prFolded = folded;
  try { localStorage.setItem(FOLD_KEY, folded ? "1" : "0"); } catch { /* private window */ }
  const view = Layout.body("pr")?.querySelector(".pr-view");
  if (!view) return;
  view.classList.toggle("is-folded", folded);
  view.querySelector(".pr-head").toggleAttribute("inert", folded);
  const btn = view.querySelector('[data-action="pr-fold"]');
  if (btn) {
    btn.setAttribute("aria-expanded", String(!folded));
    btn.dataset.tip = folded ? "Show the header (f)" : "Hide the header (f)";
    btn.innerHTML = `${Sprites.svg(folded ? "down" : "up", { scale: 2 })}<span class="tool-label">${folded ? "Show the header" : "Hide the header"}</span>`;
  }
}
// Files fold the header away, Overview brings it back; the other tabs keep what you chose.
function setPrTab(tab) {
  if (!state.pr) return;
  state.pr.tab = tab;
  renderPr();
  const want = tab === "files" ? true : tab === "overview" ? false : state.prFolded;
  if (want !== state.prFolded) requestAnimationFrame(() => requestAnimationFrame(() => applyFold(want)));
}

async function openPr(pr, { force = false, tab } = {}) {
  const same = state.pr && prKey(state.pr.pr) === prKey(pr);
  state.pr = same && !force ? state.pr : { pr, detail: null, extra: null, diff: null, tab: tab || state.pr?.tab || "overview" };
  if (tab) state.pr.tab = tab;
  markSeen(pr);
  for (const row of $$("tr[data-pr]")) row.classList.toggle("is-open", row.dataset.pr === prKey(pr));
  if (state.route.pr !== prKey(pr)) { state.route.pr = prKey(pr); history.replaceState(null, "", hashFor(state.route)); }
  Layout.activate("pr");
  renderPr();
  if (same && !force && state.pr.detail) return;
  const key = prKey(pr);
  const q = `repo=${encodeURIComponent(pr.repo)}&id=${pr.id}`;
  const still = () => state.pr && prKey(state.pr.pr) === key;
  api(`pr?${q}${force ? "&force=1" : ""}`).then((d) => { if (still()) { state.pr.detail = d; renderPr(); } })
    .catch((err) => { if (still()) { state.pr.detail = { error: err.message, threads: [] }; renderPr(); } });
  api(`pr-extra?${q}`).then((x) => { if (still()) { state.pr.extra = x; if (state.pr.tab !== "files") renderPr(); } }).catch(() => {});
  api(`pr-diff?${q}`).then((d) => { if (still()) { state.pr.diff = d; renderPr(); } })
    .catch((err) => { if (still()) { state.pr.diffError = err.message; if (state.pr.tab === "files") renderPr(); } });
  if (pr.state === "OPEN" && isMeUuid(pr.author?.uuid) && (state.features || []).includes("sync")) loadSync(pr);
}
// How far the branch is behind its destination: a fetch in your clone, so it comes a moment
// after the rest. Only the bar's own slot is redrawn, so a diff being read does not jump.
function loadSync(pr) {
  const key = prKey(pr);
  api(`pr-sync?repo=${encodeURIComponent(pr.repo)}&id=${pr.id}`)
    .then((s) => { if (state.pr && prKey(state.pr.pr) === key) { state.pr.sync = s; paintSync(); } })
    .catch(() => { /* an older server, or no clone: no bar */ });
}
function syncBar() {
  const s = state.pr?.sync;
  if (!s?.available || !s.behind) return "";
  const n = `${s.behind} ${s.behind === 1 ? "commit" : "commits"}`;
  return `<div class="sync-bar" role="status">${Sprites.svg("sync", { scale: 2 })}
    <span>This branch is <b>${n}</b> behind <code>${esc(s.dest)}</code>.</span>
    <button class="btn sync-now" type="button" data-sync>Sync now</button></div>`;
}
function paintSync() {
  const slot = $("#sync-slot");
  if (slot) slot.innerHTML = syncBar();
}
const NEEDS_RESTART = "Restart PixelDeck once: close the window, run run.sh --stop in a terminal, then click the dock icon.";
async function syncPr(button) {
  const pr = state.pr?.detail?.pr || state.pr?.pr;
  const s = state.pr?.sync;
  if (!pr || !s || !armed(button, `Confirm: merge ${s.dest} into this branch and push`)) return;
  try {
    const res = await busy(button, () => api("sync", { method: "POST", body: { repo: pr.repo, id: pr.id } }));
    state.pr.sync = { ...s, behind: 0 };
    paintSync();
    toast(res.state === "synced" ? `Synced: ${res.merged} ${res.merged === 1 ? "commit" : "commits"} from ${s.dest} merged into ${s.source}.` : "Already up to date.",
      res.state === "synced" ? "Pushed to Bitbucket. You can merge now." : "");
    openPr(pr, { force: true });
  } catch (err) {
    if (err.status === 409) {
      const files = err.data?.conflicts || [];
      toast(`Sync stopped: ${files.length} ${files.length === 1 ? "file conflicts" : "files conflict"} with ${s.dest}. Nothing was pushed.`,
        `${files.slice(0, 6).join(", ")}${files.length > 6 ? ", ..." : ""}. Resolve them in your checkout, or ask Claude with the fix button.`, true);
    } else if (/no route/.test(err.message)) toast("Sync needs the newer PixelDeck server.", NEEDS_RESTART, true);
    else toast("Could not sync the branch.", err.message, true);
  }
}
function closePr() {
  state.pr = null;
  state.route.pr = null;
  history.replaceState(null, "", hashFor(state.route));
  for (const row of $$("tr.is-open")) row.classList.remove("is-open");
  renderPr();
}

function gotoFile(i) {
  const body = $("#pr-body");
  const el = $(`#file-${i}`);
  if (!body || !el) return;
  if (el.classList.contains("is-collapsed")) toggleFile(i, true);
  body.scrollTop += el.getBoundingClientRect().top - body.getBoundingClientRect().top - 4;
}
function toggleFile(i, open) {
  const el = $(`#file-${i}`);
  if (!el) return;
  const willOpen = open ?? el.classList.contains("is-collapsed");
  el.classList.toggle("is-collapsed", !willOpen);
  el.querySelector(".file-toggle").setAttribute("aria-expanded", String(willOpen));
  const body = el.querySelector(".file-body");
  if (willOpen && !body.dataset.done) { body.innerHTML = fileBody(i); body.dataset.done = "1"; }
}
function markViewed(i, on) {
  const { pr, diff } = state.pr;
  const set = viewedSet(pr);
  const path = diff.files[i].path;
  if (on) set.add(path); else set.delete(path);
  saveViewed(pr, set);
  toggleFile(i, !on);
  $(`.file-link[data-goto="${i}"]`)?.classList.toggle("is-viewed", on);
  const done = diff.files.filter((f) => set.has(f.path)).length;
  const meter = $(".viewed-meter");
  if (meter) {
    meter.querySelector("b").textContent = done;
    $$(".viewed-bar i", meter).forEach((n, k) => n.classList.toggle("on", k < Math.round((done / diff.files.length) * 12)));
  }
  if (on && i + 1 < diff.files.length) gotoFile(i + 1);
}
function currentFileIndex() {
  const body = $("#pr-body");
  if (!body) return -1;
  const top = body.getBoundingClientRect().top;
  let best = 0;
  for (const el of $$(".file", body)) { if (el.getBoundingClientRect().top - top <= 40) best = Number(el.dataset.file); }
  return best;
}

/* ------------------------------------------------------------ launching */

let armTimer = 0;
async function launch(button, event) {
  const action = button.dataset.launch;
  const pr = state.pr?.detail?.pr || state.pr?.pr;
  if (!pr) return;
  if (action === "fix_push" && !armed(button, "Confirm: fix and push")) return;
  clearTimeout(button._disarm);
  const where = event?.shiftKey ? "window" : "app";
  // AI work: the green slime pops up here, then goes to build the new terminal tab.
  const slime = where === "app" ? Sprites.agent(button) : null;
  button.disabled = true;
  button.classList.remove("btn--armed");
  button.classList.add("is-busy");
  setLabel(button, `<span class="spin" aria-hidden="true"></span>Opening`);
  button.querySelectorAll(".spr").forEach((sp) => Sprites.play(sp));
  try {
    const record = await api("launch", { method: "POST", body: { repo: pr.repo, id: pr.id, action, where } });
    state.launches = [record, ...state.launches].slice(0, 30);
    if (record.term) {
      Term.attach(record.term, record.title);
      Layout.activate("terminal");
      if (slime) Term.buildWith(slime, record.term); else Sprites.dismiss(slime);
    } else Sprites.dismiss(slime);
    toast(record.term ? `Claude is running in the Terminal panel: ${ACTION_LABEL[action]}.` : `Claude is open in its own window: ${ACTION_LABEL[action]}.`, shortPath(record.cwd));
  } catch (err) {
    Sprites.dismiss(slime);
    toast("Could not open Claude.", err.message, true);
  } finally {
    renderPr();
  }
}

// Help dialog: replay the moments without waiting for a real merge or delete.
function replay(action, button) {
  $("#help-dialog").close();
  const sample = state.pr?.detail?.pr || state.pr?.pr || (currentData()?.merged || [])[0] || { id: 0, title: "", ticket: null };
  if (action === "fx-merge") Sprites.celebrate(sample);
  else if (action === "fx-die") Sprites.die({ left: innerWidth / 2 - 20, top: innerHeight / 2 - 20, width: 40, height: 40 }, "branch");
  else if (action === "fx-review") reviewBanner(sample, { name: "Ada Reviewer", state: ["approved", "changes_requested", "commented"][Math.floor(Math.random() * 3)] });
  else { Layout.activate("terminal"); setTimeout(() => Term.showBuild(), 150); }
}

/* -------------------------------------------------------------- toasts */

function toast(title, detail = "", bad = false, link = null) {
  const box = document.createElement("div");
  box.className = `toast${bad ? " is-bad" : ""}`;
  box.setAttribute("role", bad ? "alert" : "status");
  const go = link ? `<button class="link" type="button" data-${link.open ? "open" : "external"}="${esc(link.open || link.url)}">${esc(link.label)}</button>` : "";
  box.innerHTML = `<div>${esc(title)}${detail ? `<small>${esc(detail)}</small>` : ""}${go}</div><button class="btn btn--quiet" type="button" data-dismiss>Dismiss</button>`;
  $("#toasts").append(box);
  const kill = () => box.remove();
  box.querySelector("[data-dismiss]").addEventListener("click", kill);
  setTimeout(kill, bad ? 12000 : link ? 15000 : 7000);
}
// Icon tools keep their sprite: only the label changes when a click arms or runs them.
function getLabel(button) { const l = button.querySelector(".tool-label"); return l ? l.innerHTML : button.innerHTML; }
function setLabel(button, html) { const l = button.querySelector(".tool-label"); if (l) l.innerHTML = html; else button.innerHTML = html; }
function armed(button, confirmText) {
  if (button.classList.contains("btn--armed")) { clearTimeout(button._disarm); return true; }
  button._restore = getLabel(button);
  button.classList.add("btn--armed");
  setLabel(button, esc(confirmText));
  button._disarm = setTimeout(() => { button.classList.remove("btn--armed"); setLabel(button, button._restore); button._restore = undefined; }, 4000);
  return false;
}
async function busy(button, work) {
  const restore = button._restore ?? getLabel(button);
  button.disabled = true;
  button.classList.remove("btn--armed");
  button.classList.add("is-busy");
  setLabel(button, `<span class="spin" aria-hidden="true"></span>Working`);
  // Every confirmed action is deterministic PixelDeck work: the blue bot comes to do it.
  const bot = Sprites.worker(button);
  let ok = false;
  try { const out = await work(); ok = true; return out; } finally {
    bot.done(ok);
    button.disabled = false;
    button.classList.remove("is-busy");
    setLabel(button, restore);
    button._restore = undefined;
  }
}
async function reloadMine() {
  state.loadedAt = {};
  if (state.me) await loadPerson(state.me.uuid);
  renderAll();
}

/* ------------------------------------------------- the potential pull request */

let preview = null;
async function openPreview(repo, branch, proposal = null) {
  const dialog = $("#pr-dialog");
  preview = null;
  $("#pr-dialog-body").innerHTML = `<header class="pr-dialog-head"><h2 id="pr-dialog-title">Potential pull request</h2><p class="id">${esc(repo)}</p></header><p class="empty">Fetching ${esc(branch)} and comparing it with its destination…</p>`;
  dialog.showModal();
  try {
    preview = await api(`branch?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`);
    if (proposal) {
      // What the agent asked for, over what the branch says; the popup still shows the branch's own facts.
      Object.assign(preview, { proposal: proposal.id, title: proposal.title, description: proposal.description,
        dest: proposal.dest, close_source_branch: proposal.close_source_branch });
    }
    paintPreview();
  } catch (err) {
    $("#pr-dialog-body").innerHTML = `<header class="pr-dialog-head"><h2 id="pr-dialog-title">Potential pull request</h2></header><section class="notice" role="alert"><h2>Could not read the branch</h2><p>${esc(err.message)}</p></section><footer class="pr-dialog-foot"><button class="btn" type="button" data-action="close-preview">Close</button></footer>`;
  }
}
function paintPreview() {
  const p = preview;
  const commits = p.commits.map((c) => `<li><span class="id">${esc(c.sha)}</span><span>${esc(c.subject)}</span><time class="when">${esc(ago(c.at))}</time></li>`).join("");
  const files = p.files.map((f) => `<tr><td class="branch">${esc(f.path)}</td><td class="num figure-cell plus">${f.binary ? "bin" : `+${f.added}`}</td><td class="num figure-cell minus">${f.binary ? "" : `-${f.removed}`}</td></tr>`).join("");
  const reviewers = p.reviewers.length ? `${plural(p.reviewers.length, "default reviewer")}: ${p.reviewers.map((r) => firstName(r.name)).join(", ")}` : "No default reviewers on this repository.";
  const behind = p.behind ? `<p class="warn-line">This branch is ${plural(p.behind, "commit")} behind ${esc(p.dest)}. Bitbucket will ask for a sync before it can merge.</p>` : "";
  $("#pr-dialog-body").innerHTML = `
    <header class="pr-dialog-head"><h2 id="pr-dialog-title">Potential pull request</h2><p><span class="id">${esc(p.repo)}</span> <span class="branch">${esc(p.branch)}</span></p></header>
    <dl class="pr-facts">
      <div><dt>Commits</dt><dd class="num8">${p.ahead}</dd></div><div><dt>Files</dt><dd class="num8">${p.file_count}</dd></div>
      <div><dt>Added</dt><dd class="num8 plus">+${p.added}</dd></div><div><dt>Removed</dt><dd class="num8 minus">-${p.removed}</dd></div>
      <div><dt>Behind ${esc(p.dest)}</dt><dd class="num8">${p.behind}</dd></div></dl>${behind}
    <div class="pr-fields">
      <label class="field-block"><span>Title</span><input id="pr-title" type="text" maxlength="255" value="${esc(p.title)}" autocomplete="off"></label>
      <label class="field-block"><span>Into</span><select id="pr-dest">${p.dests.map((d) => `<option${d === p.dest ? " selected" : ""}>${esc(d)}</option>`).join("")}</select></label>
      <label class="field-block field-wide"><span>Description</span><textarea id="pr-desc" rows="6">${esc(p.description)}</textarea></label>
      <label class="check"><input id="pr-close" type="checkbox"${p.close_source_branch ? " checked" : ""}> Close the branch after the merge</label>
      <p class="pr-reviewers">${esc(reviewers)}</p></div>
    <details class="pr-more" open><summary>${plural(p.commits.length, "commit")}</summary><ol class="pr-commits">${commits}</ol></details>
    <details class="pr-more"><summary>${plural(p.file_count, "changed file")}${p.file_count > p.files.length ? `, the ${p.files.length} largest shown` : ""}</summary><table class="queue pr-files"><tbody>${files}</tbody></table></details>
    <details class="pr-more" data-diff-lazy><summary>The diff</summary><div class="mini-diff"><p class="empty">Loading the diff...</p></div></details>
    <footer class="pr-dialog-foot">
      ${p.proposal ? `<button class="btn btn--danger" type="button" data-proposal-decline="${esc(p.proposal)}">Decline</button>`
        : `<button class="btn btn--danger" type="button" data-preview-delete>Delete branch</button>`}<span class="spacer"></span>
      <button class="btn btn--quiet" type="button" data-action="close-preview">Cancel</button>
      <button class="btn btn--primary" type="button" data-create-pr>${p.proposal ? "Open pull request" : "Create pull request"}</button></footer>`;
  const lazy = $("#pr-dialog-body [data-diff-lazy]");
  lazy?.addEventListener("toggle", () => { if (lazy.open && !lazy.dataset.loaded) { lazy.dataset.loaded = "1"; loadMiniDiff(lazy); } });
}
// The diff the pull request would show, loaded when its section is opened. Every line is
// text from the branch, so it is escaped like any other content from outside.
async function loadMiniDiff(box) {
  const out = box.querySelector(".mini-diff");
  try {
    const dest = $("#pr-dest")?.value || preview.dest;
    const d = await api(`branch-diff?repo=${encodeURIComponent(preview.repo)}&branch=${encodeURIComponent(preview.branch)}&dest=${encodeURIComponent(dest)}`);
    const row = ([sign, , , text]) => `<tr class="${sign === "+" ? "is-add" : sign === "-" ? "is-del" : ""}"><td class="mini-sign">${sign === " " ? "" : sign}</td><td class="mini-text">${esc(text)}</td></tr>`;
    out.innerHTML = (d.files.map((f) => `<details class="mini-file"><summary><span class="branch">${esc(f.path)}</span>
        <span class="num figure-cell plus">${f.binary ? "bin" : `+${f.added}`}</span><span class="num figure-cell minus">${f.binary ? "" : `-${f.removed}`}</span></summary>
        ${f.binary ? '<p class="empty">Binary file.</p>' : `<table class="mini-lines"><tbody>${f.hunks.map((h) => `<tr class="mini-hunk"><td></td><td>${esc(h.header)}</td></tr>${h.lines.map(row).join("")}`).join("")}</tbody></table>`}
      </details>`).join("") || '<p class="empty">No changes.</p>') + (d.cut ? '<p class="note">The diff is long: the first part is shown.</p>' : "");
  } catch (err) { out.innerHTML = `<p class="empty">${esc(err.message)}</p>`; }
}
async function createPr(button) {
  if (preview?.proposal) return decideProposal(button, preview.proposal, "approve", true);
  if (!preview || !armed(button, "Confirm: create it")) return;
  const body = { repo: preview.repo, branch: preview.branch, dest: $("#pr-dest").value, title: $("#pr-title").value, description: $("#pr-desc").value, close_source_branch: $("#pr-close").checked };
  try {
    const res = await busy(button, () => api("pr-create", { method: "POST", body }));
    $("#pr-dialog").close();
    await reloadMine();
    toast(`Pull request #${res.id} is open on ${body.repo}.`, `${plural(res.reviewers, "reviewer")} added`, false, { label: "Open it here", open: `${body.repo}#${res.id}` });
  } catch (err) { toast("Could not create the pull request.", err.message, true); }
}
async function deleteBranch(button, repo, branch, sha) {
  if (!armed(button, "Confirm: delete it")) return;
  const at = (button.closest("tr") ? button.closest("tr").querySelector(".pr-title") || button : button).getBoundingClientRect();
  try {
    const res = await busy(button, () => api("branch-delete", { method: "POST", body: { repo, branch, sha } }));
    Sprites.die(at, "branch");
    if ($("#pr-dialog").open) $("#pr-dialog").close();
    toast(`Deleted ${branch} on ${repo}.`, `Was ${res.sha.slice(0, 8)}. To restore: ${res.restore}`);
    await reloadMine();
  } catch (err) { toast("Could not delete the branch.", err.message, true); }
}
/* Merging opens a popup like Bitbucket's: the strategies the target branch allows (its
   default picked), the commit message Bitbucket would write, closing the branch, and the
   Jira ticket with its status, which can move on once the merge is through. */
const STRATEGY = {
  merge_commit: ["Merge commit", "Keeps every commit and adds a merge commit."],
  squash: ["Squash", "One commit with all the changes, on top of the target."],
  fast_forward: ["Fast forward", "Moves the target to the branch tip. Refused when the target moved on."],
  squash_fast_forward: ["Squash, fast forward only", "One commit, only while the branch is up to date."],
  rebase_fast_forward: ["Rebase, fast forward", "Replays the commits on the target, no merge commit."],
  rebase_merge: ["Rebase, merge", "Replays the commits, then adds a merge commit."],
};
const TRANSITION_KEY = "prdesk.merge.transition.v1";
let mergeBox = null;
function mergeTicketHtml() {
  const m = mergeBox;
  const key = m.opts?.ticket?.key || m.pr.ticket?.key;
  if (!key || !(state.features || []).includes("jira")) return "";
  const url = m.opts?.ticket?.url || m.pr.ticket?.url;
  const link = url ? `<button class="link" type="button" data-external="${esc(url)}">${esc(key)}</button>` : esc(key);
  if (m.ticketState === "loading") return `<section class="merge-ticket"><h3>Ticket</h3><p class="set-hint">Reading ${esc(key)} from Jira…</p></section>`;
  if (m.ticketState === "error") {
    return `<section class="merge-ticket"><h3>Ticket ${link}</h3><p class="set-hint">Could not read it from Jira: ${esc(m.ticketError)}</p>
      <p class="set-hint">Set <code>JIRA_TOKEN</code> in the settings file to see and move its status here. <button class="link" type="button" data-merge-settings>Connections</button></p></section>`;
  }
  const t = m.ticket;
  let remembered = "";
  try { remembered = localStorage.getItem(TRANSITION_KEY) || ""; } catch { /* storage blocked */ }
  const options = [`<option value="">Leave it in ${esc(t.status.name)}</option>`, ...t.transitions.map((x) =>
    `<option value="${esc(x.id)}"${x.name === remembered || x.to === remembered ? " selected" : ""}>${esc(x.name === x.to ? x.to : `${x.name}: ${x.to}`)}</option>`)].join("");
  return `<section class="merge-ticket"><h3>Ticket ${link}</h3>
    <p class="merge-ticket-line"><span class="jira-status" data-category="${esc(t.status.category || "")}">${esc(t.status.name)}</span><span>${esc(t.summary)}</span></p>
    <label class="field-block"><span>After the merge, move it to</span><select id="merge-transition">${options}</select></label></section>`;
}
function paintMerge() {
  const m = mergeBox;
  if (!m) return;
  const body = $("#pr-dialog-body");
  if (!m.opts) {
    body.innerHTML = `<header class="pr-dialog-head"><h2 id="pr-dialog-title">Merge #${esc(m.pr.id)}</h2></header>
      ${m.error ? `<p class="set-error">${esc(m.error)}</p>` : `<p class="set-hint">Reading the pull request from Bitbucket…</p>`}
      <footer class="pr-dialog-foot"><span class="spacer"></span><button class="btn btn--quiet" type="button" data-action="close-preview">Close</button></footer>`;
    return;
  }
  if (body.querySelector("#merge-message")) { const box = body.querySelector("#merge-ticket-slot"); if (box) box.innerHTML = mergeTicketHtml(); return; }
  const o = m.opts;
  const cards = o.strategies.map((s) => {
    const [label, hint] = STRATEGY[s] || [s.replace(/_/g, " "), ""];
    return `<label class="strategy-card"><input type="radio" name="merge-strategy" value="${esc(s)}"${s === o.default ? " checked" : ""}>
      <span class="strategy-name">${esc(label)}${s === o.default ? ' <small>default</small>' : ""}</span><span class="strategy-hint">${esc(hint)}</span></label>`;
  }).join("");
  body.innerHTML = `<header class="pr-dialog-head"><h2 id="pr-dialog-title">Merge pull request #${esc(o.id)}</h2>
      <p><span class="id">${esc(o.repo)}</span> <span class="branch">${esc(o.source)}</span> into <b>${esc(o.dest)}</b></p><p>${esc(o.title || "")}</p></header>
    <fieldset class="strategies"><legend>Merge strategy</legend>${cards}</fieldset>
    <div class="pr-fields">
      <label class="field-block field-wide"><span>Commit message</span><textarea id="merge-message" rows="5">${esc(o.message)}</textarea></label>
      <label class="check"><input id="merge-close" type="checkbox"${o.close_source_branch ? " checked" : ""}> Close ${esc(o.source)} after the merge</label></div>
    <div id="merge-ticket-slot">${mergeTicketHtml()}</div>
    <p class="set-error" id="merge-error" hidden></p>
    <footer class="pr-dialog-foot"><span class="spacer"></span>
      <button class="btn btn--quiet" type="button" data-action="close-preview">Cancel</button>
      <button class="btn btn--primary" type="button" data-merge-go>${svg("merge")} Merge</button></footer>`;
}
async function openMerge(pr) {
  mergeBox = { pr, opts: null, ticket: null, ticketState: "loading" };
  paintMerge();
  if (!$("#pr-dialog").open) $("#pr-dialog").showModal();
  try { mergeBox.opts = await api(`merge-options?repo=${encodeURIComponent(pr.repo)}&id=${pr.id}`); } catch (err) { mergeBox.error = err.message; }
  const box = mergeBox;
  paintMerge();
  const key = box.opts?.ticket?.key;
  if (!key || !(state.features || []).includes("jira")) return;
  try { box.ticket = await api(`ticket?key=${encodeURIComponent(key)}`); box.ticketState = "ok"; } catch (err) { box.ticketState = "error"; box.ticketError = err.message; }
  if (mergeBox === box) paintMerge();
}
async function mergeGo(button) {
  const m = mergeBox;
  if (!m?.opts) return;
  const strategy = $('input[name="merge-strategy"]:checked')?.value;
  const transition = $("#merge-transition")?.value || "";
  const body = { repo: m.pr.repo, id: m.pr.id, strategy, message: $("#merge-message").value, close_source_branch: $("#merge-close").checked };
  const err = $("#merge-error");
  err.hidden = true;
  const merged = await doMerge(m.pr, body, button, (message) => { err.textContent = message; err.hidden = false; });
  if (!merged) return;
  $("#pr-dialog").close();
  mergeBox = null;
  if (!transition || !m.ticket) return;
  if (merged !== "MERGED") {
    // Bitbucket is still finishing it and can still refuse: the ticket stays where it is.
    toast(`${m.ticket.key} stays in ${m.ticket.status.name} for now.`, "The merge is still finishing. Move the ticket once it shows as merged.");
    return;
  }
  const step = m.ticket.transitions.find((x) => x.id === transition);
  try { localStorage.setItem(TRANSITION_KEY, step?.to || ""); } catch { /* storage blocked */ }
  try {
    const moved = await api("ticket-move", { method: "POST", body: { key: m.ticket.key, transition } });
    toast(`${moved.key} is ${moved.status.name} now.`, moved.summary);
  } catch (e) { toast(`Merged, but ${m.ticket.key} did not move.`, e.message, true); }
}

async function mergePr(button) {
  const pr = state.pr?.detail?.pr || state.pr?.pr;
  if (!pr) return;
  if ((state.features || []).includes("merge-options")) return openMerge(pr);
  if (!armed(button, `Confirm: merge #${pr.id} into ${pr.dest}`)) return;
  await doMerge(pr, { repo: pr.repo, id: pr.id }, button);
}
async function doMerge(pr, body, button, inline = null) {
  try {
    const res = await busy(button, () => api("merge", { method: "POST", body }));
    const how = res.strategy ? `Strategy: ${res.strategy.replace(/_/g, " ")}.` : "";
    if (res.state === "MERGED") {
      forgetOpen(pr); // the merge moment plays now, not a second time on the next refresh
      showMerged(pr, res.pr);
      Sprites.celebrate(pr);
      toast(`Merged #${pr.id} into ${pr.dest}.`, how);
    } else {
      toast(`Merging #${pr.id}: Bitbucket is finishing it.`, `${how} It moves to Merged on the next refresh.`);
    }
    await reloadMine();
    if (state.pr && prKey(state.pr.pr) === prKey(pr)) openPr(findPr(prKey(pr)) || state.pr.pr, { force: true });
    return res.state || "PENDING";
  } catch (err) {
    const behind = state.pr?.sync?.behind;
    let message = `Could not merge: ${err.message}`;
    if (/no route/.test(err.message)) message = "Merge needs the newer PixelDeck server.";
    else if (behind) {
      message = `Could not merge: the branch is ${behind} ${behind === 1 ? "commit" : "commits"} behind ${state.pr.sync.dest}. ${err.message} Sync it first with the bar above.`;
      const bar = $(".sync-bar");
      if (bar) { bar.classList.remove("is-nudge"); void bar.offsetWidth; bar.classList.add("is-nudge"); }
    }
    if (inline) inline(message); else toast(message, /no route/.test(err.message) ? NEEDS_RESTART : "", true);
    return false;
  }
}

async function declinePr(button) {
  const pr = state.pr?.detail?.pr || state.pr?.pr;
  if (!pr || !armed(button, `Confirm: decline #${pr.id}`)) return;
  const at = button.getBoundingClientRect();
  try {
    await busy(button, () => api("decline", { method: "POST", body: { repo: pr.repo, id: pr.id } }));
    Sprites.die(at, "pr");
    closePr();
    toast(`Declined #${pr.id} on ${pr.repo}.`, "Bitbucket cannot reopen a declined pull request.");
    await reloadMine();
  } catch (err) { toast("Could not decline the pull request.", err.message, true); }
}

/* -------------------------------------------------------------- team dialog */

let candidates = [];
async function openTeamDialog() {
  $("#team-list").innerHTML = `<p class="empty">Loading people…</p>`;
  $("#team-search").value = "";
  $("#team-dialog").showModal();
  try {
    const res = await api("candidates");
    candidates = res.candidates.map((c) => ({ ...c, picked: res.team.includes(c.uuid) }));
    paintCandidates();
  } catch (err) { $("#team-list").innerHTML = `<p class="empty">${esc(err.message)}</p>`; }
}
function paintCandidates() {
  const q = $("#team-search").value.trim().toLowerCase();
  const list = candidates.filter((c) => !q || (c.name || "").toLowerCase().includes(q)).sort((a, b) => Number(b.picked) - Number(a.picked) || (a.name || "").localeCompare(b.name || ""));
  $("#team-list").innerHTML = list.length ? list.map((c) => `<label class="pick"><input type="checkbox" data-uuid="${esc(c.uuid)}"${c.picked ? " checked" : ""}>${face(c)}<span>${esc(c.name)}</span></label>`).join("") : `<p class="empty">No one matches.</p>`;
  $("#team-count").textContent = `${candidates.filter((c) => c.picked).length} selected`;
}
async function saveTeam() {
  const uuids = candidates.filter((c) => c.picked).map((c) => c.uuid);
  try {
    const res = await api("team", { method: "POST", body: { uuids } });
    state.team = res.team;
    $("#team-dialog").close();
    toast("Team saved.", `${plural(uuids.length, "person", "people")}. Their pull requests load over the next minutes.`);
    await loadTeam();
    renderAll();
  } catch (err) { toast("Could not save the team.", err.message, true); }
}

/* ------------------------------------------------------------ top bar */

function renderBar() {
  const sel = $("#person");
  const value = state.route.who;
  const html = whoOptions("", { edit: true });
  if (sel.dataset.html !== html) { sel.innerHTML = html; sel.dataset.html = html; }
  sel.value = value;
  RetroSelect.sync(sel);
  const proj = $("#project");
  const key = state.project?.key || "";
  const popts = [`<option value="">Every project</option>`, ...(state.projects || []).map((p) => `<option value="${esc(p.key)}">${esc(p.name)}</option>`)];
  if (key && !(state.projects || []).some((p) => p.key === key)) popts.push(`<option value="${esc(key)}">${esc(state.project.name || key)}</option>`);
  const phtml = popts.join("");
  if (proj.dataset.html !== phtml) { proj.innerHTML = phtml; proj.dataset.html = phtml; }
  proj.value = key;
  RetroSelect.sync(proj);
  const { used, limit } = state.budget;
  StatusBar.paint();
  $("#refresh").title = `Refresh from Bitbucket (r). ${used} of ${limit} API calls used this hour.`;
  paintLayoutMenu();
}

// The Layout menu: which panels show, their copies, and the arcade switches.
function paintLayoutMenu() {
  const list = Layout.visibility();
  const rows = list.filter((p) => !p.copy).map((p) => {
    const copies = list.filter((c) => c.copy && c.base === p.id).map((c) => `<div class="panel-row is-copy"><span class="panel-row-name">${esc(c.title)}</span>
      <button class="mini-btn" type="button" data-panel-show="${esc(c.id)}">Show</button><button class="mini-btn" type="button" data-panel-remove="${esc(c.id)}" aria-label="Close ${esc(c.title)}">×</button></div>`).join("");
    return `<div class="panel-row"><label class="switch"><input type="checkbox" data-panel-toggle="${p.id}"${p.hidden ? "" : " checked"}><span class="switch-knob" aria-hidden="true"></span>${esc(p.title)}</label>
      ${p.copyable ? `<button class="mini-btn" type="button" data-panel-copy="${p.id}" title="A copy keeps its own person, section and range">+ Copy</button>` : ""}</div>${copies}`;
  }).join("");
  const box = $("#panels-menu-list");
  if (box.dataset.html !== rows) { box.innerHTML = rows; box.dataset.html = rows; }
  const p = Prefs.get();
  const arcade = `<label class="switch"><input type="checkbox" data-pref-toggle="games"${p.games ? " checked" : ""}><span class="switch-knob" aria-hidden="true"></span>Games beside the terminal</label>
    <label class="switch"><input type="checkbox" data-pref-toggle="bubbles"${p.bubbles ? " checked" : ""}><span class="switch-knob" aria-hidden="true"></span>The bot suggests a game while you wait</label>`;
  const a = $("#arcade-menu-list");
  if (a.dataset.html !== arcade) { a.innerHTML = arcade; a.dataset.html = arcade; }
}

function renderAll() {
  renderBar();
  renderQueue();
  renderInsights();
  if (state.pr) {
    const fresh = findPr(prKey(state.pr.pr));
    if (fresh && fresh.updated_on !== state.pr.pr.updated_on) openPr(fresh, { force: true });
  }
}

/* ------------------------------------------------------------- loading */

function setSyncing(on) { state.busy = on; $("#refresh").classList.toggle("is-busy", on); }
function applyBoot(b) {
  state.me = b.me; state.auth = b.auth; state.team = b.team || [];
  state.budget = b.budget || state.budget; state.launches = b.launches || []; state.project = b.project || null;
  state.root = b.root || "";
  state.platform = b.platform || "";
  state.features = b.features || []; // an older server has none: the page skips what it lacks
}
const signature = (d) => JSON.stringify([d.open, d.merged, d.declined, d.review_requests, d.pushed, d.synced_at, d.members, d.project]);
async function loadPerson(uuid) {
  const data = await api(`person?uuid=${encodeURIComponent(uuid)}`);
  state.auth = data.auth || state.auth;
  state.budget = data.budget || state.budget;
  const before = state.people[uuid];
  const changed = !before || signature(before) !== signature(data);
  if (changed) state.people[uuid] = data;
  state.loadedAt[uuid] = Date.now();
  if (isMeUuid(uuid)) { primeSeen(data.open || []); watchMerges(data); watchReviews(data); }
  return changed;
}
// A pull request that was open last time and is merged now gets the merge moment, once.
// The first load only records what is open, so history never fires.
const MINE_OPEN_KEY = "prdesk.mine.open.v1";
// The merged pull request moves at once: out of Open, to the top of Merged, and the view
// stays on it showing its new state, before the refresh from Bitbucket comes back.
function showMerged(pr, merged) {
  const now = { ...pr, ...(merged || {}), state: "MERGED", updated_on: merged?.updated_on || new Date().toISOString() };
  const mine = state.me && state.people[state.me.uuid];
  if (mine) {
    mine.open = (mine.open || []).filter((p) => prKey(p) !== prKey(pr));
    mine.merged = [now, ...(mine.merged || []).filter((p) => prKey(p) !== prKey(pr))];
  }
  if (state.pr && prKey(state.pr.pr) === prKey(pr)) {
    state.pr.pr = { ...state.pr.pr, ...now };
    if (state.pr.detail?.pr) state.pr.detail.pr = { ...state.pr.detail.pr, state: "MERGED" };
  }
  renderAll();
  renderPr();
}
/* A review on one of your open pull requests drops a banner from the top: who, and whether
   they approved, asked for changes or commented. The first load only records what is there,
   and so does a pull request seen for the first time. An approval cleared by a new push shows
   up as "commented" in Bitbucket's data, so "commented" counts only from no review at all. */
const REVIEWS_KEY = "prdesk.mine.reviews.v1";
const BANNER_WORD = { approved: "APPROVED", changes_requested: "ASKED FOR CHANGES", commented: "COMMENTED" };
function watchReviews(data) {
  if ((state.features || []).includes("events")) return;  // the notification centre has them
  let before = null;
  try { before = JSON.parse(localStorage.getItem(REVIEWS_KEY)); } catch { /* none yet */ }
  const now = {};
  for (const pr of data.open || []) {
    const was = before && before[prKey(pr)];
    const states = {};
    for (const r of pr.reviews || []) {
      if (!r.uuid || isMeUuid(r.uuid)) continue;
      states[r.uuid] = r.state;
      const prev = was ? was[r.uuid] : undefined;
      if (!was || prev === r.state || !BANNER_WORD[r.state]) continue;
      if (r.state === "commented" && prev && prev !== "pending") continue;
      reviewBanner(pr, r);
    }
    now[prKey(pr)] = states;
  }
  try { localStorage.setItem(REVIEWS_KEY, JSON.stringify(now)); } catch { /* private window */ }
}
function reviewBanner(pr, review) {
  const box = $("#notices");
  if (!box) return;
  // Three review banners at most; a pull request waiting for an answer is never pushed out.
  const reviews = [...box.querySelectorAll(".review-banner:not(.proposal-banner)")];
  while (reviews.length >= 3) reviews.shift().remove();
  const tone = review.state === "approved" ? "is-ok" : review.state === "changes_requested" ? "is-bad" : "is-info";
  const el = document.createElement("div");
  el.className = `review-banner ${tone}`;
  el.setAttribute("role", "status");
  el.innerHTML = `<button class="rb-open" type="button" data-open="${esc(prKey(pr))}">
      <span class="rb-bang" aria-hidden="true">!</span>
      <span class="rb-face" aria-hidden="true">${esc(initials(review.name))}</span>
      <span class="rb-text"><b class="rb-who">${esc(review.name || "Someone")}</b><span class="rb-what">${BANNER_WORD[review.state]}</span>
        <span class="rb-pr">#${esc(pr.id)} ${esc(pr.title || "")}</span></span>
    </button><button class="rb-x" type="button" aria-label="Dismiss">×</button>`;
  const leave = () => { if (!el.isConnected || el.classList.contains("is-leaving")) return; el.classList.add("is-leaving"); setTimeout(() => el.remove(), 420); };
  el.querySelector(".rb-x").addEventListener("click", leave);
  el.querySelector(".rb-open").addEventListener("click", () => setTimeout(leave, 50));
  let timer = setTimeout(leave, 9000);
  el.addEventListener("mouseenter", () => clearTimeout(timer));
  el.addEventListener("mouseleave", () => { timer = setTimeout(leave, 4000); });
  box.append(el);
}
/* Pull requests an agent asks to open (pixeldeck.py --propose): a banner stays until you
   decide. Proceed opens it as proposed; Review opens the popup; Decline tells the agent no. */
const shownProposals = new Set();
async function pollProposals() {
  if (!(state.features || []).includes("proposals")) return;
  let list;
  try { list = (await api("proposals")).proposals; } catch { return; }
  const pending = list.filter((p) => p.status === "pending");
  for (const p of pending) if (!shownProposals.has(p.id)) proposalBanner(p);
  for (const el of $$(".proposal-banner")) if (!pending.some((p) => p.id === el.dataset.proposal)) leaveBanner(el);
}
function leaveBanner(el) {
  if (!el.isConnected || el.classList.contains("is-leaving")) return;
  el.classList.add("is-leaving");
  shownProposals.delete(el.dataset.proposal);
  setTimeout(() => el.remove(), 420);
}
function proposalBanner(p) {
  const box = $("#notices");
  if (!box) return;
  shownProposals.add(p.id);
  const el = document.createElement("div");
  el.className = "review-banner proposal-banner is-ask";
  el.dataset.proposal = p.id;
  el.setAttribute("role", "alertdialog");
  el.setAttribute("aria-label", `Pull request to open: ${p.title}`);
  const facts = `${plural(p.ahead, "commit")}, ${plural(p.file_count, "file")}, +${p.added} -${p.removed}${p.close_source_branch ? ", branch closes after the merge" : ""}${p.behind ? `, ${p.behind} behind` : ""}`;
  el.innerHTML = `<div class="rb-open pb-body">
      <span class="rb-bang" aria-hidden="true">?</span>
      <span class="rb-face" aria-hidden="true">${Sprites.svg("buddy", { scale: 1 })}</span>
      <span class="rb-text"><span class="rb-what">PULL REQUEST TO OPEN</span><b class="rb-who">${esc(p.title)}</b>
        <span class="rb-pr"><span class="branch">${esc(p.repo)} ${esc(p.branch)}</span> into <b>${esc(p.dest)}</b></span>
        <span class="rb-pr">${esc(facts)}</span></span></div>
    <div class="pb-actions">
      <button class="btn btn--primary" type="button" data-proposal-proceed="${esc(p.id)}">Proceed</button>
      <button class="btn" type="button" data-proposal-review="${esc(p.id)}">Review</button>
      <button class="btn btn--quiet" type="button" data-proposal-decline="${esc(p.id)}">Decline</button></div>`;
  el._proposal = p;
  box.append(el);
}
async function decideProposal(button, id, action, fromPopup = false) {
  const fields = fromPopup ? { dest: $("#pr-dest").value, title: $("#pr-title").value, description: $("#pr-desc").value, close_source_branch: $("#pr-close").checked } : undefined;
  try {
    const res = await busy(button, () => api("proposal-decide", { method: "POST", body: { id, action, fields } }));
    for (const el of $$(`.proposal-banner[data-proposal="${CSS.escape(id)}"]`)) leaveBanner(el);
    if (fromPopup) $("#pr-dialog").close();
    if (res.status === "opened") {
      await reloadMine();
      toast(`Pull request #${res.result.id} is open on ${res.repo}.`, `${plural(res.result.reviewers, "reviewer")} added. The agent has the link.`, false, { label: "Open it here", open: `${res.repo}#${res.result.id}` });
    } else toast("Declined.", "The agent was told no.");
  } catch (err) { toast(action === "approve" ? "Could not open the pull request." : "Could not decline.", err.message, true); }
}

function forgetOpen(pr) {
  try {
    const before = JSON.parse(localStorage.getItem(MINE_OPEN_KEY)) || [];
    localStorage.setItem(MINE_OPEN_KEY, JSON.stringify(before.filter((key) => key !== prKey(pr))));
  } catch { /* private window */ }
}
function watchMerges(data) {
  let before = null;
  try { before = JSON.parse(localStorage.getItem(MINE_OPEN_KEY)); } catch { /* none yet */ }
  if (Array.isArray(before)) {
    const merged = (data.merged || []).filter((pr) => before.includes(prKey(pr)));
    merged.slice(0, 3).forEach((pr, i) => setTimeout(() => {
      Sprites.celebrate(pr);
      toast(`Merged: #${pr.id} on ${pr.repo}.`, pr.title);
    }, 600 + i * 3600));
  }
  try { localStorage.setItem(MINE_OPEN_KEY, JSON.stringify((data.open || []).map(prKey))); } catch { /* private window */ }
}
async function loadTeam() {
  const data = await api("team");
  state.budget = data.budget || state.budget;
  const changed = !state.teamData || signature(state.teamData) !== signature(data);
  if (changed) state.teamData = data;
  state.loadedAt.team = Date.now();
  return changed;
}
// A copy may show someone the address bar does not: their data loads alongside.
async function loadCopies() {
  let changed = false;
  const whos = new Set();
  for (const base of ["queue", "insights"]) for (const id of Layout.instances(base)) if (Layout.isCopy(id)) whos.add(viewOf(id).who);
  whos.delete(state.route.who);
  for (const who of whos) {
    const key = who === "team" ? "team" : whoUuid({ who });
    if (!key || Date.now() - (state.loadedAt[key] || 0) < 25000) continue;
    try { changed = (who === "team" ? await loadTeam() : await loadPerson(key)) || changed; } catch { /* the next poll tries again */ }
  }
  return changed;
}
function renderPanel(id) {
  const base = Layout.baseOf(id);
  if (base === "queue") renderQueueIn(id);
  else if (base === "insights") renderInsightsIn(id);
}
function setCopyWho(id, who) {
  if (who === "edit-team") return openTeamDialog();
  if (who !== "me" && who !== "team" && isMeUuid(who)) who = "me";
  const ids = sectionsFor(viewKind({ who })).map((x) => x.id);
  const section = ids.includes(viewOf(id).section) ? viewOf(id).section : ids[0];
  Layout.setParams(id, { who, section });
  state.animate[id] = true;
  renderPanel(id);
  loadView({ silent: true });
}

/* A notification opens its pull request here, never in the browser: from what is loaded,
   else from its owner's list (a teammate not shown yet), else read on its own. */
async function openFromEvent(ev) {
  const key = `${ev.repo}#${ev.pr_id}`;
  let pr = findPr(key);
  if (!pr && ev.owner && !isMeUuid(ev.owner) && state.people[ev.owner] === undefined) {
    try { await loadPerson(ev.owner); } catch { /* read it on its own below */ }
    pr = findPr(key);
  }
  if (!pr) {
    try { pr = (await api(`pr?repo=${encodeURIComponent(ev.repo)}&id=${Number(ev.pr_id)}`)).pr; } catch (err) {
      toast(`Could not open #${ev.pr_id}.`, err.message, true);
      return;
    }
  }
  openPr(pr);
}

async function loadView({ silent = false } = {}) {
  if (!silent) setSyncing(true);
  let changed = !silent;
  try {
    if (!state.me) { applyBoot(await api("bootstrap")); changed = true; }
    if (state.route.who === "team") changed = (await loadTeam()) || changed;
    else if (whoUuid()) changed = (await loadPerson(whoUuid())) || changed;
    if (state.me && !state.people[state.me.uuid]) changed = (await loadPerson(state.me.uuid)) || changed;
    changed = (await loadCopies()) || changed;
  } catch (err) {
    if (err.message !== "reloading") toast("Could not load.", err.message, true);
  } finally {
    if (!silent) setSyncing(false);
    if (changed) renderAll(); else renderBar();
  }
}
async function refreshNow() {
  if (state.busy) return;
  setSyncing(true);
  try {
    if (state.route.who === "team") state.teamData = await api("refresh", { method: "POST", body: { uuid: "team" } });
    else {
      state.people[whoUuid()] = await api("refresh", { method: "POST", body: { uuid: whoUuid() } });
      if (state.route.who === "me") { watchMerges(state.people[whoUuid()]); watchReviews(state.people[whoUuid()]); }
    }
    applyBoot(await api("bootstrap"));
  } catch (err) {
    if (err.auth) state.auth = err.auth;
    toast("Refresh failed.", err.message, true);
  } finally { setSyncing(false); renderAll(); }
}

/* ------------------------------------------------------------- routing */

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const who = !parts[0] || parts[0] === "me" ? "me" : parts[0];
  const kind = who === "team" ? "team" : who === "me" ? "me" : "mate";
  const ids = sectionsFor(kind).map((s) => s.id);
  const section = ids.includes(parts[1]) ? parts[1] : ids[0];
  const pr = parts[2] === "pr" && parts[3] && parts[4] ? `${parts[3]}#${parts[4]}` : null;
  return { who, section, pr };
}
function hashFor(route) {
  const who = route.who === "me" ? "me" : route.who === "team" ? "team" : encodeURIComponent(route.who);
  const base = `#/${who}/${route.section}`;
  if (!route.pr) return base;
  const [repo, id] = route.pr.split("#");
  return `${base}/pr/${encodeURIComponent(repo)}/${id}`;
}
function go(route) {
  if (route.who !== "me" && route.who !== "team" && isMeUuid(route.who)) route.who = "me";
  const kind = route.who === "team" ? "team" : route.who === "me" ? "me" : "mate";
  const ids = sectionsFor(kind).map((s) => s.id);
  if (!ids.includes(route.section)) route.section = ids[0];
  const whoChanged = route.who !== state.route.who;
  const changed = whoChanged || route.section !== state.route.section;
  state.route = { ...route, pr: state.route.pr };
  state.showAll = false;
  state.cursor = -1;
  history.replaceState(null, "", hashFor(state.route));
  if (changed) {
    state.animate.queue = true;
    if (whoChanged) state.animate.insights = true;
    renderBar();
    renderQueue();
    if (whoChanged) renderInsights();
  }
  const key = route.who === "team" ? "team" : whoUuid();
  const fresh = currentData() && Date.now() - (state.loadedAt[key] || 0) < 60000;
  if (!fresh) loadView({ silent: !!currentData() });
}
function findPr(key) {
  const pools = [...Object.values(state.people).flatMap((d) => [d.open, d.merged, d.review_requests, d.declined]), ...(state.teamData?.members || []).flatMap((m) => [m.open, m.merged, m.declined])];
  for (const pool of pools) {
    const hit = (pool || []).find((pr) => prKey(pr) === key);
    if (hit) return hit;
  }
  return null;
}

/* -------------------------------------------------------------- keyboard */

function moveCursor(step) {
  const rows = $$("tr[data-pr]", Layout.body("queue"));
  if (!rows.length) return;
  state.cursor = Math.max(0, Math.min(rows.length - 1, state.cursor + step));
  rows.forEach((r, i) => r.classList.toggle("is-cursor", i === state.cursor));
  const row = rows[state.cursor];
  const box = Layout.body("queue").closest(".slot-body");
  if (box) {
    const rb = row.getBoundingClientRect(), bb = box.getBoundingClientRect();
    if (rb.top < bb.top || rb.bottom > bb.bottom) box.scrollTop += rb.top - bb.top - bb.height / 3;
  }
}
function onKey(e) {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "") || document.activeElement?.closest(".xterm");
  if (e.key === "Escape") {
    if ($("#help-dialog").open || $("#team-dialog").open || $("#pr-dialog").open) return;
    if (document.activeElement?.id === "filter" && state.filter) { state.filter = ""; $("#filter").value = ""; renderQueue(); }
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key;
  if (k === "/") { e.preventDefault(); $("#filter").focus(); }
  else if (k === "r") refreshNow();
  else if (k === "?") $("#help-dialog").showModal();
  else if (k === "j") moveCursor(1);
  else if (k === "k") moveCursor(-1);
  else if (k === "Enter" && state.cursor >= 0) { const row = $$("tr[data-pr]", Layout.body("queue"))[state.cursor]; const pr = row && findPr(row.dataset.pr); if (pr) openPr(pr); }
  else if (k === "t") { Layout.activate("terminal"); setTimeout(() => Term.fit(), 50); }
  else if (k === "q") Layout.activate("queue");
  else if (k === "i") Layout.activate("insights");
  else if (k === "p" && state.pr) Layout.activate("pr");
  else if (state.pr && "12345".includes(k)) setPrTab(["overview", "files", "threads", "commits", "tasks"][Number(k) - 1]);
  else if (state.pr && k === "f") applyFold(!state.prFolded);
  else if (state.pr?.tab === "files" && state.pr.diff && (k === "n" || k === "b")) { const i = currentFileIndex(); gotoFile(Math.max(0, Math.min(state.pr.diff.files.length - 1, i + (k === "n" ? 1 : -1)))); }
  else if (state.pr?.tab === "files" && state.pr.diff && k === "v") { const i = currentFileIndex(); const on = !viewedSet(state.pr.pr).has(state.pr.diff.files[i].path); markViewed(i, on); const box = $(`[data-viewed="${i}"]`); if (box) box.checked = on; }
}

/* -------------------------------------------------------------- events */

function bind() {
  document.addEventListener("click", (e) => {
    const t = e.target;
    const ext = t.closest("[data-external]");
    if (ext) { e.preventDefault(); openExternal(ext.dataset.external); return; }
    const anchor = t.closest(".entry-body a[href^='http']");
    if (anchor) { e.preventDefault(); openExternal(anchor.href); return; }
    const previewBtn = t.closest("[data-preview]");
    if (previewBtn) return openPreview(previewBtn.dataset.preview, previewBtn.dataset.branch);
    const deleteBtn = t.closest("[data-delete]");
    if (deleteBtn) return deleteBranch(deleteBtn, deleteBtn.dataset.delete, deleteBtn.dataset.branch, deleteBtn.dataset.sha);
    if (t.closest("[data-preview-delete]") && preview) return deleteBranch(t.closest("[data-preview-delete]"), preview.repo, preview.branch, preview.tip);
    if (t.closest("[data-create-pr]")) return createPr(t.closest("[data-create-pr]"));
    if (t.closest("[data-decline]")) return declinePr(t.closest("[data-decline]"));
    if (t.closest("[data-merge]")) return mergePr(t.closest("[data-merge]"));
    if (t.closest("[data-merge-go]")) return mergeGo(t.closest("[data-merge-go]"));
    if (t.closest("[data-merge-settings]")) { $("#pr-dialog").close(); return Settings.open("connections"); }
    if (t.closest("[data-sync]")) return syncPr(t.closest("[data-sync]"));
    const preset = t.closest("[data-date-preset]");
    if (preset) return setDates({ preset: preset.dataset.datePreset });
    const verdict = t.closest("[data-review]");
    if (verdict) return reviewPr(verdict);
    const proceed = t.closest("[data-proposal-proceed]");
    if (proceed) return decideProposal(proceed, proceed.dataset.proposalProceed, "approve");
    const refuse = t.closest("[data-proposal-decline]");
    if (refuse) return decideProposal(refuse, refuse.dataset.proposalDecline, "decline", !!refuse.closest("#pr-dialog"));
    const review = t.closest("[data-proposal-review]");
    if (review) { const p = review.closest(".proposal-banner")?._proposal; if (p) openPreview(p.repo, p.branch, p); return; }
    const linkBtn = t.closest("[data-copy-link]");
    if (linkBtn) return copyLink(linkBtn);
    const copyBtn = t.closest("[data-copy-threads]");
    if (copyBtn) return copyThreads(copyBtn);
    const launchBtn = t.closest("[data-launch]");
    if (launchBtn) return launch(launchBtn, e);
    const opener = t.closest("[data-open]");
    if (opener && !t.closest("tr[data-pr]")) { e.preventDefault(); const pr = findPr(opener.dataset.open); if (pr) openPr(pr); return; }
    const row = t.closest("tr[data-pr]");
    if (row) { e.preventDefault(); const pr = findPr(row.dataset.pr); if (pr) openPr(pr); return; }
    const rangeBtn = t.closest("[data-range]");
    if (rangeBtn) return setRange(rangeBtn.dataset.rangePanel, rangeBtn.dataset.range);
    const person = t.closest("tr[data-person]");
    if (person) {
      const id = panelOf(person);
      if (id && Layout.isCopy(id)) return setCopyWho(id, person.dataset.person);
      return go({ who: person.dataset.person, section: person.dataset.person === "me" ? "needs" : "open" });
    }
    const copySec = t.closest("[data-copy-section]");
    if (copySec) {
      e.preventDefault();
      const id = panelOf(copySec);
      Layout.setParams(id, { section: copySec.dataset.copySection });
      state.animate[id] = true;
      renderQueueIn(id);
      return;
    }
    const chip = t.closest(".sec-chip");
    if (chip) { e.preventDefault(); const r = parseHashFrom(chip.getAttribute("href")); go({ ...state.route, section: r.section }); return; }
    const tab = t.closest(".pr-view [data-tab]");
    if (tab && state.pr) { setPrTab(tab.dataset.tab); return; }
    const goto = t.closest("[data-goto]");
    if (goto) { gotoFile(Number(goto.dataset.goto)); return; }
    const gotoPath = t.closest("[data-goto-file]");
    if (gotoPath && state.pr?.diff) { const i = state.pr.diff.files.findIndex((f) => f.path === gotoPath.dataset.gotoFile); if (i >= 0) { setPrTab("files"); setTimeout(() => gotoFile(i), 30); } return; }
    const toggle = t.closest("[data-toggle-file]");
    if (toggle) { toggleFile(Number(toggle.dataset.toggleFile)); return; }
    const action = t.closest("[data-action]")?.dataset.action;
    if (action === "close-pr") return closePr();
    if (action === "close-preview") return $("#pr-dialog").close();
    if (action === "retry") return refreshNow();
    if (action === "edit-team") return openTeamDialog();
    if (action === "show-all") { state.showAll = true; return renderQueue(); }
    if (action === "reload-pr" && state.pr) return openPr(state.pr.pr, { force: true });
    const copyPanel = t.closest("[data-panel-copy]");
    if (copyPanel) { Layout.duplicate(copyPanel.dataset.panelCopy); return; }
    const removePanel = t.closest("[data-panel-remove]");
    if (removePanel) { Layout.remove(removePanel.dataset.panelRemove); return; }
    const showPanel = t.closest("[data-panel-show]");
    if (showPanel) { Layout.activate(showPanel.dataset.panelShow); return; }
    if (action === "preset-split") { Layout.preset("split"); return; }
    if (action === "preset-compact") { Layout.preset("compact"); return; }
    if (action === "close-help") return $("#help-dialog").close();
    if (action === "open-keys") { $("#settings-dialog").close(); return $("#help-dialog").showModal(); }
    if (action === "pr-fold") return applyFold(!state.prFolded);
    if (["fx-merge", "fx-die", "fx-run", "fx-review"].includes(action)) return replay(action, t.closest("[data-action]"));
  });
  document.addEventListener("change", async (e) => {
    const t = e.target;
    if (t.matches("[data-viewed]")) return markViewed(Number(t.dataset.viewed), t.checked);
    if (t.matches("[data-date-from]")) return setDates({ from: t.value });
    if (t.matches("[data-date-to]")) return setDates({ to: t.value });
    if (t.matches("[data-copy-who]")) return setCopyWho(t.dataset.copyWho, t.value);
    if (t.matches("[data-panel-toggle]")) { if (t.checked) Layout.show(t.dataset.panelToggle); else Layout.hide(t.dataset.panelToggle); renderBar(); return; }
    if (t.id === "person") {
      const v = t.value;
      if (v === "edit-team") { t.value = state.route.who; return openTeamDialog(); }
      return go({ who: v, section: state.route.section });
    }
    if (t.id === "project") {
      try {
        const res = await api("project", { method: "POST", body: { key: t.value } });
        state.project = res.project; state.people = {}; state.teamData = null; state.loadedAt = {};
        state.animate = {};
        renderAll();
        await loadView();
        toast(res.project ? `Showing ${res.project.name} only.` : "Showing every project.");
      } catch (err) { toast("Could not switch the project.", err.message, true); }
    }
  });
  $("#refresh").addEventListener("click", refreshNow);
  // A late answer from Jira must never paint over another popup opened since.
  $("#pr-dialog").addEventListener("close", () => { mergeBox = null; });
  $("#team-search").addEventListener("input", paintCandidates);
  $("#team-list").addEventListener("change", (e) => {
    const box = e.target.closest("input[data-uuid]");
    const c = box && candidates.find((x) => x.uuid === box.dataset.uuid);
    if (c) c.picked = box.checked;
    $("#team-count").textContent = `${candidates.filter((x) => x.picked).length} selected`;
  });
  $("#team-save").addEventListener("click", saveTeam);
  $("#filter").addEventListener("input", (e) => { state.filter = e.target.value; renderQueue(); });
  document.addEventListener("keydown", onKey);

  const tip = $("#tooltip");
  document.addEventListener("mouseover", (e) => {
    const g = e.target.closest("[data-tip]");
    if (!g) { tip.hidden = true; return; }
    tip.textContent = g.dataset.tip;
    tip.hidden = false;
  });
  document.addEventListener("mousemove", (e) => {
    if (tip.hidden) return;
    const w = tip.offsetWidth;
    tip.style.left = `${Math.min(innerWidth - w - 8, Math.max(8, e.clientX - w / 2))}px`;
    tip.style.top = `${e.clientY - 36}px`;
  });
  window.addEventListener("hashchange", () => {
    const r = parseHash();
    if (r.who !== state.route.who || r.section !== state.route.section) go(r);
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadView({ silent: true }); });
}
function parseHashFrom(href) {
  const saved = location.hash;
  const parts = href.replace(/^#\/?/, "").split("/");
  return { section: decodeURIComponent(parts[1] || "") , saved };
}

/* ---------------------------------------------------------------- boot */

/* The title screen. Each step is a real stage of the first load; the screen stays up at
   least MIN ms from page start so it reads as a title screen, and any key or click skips
   what is left of that. A load slower than GIVE_UP opens the app anyway, still syncing.
   The slime and the bot race along the bar: friends, but the lead changes at every step,
   one of them wins at the end and the other says GG. */
const BootScreen = (() => {
  const el = document.getElementById("boot");
  const MIN = new URLSearchParams(location.search).has("panel") ? 0 : 1300;
  const GIVE_UP = 12000;
  const STEPS = { assets: [0.25, "LOADING ASSETS"], connect: [0.5, "CONNECTING"], data: [0.8, "FETCHING PULL REQUESTS"], ready: [1, "READY!"] };
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let skip = false, gone = !el;
  const onSkip = () => { skip = true; };
  addEventListener("keydown", onSkip);
  el?.addEventListener("pointerdown", onSkip);
  const RACERS = { bot: { run: [[0, 2], 140], happy: 4, ko: 3, name: "BOT" }, buddy: { run: [[0, 1], 170], happy: 2, ko: 3, name: "SLIME" } };
  const racers = [["bot", el?.querySelector(".boot-runner.is-bot")], ["buddy", el?.querySelector(".boot-runner.is-blob")]].filter(([, r]) => r);
  for (const [who, r] of racers) { r.innerHTML = Sprites.svg(who, { scale: 3 }); Sprites.play(r.firstElementChild, RACERS[who].run); }
  let leader = Math.random() < 0.5 ? 0 : 1;
  function jostle() {
    leader = 1 - leader;
    racers.forEach(([, r], i) => r.style.setProperty("--lead", i === leader ? "12px" : "-12px"));
  }
  // A warm load is done in a few ms, so each step holds a moment and the bar visibly fills.
  const HOLD = MIN ? 260 : 0;
  let shown = 0, queue = Promise.resolve();
  function step(name, text) {
    queue = queue.then(async () => {
      const wait = shown + HOLD - performance.now();
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
      if (gone) return;
      const [p, label] = STEPS[name];
      el.style.setProperty("--p", p);
      if (name !== "ready") jostle();
      el.querySelector("#boot-step").textContent = text || label;
      shown = performance.now();
    });
    return queue;
  }
  async function finish(error = "") {
    if (gone || el.classList.contains("is-ready") || el.classList.contains("is-error")) return;
    const winner = Math.random() < 0.5 ? 0 : 1;
    const [winWho] = racers[winner] || ["bot"];
    await step("ready", error || `READY! ${RACERS[winWho].name} WINS`);
    if (gone) return;
    el.classList.add(error ? "is-error" : "is-ready");
    racers.forEach(([who, r], i) => {
      Sprites.stop(r.firstElementChild, error ? RACERS[who].ko : RACERS[who].happy);
      r.style.setProperty("--lead", error ? "0px" : i === winner ? "16px" : "-16px");
      r.classList.toggle("is-winner", !error && i === winner);
      r.classList.toggle("is-gg", !error && i !== winner);
    });
    const until = Math.max(performance.now() + (error ? 1500 : 1000), MIN);  // long enough to see who won
    while (performance.now() < until && !skip) await new Promise((r) => setTimeout(r, 40));
    gone = true;
    removeEventListener("keydown", onSkip);
    el.classList.add("is-leaving");
    setTimeout(() => el.remove(), calm ? 200 : 650);
  }
  step("assets");
  setTimeout(() => finish(), GIVE_UP);
  return { step, finish };
})();

async function boot() {
  Layout.init({
    panels: [
      { id: "queue", title: "Queue", copyable: true }, { id: "pr", title: "Pull request" },
      { id: "insights", title: "Insights", copyable: true }, { id: "terminal", title: "Terminal" },
      { id: "repos", title: "Repos", place: "left" }, { id: "music", title: "Music", place: "hidden" },
    ],
    onChange: () => { for (const b of $$(".chart-box")) b.dataset.drawn = ""; paintCharts(); Term.fit(); },
    paramsFor: (id) => { const v = viewOf(id); return { who: v.who, section: v.section, range: v.range }; },
    onCreate: (id) => { state.animate[id] = true; renderPanel(id); renderBar(); loadView({ silent: true }); },
    onRemove: (id) => { delete state.animate[id]; renderBar(); },
  });
  Term.mount(Layout.body("terminal"));
  Arcade.attach(Layout.body("terminal"));
  Repos.mount(Layout.body("repos"));
  Music.mount(Layout.body("music"));
  RetroSelect.enhanceAll();
  Prefs.onChange(() => paintLayoutMenu());
  bind();
  state.route = parseHash();
  renderAll();
  renderPr();
  setSyncing(true);
  BootScreen.step("connect");
  try { applyBoot(await api("bootstrap")); } catch (err) { if (err.message !== "reloading") toast("Dashboard server unreachable.", err.message, true); }
  setSyncing(false);
  api("projects").then((r) => { state.projects = r.projects; state.project = r.project; renderBar(); }).catch(() => {});
  BootScreen.step("data");
  await loadView();
  BootScreen.finish(state.me ? "" : "NO CONNECTION").then(() => Guide.firstRun());
  if (state.route.pr) { const pr = findPr(state.route.pr); if (pr) openPr(pr); }
  // The server re-reads your open pull requests every minute; the page asks it twice as often.
  setInterval(() => { if (!document.hidden) loadView({ silent: true }); }, 30000);
  pollProposals();
  setInterval(pollProposals, 3000);
  StatusBar.start();
  if (!Layout.isSolo() && (state.features || []).includes("events")) {
    Notify.start({
      api, me: () => state.me, ready: () => !!state.me,
      onSettings: (tab) => Settings.open(tab),
      onOpen: openFromEvent,
    });
  }
}

boot();
