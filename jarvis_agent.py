"""
jarvis_agent.py - Jarvis v6 "Muse mode": a tool-using agent loop.

Owner's ask: "Jarvis should work like Muse works — the same way,
inside my computer." So instead of only routing single commands, Jarvis
can take a GOAL, plan silently, and chain real tools on this Mac —
open apps, search the web, read pages and files, write and run code,
send messages (still confirm-gated) — verifying each step from the
tool's own result, then report back in 2-3 spoken sentences.

Entry points:
- explicit: "do this task ...", "jarvis task ...", "work on this ...",
  "agent ..." / "task ..." prefixes;
- implicit: a multi-step utterance ("... and then ...", or a comma
  followed by a second command verb) that normal routing did NOT
  handle — brain.think calls maybe_agent() after local actions and
  before plain chat;
- "in the background" anywhere in the goal runs it as a jarvis_tasks
  background task whose completion announces itself (v4 pattern).

Bounds: config agent_max_steps tool steps (default 8) and a ~120s soft
timeout, checked between steps; then it stops and summarises honestly.
No shell tool. No delete/move tools (the confirm-gated text routes
already cover those). Every tool is wrapped: failures come back as
"ERROR: ..." tool results so the agent can adapt instead of crashing.
"""

import datetime
import json
import re
import time

import jarvis_brain
import jarvis_config
import jarvis_hud

# Models that rejected the tools= parameter this session: (provider, model).
_tools_unsupported = set()

AGENT_SYSTEM = """You are Jarvis, acting as an agent inside Umesh's Mac to carry out a goal he gave you by voice. You work like a careful operator: plan silently, then use the tools to DO the goal step by step. Read every tool result before deciding the next step. Never claim something worked unless a tool result confirms it - if a tool returns an ERROR, adapt (different tool, different wording) or stop and report honestly. Sends and code runs ask Umesh for confirmation by themselves; if a tool result says he declined or that it was not sent/run, respect that and stop that branch. Your final message is SPOKEN aloud to him: two or three short sentences maximum, plain words, no tool names, no jargon - say what was done and the result. If the goal could not be completed, say what got done and what is missing, honestly."""

_MAX_SECONDS = 120


def _hud_step(text):
    try:
        jarvis_hud.emit("task", text)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tool registry: thin, typed wrappers over existing Jarvis functions.
# Every callable takes (args_dict, confirm) and returns a result string.
# ---------------------------------------------------------------------------

def _t_open_app(a, confirm):
    import jarvis_actions
    name = str(a.get("name") or "").strip()
    if not name:
        return "ERROR: open_app needs a name"
    result = jarvis_actions.try_action(f"open {name}")
    return result or f"ERROR: could not open {name}"


def _t_open_url(a, confirm):
    import jarvis_web
    url = str(a.get("url") or "").strip()
    if not url:
        return "ERROR: open_url needs a url"
    return f"Opened {url}." if jarvis_web.open_url(url) else \
        f"ERROR: the browser did not open {url}"


def _t_web_search(a, confirm):
    import jarvis_web
    return jarvis_web.search_and_tell(str(a.get("topic") or ""))


def _t_job_search(a, confirm):
    import jarvis_web
    return jarvis_web.job_search(str(a.get("query") or ""))


def _t_read_website(a, confirm):
    import jarvis_web
    return jarvis_web.read_website(str(a.get("url") or ""))


def _t_read_file(a, confirm):
    import jarvis_read
    return jarvis_read.read_file(str(a.get("name") or ""))


def _t_create_code_file(a, confirm):
    import jarvis_code
    return jarvis_code.create_code(str(a.get("name") or ""),
                                   str(a.get("task_description") or ""))


def _t_run_code_file(a, confirm):
    import jarvis_code
    return jarvis_code.run_last_code(confirm=confirm)


def _t_send_imessage(a, confirm):
    import jarvis_actions
    return jarvis_actions.send_imessage(str(a.get("contact") or ""),
                                        str(a.get("message") or ""),
                                        confirm=confirm)


