#!/usr/bin/env python3
"""
test_agent.py - Jarvis v6 "Muse mode" agent loop + v7 brain split.

    python3 tests/test_agent.py

The brain client is a scripted fake returning canned tool_calls; the
web layer, HUD, and system helpers are stubbed. No network, nothing
opens, nothing is really sent (the iMessage test uses the REAL
confirm-gated send path with a declining confirm).
"""

import os
import sys
import tempfile

_TMP_HOME = tempfile.mkdtemp(prefix="jarvis-test-home-")
os.environ["HOME"] = _TMP_HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json as _json  # noqa: E402

os.makedirs(os.path.join(_TMP_HOME, ".openjarvis"), exist_ok=True)
with open(os.path.join(_TMP_HOME, ".openjarvis", "free.key"), "w") as _f:
    _f.write("gsk_test_key_not_real")
with open(os.path.join(_TMP_HOME, ".openjarvis", "contacts.json"), "w") as _f:
    _json.dump({"Mom": {"phone": "+1 555 010 2030",
                        "email": "mom@example.com"}}, _f)

import jarvis_actions  # noqa: E402
import jarvis_agent  # noqa: E402
import jarvis_brain  # noqa: E402
import jarvis_config  # noqa: E402
import jarvis_hud  # noqa: E402
import jarvis_tasks  # noqa: E402
import jarvis_web  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    PASSED.append(name)
    print(f"PASS: {name}")


# ---------------------------------------------------------------------------
# Scripted fake chat client
# ---------------------------------------------------------------------------

def _msg(content=None, tool_calls=None):
    return type("M", (), {"content": content, "tool_calls": tool_calls})()


def _resp(content=None, tool_calls=None):
    return type("R", (), {"choices": [type("C", (), {
        "message": _msg(content, tool_calls)})()]})()


def _tc(name, args, cid="call_1"):
    import json as _j
    fn = type("F", (), {"name": name, "arguments": _j.dumps(args)})()
    return type("TC", (), {"id": cid, "function": fn})()


class _ScriptedCompletions:
    """Pops one scripted step per create(); Exceptions are raised."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, model=None, messages=None, **kwargs):
        self.calls.append({"model": model, "kwargs": kwargs})
        if not self.script:
            return _resp("script exhausted")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class _ModelRouterCompletions:
    """Routes by model name: handlers map model -> response/Exception."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.models = []

    def create(self, model=None, messages=None, **kwargs):
        self.models.append(model)
        step = self.handlers[model]
        if isinstance(step, Exception):
            raise step
        return step


def _client(completions):
    return type("Client", (), {"chat": type("Chat", (), {
        "completions": completions})()})()


HUD_EVENTS = []
SEARCHED = []


def _install_common_stubs():
    jarvis_hud.emit = lambda kind, text: HUD_EVENTS.append((kind, text))
    jarvis_web.search_and_tell = lambda topic, open_results=False: \
        SEARCHED.append(topic) or f"Here is what I found about {topic}."


# ---------------------------------------------------------------------------
# (i) one tool then final text; (ii) a two-tool chain
# ---------------------------------------------------------------------------

def test_single_and_chain():
    _install_common_stubs()
    jarvis_agent._reset_for_tests()
    HUD_EVENTS.clear()
    SEARCHED.clear()
    comp = _ScriptedCompletions([
        _resp(tool_calls=[_tc("web_search", {"topic": "electric cars"})]),
        _resp("Done - I searched the web for electric cars and found the top results."),
    ])
    jarvis_brain._get_client = lambda provider: _client(comp)
    out = jarvis_agent.run_agent_task("search the web for electric cars",
                                      explicit=True)
    check("agent: tool executed (stubbed web layer)",
          SEARCHED == ["electric cars"], repr(SEARCHED))
    check("agent: final summary returned",
          out is not None and out.startswith("Done - I searched"), repr(out))
    check("agent: tools= was passed to the model",
          comp.calls and "tools" in comp.calls[0]["kwargs"])
    check("agent: step emitted to the HUD",
          any(k == "task" and "Step 1" in t for k, t in HUD_EVENTS),
          repr(HUD_EVENTS))

    HUD_EVENTS.clear()
    SEARCHED.clear()
    comp2 = _ScriptedCompletions([
        _resp(tool_calls=[_tc("web_search", {"topic": "monsoon news"}, "c1")]),
        _resp(tool_calls=[_tc("get_time", {}, "c2")]),
        _resp("Searched the news and checked the time for you."),
    ])
    jarvis_brain._get_client = lambda provider: _client(comp2)
    out = jarvis_agent.run_agent_task("check the news and then the time",
                                      explicit=True)
    check("agent: two-tool chain both ran",
          SEARCHED == ["monsoon news"], repr(SEARCHED))
    check("agent: chain final summary returned",
          out == "Searched the news and checked the time for you.", repr(out))
    steps = [t for k, t in HUD_EVENTS if k == "task"]
    check("agent: two HUD steps, in order",
          len(steps) == 2 and "Step 1" in steps[0] and "Step 2" in steps[1],
          repr(steps))


