"""The Claude panel: Claude Code as a conversation beside your pull requests, the way the VS Code
and Windsurf extensions show it. The terminal stays: this is the other way to run it.

Each chat runs the Claude Code CLI you already use (signed in as you, with your settings, your
CLAUDE.md and your tools), with no terminal:

    claude -p --input-format stream-json --output-format stream-json --verbose
           --include-partial-messages --permission-prompt-tool stdio

It reads your messages as JSON lines on stdin and writes what happens as JSON lines on stdout.
With the stdio permission tool, a tool that needs your yes comes back as a control_request
(can_use_tool): the panel shows it with Allow and Deny, and the answer goes back as a
control_response. It is the protocol the Claude Agent SDK, and the editor extensions built on
it, speak with the same CLI. The same channel answers what the extensions show around the chat,
read from Claude Code itself rather than written here: its models and their effort levels
(initialize), its slash commands, your account and plan, your usage windows (get_usage), the
context a session fills (get_context_usage); and it changes the model, the effort and the
permission mode of a live session (set_model, apply_flag_settings, set_permission_mode).

The sessions Claude Code keeps on this computer (~/.claude/projects/<folder>/<id>.jsonl) are
listed by folder, newest first, with the title Claude Code gave them; one opens here with the
end of its history and goes on with --resume.

The page asks /api/chat for what is new (a request waits a few seconds for news, so text shows
as it is written). Each chat's events are kept in the state folder, so a chat reads the same
after a restart. Folders come from the server's own list (your clones, a pull request's
worktree): the page names one, it never gives a path.
"""

import json
import os
import re
import secrets
import subprocess
import threading
import time
import traceback
from pathlib import Path

import config
import gitscan
import launcher

FEATURES = ["claude-panel", "claude-sessions"]
# Ask (Claude Code's default), accept edits, plan, and auto where the model has it. Never the
# modes that skip your yes altogether: those stay a terminal's, chosen on purpose.
MODES = {"default": "Ask before edits", "acceptEdits": "Accept edits", "plan": "Plan mode", "auto": "Auto"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}")  # an alias or a full name, as Claude Code lists them
SESSION = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CHATS_MAX = 40          # chats kept; the oldest stopped one goes first
EVENTS_MAX = 1500       # events kept per chat
TEXT_MAX = 6000         # a tool's input or result, as the panel shows it
MESSAGE_MAX = 200_000   # one message to Claude (a pull request brief included)
WAIT_MAX = 10.0         # seconds a read of the chat waits for news
ANSWER_WAIT = 20.0      # seconds a question to Claude Code (models, usage, context) may take
SESSIONS_MAX = 60       # sessions listed per folder
HISTORY_BYTES = 4 * 1024 * 1024   # the end of a session file read to show its history
HISTORY_EVENTS = 300


def model_ok(model):
    return model == "" or bool(MODEL.fullmatch(model or ""))


def command(model="", mode="default", session=None, effort="", add_dirs=()):
    """The claude command line of one chat."""
    argv = [launcher.claude_binary(), "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages", "--permission-prompt-tool", "stdio"]
    if mode in MODES and mode != "default":
        argv += ["--permission-mode", mode]
    if model and model != "default" and model_ok(model):
        argv += ["--model", model]
    if effort in EFFORTS:
        argv += ["--effort", effort]
    for folder in add_dirs:
        argv += ["--add-dir", str(folder)]
    if session:
        argv += ["--resume", session]
    return argv


def drops_folder():
    """Where files attached in the panel land (the terminals' drop folder): Claude may read it."""
    return config.folder("state") / "drops"


def _env():
    # A server started from inside Claude Code inherits its markers, and a nested claude would
    # refuse to start: the chat gets a clean slate, as a terminal does.
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE")}


def _clip(text, limit=TEXT_MAX):
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters)"