def _t_type_text(a, confirm):
    import jarvis_actions
    text = str(a.get("text") or "")
    if not text:
        return "ERROR: type_text needs text"
    ok = jarvis_actions._osa(
        f'tell application "System Events" to keystroke '
        f'"{jarvis_actions._asq(text)}"')
    return f"Typed: {text}" if ok else "ERROR: typing failed (Accessibility?)"


_KEY_CODES = {"enter": 36, "return": 36, "tab": 48, "escape": 53, "esc": 53,
              "space": 49, "delete": 51, "backspace": 51, "up": 126,
              "down": 125, "left": 123, "right": 124}


def _t_press_key(a, confirm):
    import jarvis_actions
    key = str(a.get("key") or "").strip().lower()
    code = _KEY_CODES.get(key)
    if code is None:
        return f"ERROR: unknown key {key!r}"
    ok = jarvis_actions._osa(
        f'tell application "System Events" to key code {code}')
    return f"Pressed {key}." if ok else f"ERROR: could not press {key}"


def _t_click_element(a, confirm):
    import jarvis_actions
    return jarvis_actions.click_named(str(a.get("name") or ""))


def _t_set_reminder(a, confirm):
    import jarvis_actions
    text = str(a.get("text") or "").strip()
    if not text:
        return "ERROR: set_reminder needs text"
    when = str(a.get("when") or "")
    due = jarvis_actions._due_datetime(when) if when else None
    return jarvis_actions.create_reminder(text, due)


def _t_take_screenshot(a, confirm):
    import jarvis_actions
    return jarvis_actions.take_screenshot()


def _t_get_time(a, confirm):
    return "It is currently " + datetime.datetime.now().strftime("%I:%M %p")


def _t_battery_status(a, confirm):
    import jarvis_actions
    return jarvis_actions.battery_status()


_REGISTRY = {
    "open_app": _t_open_app,
    "open_url": _t_open_url,
    "web_search": _t_web_search,
    "job_search": _t_job_search,
    "read_website": _t_read_website,
    "read_file": _t_read_file,
    "create_code_file": _t_create_code_file,
    "run_code_file": _t_run_code_file,
    "send_imessage": _t_send_imessage,
    "type_text": _t_type_text,
    "press_key": _t_press_key,
    "click_element": _t_click_element,
    "set_reminder": _t_set_reminder,
    "take_screenshot": _t_take_screenshot,
    "get_time": _t_get_time,
    "battery_status": _t_battery_status,
}