# ---------------------------------------------------------------------------
# (iii) confirm-declined send: refusal travels back, agent reports honestly
# ---------------------------------------------------------------------------

def test_send_declined():
    _install_common_stubs()
    osa_calls = []
    jarvis_actions._osa = lambda s: osa_calls.append(s) or True
    jarvis_actions._pause = lambda s: None
    # Direct registry check with the REAL confirm-gated send path.
    result = jarvis_agent._run_tool(
        "send_imessage", {"contact": "Mom", "message": "hi"},
        confirm=lambda prompt: False)
    check("agent: declined send -> tool result carries the refusal",
          "did not send" in result.lower(), result)
    check("agent: nothing was actually sent (no osascript ran)",
          osa_calls == [], repr(osa_calls))

    comp = _ScriptedCompletions([
        _resp(tool_calls=[_tc("send_imessage",
                              {"contact": "Mom", "message": "hi"})]),
        _resp("I didn't send it - you said no when I asked."),
    ])
    jarvis_brain._get_client = lambda provider: _client(comp)
    out = jarvis_agent.run_agent_task(
        "do this task: message Mom saying hi", confirm=lambda p: False,
        explicit=True)
    check("agent: final summary admits the message was not sent",
          out is not None and "didn't send" in out, repr(out))


# ---------------------------------------------------------------------------
# (iv) tools rejected everywhere -> None (caller degrades to plain chat)
# ---------------------------------------------------------------------------

def test_tools_rejected_degrades():
    _install_common_stubs()
    jarvis_agent._reset_for_tests()
    comp = _ScriptedCompletions(
        [Exception("tools are not supported by this model")] * 12)
    jarvis_brain._get_client = lambda provider: _client(comp)
    out = jarvis_agent.run_agent_task("search for something", explicit=True)
    check("agent: all models reject tools -> None (degrade)",
          out is None, repr(out))
    models = jarvis_brain._models_for("groq", tier="deep")
    check("agent: rejecting models are latched for the session",
          all(("groq", m) in jarvis_agent._tools_unsupported for m in models),
          repr(jarvis_agent._tools_unsupported))
    jarvis_agent._reset_for_tests()


# ---------------------------------------------------------------------------
# (v) max-steps cutoff summarises instead of looping forever
# ---------------------------------------------------------------------------

def test_max_steps_cutoff():
    _install_common_stubs()
    jarvis_agent._reset_for_tests()
    cfg = jarvis_config.load_config(force_reload=True)
    cfg["agent_max_steps"] = 3
    try:
        script = [_resp(tool_calls=[_tc("get_time", {}, f"c{i}")])
                  for i in range(3)]
        script.append(_resp("I checked the time three times and stopped there."))
        comp = _ScriptedCompletions(script)
        jarvis_brain._get_client = lambda provider: _client(comp)
        HUD_EVENTS.clear()
        out = jarvis_agent.run_agent_task("keep checking the time",
                                          explicit=True)
        steps = [t for k, t in HUD_EVENTS if k == "task"]
        check("agent: cutoff stops at agent_max_steps",
              len(steps) == 3, repr(steps))
        check("agent: cutoff still summarises",
              out == "I checked the time three times and stopped there.",
              repr(out))
    finally:
        cfg["agent_max_steps"] = 8


# ---------------------------------------------------------------------------
# (vi) trigger detection table
# ---------------------------------------------------------------------------

