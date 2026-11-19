"use strict";

/* The lock screen: when you lock the computer (Super+L, Windows+L) and come back, PixelDeck
   shows "Let's code" in eight languages and a new line to get you going. With a passcode, type
   it and press Enter; without one, any key or a click lets you in. Desk, Lock PixelDeck shows
   it on demand.

   The server counts the system's locks (screenlock.py) and the status poll carries the count:
   when it goes up, the screen comes. Coming back to the window asks at once. The passcode is a
   privacy screen for the window, not a second lock on the computer; it is checked by the
   server, which keeps only a salted hash. */

const LockScreen = (() => {
  const LETS = [["Yalla ncodiw", "TOUNSI"], ["Let's code", "ENGLISH"], ["Codons", "FRANÇAIS"], ["Lass uns coden", "DEUTSCH"],
    ["コードを書こう", "日本語"], ["Давай кодить", "РУССКИЙ"], ["Vamos codar", "PORTUGUÊS"], ["¡A programar!", "ESPAÑOL"]];
  const LINES = [
    "Small commits, big wins.", "Today's bug is tomorrow's war story.", "Ship it, then make it shine.",
    "The best code is the code you can delete later.", "One green build at a time.", "You've fixed harder things before breakfast.",
    "Read the error message. It's trying to help.", "Future you will thank present you for that test.",
    "Name it well and half the docs write themselves.", "A review comment is a gift, not a verdict.",
    "Make it work, make it right, then make it fast.", "Every senior dev was once stuck on a semicolon.",
    "Leave the code a little cleaner than you found it.", "The rubber duck believes in you.",
    "Merge conflicts are just two good ideas meeting.", "Tiny steps still climb mountains.",
    "Debugging is detective work, and you are the detective.", "Done is a feature.",
  ];
  const LINE_KEY = "prdesk.lock.line.v1";
  let dlg = null, stop = null, clock = 0, typing = 0, code = "", hasCode = false, seenLocks = null, busy = false;
  const has = () => (state.features || []).includes("lock");
  const followOs = () => Prefs.get("lockScreen") !== false;

  function nextLine() {
    let last = -1;
    try { last = Number(localStorage.getItem(LINE_KEY)); } catch { /* storage blocked */ }
    let i = Math.floor(Math.random() * LINES.length);
    if (i === last) i = (i + 1) % LINES.length;
    try { localStorage.setItem(LINE_KEY, String(i)); } catch { /* storage blocked */ }
    return LINES[i];
  }
  function typeLine() {
    const el = dlg?.querySelector(".lk-line");
    if (!el) return;
    const line = nextLine();
    clearInterval(typing);
    if (matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motion === "calm") { el.textContent = line; return; }
    let n = 0;
    el.textContent = "";
    typing = setInterval(() => { n++; el.textContent = line.slice(0, n); if (n >= line.length) clearInterval(typing); }, 34);
  }
  function paintClock() {
    const el = dlg?.querySelector(".lk-clock");
    if (!el) return;
    const now = new Date();
    el.querySelector("b").textContent = now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    el.querySelector("small").textContent = now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }).toUpperCase();
  }
  function paintSound() {
    const b = dlg?.querySelector("[data-lk-sound]");
    if (!b) return;
    const on = Sound.on();
    b.setAttribute("aria-pressed", String(on));
    b.querySelector("span").textContent = on ? "SOUND ON" : "SOUND OFF";
  }
  function paintDots(mood = "") {
    const d = dlg?.querySelector(".lk-dots");
    if (!d) return;
    d.innerHTML = Array.from({ length: Math.max(4, code.length) }, (_, i) => `<i class="${i < code.length ? "is-on" : ""}"></i>`).join("");
    d.classList.remove("is-wrong");
    if (mood === "wrong") { void d.offsetWidth; d.classList.add("is-wrong"); }
  }
  function say(text, tone = "") {
    const s = dlg?.querySelector(".lk-say");
    if (!s) return;
    s.textContent = text;
    s.dataset.tone = tone;
  }

  async function refreshCode() {
    if (!has()) { hasCode = false; return; }
    try { hasCode = !!(await api("lock-state")).has_code; } catch { /* keep what we had */ }
  }

  /** Show the lock screen. */
  async function show() {
    if (dlg || (typeof Layout !== "undefined" && Layout.isSolo())) return;
    await refreshCode();
    if (dlg) return;
    code = "";
    dlg = document.createElement("dialog");
    dlg.className = "lk";
    dlg.setAttribute("aria-label", "PixelDeck is locked");
    dlg.innerHTML = `<div class="lk-cabinet">
      <p class="lk-clock"><b></b><small></small></p>
      <p class="lk-who">${typeof Sprites !== "undefined" ? Sprites.svg("bot", { scale: 2, idle: "blink" }) : ""}<span>${esc(state.me?.name || "PixelDeck")}</span></p>
      <button class="lk-sound" type="button" data-lk-sound aria-pressed="true"><span>SOUND ON</span></button>
      <span class="wl-flash" aria-hidden="true"></span>
      <div class="lk-scene">
        <canvas class="lk-canvas" aria-hidden="true"></canvas>
        <p class="lk-lang num8"></p>
        <h1 class="visually-hidden">Let's code</h1>
        <p class="lk-line" aria-live="polite"></p>
        <div class="lk-crew" aria-hidden="true">${typeof Sprites !== "undefined" ? Sprites.svg("buddy", { scale: 4 }) : ""}</div>
        <div class="lk-gate">${hasCode
          ? `<div class="lk-dots" aria-hidden="true"></div><label class="lk-code-label"><span class="visually-hidden">Passcode</span>
              <input class="lk-code" type="password" inputmode="numeric" autocomplete="off" maxlength="8" aria-describedby="lk-say"></label>
             <p class="lk-say num8" id="lk-say">TYPE YOUR PASSCODE, THEN ENTER</p>`
          : `<button class="lk-coin" type="button" data-lk-open>INSERT COIN</button><p class="lk-say num8" id="lk-say">OR PRESS ANY KEY</p>`}</div>
      </div></div>`;
    document.body.append(dlg);
    dlg.addEventListener("cancel", (e) => e.preventDefault());  // Escape never skips the lock
    dlg.addEventListener("keydown", onKey);
    dlg.showModal();
    paintSound();
    paintClock();
    clock = setInterval(paintClock, 1000);
    paintDots();
    typeLine();
    stop = PixelWords.cycle(dlg.querySelector(".lk-canvas"), LETS.map(([w]) => w), { shift: 3, hold: 1800, breakUp: false,
      onWord: (i) => { const l = dlg?.querySelector(".lk-lang"); if (l) l.textContent = LETS[i][1]; } });
    (dlg.querySelector(".lk-code") || dlg.querySelector("[data-lk-open]"))?.focus();
  }

  function open() {
    if (!dlg) return;
    Sound.ok();
    const f = dlg.querySelector(".wl-flash");
    f?.classList.add("is-on");
    stop?.();
    clearInterval(clock);
    clearInterval(typing);
    const gone = dlg;
    dlg = null;
    gone.classList.add("is-leaving");
    setTimeout(() => { gone.close(); gone.remove(); }, 380);
  }

  async function tryCode() {
    if (busy || !dlg) return;
    const input = dlg.querySelector(".lk-code");
    if (!code) return;
    busy = true;
    let answer = { ok: false, wait: 0 };
    try { answer = await api("lock-check", { method: "POST", body: { code } }); } catch (err) { say(String(err.message || "Could not check it").toUpperCase(), "bad"); }
    busy = false;
    if (!dlg) return;
    if (answer.ok) return open();
    Sound.wrong();
    code = "";
    if (input) input.value = "";
    paintDots("wrong");
    say(answer.wait ? `TOO MANY TRIES: WAIT ${answer.wait} SECONDS` : "NOT THIS ONE, TRY AGAIN", "bad");
  }

  function onKey(e) {
    if (!dlg) return;
    if (!hasCode) {
      if (["Shift", "Control", "Alt", "Meta", "Tab"].includes(e.key) || e.target.closest?.("[data-lk-sound]")) return;
      e.preventDefault();
      Sound.coin();
      return open();
    }
    if (e.key === "Enter") { e.preventDefault(); tryCode(); return; }
    // Digits typed while something else has the focus (the sound key, a click) go to the code.
    const input = dlg.querySelector(".lk-code");
    if (input && e.target !== input && /^[0-9]$/.test(e.key)) input.focus();
  }
  document.addEventListener("input", (e) => {
    if (!dlg || !e.target.matches?.(".lk-code")) return;
    const clean = e.target.value.replace(/[^0-9]/g, "").slice(0, 8);
    if (clean !== e.target.value) e.target.value = clean;
    if (clean.length > code.length) Sound.key(clean.length);
    code = clean;
    paintDots();
    say("TYPE YOUR PASSCODE, THEN ENTER");
  });
  document.addEventListener("click", (e) => {
    if (!dlg || !dlg.contains(e.target)) return;
    if (e.target.closest("[data-lk-sound]")) { Sound.set(!Sound.on()); paintSound(); Sound.key(3); dlg.querySelector(".lk-code")?.focus(); return; }
    if (e.target.closest("[data-lk-open]")) { Sound.coin(); return open(); }
    if (hasCode) dlg.querySelector(".lk-code")?.focus();
    else if (!e.target.closest("button")) { Sound.coin(); open(); }
  });

  /** The status poll's lock block: a new system lock shows the screen. */
  function seen(lock) {
    if (!lock || typeof lock.locks !== "number") return;
    const first = seenLocks === null;
    const more = !first && lock.locks > seenLocks;
    seenLocks = lock.locks;
    if (!followOs()) return;
    if (more || (!first && lock.locked)) show();
  }
  // Coming back to the window: ask at once rather than at the next poll.
  document.addEventListener("visibilitychange", async () => {
    if (document.hidden || !has() || !followOs() || dlg) return;
    try { seen(await api("lock-state")); } catch { /* the poll will tell */ }
  });

  /* ---------------------------------------------------------------- settings */
  function settingsHtml() {
    if (!has()) return "";
    const p = Prefs.get();
    return `<div class="set-block"><h3>Lock screen</h3>
      <div class="switch-grid"><label class="switch"><input type="checkbox" data-pref-toggle="lockScreen"${p.lockScreen !== false ? " checked" : ""}><span class="switch-knob" aria-hidden="true"></span><span>Lock with the computer<small>Super+L or Windows+L shows "Let's code" when you come back</small></span></label>
        <label class="switch"><input type="checkbox" data-pref-toggle="sounds"${p.sounds !== false ? " checked" : ""}><span class="switch-knob" aria-hidden="true"></span><span>Sounds<small>the coin, the keys and the jingle</small></span></label></div>
      <form class="set-form lk-form" data-lk-form autocomplete="off">
        <p class="set-hint">${hasCode ? "A passcode is set." : "No passcode: any key opens it."} A passcode keeps a glance at your screen off your pull requests; the computer's own lock stays the real one. Forgot it? Run <code>pixeldeck.py --forget-passcode</code>.</p>
        ${hasCode ? '<label class="field-block"><span>Current passcode</span><input type="password" name="current" inputmode="numeric" maxlength="8" autocomplete="off"></label>' : ""}
        <label class="field-block"><span>${hasCode ? "New passcode" : "Passcode"} (4 to 8 digits)</span><input type="password" name="code" inputmode="numeric" maxlength="8" autocomplete="new-password"></label>
        <p class="set-error" data-lk-error hidden></p>
        <p class="set-actions"><button class="btn btn--primary" type="submit">${hasCode ? "Change it" : "Set it"}</button>${hasCode ? ' <button class="btn btn--quiet" type="button" data-lk-clear>Remove it</button>' : ""}
          <button class="btn" type="button" data-lk-now>Lock now</button></p></form></div>`;
  }
  async function saveCode(body, form) {
    const err = form.querySelector("[data-lk-error]");
    err.hidden = true;
    try {
      const out = await api("lock-code", { method: "POST", body });
      hasCode = out.has_code;
      toast(hasCode ? "Passcode set." : "Passcode removed.", hasCode ? "The lock screen asks for it now." : "");
      Settings.paint();
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  }
  document.addEventListener("submit", (e) => {
    const form = e.target.closest?.("[data-lk-form]");
    if (!form) return;
    e.preventDefault();
    saveCode({ code: form.code.value, current: form.current?.value || "" }, form);
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-lk-now]")) { document.getElementById("settings-dialog")?.close(); show(); }
    const clear = e.target.closest("[data-lk-clear]");
    if (clear) saveCode({ clear: true, current: clear.form.current?.value || "" }, clear.form);
  });

  return { show, seen, settingsHtml, refreshCode, isOpen: () => !!dlg };
})();