def _result_text(content):
    """A tool result's text: a string, or a list of blocks of which the text ones count."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else
                         f"[{b.get('type', 'block')}]" if isinstance(b, dict) else str(b) for b in content)
    return "" if content is None else str(content)


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


class Asked:
    """Questions sent to one Claude Code process, waiting for their control_response."""

    def __init__(self):
        self.slots, self.lock = {}, threading.Lock()

    def open(self, rid):
        slot = {"event": threading.Event(), "answer": None}
        with self.lock:
            self.slots[rid] = slot
        return slot

    def answer(self, response):
        with self.lock:
            slot = self.slots.pop(response.get("request_id"), None)
        if slot:
            slot["answer"] = response
            slot["event"].set()
        return slot is not None

    def close(self):
        with self.lock:
            slots, self.slots = list(self.slots.values()), {}
        for slot in slots:
            slot["event"].set()


def _awaited(slot, wait, rid, asked):
    if not slot["event"].wait(wait):
        with asked.lock:
            asked.slots.pop(rid, None)
        raise ValueError("Claude Code did not answer in time")
    answer = slot["answer"]
    if answer is None:
        raise ValueError("Claude Code stopped before it answered")
    if answer.get("subtype") == "error":
        raise ValueError(answer.get("error") or "Claude Code refused it")
    return answer.get("response") or {}


class Chat:
    def __init__(self, store, info, events=None):
        self.store = store
        self.info = info  # id, title, cwd, created, session, model, effort, mode, origin, status
        self.events = list(events or [])
        self.seq = self.events[-1]["seq"] if self.events else 0
        self.live = ""      # the text Claude is writing right now
        self.pending = {}   # request id -> a permission request waiting for you
        self.proc = None
        self.meta = None    # Claude Code's own answer to initialize: models, commands, account
        self.stderr = []
        self.asked = Asked()
        self.lock = threading.RLock()
        self.news = threading.Condition(self.lock)
        self.wlock = threading.Lock()

    # ---- what the page reads ----

    def public(self):
        with self.lock:
            return {**self.info, "alive": self.alive(), "pending": list(self.pending), "events": self.seq}

    def read(self, after=0, wait=False, seen=None):
        """The events after seq `after`, the text being written, and the chat. With wait, hold
        on a few seconds while nothing is new to the page: no event after `after`, and the text
        being written still `seen` characters long."""
        with self.news:
            if wait and self.seq <= after and (seen is None or len(self.live) == seen):
                self.news.wait(WAIT_MAX)
            fresh = [e for e in self.events if e["seq"] > after]
            return {"chat": self.public(), "events": fresh, "live": self.live}

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    # ---- what the page asks ----

    def send(self, text, brief_title=None):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("write something first")
        if len(text) > MESSAGE_MAX:
            raise ValueError(f"a message is at most {MESSAGE_MAX // 1000} k characters")
        if not self.alive():
            self._spawn()
        self._add({"kind": "user", "brief": True, "title": brief_title, "text": text} if brief_title else {"kind": "user", "text": text})
        with self.lock:
            self.info["status"] = "working"
        self._write({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    def brief(self, text, title):
        """A pull request's brief as the first message: shown folded, sent whole."""
        self.send(text, brief_title=title)

    def answer(self, request_id, allow, always=False):
        """Your yes or no to a tool. Always also keeps the rule Claude Code suggested (this tool,
        or this command, for the rest of the session)."""
        with self.lock:
            request = self.pending.pop(request_id, None)
        if request is None:
            raise ValueError("that question is no longer waiting")
        if allow:
            response = {"behavior": "allow", "updatedInput": request.get("input") or {}}
            if always and request.get("permission_suggestions"):
                response["updatedPermissions"] = request["permission_suggestions"]
        else:
            response = {"behavior": "deny", "message": "The user said no in PixelDeck."}
        if request.get("tool_use_id"):
            response["toolUseID"] = request["tool_use_id"]
        self._write({"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": response}})
        self._add({"kind": "answer", "request": request_id, "allow": bool(allow), "always": bool(allow and always)})
        with self.lock:
            if not self.pending:
                self.info["status"] = "working"

    def control(self, request, wait=0.0):
        """A question to Claude Code; with wait, its answer (never from the reader's thread)."""
        rid = secrets.token_hex(8)
        slot = self.asked.open(rid) if wait else None
        try:
            self._write({"type": "control_request", "request_id": rid, "request": request})
        except ValueError:
            if slot:
                self.asked.answer({"request_id": rid})
            raise
        return _awaited(slot, wait, rid, self.asked) if slot else None

    def interrupt(self):
        """Stop the turn under way; the chat stays open for the next message."""
        if self.alive():
            self.control({"subtype": "interrupt"})

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError("unknown permission mode")
        with self.lock:
            self.info["mode"] = mode
        if self.alive():
            self.control({"subtype": "set_permission_mode", "mode": mode})
        self.store.save()

    def set_model(self, model=None, effort=None):
        """Another model or effort: at once on a live session, else from its next start."""
        if model is not None and not model_ok(model):
            raise ValueError("unknown model")
        if effort is not None and effort not in ("",) + EFFORTS:
            raise ValueError("unknown effort level")
        with self.lock:
            if model is not None:
                self.info["model"] = model
            if effort is not None:
                self.info["effort"] = effort
        if self.alive():
            if model is not None:
                self.control({"subtype": "set_model", "model": model or "default"}, wait=ANSWER_WAIT)
            if effort:
                self.control({"subtype": "apply_flag_settings", "settings": {"effortLevel": effort}}, wait=ANSWER_WAIT)
        self.store.save()

    def context(self):
        """How full the session's context is: Claude Code's own count, while it runs."""
        if not self.alive():
            return self.info.get("context")
        raw = self.control({"subtype": "get_context_usage"}, wait=ANSWER_WAIT) or {}
        found = {k: raw.get(k) for k in ("percentage", "totalTokens", "maxTokens", "autoCompactThreshold", "model")}
        with self.lock:
            self.info["context"] = found
        return found

    def stop(self):
        """End the chat's process; its session can be resumed by writing again."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        with self.lock:
            self.info["status"] = "stopping"
        try:
            proc.stdin.close()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()

    # ---- the process ----

    def _spawn(self):
        argv = command(self.info.get("model") or "", self.info.get("mode") or "default", self.info.get("session"),
                       self.info.get("effort") or "", add_dirs=[drops_folder()])
        if not os.path.isdir(self.info["cwd"]):
            raise ValueError(f"the folder {self.info['cwd']} is gone")
        drops_folder().mkdir(parents=True, exist_ok=True)
        self.stderr = []
        self.proc = subprocess.Popen(argv, cwd=self.info["cwd"], env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     creationflags=gitscan.NO_WINDOW)
        with self.lock:
            self.info["status"] = "starting"
        proc = self.proc
        threading.Thread(target=self._read, args=(proc,), name=f"chat-{self.info['id']}", daemon=True).start()
        threading.Thread(target=self._drain, args=(proc,), name=f"chat-err-{self.info['id']}", daemon=True).start()
        # What the extensions ask first: the session's models, commands and account.
        threading.Thread(target=self._initialize, name=f"chat-init-{self.info['id']}", daemon=True).start()

    def _initialize(self):
        try:
            self.meta = self.control({"subtype": "initialize"}, wait=ANSWER_WAIT)
            INFO.learn(self.meta)
        except ValueError:
            pass  # an older Claude Code: the chat works without the extras

    def _write(self, message):
        line = json.dumps(message, ensure_ascii=False) + "\n"
        with self.wlock:
            if not self.alive():
                raise ValueError("this chat has stopped: write again to resume it")
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except OSError as exc:
                raise ValueError(f"Claude Code is not listening: {exc}") from None

    def _drain(self, proc):
        for line in proc.stderr:
            self.stderr = (self.stderr + [line.rstrip()])[-20:]
        proc.stderr.close()

    def _read(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            try:
                self._on(event)
            except Exception:  # noqa: BLE001 - one odd line must not end the chat
                traceback.print_exc()
        code = proc.wait()
        for pipe in (proc.stdout, proc.stdin):
            try:
                pipe.close()
            except OSError:
                pass
        self.asked.close()
        with self.lock:
            if self.proc is not proc:
                return
            asked = self.info.get("status") == "stopping"
            self.pending.clear()
            self.live = ""
            self.info["status"] = "stopped" if code == 0 or asked else "error"
        if code not in (0, None) and not asked:
            said = " ".join(s for s in self.stderr[-3:] if s)[:600]
            self._add({"kind": "error", "text": f"Claude Code stopped (exit {code}).{' ' + said if said else ''}"})
        else:
            self._add({"kind": "stopped"})
        self.store.save()

    # ---- what Claude Code says ----

    def _on(self, event):
        kind = event.get("type")
        if kind == "stream_event":
            inner = event.get("event") or {}
            delta = inner.get("delta") or {}
            with self.news:
                if inner.get("type") == "message_start":
                    self.live = ""
                elif inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    self.live += delta.get("text") or ""
                    self.news.notify_all()
            return
        if kind == "control_response":
            self.asked.answer(event.get("response") or {})
            return
        if kind == "system" and event.get("subtype") == "init":
            with self.lock:
                self.info["session"] = event.get("session_id") or self.info.get("session")
                self.info["model_name"] = event.get("model")
                self.info["version"] = event.get("claude_code_version")
                self.info["status"] = "working"
            self._add({"kind": "system", "text": " · ".join(x for x in (f"Claude Code {event.get('claude_code_version') or ''}".strip(),
                                                                       event.get("model"), event.get("permissionMode")) if x)})
            self.store.save()
            return
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and (block.get("text") or "").strip():
                    self._add({"kind": "assistant", "text": block["text"]})
                elif block.get("type") == "tool_use":
                    self._add({"kind": "tool", "id": block.get("id"), "name": block.get("name"), "input": _clip(block.get("input"))})
            with self.lock:
                self.live = ""
            return
        if kind == "user":
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    self._add({"kind": "tool_result", "id": block.get("tool_use_id"), "error": bool(block.get("is_error")),
                               "text": _clip(_result_text(block.get("content")))})
            return
        if kind == "control_request":
            rid, request = event.get("request_id"), event.get("request") or {}
            if not rid:
                return
            if request.get("subtype") == "can_use_tool":
                with self.lock:
                    self.pending[rid] = request
                    self.info["status"] = "waiting"
                self._add({"kind": "permission", "request": rid, "tool": request.get("tool_name"),
                           "title": request.get("display_name") or request.get("tool_name") or "a tool",
                           "description": request.get("description") or "", "reason": request.get("decision_reason") or "",
                           "input": _clip(request.get("input")), "always": bool(request.get("permission_suggestions"))})
            else:
                # Hooks, MCP servers of the host, dialogs: PixelDeck offers none of them.
                self._write({"type": "control_response", "response": {
                    "subtype": "error", "request_id": rid, "error": f"PixelDeck does not answer {request.get('subtype')}"}})
            return
        if kind == "control_cancel_request":
            rid = event.get("request_id")
            with self.lock:
                dropped = self.pending.pop(rid, None)
            if dropped is not None:
                self._add({"kind": "answer", "request": rid, "allow": False, "cancelled": True})
            return
        if kind == "result":
            with self.lock:
                self.live = ""
                self.pending.clear()
                self.info["status"] = "idle"
                self.info["session"] = event.get("session_id") or self.info.get("session")
            self._add({"kind": "result", "ok": not event.get("is_error"), "subtype": event.get("subtype"),
                       "turns": event.get("num_turns"), "ms": event.get("duration_ms"), "cost": event.get("total_cost_usd"),
                       "text": _clip(event.get("result") or "") if event.get("is_error") else ""})
            self.store.save()

    def _add(self, event):
        with self.news:
            self.seq += 1
            event = {"seq": self.seq, "at": time.time(), **event}
            self.events.append(event)
            if len(self.events) > EVENTS_MAX:
                del self.events[: len(self.events) - EVENTS_MAX]
            self.news.notify_all()
        self.store.append(self, event)


class Chats:
    """Every chat, newest first, with its events on disk."""

    def __init__(self, folder=None):
        self.folder = folder or config.folder("state") / "chats"
        self.chats = {}
        self.lock = threading.RLock()
        # One writer at a time: a chat's reader thread and a page's request both save, and
        # Windows refuses a second hand on a file being replaced.
        self.io = threading.Lock()
        self.loaded = False

    def _load(self):
        if self.loaded:
            return
        self.loaded = True
        try:
            infos = json.loads((self.folder / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            infos = []
        for info in infos if isinstance(infos, list) else []:
            if not isinstance(info, dict) or not str(info.get("id", "")).isalnum():
                continue
            events = []
            try:
                for line in (self.folder / f"{info['id']}.jsonl").read_text(encoding="utf-8").splitlines()[-EVENTS_MAX:]:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
            except OSError:
                pass
            info["status"] = "stopped"
            self.chats[info["id"]] = Chat(self, info, events)

    def save(self):
        with self.lock:
            infos = [{k: v for k, v in c.info.items()} for c in self.chats.values()]
        try:
            with self.io:
                _write_private(self.folder / "index.json", json.dumps(infos))
        except OSError:
            pass  # the chats go on; the list on disk catches up at the next save

    def append(self, chat, event):
        path = self.folder / f"{chat.info['id']}.jsonl"
        try:
            with self.io:
                self.folder.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                if event["seq"] % 500 == 0:  # now and then, cut the file to what is kept
                    with chat.lock:
                        kept = list(chat.events)
                    _write_private(path, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept))
        except OSError:
            pass  # the chat goes on; only its history after a restart is shorter

    def list(self):
        with self.lock:
            self._load()
            return [c.public() for c in sorted(self.chats.values(), key=lambda c: -c.info.get("created", 0))]

    def get(self, chat_id):
        with self.lock:
            self._load()
            chat = self.chats.get(str(chat_id))
        if chat is None:
            raise KeyError("no such chat")
        return chat

    def start(self, cwd, title, model="", mode="default", origin=None, effort="", session=None, events=None):
        if mode not in MODES:
            raise ValueError("unknown permission mode")
        if not model_ok(model):
            raise ValueError("unknown model")
        if effort not in ("",) + EFFORTS:
            raise ValueError("unknown effort level")
        info = {"id": secrets.token_hex(6), "title": str(title)[:120], "cwd": str(cwd), "created": time.time(),
                "session": session, "model": model, "effort": effort, "mode": mode, "origin": origin, "status": "new"}
        with self.lock:
            self._load()
            self._trim()
            chat = self.chats[info["id"]] = Chat(self, info)
        for event in events or []:
            chat._add(event)
        self.save()
        return chat

    def by_session(self, session):
        with self.lock:
            self._load()
            return next((c for c in self.chats.values() if c.info.get("session") == session), None)

    def _trim(self):
        while len(self.chats) >= CHATS_MAX:
            old = [c for c in self.chats.values() if not c.alive()]
            if not old:
                return
            self.forget(min(old, key=lambda c: c.info.get("created", 0)).info["id"], save=False)

    def forget(self, chat_id, save=True):
        with self.lock:
            chat = self.chats.pop(str(chat_id), None)
        if chat is None:
            raise KeyError("no such chat")
        chat.stop()
        try:
            (self.folder / f"{chat.info['id']}.jsonl").unlink(missing_ok=True)
        except OSError:
            pass
        if save:
            self.save()

    def alive(self):
        with self.lock:
            return [c for c in self.chats.values() if c.alive()]

    def running(self):
        return len(self.alive())

    def stop_all(self):
        with self.lock:
            chats = list(self.chats.values())
        for chat in chats:
            chat.stop()


# ---- what Claude Code says about itself: models, commands, account, usage -----------------------

class Info:
    """Claude Code's own answers, kept a while: initialize (models with their effort levels,
    slash commands, account and plan) for ten minutes, get_usage (the five-hour and weekly
    windows) for one. A running chat answers; with none, a short-lived claude does, without a
    message to any model, so it costs no tokens."""

    INIT_TTL, USAGE_TTL = 600, 60

    def __init__(self):
        self.lock = threading.Lock()
        self.init, self.init_at, self.usage, self.usage_at, self.error = None, 0.0, None, 0.0, None

    def learn(self, meta):
        if isinstance(meta, dict) and meta.get("models") is not None:
            self.init, self.init_at = meta, time.time()

    def get(self, cwd, force=False):
        with self.lock:
            now = time.time()
            want_init = force or not self.init or now - self.init_at > self.INIT_TTL
            want_usage = force or not self.usage or now - self.usage_at > self.USAGE_TTL
            if want_init or want_usage:
                try:
                    self._ask(cwd, want_init, want_usage)
                    self.error = None
                except (OSError, ValueError, RuntimeError) as exc:
                    self.error = str(exc)
            return self.public()

    def _ask(self, cwd, want_init, want_usage):
        live = CHATS.alive()
        if live:
            chat = live[0]
            if want_init:
                self.learn(chat.control({"subtype": "initialize"}, wait=ANSWER_WAIT))
            if want_usage:
                self.usage, self.usage_at = chat.control({"subtype": "get_usage"}, wait=ANSWER_WAIT), time.time()
            return
        answers = probe(cwd, ([{"subtype": "initialize"}] if want_init else []) + ([{"subtype": "get_usage"}] if want_usage else []))
        if want_init:
            self.learn(answers.pop(0))
        if want_usage:
            self.usage, self.usage_at = answers.pop(0), time.time()

    def public(self):
        init = self.init or {}
        account = init.get("account") or {}
        limits = (self.usage or {}).get("rate_limits") or {}
        window = lambda w: {k: w.get(k) for k in ("utilization", "resets_at")} if isinstance(w, dict) else None  # noqa: E731
        return {
            "models": [{k: m.get(k) for k in ("value", "displayName", "description", "resolvedModel", "supportsEffort",
                                              "supportedEffortLevels", "supportsAutoMode", "supportsFastMode")}
                       for m in init.get("models") or [] if isinstance(m, dict) and model_ok(str(m.get("value") or ""))],
            "commands": [{k: c.get(k) for k in ("name", "description", "argumentHint")} for c in init.get("commands") or [] if isinstance(c, dict)],
            "account": {"email": account.get("email"), "plan": account.get("subscriptionType"), "organization": account.get("organization")},
            "mode": init.get("current_permission_mode"),
            "usage": {"five_hour": window(limits.get("five_hour")), "seven_day": window(limits.get("seven_day")),
                      "available": (self.usage or {}).get("rate_limits_available")},
            "usage_at": self.usage_at or None,
            "error": self.error,
        }


def probe(cwd, requests, wait=ANSWER_WAIT):
    """Ask a fresh claude some questions (no message to a model) and close it."""
    argv = command()
    proc = subprocess.Popen(argv, cwd=str(cwd), env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=gitscan.NO_WINDOW)
    asked = Asked()

    def reader():
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "control_response":
                asked.answer(event.get("response") or {})
        asked.close()

    threading.Thread(target=reader, name="claude-probe", daemon=True).start()
    out = []
    try:
        for request in requests:
            rid = secrets.token_hex(8)
            slot = asked.open(rid)
            proc.stdin.write(json.dumps({"type": "control_request", "request_id": rid, "request": request}) + "\n")
            proc.stdin.flush()
            out.append(_awaited(slot, wait, rid, asked))
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
        for pipe in (proc.stdout,):
            try:
                pipe.close()
            except OSError:
                pass
    return out


# ---- the sessions Claude Code keeps on this computer ------------------------------------------

def claude_home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def project_dir(cwd):
    """Where Claude Code keeps a folder's sessions: projects/<the path, each non-alphanumeric as ->."""
    base = claude_home() / "projects"
    name = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    if (base / name).is_dir():
        return base / name
    try:  # the drive letter's case differs between the tools that wrote it
        return next((p for p in base.iterdir() if p.is_dir() and p.name.lower() == name.lower()), None)
    except OSError:
        return None


