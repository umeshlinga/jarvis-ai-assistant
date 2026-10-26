"""
test_voice.py - v3.1 voice fixes, plain-python runnable:
    python3 tests/test_voice.py

Covers the Whisper fail-once latch (a broken Whisper load must be tried
ONCE per session, then the Google recognizer takes over with no slow
retry per sentence) and the stt_language config default.
No mic, no network: record/transcribe helpers are stubbed.
"""

import io
import os
import sys
import tempfile

# Isolate config/history under a temp HOME before importing jarvis modules.
_TMP_HOME = tempfile.mkdtemp(prefix="jarvis-test-home-")
os.environ["HOME"] = _TMP_HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jarvis_config  # noqa: E402
import jarvis_voice  # noqa: E402

PASSED = []


def check(name, cond):
    assert cond, f"FAILED: {name}"
    PASSED.append(name)
    print(f"PASS: {name}")


def main():
    cfg = jarvis_config.load_config(force_reload=True)
    check("stt_language default is en-IN", cfg.get("stt_language") == "en-IN")

    # Stub recording + google; make whisper always raise.
    calls = {"whisper": 0, "google": 0}
    jarvis_voice._record_wav_bytes = lambda *a, **k: io.BytesIO(b"fake-wav")
    jarvis_voice._whisper_available = lambda: True

    def boom(_path):
        calls["whisper"] += 1
        raise RuntimeError("open() got an unexpected keyword argument 'metadata_errors'")

    def fake_google(_wav):
        calls["google"] += 1
        return "hello jarvis"

    jarvis_voice._transcribe_whisper = boom
    jarvis_voice._transcribe_google = fake_google
    jarvis_voice._whisper_broken = False

    out1 = jarvis_voice.listen_once()
    check("first listen falls back to google after whisper failure", out1 == "hello jarvis")
    check("whisper tried exactly once", calls["whisper"] == 1)
    check("latch set after failure", jarvis_voice._whisper_broken is True)

    out2 = jarvis_voice.listen_once()
    out3 = jarvis_voice.listen_once()
    check("later listens still answer via google", out2 == "hello jarvis" and out3 == "hello jarvis")
    check("whisper NOT retried after latch (speed fix)", calls["whisper"] == 1)
    check("google used for every fallback", calls["google"] == 3)

    # ---- v4: Groq Whisper ears (every layer stubbed - NO network) ----
    import time as _time
    jarvis_voice._groq_key = lambda: "test-groq-key"
    groq_calls = {"n": 0}

    def fake_groq_ok(_wav):
        groq_calls["n"] += 1
        return "heard by groq"

    jarvis_voice._transcribe_groq = fake_groq_ok
    out = jarvis_voice.listen_once()
    check("auto order: groq ears first", out == "heard by groq" and groq_calls["n"] == 1)
    check("groq success: google untouched", calls["google"] == 3)

    def fake_groq_boom(_wav):
        groq_calls["n"] += 1
        raise RuntimeError("503 from groq")

    jarvis_voice._transcribe_groq = fake_groq_boom
    out = jarvis_voice.listen_once()
    check("groq error falls through (whisper latched off -> google)",
          out == "hello jarvis" and calls["google"] == 4)
    check("groq latch set for ~60s", jarvis_voice._groq_stt_broken_until > _time.time())
    n_after_error = groq_calls["n"]
    out = jarvis_voice.listen_once()
    check("groq latched: not retried within the minute",
          groq_calls["n"] == n_after_error and out == "hello jarvis")

    # Latch expired + local whisper healthy -> order groq -> whisper.
    jarvis_voice._groq_stt_broken_until = 0.0
    jarvis_voice._whisper_broken = False
    jarvis_voice._transcribe_whisper = lambda _p: "heard by local whisper"
    out = jarvis_voice.listen_once()
    check("groq error then local whisper next", out == "heard by local whisper")

    # Explicit google engine is honoured even with a Groq key present.
    jarvis_config.load_config()["stt_engine"] = "google"
    out = jarvis_voice.listen_once()
    check("explicit google engine honoured", out == "hello jarvis")
    jarvis_config.load_config()["stt_engine"] = "auto"

    print(f"\n{len(PASSED)}/{len(PASSED)} voice tests passed.")


if __name__ == "__main__":
    main()
