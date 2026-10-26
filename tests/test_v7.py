#!/usr/bin/env python3
"""
test_v7.py - Jarvis v7 "One Icon" tests, plain-python runnable:
    python3 tests/test_v7.py

Covers: key-shape guards + the poisoned-slot self-heal (his Groq key
sitting in openai.key must be IGNORED, never sent to OpenAI), the error
scrubber, per-rung TTS diagnostics + voice_probe, the hud_auto_open ->
hud_auto_open_browser rename (old saved key ignored, default False),
the native-window state model (headless-safe), the --doctor smoke test,
and installer statics (bash -n, wrong-key rejection lines, Jarvis.app).

No network, no mic, no display: SDK clients are stubbed and the doctor
runs in a fresh temp HOME.
"""

import io
import os
import subprocess
import sys
import tempfile
import types as _types
from contextlib import redirect_stdout

_TMP_HOME = tempfile.mkdtemp(prefix="jarvis-v7-home-")
os.environ["HOME"] = _TMP_HOME
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import jarvis_brain  # noqa: E402
import jarvis_config  # noqa: E402
import jarvis_hud  # noqa: E402
import jarvis_voice  # noqa: E402
import jarvis_window  # noqa: E402

PASSED = []


def check(name, cond, extra=""):
    assert cond, f"FAILED: {name} {extra}"
    PASSED.append(name)
    print(f"PASS: {name}")


def _oj(*parts):
    return os.path.join(_TMP_HOME, ".openjarvis", *parts)


def _write(name, content):
    os.makedirs(_oj(), exist_ok=True)
    with open(_oj(name), "w") as f:
        f.write(content)


class _Resp:
    def __init__(self, text):
        self.choices = [_types.SimpleNamespace(
            message=_types.SimpleNamespace(content=text))]


