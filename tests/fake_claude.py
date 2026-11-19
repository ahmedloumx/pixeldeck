"""A stand-in for `claude -p --input-format stream-json --output-format stream-json
--permission-prompt-tool stdio`, for test_chats.py: it speaks the same JSON lines.

Each message from the user starts a turn: Claude says hello, streams a few words, asks to run a
command and waits for the answer to that question. Allowed, the tool runs and its result comes
back; denied, it says so. The turn ends with a result line. A message that says "deny-me" gets
the same question; a message that says "hook" first asks the host something it cannot answer.
"""

import json
import sys


def say(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    return json.loads(line) if line.strip() else None


ANSWERS = {
    "initialize": {
        "models": [{"value": "default", "displayName": "Default (recommended)", "description": "Opus 5.5 · Best for everyday tasks",
                    "supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsAutoMode": True},
                   {"value": "haiku", "displayName": "Haiku", "description": "Haiku 4.5 · Fastest"},
                   {"value": "bad model; rm -rf", "displayName": "Not a model name"}],
        "commands": [{"name": "compact", "description": "Clear the history but keep a summary", "argumentHint": "<instructions>"},
                     {"name": "review", "description": "Review a pull request", "argumentHint": ""}],
        "account": {"email": "someone@example.com", "subscriptionType": "Claude Pro", "organization": None},
        "current_permission_mode": "default"},
    "get_usage": {"rate_limits_available": True, "rate_limits": {
        "five_hour": {"utilization": 38, "resets_at": "2026-11-19T21:00:00+00:00", "used_dollars": None},
        "seven_day": {"utilization": 79, "resets_at": "2026-11-21T12:00:00+00:00"}}},
    "get_context_usage": {"percentage": 3, "totalTokens": 29976, "maxTokens": 1000000, "categories": []},
}


def ack(message):
    """Every question gets an answer: the canned one when there is one, else an empty success.
    The model asked for last is kept, so a test can read it back."""
    request = message.get("request") or {}
    answer = ANSWERS.get(request.get("subtype"), {})
    if request.get("subtype") == "set_model":
        ANSWERS["get_context_usage"]["model"] = request.get("model")
    if request.get("subtype") == "apply_flag_settings":
        ANSWERS["get_context_usage"]["effort"] = (request.get("settings") or {}).get("effortLevel")
    say({"type": "control_response", "response": {"subtype": "success", "request_id": message["request_id"], "response": answer}})


def answer_to(request_id):
    """The host's answer to one question; an interrupt or a mode change meanwhile is acknowledged."""
    while True:
        message = read()
        if message is None:
            sys.exit(0)
        if message.get("type") == "control_request":
            ack(message)
        elif message.get("type") == "control_response" and message["response"].get("request_id") == request_id:
            return message


say({"type": "system", "subtype": "init", "session_id": "sess-1", "model": "fake-model", "claude_code_version": "9.9.9",
     "permissionMode": "default", "cwd": "."})
turn = 0
while True:
    message = read()
    if message is None:
        break
    if message.get("type") == "control_request":  # interrupt or mode: acknowledged, nothing else
        ack(message)
        continue
    if message.get("type") != "user":
        continue
    turn += 1
    text = message["message"]["content"][0]["text"]
    if "hook" in text:
        say({"type": "control_request", "request_id": f"hook-{turn}", "request": {"subtype": "hook_callback", "callback_id": "x"}})
        reply = answer_to(f"hook-{turn}")
        say({"type": "assistant", "message": {"content": [{"type": "text", "text": f"host said {reply['response']['subtype']}"}]}})
    say({"type": "stream_event", "event": {"type": "message_start"}})
    for word in ("Let ", "me ", "look."):
        say({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": word}}})
    say({"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me look."},
                                                      {"type": "tool_use", "id": f"tool-{turn}", "name": "Bash", "input": {"command": "echo hi"}}]}})
    say({"type": "control_request", "request_id": f"ask-{turn}", "request": {
        "subtype": "can_use_tool", "tool_name": "Bash", "display_name": "Bash", "description": "Print hi",
        "input": {"command": "echo hi"}, "tool_use_id": f"tool-{turn}",
        "permission_suggestions": [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "echo hi"}], "behavior": "allow", "destination": "session"}]}})
    response = answer_to(f"ask-{turn}")["response"]["response"]
    if response.get("behavior") == "allow":
        rules = len(response.get("updatedPermissions") or [])
        say({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"tool-{turn}", "content": "hi", "is_error": False}]}})
        said = f"Ran it ({'always' if rules else 'once'}, tool {response.get('toolUseID')})."
    else:
        say({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"tool-{turn}", "content": response.get("message"), "is_error": True}]}})
        said = "You said no."
    say({"type": "assistant", "message": {"content": [{"type": "text", "text": said}]}})
    say({"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "duration_ms": 1500,
         "total_cost_usd": 0.01, "session_id": "sess-1", "result": said})