def _tail_lines(path, size):
    """The last lines of a file, within its last `size` bytes (a session file can be huge)."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        fh.seek(max(0, end - size))
        data = fh.read()
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if end > size else lines  # the first may be cut


_titles = {}


def _title(path):
    """The title Claude Code gave a session (its newest, or yours if you renamed it), else the
    last thing you asked in it. Kept until the file changes."""
    stamp = path.stat().st_mtime
    hit = _titles.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    title, prompt = None, None
    for size in (256 * 1024, 4 * 1024 * 1024):
        for line in reversed(_tail_lines(path, size)):
            if '"custom-title"' not in line and '"ai-title"' not in line and '"last-prompt"' not in line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "custom-title" and event.get("customTitle"):
                title = event["customTitle"]
                break
            if kind == "ai-title" and event.get("aiTitle") and not title:
                title = event["aiTitle"]
            if kind == "last-prompt" and event.get("lastPrompt") and not prompt:
                prompt = event["lastPrompt"]
        if title or prompt:
            break
    found = " ".join(str(title or prompt or "Untitled session").split())[:120]
    _titles[path] = (stamp, found)
    return found


def sessions(cwd, limit=SESSIONS_MAX):
    """A folder's Claude Code sessions, newest first: id, title, last change, size."""
    folder = project_dir(cwd)
    if folder is None:
        return []
    files = []
    for path in folder.glob("*.jsonl"):
        if SESSION.fullmatch(path.stem):
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
    out = []
    for stamp, path in sorted(files, reverse=True)[:limit]:
        try:
            out.append({"id": path.stem, "title": _title(path), "at": stamp, "size": path.stat().st_size})
        except OSError:
            continue
    return out


