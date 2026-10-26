#!/usr/bin/env python3
"""
test_brain_tiers.py - Jarvis v4 two-speed brain tests (fully stubbed
client - NO network, NO real API key).

Run:  python3 tests/test_brain_tiers.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMPHOME = tempfile.mkdtemp(prefix="jarvis_brain_test_")
os.environ["HOME"] = _TMPHOME
os.makedirs(os.path.join(_TMPHOME, ".openjarvis"), exist_ok=True)
with open(os.path.join(_TMPHOME, ".openjarvis", "free.key"), "w") as f:
    f.write("gsk_test_key_not_real")

import jarvis_brain  # noqa: E402
import jarvis_config  # noqa: E402

PASSED = []


def check(name, cond):
    assert cond, f"FAILED: {name}"
    PASSED.append(name)
    print(f"PASS: {name}")


class _Resp:
    def __init__(self, text):
        self.choices = [type("C", (), {
            "message": type("M", (), {"content": text})()})()]


RECORDED = []


class _FakeCompletions:
    def create(self, model, messages, max_tokens, temperature):
        RECORDED.append({"model": model, "messages": messages})
        return _Resp("stubbed brain answer")


class _FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _FakeCompletions()})()


def main():
    cfg = jarvis_config.load_config()
    jarvis_brain._get_client = lambda provider: _FakeClient()
    jarvis_brain._client = None
    jarvis_brain._client_provider = None
    jarvis_brain._history = None
    jarvis_brain._cooldown_until = 0.0

    # Everyday chat -> FAST tier model first.
    r = jarvis_brain.think("hello my friend, how is your day going")
    check("chat answered by stubbed brain", r == "stubbed brain answer")
    check("chat routed to fast model", RECORDED[-1]["model"] == cfg["fast_model"])
    sysmsg = RECORDED[-1]["messages"][0]["content"]
    check("system prompt carries personal context",
          "Umesh Linga, bioinformatician in Indianapolis" in sysmsg)
    check("system prompt carries the brevity rule",
          "two to four" in sysmsg.lower())

    # Advisory question -> DEEP tier first model.
    r = jarvis_brain.think("what do you think about changing careers at this point")
    check("advisory answered", r == "stubbed brain answer")
    check("advisory routed to deep tier first model",
          RECORDED[-1]["model"] == cfg["groq_models"][0])

    # complete() helper: deep flag picks the deep tier, returns raw text.
    out = jarvis_brain.complete("summarise this page", system="You summarise.", deep=True)
    check("complete() deep returns text", out == "stubbed brain answer")
    check("complete() deep used deep model", RECORDED[-1]["model"] == cfg["groq_models"][0])
    out = jarvis_brain.complete("quick question", deep=False)
    check("complete() fast used fast model", RECORDED[-1]["model"] == cfg["fast_model"])

    # History persisted across turns (30-turn memory).
    hist = jarvis_brain._load_history()
    check("history persisted", len(hist) >= 4)

    print(f"\n{len(PASSED)}/{len(PASSED)} brain-tier tests passed.")


if __name__ == "__main__":
    main()
