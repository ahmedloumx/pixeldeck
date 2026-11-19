"use strict";

/* The YouTube queue: the videos that play after the current one. Each function takes a queue
   and gives a new one back, never changing the one it got, so music-youtube.js keeps, saves and
   paints it, and the tests run these without a page. A video is in the queue at most once. */

const TubeQueue = (() => {
  const MAX = 200;
  const ID = /^[0-9A-Za-z_-]{11}$/;
  const ok = (v) => !!v && typeof v === "object" && ID.test(v.id);

  /** Videos at the end, skipping those already queued; with next, right at the front instead
      (moved there if queued already), so they play after the current one. */
  function add(queue, items, { next = false } = {}) {
    const fresh = [];
    for (const v of items || []) if (ok(v) && !fresh.some((f) => f.id === v.id)) fresh.push(v);
    if (next) {
      const ids = new Set(fresh.map((v) => v.id));
      return [...fresh, ...queue.filter((v) => !ids.has(v.id))].slice(0, MAX);
    }
    const have = new Set(queue.map((v) => v.id));
    return [...queue, ...fresh.filter((v) => !have.has(v.id))].slice(0, MAX);
  }
  /** The first video and the queue without it. */
  function take(queue) {
    return { item: queue[0] || null, rest: queue.slice(1) };
  }
  function remove(queue, i) {
    return queue.filter((_v, j) => j !== i);
  }
  /** The videos of a playlist after the one that plays from it: those are what comes next. */
  function after(items, id) {
    const at = items.findIndex((v) => v.id === id);
    return at < 0 ? items : items.slice(at + 1);
  }

  return { MAX, add, take, remove, after };
})();
