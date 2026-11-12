"use strict";

/* Section visuals: the merge tree (a git graph of tickets merging into the trunk)
   and age bars for anything still waiting. Plain SVG and HTML, drawn from data. */

const TREE_ROW = 24;
const TREE_LANE = 18;

const branchTicket = (name) => {
  const hit = /(?:^|[/_-])([A-Z][A-Z0-9]{1,9}-\d+)/.exec(name || "");
  return hit && !/-BUG-/.test(hit[0]) ? hit[1] : "";
};

/* Merge tree: every pull request forks off the trunk when it was opened. Merged ones
   step back in; declined ones end on a tombstone; open ones and branches pushed
   without a pull request run up to "now", where the bot or a flag stands on them. */
function mergeTree(merged, open = [], { limit = 30, declined = [], pushed = [] } = {}) {
  const prs = merged.slice(0, limit);
  // declined ones only inside the span the shown merges cover, so a long tail does not stretch the tree
  const floor = prs.length ? Math.min(...prs.map((p) => Date.parse(p.created_on))) : -Infinity;
  const gone = declined.filter((p) => Date.parse(p.updated_on) >= floor).slice(0, 12);
  const planted = pushed.slice(0, 10);
  if (!prs.length && !open.length && !gone.length && !planted.length) return `<p class="panel-empty">No merges to draw yet.</p>`;

  const events = [];
  for (const pr of prs) {
    events.push({ t: Date.parse(pr.updated_on), kind: "merge", pr, key: prKey(pr) });
    events.push({ t: Date.parse(pr.created_on), kind: "fork", pr, key: prKey(pr) });
  }
  for (const pr of gone) {
    events.push({ t: Date.parse(pr.updated_on), kind: "decline", pr, key: prKey(pr) });
    events.push({ t: Date.parse(pr.created_on), kind: "fork", pr, key: prKey(pr), ends: "declined" });
  }
  for (const pr of open) events.push({ t: Date.parse(pr.created_on), kind: "fork", pr, key: prKey(pr), open: true });
  for (const b of planted) events.push({ t: b.pushed_at * 1000, kind: "push", branch: b, key: `branch:${b.repo}/${b.branch}`, open: true });
  events.sort((a, b) => b.t - a.t || (a.kind === "merge" || a.kind === "decline" ? -1 : 1));
  const rows = [{ kind: "now", t: Date.now() }, ...events];

  // lanes are handed out from the oldest event up, and freed when a branch merges or dies
  const laneOf = new Map();
  const free = [];
  let lanes = 0;
  for (let i = rows.length - 1; i >= 1; i--) {
    const r = rows[i];
    if (r.kind === "fork" || r.kind === "push") {
      free.sort((a, b) => a - b);
      const lane = free.length ? free.shift() : ++lanes;
      laneOf.set(r.key, lane);
      r.lane = lane;
    } else {
      r.lane = laneOf.get(r.key) || ++lanes;
      free.push(r.lane);
    }
  }
  const index = new Map();
  rows.forEach((r, i) => { if (r.key) index.set(`${r.key}|${r.kind}`, i); });
  const x0 = 10, xl = (lane) => x0 + lane * TREE_LANE, yr = (i) => i * TREE_ROW + TREE_ROW / 2;
  const width = x0 + (lanes + 1) * TREE_LANE;
  const height = rows.length * TREE_ROW;
  let g = `<line class="trunk" x1="${x0}" x2="${x0}" y1="${yr(0)}" y2="${height}"/>`;
  let cast = "";
  const drawn = new Set();
  rows.forEach((r) => {
    if (!r.key || drawn.has(r.key)) return;
    drawn.add(r.key);
    const lane = laneOf.get(r.key) ?? r.lane, x = xl(lane);
    const iF = index.get(`${r.key}|fork`) ?? index.get(`${r.key}|push`);
    const iM = index.get(`${r.key}|merge`);
    const iD = index.get(`${r.key}|decline`);
    const pr = r.pr;
    const tone = r.kind === "push" ? "is-pushed" : iD != null ? "is-declined" : iM == null ? "is-open" : `lane-${lane % 4}`;
    const iEnd = iM ?? iD ?? 0;
    g += `<path class="branch ${tone}" data-pr="${esc(r.key)}" d="M${x0},${yr(iF)} H${x} V${yr(iEnd)}${iM != null ? ` H${x0}` : ""}"/>`;
    g += `<rect class="node fork" x="${x0 - 3}" y="${yr(iF) - 3}" width="6" height="6"/>`;
    if (r.kind === "push") {
      const b = r.branch;
      cast += Sprites.svg("flag", { scale: 1.5, x: x - 4, y: yr(0) - 7, idle: "wave", tip: esc(`${b.branch} on ${b.repo}: pushed ${ago(new Date(b.pushed_at * 1000).toISOString())}, no pull request yet`) });
    } else if (iD != null) {
      cast += Sprites.svg("tomb", { scale: 1.5, x: x - 6, y: yr(iD) - 7, tip: esc(`Declined #${pr.id} on ${shortDate(pr.updated_on)}: ${pr.title}`) });
    } else if (iM == null) {
      cast += Sprites.svg("bot", { scale: 1, x: x - 6, y: yr(0) - 7, idle: "blink", tip: esc(`Open #${pr.id}: ${pr.title}`) });
    } else {
      g += `<rect class="node merge ${tone}" x="${x0 - 5}" y="${yr(iM) - 5}" width="10" height="10" data-tip="${esc(`Merged #${pr.id} on ${shortDate(pr.updated_on)}: ${pr.title}`)}"/>`;
    }
  });

  const nowNote = [open.length ? plural(open.length, "open pull request") : "", planted.length ? plural(planted.length, "branch", "branches") + " without a pull request" : ""].filter(Boolean).join(", ");
  const chip = (key, cls) => key ? `<span class="tree-ticket ${cls}">${esc(key)}</span>` : `<span class="tree-ticket is-none">no ticket</span>`;
  const labels = rows.map((r) => {
    if (r.kind === "now") return `<li class="tree-row is-now"><span class="when">now</span>${nowNote ? `<span class="tree-note">${esc(nowNote)}</span>` : ""}</li>`;
    if (r.kind === "push") {
      const b = r.branch;
      return `<li class="tree-row is-pushed" data-pr-row="${esc(r.key)}">${chip(branchTicket(b.branch), "is-pushed")}
        <button class="tree-title" type="button" data-preview="${esc(b.repo)}" data-branch="${esc(b.branch)}">${esc(b.subject || b.branch)}</button>
        <span class="id">${esc(b.repo)}, no pull request</span><time class="when">${esc(ago(new Date(b.pushed_at * 1000).toISOString()))}</time></li>`;
    }
    const pr = r.pr, ticket = pr.ticket?.key || "";
    if (r.kind === "fork" && r.open) {
      return `<li class="tree-row is-open" data-pr-row="${esc(r.key)}" data-ticket="${esc(ticket)}">${chip(ticket, "is-open")}
        <button class="tree-title" type="button" data-open="${esc(r.key)}">${esc(pr.title)}</button>
        <span class="id">${esc(pr.repo)} #${pr.id}, open</span><time class="when">${esc(ago(pr.created_on))}</time></li>`;
    }
    if (r.kind === "fork") {
      return `<li class="tree-row is-fork" data-pr-row="${esc(r.key)}" data-ticket="${esc(ticket)}"><span class="tree-note">opened #${pr.id}</span><time class="when">${esc(ago(pr.created_on))}</time></li>`;
    }
    if (r.kind === "decline") {
      return `<li class="tree-row is-declined" data-pr-row="${esc(r.key)}" data-ticket="${esc(ticket)}">${chip(ticket, "is-declined")}
        <button class="tree-title" type="button" data-open="${esc(r.key)}">${esc(pr.title)}</button>
        <span class="id">${esc(pr.repo)} #${pr.id}, declined</span><time class="when">${esc(shortDate(pr.updated_on))}</time></li>`;
    }
    return `<li class="tree-row is-merge" data-pr-row="${esc(r.key)}" data-ticket="${esc(ticket)}">${chip(ticket, "")}
      <button class="tree-title" type="button" data-open="${esc(r.key)}">${esc(pr.title)}</button>
      <span class="id">${esc(pr.repo)} #${pr.id}</span><time class="when">${esc(shortDate(pr.updated_on))}</time></li>`;
  }).join("");

  const legend = [
    prs.length ? `<span><i class="swatch-line" aria-hidden="true"></i>merged</span>` : "",
    open.length ? `<span>${Sprites.svg("bot", { scale: 1 })}open pull request</span>` : "",
    planted.length ? `<span>${Sprites.svg("flag", { scale: 1.5 })}pushed, no pull request</span>` : "",
    gone.length ? `<span>${Sprites.svg("tomb", { scale: 1.5 })}declined</span>` : "",
  ].filter(Boolean).join("");
  return `<div class="tree" style="--rows:${rows.length}">
    <svg class="tree-graph" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="Merge tree: pull requests branching off and merging back, newest at the top">${g}${cast}</svg>
    <ol class="tree-labels">${labels}</ol>
  </div>${legend ? `<p class="tree-legend">${legend}</p>` : ""}`;
}