def history(path, limit=HISTORY_EVENTS):
    """The end of a session as the panel's events: your messages, Claude's answers, its tools."""
    out = []
    for line in _tail_lines(path, HISTORY_BYTES):
        if '"type":"user"' not in line.replace(" ", "") and '"type":"assistant"' not in line.replace(" ", ""):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind not in ("user", "assistant") or event.get("isMeta") or event.get("isSidechain") or event.get("isCompactSummary"):
            continue
        content = (event.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content if isinstance(content, list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind_of = block.get("type")
            if kind_of == "text" and (block.get("text") or "").strip():
                text = block["text"]
                if kind == "user" and text.lstrip().startswith("<"):
                    continue  # what Claude Code adds itself: reminders, command echoes
                out.append({"kind": "user" if kind == "user" else "assistant", "text": _clip(text, MESSAGE_MAX // 10)})
            elif kind_of == "tool_use" and kind == "assistant":
                out.append({"kind": "tool", "id": block.get("id"), "name": block.get("name"), "input": _clip(block.get("input"))})
            elif kind_of == "tool_result" and kind == "user":
                out.append({"kind": "tool_result", "id": block.get("tool_use_id"), "error": bool(block.get("is_error")),
                            "text": _clip(_result_text(block.get("content")))})
    out = out[-limit:]
    if out:
        out.insert(0, {"kind": "system", "text": "Earlier in this session (its end, as Claude Code kept it)"})
    return out


_folders = {"at": 0, "root": None, "list": []}


def folders(root):
    """Where a new chat can work: all your clones, or one of them. The page names one by id.
    Reading the clones runs git once per folder, so the list is kept a minute."""
    if _folders["root"] == str(root) and time.time() - _folders["at"] < 60:
        return _folders["list"]
    out = [{"id": "", "label": "All your clones", "path": str(root)}]
    try:
        out += [{"id": slug, "label": slug, "path": str(path)} for slug, path in sorted(gitscan.repo_dirs(root).items())]
    except OSError:
        pass
    _folders.update(at=time.time(), root=str(root), list=out)
    return out


def _folder(name):
    where = next((f for f in folders(config.workspace_root()) if f["id"] == str(name or "")), None)
    if where is None:
        raise ValueError("pick one of your clones")
    return where


CHATS = Chats()
INFO = Info()


def route(handler, engine, method, name, query, body):
    """The panel's routes; None for any other."""
    if not (name.startswith("chat") or name.startswith("claude-")):
        return None
    q = lambda k: (query.get(k) or [""])[0]  # noqa: E731
    if method == "GET" and name == "chats":
        try:
            program, problem = launcher.claude_binary(), None
        except RuntimeError as exc:
            program, problem = None, str(exc)
        return {"chats": CHATS.list(), "folders": [{k: f[k] for k in ("id", "label")} for f in folders(config.workspace_root())],
                "modes": MODES, "efforts": list(EFFORTS), "program": program, "problem": problem}
    if method == "GET" and name == "chat":
        after = int(q("after")) if q("after").isdigit() else 0
        seen = int(q("live")) if q("live").isdigit() else None
        return CHATS.get(q("id")).read(after, wait=q("wait") == "1", seen=seen)
    if method == "GET" and name == "chat-context":
        return {"context": CHATS.get(q("id")).context()}
    if method == "GET" and name == "claude-info":
        launcher.claude_binary()  # a missing claude says so plainly
        return INFO.get(config.workspace_root(), force=q("refresh") == "1")
    if method == "GET" and name == "claude-sessions":
        where = _folder(q("folder"))
        mine = {c["session"]: c for c in CHATS.list() if c.get("session")}
        return {"folder": where["id"], "sessions": [{**s, "chat": (mine.get(s["id"]) or {}).get("id"),
                                                     "alive": bool((mine.get(s["id"]) or {}).get("alive"))}
                                                    for s in sessions(where["path"])]}
    if method != "POST":
        return None
    if name == "chat-start":
        where = _folder(body.get("folder"))
        chat = CHATS.start(where["path"], body.get("title") or f"Chat · {where['label']}", model=str(body.get("model") or ""),
                           mode=str(body.get("mode") or "default"), effort=str(body.get("effort") or ""))
        if body.get("text"):
            chat.send(body["text"])
        return chat.public()
    if name == "chat-open":
        # A session Claude Code kept: its chat here if it has one, else a new one on its history.
        where = _folder(body.get("folder"))
        session = str(body.get("session") or "")
        found = next((s for s in sessions(where["path"]) if s["id"] == session), None)
        if found is None:
            raise ValueError("no such session in that folder")
        chat = CHATS.by_session(session)
        if chat is None:
            path = project_dir(where["path"]) / f"{session}.jsonl"
            chat = CHATS.start(where["path"], found["title"], session=session, origin={"session": session},
                               model=str(body.get("model") or ""), mode=str(body.get("mode") or "default"),
                               effort=str(body.get("effort") or ""), events=history(path))
        return chat.public()
    chat = CHATS.get(str(body.get("id") or ""))
    if name == "chat-send":
        chat.send(body.get("text"))
    elif name == "chat-answer":
        chat.answer(str(body.get("request") or ""), body.get("allow") is True, body.get("always") is True)
    elif name == "chat-interrupt":
        chat.interrupt()
    elif name == "chat-mode":
        chat.set_mode(str(body.get("mode") or ""))
    elif name == "chat-model":
        chat.set_model(None if body.get("model") is None else str(body.get("model")),
                       None if body.get("effort") is None else str(body.get("effort")))
    elif name == "chat-stop":
        chat.stop()
    elif name == "chat-forget":
        CHATS.forget(chat.info["id"])
        return {"chats": CHATS.list()}
    else:
        return None
    return chat.public()