def main():
    # =====================================================================
    # A1: key-shape validators
    # =====================================================================
    check("groq prefix: gsk_ accepted", jarvis_brain.valid_key_prefix("groq", "gsk_abc123"))
    check("groq prefix: sk- rejected", not jarvis_brain.valid_key_prefix("groq", "sk-abc123"))
    check("openai prefix: sk- accepted", jarvis_brain.valid_key_prefix("openai", "sk-abc123"))
    check("openai prefix: sk-proj- accepted", jarvis_brain.valid_key_prefix("openai", "sk-proj-xyz"))
    check("openai prefix: sk-svcacct- accepted", jarvis_brain.valid_key_prefix("openai", "sk-svcacct-9"))
    check("openai prefix: gsk_ rejected", not jarvis_brain.valid_key_prefix("openai", "gsk_xyz"))
    check("empty key rejected", not jarvis_brain.valid_key_prefix("groq", ""))

    # =====================================================================
    # B-probe, phase 1: no keys at all -> rungs report "not configured"
    # =====================================================================
    probe = dict()
    for level, text in jarvis_voice.voice_probe():
        probe[text.split(":")[0]] = (level, text)
    check("probe: groq rung not configured without a key",
          any("not configured" in t and "Groq" in t for _l, t in probe.values()))
    check("probe: openai rung not configured without a key",
          any("not configured" in t and "OpenAI" in t for _l, t in probe.values()))
    check("probe: say rung always reported",
          any("macOS say" in t for _l, t in probe.values()))

    # =====================================================================
    # A2: THE POISONED SLOT - openai.key holds a Groq key (his v6 Mac)
    # =====================================================================
    _write("openai.key", "gsk_poisoned_groq_key_value")
    check("poisoned: openai_key() returns None", jarvis_brain.openai_key() is None)
    check("poisoned: providers_available excludes openai",
          jarvis_brain.providers_available() == [])
    check("poisoned: deep providers also exclude openai",
          jarvis_brain.deep_providers_available() == [])
    check("poisoned: slot state is wrong-key",
          jarvis_brain.key_slot_state("openai") == "wrong-key")
    check("poisoned: groq slot state is absent",
          jarvis_brain.key_slot_state("groq") == "absent")
    notes = jarvis_brain.key_notes()
    check("poisoned: the exact plain note is recorded once",
          notes.count("Note: the OpenAI slot held a Groq key - ignored it; "
                      "Jarvis is Groq-only, as you chose.") == 1,
          repr(notes))

    # With a valid Groq key added, deep work must use Groq ONLY - the
    # poisoned OpenAI slot is never even handed to the client factory.
    _write("free.key", "gsk_valid_test_key")
    factory_calls = []

    class _DeepCompletions:
        def create(self, model, messages, max_tokens, temperature):
            return _Resp("deep answer")

    class _DeepClient:
        def __init__(self):
            self.chat = _types.SimpleNamespace(completions=_DeepCompletions())

    _real_factory = jarvis_brain._get_client

    def _counting_factory(provider):
        factory_calls.append(provider)
        return _DeepClient()

    jarvis_brain._get_client = _counting_factory
    jarvis_brain._client = None
    jarvis_brain._client_provider = None
    try:
        out = jarvis_brain.complete("think hard about this", deep=True)
    finally:
        jarvis_brain._get_client = _real_factory
    check("poisoned: deep complete() answered via groq", out == "deep answer")
    check("poisoned: client factory only ever built a groq client",
          factory_calls == ["groq"], repr(factory_calls))
    check("healed: providers_available is groq-only",
          jarvis_brain.providers_available() == ["groq"])
    check("healed: slot state groq ok", jarvis_brain.key_slot_state("groq") == "ok")

    # Banner in a real process: Groq-only brain line + the note, and NO
    # phantom "OpenAI deep/agent" split.
    env = dict(os.environ, HOME=_TMP_HOME)
    r = subprocess.run([sys.executable, "jarvis.py", "--once",
                        "what time is it", "--no-speak"],
                       cwd=_REPO, env=env, capture_output=True, text=True,
                       timeout=60)
    check("banner process exits 0", r.returncode == 0, r.stderr[-400:])
    check("banner says JARVIS v7", "JARVIS v7" in r.stdout)
    check("banner brain line is Groq (cloud)", "Brain : Groq (cloud)" in r.stdout)
    check("banner has NO phantom OpenAI split",
          "OpenAI deep/agent" not in r.stdout)
    check("banner prints the wrong-key note",
          "Note: the OpenAI slot held a Groq key - ignored it" in r.stdout)

    # =====================================================================
    # B: error scrubber - key material never survives
    # =====================================================================
    scrubbed = jarvis_voice._scrub_error(
        "Error code: 401 - Incorrect API key provided: "
        "gsk_r2Nj1234567890E9Hb - see platform.openai.com")
    check("scrubber strips the groq key body",
          "r2Nj1234567890" not in scrubbed and "1234567890" not in scrubbed)
    scrubbed2 = jarvis_voice._scrub_error("auth failed for sk-proj-abcdef123456789")
    check("scrubber strips the openai key body", "abcdef123456789" not in scrubbed2)
    check("scrubber truncates", len(jarvis_voice._scrub_error("x" * 400)) <= 91)

    # =====================================================================
    # B: per-rung TTS diagnostics - BOTH failures named, then the final line
    # =====================================================================
    _real_voice_groq = jarvis_voice._groq_key
    _real_voice_openai = jarvis_voice._openai_key
    _real_tts_client = jarvis_voice._tts_client
    jarvis_voice._groq_key = lambda: "gsk_test"
    jarvis_voice._openai_key = lambda: "sk-test"

    def _boom_client(provider, timeout=30.0):
        raise RuntimeError("Error code: 401 - bad credential gsk_leakme123456")

    jarvis_voice._tts_client = _boom_client
    jarvis_voice._tts_neural_broken = False
    jarvis_voice._tts_rungs_logged = set()
    jarvis_voice._tts_final_logged = False
    buf = io.StringIO()
    with redirect_stdout(buf):
        ok = jarvis_voice._speak_neural("Hello there.")
    out_log = buf.getvalue()
    check("neural chain reports failure", ok is False)
    check("groq rung failure is logged by name",
          "[Voice: Groq Orpheus failed (" in out_log, out_log)
    check("openai rung failure is logged by name",
          "[Voice: OpenAI voice failed (" in out_log, out_log)
    check("final Mac-voice line is logged",
          "[Voice: using Mac voice for this session]" in out_log)
    check("no key material in the rung log", "leakme123456" not in out_log)
    check("neural latch engaged after total failure",
          jarvis_voice._tts_neural_broken is True)

    # Probe with a working stubbed client + the poisoned openai slot:
    # groq rung PASS, openai rung FAIL "key invalid".
    class _ProbeSpeech:
        def create(self, **kw):
            return _types.SimpleNamespace(content=b"RIFF-fake-wav")

    class _ProbeClient:
        def __init__(self):
            self.audio = _types.SimpleNamespace(speech=_ProbeSpeech())

    jarvis_voice._tts_client = lambda provider, timeout=30.0: _ProbeClient()
    probe2 = jarvis_voice.voice_probe()
    check("probe: groq rung ok with a working client",
          any(l == "PASS" and "Groq Orpheus" in t and "ok" in t
              for l, t in probe2), repr(probe2))
    check("probe: poisoned openai rung is key-invalid",
          any(l == "FAIL" and "OpenAI" in t and "key invalid" in t
              for l, t in probe2), repr(probe2))
    jarvis_voice._tts_client = _real_tts_client
    jarvis_voice._groq_key = _real_voice_groq
    jarvis_voice._openai_key = _real_voice_openai
    jarvis_voice._tts_neural_broken = False
    jarvis_voice._tts_rungs_logged = set()
    jarvis_voice._tts_final_logged = False

    # =====================================================================
    # D1: hud_auto_open_browser - default False, the OLD key is ignored
    # =====================================================================
    check("config default: hud_auto_open_browser is False",
          jarvis_config.DEFAULTS.get("hud_auto_open_browser") is False)
    check("config default: old hud_auto_open key retired",
          "hud_auto_open" not in jarvis_config.DEFAULTS)

    opened = []
    _real_is_running = jarvis_hud.is_running
    _real_open_hud = jarvis_hud.open_hud
    jarvis_hud.is_running = lambda: True
    jarvis_hud.open_hud = lambda: opened.append(1) or True
    try:
        _write("config.json", '{"hud_auto_open": true}')
        jarvis_config.load_config(force_reload=True)
        jarvis_voice._maybe_open_hud()
        check("old saved hud_auto_open=true does NOT open the browser",
              opened == [])
        _write("config.json", '{"hud_auto_open_browser": true}')
        jarvis_config.load_config(force_reload=True)
        jarvis_voice._maybe_open_hud()
        check("new key true opens the browser (explicit opt-in)",
              opened == [1])
    finally:
        jarvis_hud.is_running = _real_is_running
        jarvis_hud.open_hud = _real_open_hud
        _write("config.json", "{}")
        jarvis_config.load_config(force_reload=True)

    # =====================================================================
    # D2: the native window's state model (headless-safe)
    # =====================================================================
    jarvis_window._reset_for_tests()
    check("window availability is a bool",
          isinstance(jarvis_window.available(), bool))
    check("window not active headlessly", jarvis_window.is_active() is False)
    jarvis_window.set_state(state="speaking", heard="open github",
                            spoken="Opening GitHub.")
    st = jarvis_window.get_state()
    check("window model records state/heard/spoken",
          st["state"] == "speaking" and st["heard"] == "open github"
          and st["spoken"] == "Opening GitHub.")
    jarvis_window.show_alert("Message from Mom: running late")
    st = jarvis_window.get_state()
    check("show_alert records the alert + alert state (no window needed)",
          st["alert"] == "Message from Mom: running late" and st["state"] == "alert")
    jarvis_window.queue_command("what did I miss")
    check("window command queue drains in order",
          jarvis_window.poll_commands() == ["what did I miss"])
    check("window command queue empties", jarvis_window.poll_commands() == [])
    jarvis_window.request_quit()
    check("quit request latches", jarvis_window.quit_requested() is True)
    jarvis_window._reset_for_tests()
    check("quit resets for tests", jarvis_window.quit_requested() is False)

    # No tkinter at all: import must degrade to safe no-ops.
    code = (
        "import sys; sys.modules['tkinter'] = None\n"
        "import jarvis_window as w\n"
        "assert w.available() is False\n"
        "w.set_state(state='listening', heard='x', spoken='y')\n"
        "w.show_alert('hi')\n"
        "w.queue_command('what did I miss')\n"
        "assert w.poll_commands() == ['what did I miss']\n"
        "assert w.run_window() is False\n"
        "print('no-tk OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=_REPO,
                       env=dict(os.environ, HOME=_TMP_HOME),
                       capture_output=True, text=True, timeout=60)
    check("window module is a safe no-op without tkinter",
          r.returncode == 0 and "no-tk OK" in r.stdout, r.stderr[-300:])

    # =====================================================================
    # C: --doctor smoke test in a fresh temp HOME (no keys)
    # =====================================================================
    fresh = tempfile.mkdtemp(prefix="jarvis-v7-doctor-")
    r = subprocess.run([sys.executable, "jarvis.py", "--doctor"], cwd=_REPO,
                       env=dict(os.environ, HOME=fresh),
                       capture_output=True, text=True, timeout=90)
    out = r.stdout
    check("doctor exits 0", r.returncode == 0, r.stderr[-400:])
    for needle in ("JARVIS DOCTOR", "PASS: Python", "Groq key",
                   "OpenAI key: not set", "Brain routes",
                   "Ears (speech-to-text)", "Voice - ",
                   "Notification Center", "Accessibility", "HUD port",
                   "Internet:"):
        check(f"doctor prints the '{needle}' line", needle in out)
    check("doctor never tracebacks", "Traceback" not in r.stderr)

    # =====================================================================
    # E: installer statics + the app icon
    # =====================================================================
    with open(os.path.join(_REPO, "install.sh")) as f:
        installer = f.read()
    check("installer rejects a Groq key at the OpenAI step",
          "That looks like a Groq key, not OpenAI - skipped. "
          "Jarvis stays Groq-only." in installer)
    check("installer rejects an OpenAI key at the Groq step",
          "That looks like an OpenAI key, not a Groq key - skipped." in installer)
    check("installer builds Jarvis.app", "Jarvis.app" in installer)
    check("installer plist carries the bundle id",
          "com.umeshlinga.jarvis" in installer)
    check("installer app launches --window mode", "jarvis.py --window" in installer)
    r = subprocess.run(["bash", "-n", "install.sh"], cwd=_REPO,
                       capture_output=True, text=True, timeout=30)
    check("install.sh is bash -n clean", r.returncode == 0, r.stderr[-300:])

    icon = os.path.join(_REPO, "app", "icon.png")
    check("app/icon.png exists", os.path.exists(icon))
    if os.path.exists(icon):
        with open(icon, "rb") as f:
            sig = f.read(8)
        check("app/icon.png is a real PNG",
              sig == b"\x89PNG\r\n\x1a\n" and os.path.getsize(icon) > 1000)

    print(f"\n{len(PASSED)}/{len(PASSED)} v7 tests passed.")


if __name__ == "__main__":
    main()
