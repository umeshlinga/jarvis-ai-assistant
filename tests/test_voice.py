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

# Captured before main() stubs the transcribers (v4.1 prompt test needs
# the real Groq STT function, with only the SDK stubbed).
_REAL_TRANSCRIBE_GROQ = jarvis_voice._transcribe_groq

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

    # ---- v4.1: Groq STT gets whisper-large-v3 + the vocabulary prompt ----
    # Only the OpenAI SDK module is faked; the real _transcribe_groq runs.
    import types as _types
    jarvis_voice._transcribe_groq = _REAL_TRANSCRIBE_GROQ
    stt_state = {"fail_prompt": False, "calls": []}

    class _FakeTranscriptions:
        def create(self, **kwargs):
            stt_state["calls"].append(kwargs)
            if stt_state["fail_prompt"] and "prompt" in kwargs:
                raise TypeError("got an unexpected keyword argument 'prompt'")
            return _types.SimpleNamespace(text="heard with prompt")

    class _FakeOpenAIClient:
        def __init__(self, api_key=None, base_url=None, **kw):
            self.audio = _types.SimpleNamespace(transcriptions=_FakeTranscriptions())

    fake_openai = _types.ModuleType("openai")
    fake_openai.OpenAI = _FakeOpenAIClient
    _prior_openai = sys.modules.get("openai")
    sys.modules["openai"] = fake_openai
    try:
        out = jarvis_voice._transcribe_groq(io.BytesIO(b"fake-wav"))
        check("groq STT (real fn, fake SDK) returns text", out == "heard with prompt")
        call = stt_state["calls"][-1]
        check("groq STT default model is whisper-large-v3",
              call.get("model") == "whisper-large-v3")
        check("groq STT sends the vocabulary prompt",
              "bioinformatics" in str(call.get("prompt", "")))
        stt_state["fail_prompt"] = True
        stt_state["calls"].clear()
        out = jarvis_voice._transcribe_groq(io.BytesIO(b"fake-wav"))
        check("groq STT retries without prompt when the SDK rejects it",
              out == "heard with prompt" and "prompt" not in stt_state["calls"][-1])
    finally:
        if _prior_openai is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = _prior_openai

    # ---- v4.1: chunk_text (Orpheus ~200-char limit) ----
    chunks = jarvis_voice.chunk_text(
        "One sentence here. Two sentence follows! And a third one?", limit=30)
    check("chunk_text respects the limit",
          chunks and all(len(c) <= 30 for c in chunks) and len(chunks) >= 2)
    check("chunk_text keeps sentences whole", chunks[0] == "One sentence here.")
    long_chunks = jarvis_voice.chunk_text("word " * 60, limit=50)
    check("chunk_text splits over-long text within the limit",
          len(long_chunks) > 1 and all(len(c) <= 50 for c in long_chunks))
    check("chunk_text of empty text is empty", jarvis_voice.chunk_text("   ") == [])

    # ---- v4.1: neural (human) voice - order, playback, latch ----
    check("tts_status advertises the neural voice",
          "neural" in jarvis_voice.tts_status()
          and "daniel" in jarvis_voice.tts_status())
    jarvis_voice._tts_neural_broken = False
    legacy_spoken = []
    jarvis_voice._speak_legacy = lambda text: legacy_spoken.append(text)
    played = []
    jarvis_voice._play_audio_file = lambda path: played.append(
        (path, os.path.exists(path)))
    synth_calls = []

    class _FakeSpeech:
        def create(self, model, voice, input, response_format):
            synth_calls.append({"model": model, "voice": voice, "input": input})
            return _types.SimpleNamespace(content=b"RIFF-fake-wav")

    class _FakeTTSClient:
        def __init__(self):
            self.audio = _types.SimpleNamespace(speech=_FakeSpeech())

    jarvis_voice._tts_client = lambda provider: _FakeTTSClient()
    jarvis_voice.speak("Hello Umesh, this is the human voice test.")
    check("neural TTS spoke the reply (legacy voice untouched)",
          not legacy_spoken and len(played) == 1)
    check("neural TTS used Orpheus + daniel by default",
          synth_calls and synth_calls[0]["model"] == "canopylabs/orpheus-v1-english"
          and synth_calls[0]["voice"] == "daniel")
    check("temp wav existed while playing", all(ex for _p, ex in played))
    check("temp wavs cleaned up afterwards",
          all(not os.path.exists(p) for p, _ex in played))

    class _BoomSpeech:
        def create(self, **kw):
            raise RuntimeError("Error code: 404 - model_not_found")

    class _BoomTTSClient:
        def __init__(self):
            self.audio = _types.SimpleNamespace(speech=_BoomSpeech())

    jarvis_voice._tts_client = lambda provider: _BoomTTSClient()
    jarvis_voice.speak("This reply must still be spoken somehow.")
    check("neural failure latches the neural voice off",
          jarvis_voice._tts_neural_broken is True)
    check("legacy (Mac) voice took over after the neural failure",
          legacy_spoken and "spoken somehow" in legacy_spoken[-1])
    n_legacy = len(legacy_spoken)
    jarvis_voice.speak("And this one too.")
    check("latched: later replies go straight to the legacy voice",
          len(legacy_spoken) == n_legacy + 1)

    print(f"\n{len(PASSED)}/{len(PASSED)} voice tests passed.")


if __name__ == "__main__":
    main()