function ageText(ms) {
  const d = Math.floor(ms / DAY), h = Math.floor((ms % DAY) / HOUR);
  if (d >= 1) return `${d}d ${h}h`;
  if (h >= 1) return `${h}h`;
  return `${Math.max(1, Math.round(ms / 60000))}m`;
}

function toneOf(pr) {
  const s = signals(pr);
  if (s.changes.length) return "bad";
  if (s.awaiting || s.tasks) return "you";
  if (s.approved) return "ok";
  return "cool";
}

function ageChart(prs, { showAuthor = false, limit = 10 } = {}) {
  if (!prs.length) return "";
  const now = Date.now();
  const rows = prs.map((pr) => ({ pr, age: now - Date.parse(pr.created_on), idle: now - Date.parse(pr.updated_on) }))
    .sort((a, b) => b.age - a.age).slice(0, limit);
  const max = Math.max(...rows.map((r) => r.age), HOUR);
  const items = rows.map(({ pr, age, idle }, i) => `
    <li class="age-row" data-open="${esc(prKey(pr))}" style="--i:${i}">
      <span class="age-label"><span class="id">#${pr.id}</span> ${esc(showAuthor ? `${firstName(pr.author.name)}: ${pr.title}` : pr.title)}</span>
      <span class="age-track" data-tip="${esc(`Opened ${ago(pr.created_on)}, last activity ${ago(pr.updated_on)}`)}">
        <span class="age-fill is-${toneOf(pr)}" style="--w:${(age / max).toFixed(3)}"></span>
        <span class="age-tick" style="--x:${((age - idle) / max).toFixed(3)}"></span>
      </span>
      <span class="age-value">${ageText(age)}</span>
    </li>`).join("");
  return `<ol class="age-chart">${items}</ol>
    <p class="age-legend"><span class="age-key is-bad"></span>changes requested <span class="age-key is-you"></span>your move <span class="age-key is-ok"></span>approved <span class="age-key is-cool"></span>with reviewers <span class="age-key tick"></span>last activity</p>`;
}

function pushedChart(items) {
  if (!items.length) return "";
  const now = Date.now() / 1000;
  const max = Math.max(...items.map((b) => now - b.pushed_at), 3600);
  return `<ol class="age-chart">${items.slice(0, 10).map((b, i) => `
    <li class="age-row is-static" style="--i:${i}">
      <span class="age-label"><span class="id">${esc(b.repo)}</span> ${esc(b.branch)}</span>
      <span class="age-track"><span class="age-fill is-cool" style="--w:${((now - b.pushed_at) / max).toFixed(3)}"></span></span>
      <span class="age-value">${ageText((now - b.pushed_at) * 1000)}</span>
    </li>`).join("")}</ol>`;
}