def test_triggers():
    _install_common_stubs()
    cfg = jarvis_config.load_config(force_reload=True)
    cfg["agent_enabled"] = True
    real_run = jarvis_agent.run_agent_task
    goals = []

    def fake_run(goal, confirm=None, explicit=False):
        goals.append((goal, explicit))
        return "AGENTED"

    jarvis_agent.run_agent_task = fake_run
    try:
        check("trigger: 'do this task:' prefix",
              jarvis_agent.maybe_agent("do this task: search for news") == "AGENTED"
              and goals[-1] == ("search for news", True), repr(goals))
        check("trigger: 'agent' prefix",
              jarvis_agent.maybe_agent("agent look up the weather story") == "AGENTED")
        check("trigger: 'and then' multi-step",
              jarvis_agent.maybe_agent(
                  "open chrome and then search for news") == "AGENTED")
        check("trigger: comma + second verb",
              jarvis_agent.maybe_agent(
                  "search for flights, then book the cheapest") == "AGENTED")
        check("no trigger: 'what time is it' stays a plain question",
              jarvis_agent.maybe_agent("what time is it") is None)
        check("no trigger: plain chat stays chat",
              jarvis_agent.maybe_agent("tell me a joke") is None)
        check("no trigger: comma without a command verb",
              jarvis_agent.maybe_agent(
                  "hello my friend, how is your day going") is None)
        check("no trigger: bare prefix with no goal",
              jarvis_agent.maybe_agent("do this task") is None)
        cfg["agent_enabled"] = False
        check("no trigger: agent_enabled=False disables even explicit asks",
              jarvis_agent.maybe_agent("do this task: search for news") is None)
        cfg["agent_enabled"] = True
    finally:
        jarvis_agent.run_agent_task = real_run

    # Background goals ride jarvis_tasks and announce on completion.
    ran = []
    jarvis_agent.run_agent_task = lambda goal, confirm=None, explicit=False: \
        ran.append(goal) or "Background result."
    try:
        out = jarvis_agent.maybe_agent(
            "do this task: tidy my downloads summary in the background")
        check("background: returns the started line",
              out is not None and "background task" in out, repr(out))
        jarvis_tasks.wait_all(timeout=5)
        check("background: the task ran with the phrase stripped",
              ran == ["tidy my downloads summary"], repr(ran))
    finally:
        jarvis_agent.run_agent_task = real_run


# ---------------------------------------------------------------------------
# Part 7: brain split - groq-only first, then with an OpenAI key
# ---------------------------------------------------------------------------

def test_brain_split():
    check("split: groq-only env -> deep providers are just groq",
          jarvis_brain.deep_providers_available() == ["groq"],
          repr(jarvis_brain.deep_providers_available()))
    check("split: groq deep list is headed by the bigger gpt-oss-120b",
          jarvis_brain._models_for("groq", tier="deep")[0]
          == "openai/gpt-oss-120b")
    check("split: fast chat order unchanged (groq first)",
          jarvis_brain.providers_available()[0] == "groq")

    # Give him an OpenAI key: deep/agent work upgrades automatically.
    with open(os.path.join(_TMP_HOME, ".openjarvis", "openai.key"), "w") as f:
        f.write("sk-test-not-real")
    cfg = jarvis_config.load_config(force_reload=True)
    cfg["deep_provider"] = "auto"
    check("split: openai key present -> deep providers start with openai",
          jarvis_brain.deep_providers_available()[0] == "openai",
          repr(jarvis_brain.deep_providers_available()))
    check("split: openai model list is gpt-4.1 then the mini fallback",
          jarvis_brain._models_for("openai", tier="deep")
          == ["gpt-4.1", "gpt-4.1-mini"],
          repr(jarvis_brain._models_for("openai", tier="deep")))
    check("split: fast tier still starts on groq",
          jarvis_brain.providers_available()[0] == "groq")

    # Agent model selection: gpt-4.1 errors -> falls to gpt-4.1-mini.
    jarvis_agent._reset_for_tests()
    router = _ModelRouterCompletions({
        "gpt-4.1": Exception("server error from the big model"),
        "gpt-4.1-mini": _resp("Handled by the fallback model."),
        "openai/gpt-oss-120b": _resp("should not reach groq"),
    })
    jarvis_brain._get_client = lambda provider: _client(router)
    out = jarvis_agent.run_agent_task("do a small task", explicit=True)
    check("split: agent started on openai gpt-4.1",
          router.models and router.models[0] == "gpt-4.1", repr(router.models))
    check("split: agent fell back to gpt-4.1-mini on error",
          len(router.models) > 1 and router.models[1] == "gpt-4.1-mini"
          and out == "Handled by the fallback model.",
          repr((router.models, out)))

    # complete(deep=True) uses the deep provider order too.
    recorded = []

    class _Rec:
        def create(self, model=None, messages=None, **kw):
            recorded.append(model)
            return _resp("deep answer")

    jarvis_brain._get_client = lambda provider: _client(_Rec())
    jarvis_brain._cooldown_until = 0.0
    out = jarvis_brain.complete("summarise", deep=True)
    check("split: complete(deep=True) ran on openai gpt-4.1 first",
          out == "deep answer" and recorded == ["gpt-4.1"], repr(recorded))


def main():
    test_single_and_chain()
    test_send_declined()
    test_tools_rejected_degrades()
    test_max_steps_cutoff()
    test_triggers()
    test_brain_split()
    print(f"\n{len(PASSED)}/{len(PASSED)} agent tests passed.")


if __name__ == "__main__":
    main()
