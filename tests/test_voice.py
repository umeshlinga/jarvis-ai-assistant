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

    print(f"\n{len(PASSED)}/{len(PASSED)} voice tests passed.")


if __name__ == "__main__":
    main()