def _schema(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_S = {"type": "string"}
TOOLS = [
    _schema("open_app", "Open an app, website, or folder on the Mac by name.",
            {"name": _S}, ["name"]),
    _schema("open_url", "Open a full URL in the default browser.",
            {"url": _S}, ["url"]),
    _schema("web_search", "Search the web for a topic; returns the top findings as text.",
            {"topic": _S}, ["topic"]),
    _schema("job_search", "Search for jobs (opens LinkedIn Jobs results too); the query should name role and place.",
            {"query": _S}, ["query"]),
    _schema("read_website", "Fetch a web page and return a spoken-style summary of it.",
            {"url": _S}, ["url"]),
    _schema("read_file", "Find a file on the Mac by name and return a summary of its contents.",
            {"name": _S}, ["name"]),
    _schema("create_code_file", "Write a new Python file in the code sandbox from a task description.",
            {"name": _S, "task_description": _S}, ["name", "task_description"]),
    _schema("run_code_file", "Run the most recently created code file. Asks the owner to confirm first; if he declines, the result says so.",
            {}, []),
    _schema("send_imessage", "Send an iMessage to a contact. Asks the owner to confirm first; if he declines, the result says it was not sent.",
            {"contact": _S, "message": _S}, ["contact", "message"]),
    _schema("type_text", "Type text into the currently focused field/app.",
            {"text": _S}, ["text"]),
    _schema("press_key", "Press one key: enter, tab, escape, space, delete, up, down, left, right.",
            {"key": _S}, ["key"]),
    _schema("click_element", "Click a visible UI element by its name or title in the front app.",
            {"name": _S}, ["name"]),
    _schema("set_reminder", "Create a reminder. 'when' may be like 'tomorrow at 6 pm' or empty.",
            {"text": _S, "when": _S}, ["text"]),
    _schema("take_screenshot", "Take a screenshot of the Mac screen.", {}, []),
    _schema("get_time", "Get the current local time.", {}, []),
    _schema("battery_status", "Get the Mac battery level and charging state.", {}, []),
]

_DESCRIBE_ARG = {"open_app": "name", "open_url": "url", "web_search": "topic",
                 "job_search": "query", "read_website": "url",
                 "read_file": "name", "create_code_file": "name",
                 "send_imessage": "contact", "type_text": "text",
                 "click_element": "name", "set_reminder": "text"}


def _describe(step_no, name, args):
    arg = ""
    key = _DESCRIBE_ARG.get(name)
    if key and args.get(key):
        arg = f" {args[key]}"
    human = name.replace("_", " ")
    return f"Step {step_no}: {human}{arg}"


def _run_tool(name, args, confirm):
    """Run one registry tool; failures become ERROR strings, never raises."""
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"ERROR: unknown tool {name}"
    try:
        return str(fn(args or {}, confirm))
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

_TRY_NEXT_MODEL = object()


def _is_tools_rejection(e):
    s = str(e).lower()
    status = getattr(e, "status_code", None)
    return "tool" in s or "tool_choice" in s or status == 400


def _assistant_tool_msg(msg, tool_calls):
    tcs = []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        tcs.append({
            "id": getattr(tc, "id", "call_0"),
            "type": "function",
            "function": {
                "name": getattr(fn, "name", "") if fn else "",
                "arguments": (getattr(fn, "arguments", "") or "{}") if fn else "{}",
            },
        })
    return {"role": "assistant",
            "content": getattr(msg, "content", None) or "",
            "tool_calls": tcs}


def _summarise(client, model, msgs, steps_log):
    """One no-tools call asking for the final spoken summary."""
    try:
        msgs2 = list(msgs) + [{
            "role": "user",
            "content": ("Time to report back. In two or three short "
                        "spoken sentences, say what you got done and "
                        "what, if anything, is still missing. Plain "
                        "words, no tool names."),
        }]
        resp = client.chat.completions.create(
            model=model, messages=msgs2, max_tokens=300, temperature=0.4)
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception:
        return None


def _run_loop(client, provider, model, goal, confirm, max_steps, explicit):
    msgs = [{"role": "system", "content": AGENT_SYSTEM},
            {"role": "user", "content": goal}]
    steps_log = []
    steps = 0
    started = time.time()
    while steps < max_steps:
        if time.time() - started > _MAX_SECONDS:
            break
        try:
            resp = client.chat.completions.create(
                model=model, messages=msgs, tools=TOOLS,
                tool_choice="auto", max_tokens=800, temperature=0.3)
        except Exception as e:
            if steps == 0:
                # Any step-0 failure: try the next model. Only a tools
                # rejection latches - a dead model is the brain's latch.
                if _is_tools_rejection(e):
                    _tools_unsupported.add((provider, model))
                return _TRY_NEXT_MODEL
            break
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            final = (getattr(msg, "content", None) or "").strip()
            if not explicit and steps == 0:
                # Implicit trigger: the agent must ACT, not chat. A
                # text-only first move means this wasn't a task after
                # all - let normal chat handle it.
                return None
            if final:
                return final
            break
        msgs.append(_assistant_tool_msg(msg, tool_calls))
        for tc in tool_calls:
            if steps >= max_steps or time.time() - started > _MAX_SECONDS:
                break
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", "") if fn else ""
            try:
                args = json.loads((getattr(fn, "arguments", "") or "{}")
                                  if fn else "{}")
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            steps += 1
            _hud_step(_describe(steps, name, args))
            result = _run_tool(name, args, confirm)
            steps_log.append((name, result))
            msgs.append({"role": "tool",
                         "tool_call_id": getattr(tc, "id", f"call_{steps}"),
                         "content": result})
    summary = _summarise(client, model, msgs, steps_log)
    if summary:
        return summary
    if steps_log:
        names = ", ".join(n for n, _ in steps_log)
        return (f"I worked on it and took {len(steps_log)} steps ({names}), "
                f"but I couldn't finish everything. It may help to split "
                f"the task into smaller pieces.")
    return None


def run_agent_task(goal, confirm=None, explicit=False):
    """
    Run the agent loop for `goal`. Returns the final spoken summary, or
    None when the agent is disabled, no provider exists, or no model
    accepts tools - the caller then degrades to normal chat.
    """
    try:
        cfg = jarvis_config.load_config()
        if not cfg.get("agent_enabled", True):
            return None
        max_steps = int(cfg.get("agent_max_steps", 8))
    except Exception:
        return None
    providers = jarvis_brain.deep_providers_available()
    if not providers:
        return None
    for provider in providers:
        for model in jarvis_brain._models_for(provider, tier="deep"):
            if (provider, model) in _tools_unsupported:
                continue
            try:
                client = jarvis_brain._get_client(provider)
            except Exception:
                continue
            result = _run_loop(client, provider, model, goal, confirm,
                               max_steps, explicit)
            if result is _TRY_NEXT_MODEL:
                continue
            return result
    return None


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

_EXPLICIT_PREFIXES = ("do this task", "jarvis task", "work on this",
                      "agent", "task")

_SECOND_VERBS = {
    "open", "search", "find", "read", "create", "write", "send", "type",
    "click", "play", "set", "remind", "check", "look", "tell", "add",
    "book", "download", "call", "save", "make", "run", "fix",
}


def _explicit_goal(q):
    """The goal text after an explicit agent prefix, '' for a bare
    prefix, or None when the utterance has no agent prefix."""
    text = (q or "").strip()
    low = text.lower()
    for prefix in _EXPLICIT_PREFIXES:
        if low == prefix:
            return ""
        if low.startswith(prefix) and len(low) > len(prefix) \
                and low[len(prefix)] in " :,-":
            return text[len(prefix):].strip(" :,-")
    return None


def _looks_multi_step(q):
    low = (q or "").lower()
    if " and then " in low:
        return True
    if "," in low:
        after = low.split(",", 1)[1]
        return any(re.search(r"\b" + v + r"\b", after) for v in _SECOND_VERBS)
    return False


def maybe_agent(q, confirm=None):
    """
    Entry point used by jarvis_brain.think (after local actions, before
    plain chat). Returns a final summary string when the utterance
    triggered agent mode and the agent produced one; None otherwise.
    """
    try:
        cfg = jarvis_config.load_config()
        if not cfg.get("agent_enabled", True):
            return None
        goal = _explicit_goal(q)
        explicit = goal is not None
        if not explicit:
            if not _looks_multi_step(q):
                return None
            goal = (q or "").strip()
        if not goal:
            return None
        if "in the background" in goal.lower():
            g = re.sub(r"(?i)\bin the background\b", "", goal).strip(" ,")
            if g:
                import jarvis_tasks
                jarvis_tasks.start(
                    f"Agent task: {g[:40]}",
                    lambda: run_agent_task(g, confirm=confirm,
                                           explicit=explicit))
                return ("Started that as a background task - I'll speak "
                        "up when it's done. Ask me anything else "
                        "meanwhile.")
        return run_agent_task(goal, confirm=confirm, explicit=explicit)
    except Exception:
        return None


def _reset_for_tests():
    _tools_unsupported.clear()
