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

    # =====================================================================
    # v5: speak policy - silent actions, spoken information, trimming
    # =====================================================================
    SILENT_ACKS = [
        "Opening Google Chrome.", "Opening your browser.",
        "Volume set to 50 percent.", "Playing music in Spotify.",
        "Music paused in Spotify.", "Copied report.pdf to your downloads.",
        "Moved report.pdf to your documents.",
        "Folder created: receipts, in your desktop.",
        "Screenshot saved to your Desktop as Jarvis_Screenshot_20261026.png.",
        "Timer set for 5 minutes. I will speak when it is done.",
        "Dark mode toggled.", "Locking your screen.", "Typed: hello world",
        "Pressed Enter.", "Note saved: buy milk",
        "Reminder created: call mom - due Friday at 06:00 PM.",
        "Wi-Fi turned off.", "Brightness increased.", "Scrolled up.",
        "Event added to your calendar: standup, Friday at 09:00 AM, for one hour.",
    ]
    for s in SILENT_ACKS:
        check(f"classify silent: {s[:38]}",
              jarvis_voice.classify_reply(s) == "silent")
    SPEAK_INFO = [
        "It is currently 07:27 AM",
        "Today is Monday, October 26, 2026",
        "Your battery is at 87 percent, currently discharging.",
        "I couldn't find a file matching notes to copy.",
        "Moving a file needs your confirmation. I did not move report.pdf - ask again and say yes to confirm.",
        "Your clipboard says: hello there",
        "System information - Chip: Apple M1; macOS version 26.0.",
        "Your timer is done - 5 minutes are up.",
        "Here's what I found about electric cars. Tesla - Tesla builds electric cars.",
        "I searched for bioinformatics jobs in Indianapolis and opened LinkedIn Jobs with the live results.",
    ]
    for s in SPEAK_INFO:
        check(f"classify speak: {s[:38]}",
              jarvis_voice.classify_reply(s) == "speak")
    check("classify: chat always speaks (even ack-shaped openings)",
          jarvis_voice.classify_reply("Opening thoughts on this are mixed at best.",
                                      source="chat") == "speak")
    check("classify: empty is silent", jarvis_voice.classify_reply("") == "silent")

    # present_reply: acks printed but not spoken; info spoken; the
    # speak_actions config restores the old speak-everything behaviour.
    audio_out = []
    _real_speak_audio = jarvis_voice._speak_audio
    jarvis_voice._speak_audio = lambda text: audio_out.append(text)
    try:
        kind = jarvis_voice.present_reply("Opening Google Chrome.")
        check("present_reply: ack stays silent", kind == "silent" and not audio_out)
        kind = jarvis_voice.present_reply("It is currently 07:27 AM", source="action")
        check("present_reply: info is spoken",
              kind == "speak" and audio_out == ["It is currently 07:27 AM"])
        jarvis_config.load_config()["speak_actions"] = True
        try:
            kind = jarvis_voice.present_reply("Opening Google Chrome.")
            check("present_reply: speak_actions=True speaks acks again",
                  kind == "speak" and audio_out[-1] == "Opening Google Chrome.")
        finally:
            jarvis_config.load_config()["speak_actions"] = False
    finally:
        jarvis_voice._speak_audio = _real_speak_audio

    # trim_for_speech: chat ~55 words by whole sentences; info ~110.
    long_chat = " ".join(
        f"Sentence number {i} talks about the plan in some more detail here today."
        for i in range(8))  # 8 sentences x 13 words = 104 words
    trimmed = jarvis_voice.trim_for_speech(long_chat, source="chat")
    check("trim: chat cut to <=55 words", len(trimmed.split()) <= 55)
    check("trim: chat keeps whole sentences", trimmed.endswith("."))
    check("trim: info route keeps up to 110 words",
          jarvis_voice.trim_for_speech(long_chat, source="action") == long_chat)
    huge = " ".join(["word"] * 80)  # one 80-word "sentence"
    cut = jarvis_voice.trim_for_speech(huge, source="chat")
    check("trim: a huge first sentence is hard-cut at the limit",
          len(cut.split()) == 55 and cut.endswith("…"))
    check("trim: short text untouched",
          jarvis_voice.trim_for_speech("It is currently 07:27 AM", source="chat")
          == "It is currently 07:27 AM")

    # Greeting: one short line now.
    g = jarvis_voice.startup_greeting()
    check("v5 greeting is one short line",
          "I'm listening" in g and len(g) < 60 and "briefing" not in g.lower())

    # =====================================================================
    # v5: stop / barge-in
    # =====================================================================
    spoken = "a long spoken reply about markets weather and other things today"
    check("stop word alone is a stop command",
          jarvis_voice._is_stop_command("stop", spoken) is True)
    check("'jarvis stop' is a stop command",
          jarvis_voice._is_stop_command("jarvis stop", spoken) is True)
    check("'stop talking' is a stop command",
          jarvis_voice._is_stop_command("stop talking", spoken) is True)
    check("'shut up' is a stop command",
          jarvis_voice._is_stop_command("shut up", spoken) is True)
    check("no stop word, no stop",
          jarvis_voice._is_stop_command("what is the weather", spoken) is False)
    check("empty transcript, no stop",
          jarvis_voice._is_stop_command("", spoken) is False)
    echo_spoken = "remember to stop by the store and stop the car near the market today"
    check("echo of his own speech is NOT a stop command",
          jarvis_voice._is_stop_command(
              "to stop by the store and stop the car near the market today",
              echo_spoken) is False)
    check("shouted stop over unrelated speech IS a stop command",
          jarvis_voice._is_stop_command(
              "stop stop please stop now", echo_spoken) is True)
    check("pure stop: 'stop'", jarvis_voice._is_pure_stop("stop") is True)
    check("pure stop: 'shut up'", jarvis_voice._is_pure_stop("shut up") is True)
    check("pure stop: 'jarvis stop talking'",
          jarvis_voice._is_pure_stop("jarvis stop talking") is True)
    check("NOT pure stop: 'stop the music' (a real command)",
          jarvis_voice._is_pure_stop("stop the music") is False)
    check("NOT pure stop: 'stopwatch'",
          jarvis_voice._is_pure_stop("stopwatch") is False)

    # stop_speaking() kills the live player and sets the flag.
    class _FakeProc:
        def __init__(self):
            self.terminated = False

        def terminate(self):
            self.terminated = True

    fake_proc = _FakeProc()
    jarvis_voice._register_player(fake_proc)
    jarvis_voice._stop_event.clear()
    jarvis_voice.stop_speaking()
    check("stop_speaking terminates the live player", fake_proc.terminated is True)
    check("stop_speaking sets the stop flag", jarvis_voice._stop_event.is_set())
    jarvis_voice._clear_player()
    jarvis_voice._stop_event.clear()

    # Chunk queue aborts after a stop: 3 chunks queued, stop lands
    # during the first playback -> only 1 chunk is ever played and the
    # legacy voice never takes over.
    jarvis_voice._tts_neural_broken = False
    legacy_spoken.clear()
    played_chunks = []

    def _play_then_stop(path):
        played_chunks.append(path)
        jarvis_voice._stop_event.set()  # he barges in mid-first-chunk

    jarvis_voice._play_audio_file = _play_then_stop
    jarvis_voice._tts_client = lambda provider: _FakeTTSClient()
    three_chunks = (
        "This is sentence number one and it is deliberately made fairly long "
        "so the chunking splits replies into pieces today. "
        "This is sentence number two and it is also deliberately made long "
        "enough that it must become its very own chunk here. "
        "This is sentence number three and it follows the same pattern so "
        "the test can watch the queue abort after the stop.")
    jarvis_voice.speak(three_chunks)
    check("chunk queue aborted after stop (1 of 3 chunks played)",
          len(played_chunks) == 1)
    check("stopped speech never falls through to the legacy voice",
          not legacy_spoken)
    jarvis_voice._stop_event.clear()

    # Stop-listener thread: hears 'stop' over speech -> stops it.
    import time as _t
    jarvis_voice._stop_listen_enabled = True
    jarvis_voice._stop_listener_disabled = False
    jarvis_voice._record_stop_window = lambda seconds=1.1: io.BytesIO(b"win")
    jarvis_voice._transcribe_stop_window = lambda wav: "stop"
    jarvis_voice._current_spoken_text = spoken
    jarvis_voice._speech_active.set()
    jarvis_voice._maybe_start_stop_listener()
    for _ in range(60):
        if jarvis_voice._stop_event.is_set():
            break
        _t.sleep(0.05)
    check("stop listener heard 'stop' and stopped the speech",
          jarvis_voice._stop_event.is_set())
    jarvis_voice._speech_active.clear()
    jarvis_voice._stop_event.clear()
    _t.sleep(0.2)

    # Stop listener: an echo window does NOT stop him (run synchronously;
    # the fake transcription clears speech_active on its 2nd call).
    echo_calls = {"n": 0}

    def _echo_transcribe(wav):
        echo_calls["n"] += 1
        if echo_calls["n"] >= 2:
            jarvis_voice._speech_active.clear()
        return "to stop by the store and stop the car near the market today"

    jarvis_voice._transcribe_stop_window = _echo_transcribe
    jarvis_voice._current_spoken_text = echo_spoken
    jarvis_voice._speech_active.set()
    jarvis_voice._stop_listener_loop()
    check("stop listener ignores Jarvis hearing himself",
          not jarvis_voice._stop_event.is_set())

    # Stop listener: a mic that can't open disables it silently.
    def _boom_record(seconds=1.1):
        raise RuntimeError("input device busy")

    jarvis_voice._record_stop_window = _boom_record
    jarvis_voice._stop_listener_disabled = False
    jarvis_voice._speech_active.set()
    jarvis_voice._stop_listener_loop()
    check("mic failure disables the stop listener for the session",
          jarvis_voice._stop_listener_disabled is True)
    jarvis_voice._speech_active.clear()
    jarvis_voice._stop_listen_enabled = False
    jarvis_voice._current_spoken_text = ""

    print(f"\n{len(PASSED)}/{len(PASSED)} voice tests passed.")


if __name__ == "__main__":
    main()
