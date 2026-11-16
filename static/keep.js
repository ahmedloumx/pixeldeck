"use strict";

/* Kept settings: the layout, theme, terminal names and order, and a few more, are copied to the
   server and come back from it in the page itself (a JSON island the server writes into
   index.html). The window can be killed before WebKit writes its local storage to disk; the
   server's copy is still there. This runs before every other script, so they all read the
   newest settings. */

const Keep = (() => {
  const KEYS = ["prdesk.layout.v2", "prdesk.prefs.v1", "prdesk.termnames.v1", "prdesk.termorder.v1", "prdesk.termtitles.v1",
    "prdesk.insights.range.v1", "prdesk.datefilter.v1", "prdesk.notify.v1", "prdesk.dock.width.v1", "prdesk.restore.v1"];
  const STAMP = "prdesk.keep.stamp.v1";
  const get = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
  const set = (k, v) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* storage blocked */ } };

  // The server's copy wins when it is newer than what this browser last sent or took.
  let island = null;
  try { island = JSON.parse(document.getElementById("ui-state")?.textContent || "null"); } catch { island = null; }
  const localStamp = Number(get(STAMP)) || 0;
  if (island && island.values && Number(island.stamp) > localStamp) {
    for (const k of KEYS) if (k in island.values) set(k, island.values[k]);
    set(STAMP, String(island.stamp));
  }

  let sent = Object.fromEntries(KEYS.map((k) => [k, get(k)]));
  const token = () => document.querySelector('meta[name="desk-token"]')?.content || "";
  function changes() {
    const out = {};
    for (const k of KEYS) { const v = get(k); if (v !== sent[k]) out[k] = v; }
    return out;
  }
  async function flush(keepalive = false) {
    if (!island) return;  // an older server has no route for this
    const values = changes();
    if (!Object.keys(values).length) return;
    const stamp = Date.now();
    try {
      const res = await fetch("/api/ui-state", { method: "POST", keepalive, headers: { "Content-Type": "application/json", "X-Desk-Token": token() },
        body: JSON.stringify({ values, stamp }) });
      if (!res.ok) return;
      Object.assign(sent, values);
      set(STAMP, String(stamp));
    } catch { /* the next round tries again */ }
  }
  setInterval(flush, 2000);
  addEventListener("pagehide", () => flush(true));
  document.addEventListener("visibilitychange", () => { if (document.hidden) flush(true); });

  // Restored terminals get new ids: their names, titles and place in the order move with them.
  function carryTerminals(moved) {
    const parse = (k, fallback) => { try { return JSON.parse(get(k)) || fallback; } catch { return fallback; } };
    for (const key of ["prdesk.termnames.v1", "prdesk.termtitles.v1"]) {
      const all = parse(key, {});
      for (const m of moved) if (m.new && all[m.old]) { all[m.new] = all[m.old]; delete all[m.old]; }
      set(key, JSON.stringify(all));
    }
    const order = parse("prdesk.termorder.v1", []);
    const swap = Object.fromEntries(moved.filter((m) => m.new).map((m) => [m.old, m.new]));
    set("prdesk.termorder.v1", JSON.stringify(Array.isArray(order) ? order.map((id) => swap[id] || id) : []));
    flush();
  }
  return { flush, carryTerminals, keys: KEYS };
})();
