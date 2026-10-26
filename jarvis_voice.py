"""
jarvis_voice.py v2 - Speech Recognition & Spoken Replies (M1 fix).

THE M1 MIC FIX:
The original used speech_recognition's Microphone(), which needs PyAudio.
PyAudio has no reliable Apple-Silicon install (it compiles against
portaudio and fails on many M1 setups) and was missing from the original
requirements.txt - so the mic simply broke.

v2 records with `sounddevice` instead: it ships prebuilt arm64 wheels,
needs no compilation, and gives us raw samples. We write those samples to
an in-memory WAV and hand the WAV to SpeechRecognition's recognizer via
AudioFile - exactly the recognizer the original used, with no PyAudio
required at runtime. If no microphone / sounddevice is available, Jarvis
falls back to text mode instead of crashing.

Speaking (v4.1): a neural HUMAN voice first - Groq's Orpheus TTS using
the same Groq key as the brain/ears (OpenAI TTS as second choice) -
played with afplay; macOS `say` (voice + rate from config) is the
always-available fallback and the offline voice. On non-Mac systems it
prints only (pyttsx3 is an optional extra, never a requirement).

v5 "Autonomous Butler": playback is killable (stop_speaking) and a
stop-listener thread watches the mic while Jarvis talks, so "Jarvis,
stop" cuts him off mid-sentence; the speak policy (present_reply)
keeps pure action acknowledgements silent and trims spoken answers;
the proactive butler (jarvis_butler) delivers queued alerts between
listen cycles.
"""

import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave

import jarvis_butler
import jarvis_config
import jarvis_hud
import jarvis_tasks

SAMPLE_RATE = 16000
CHANNELS = 1

_whisper_model_cache = None
# v3.1 speed fix: if the Whisper load fails ONCE (e.g. a library version
# clash on the machine), remember it for the whole session and go straight
# to the Google recognizer. v3.0 retried the slow failing load after every
# single sentence, which made every reply feel slow.
_whisper_broken = False
_whisper_broken_reason = ""
# v4: Groq Whisper STT failure latch - a 60-second rest, not forever:
# one bad network minute must not kill the fast ears for the session.
_groq_stt_broken_until = 0.0
# v4.1: neural-TTS (human voice) failure latch - if the neural voice
# fails once, Jarvis says so once and uses the Mac voice for the rest
# of the session. No retry spam, and a reply is never blocked by TTS.
_tts_neural_broken = False

# ---------------------------------------------------------------------------
# v5: killable playback + barge-in ("Jarvis, stop")
# ---------------------------------------------------------------------------
# The live audio player (an afplay / say subprocess, or a pyttsx3 engine)
# is tracked so stop_speaking() can kill it mid-sentence. A stop-listener
# thread watches the mic WHILE Jarvis talks and acts on stop words only.
_current_player = None            # Popen handle or pyttsx3 engine
_player_lock = threading.Lock()
_stop_event = threading.Event()   # set by stop_speaking(); aborts chunk queues
_speech_active = threading.Event()  # set while audio is actually playing
_speech_lock = threading.Lock()   # serialise concurrent speak() calls
_current_spoken_text = ""         # what Jarvis is saying (echo guard input)
_stop_listen_enabled = False      # listen_loop turns the barge-in ear on
_stop_listener_running = False
_stop_listener_disabled = False   # session latch: mic can't open alongside


def _register_player(player):
    global _current_player
    with _player_lock:
        _current_player = player


def _clear_player(player=None):
    global _current_player
    with _player_lock:
        if player is None or _current_player is player:
            _current_player = None


def stop_speaking():
    """
    Stop whatever Jarvis is saying, RIGHT NOW: set the stop flag (queued
    speech chunks abort) and kill the live player. Safe to call any time.
    """
    _stop_event.set()
    with _player_lock:
        player = _current_player
    if player is not None:
        try:
            if hasattr(player, "terminate"):       # subprocess (afplay / say)
                player.terminate()
            elif hasattr(player, "stop"):          # pyttsx3 engine
                player.stop()
        except Exception:
            pass
    _hud_emit("state", "stopped")


def _hud_emit(kind, text):
    """Feed the live HUD (no-op cost when the server isn't running)."""
    try:
        jarvis_hud.emit(kind, text)
    except Exception:
        pass


def _register_announcer(no_speak=False):
    """Background tasks speak their updates through this announcer."""
    def _announce(msg):
        _hud_emit("task", msg)
        # v5: announcements obey the speak policy too (printed in full,
        # spoken trimmed) - source "task" always deserves a voice.
        present_reply(msg, no_speak=no_speak, source="task")
    try:
        jarvis_tasks.set_announcer(_announce)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# v5: stop-word spotting (barge-in while Jarvis is talking)
# ---------------------------------------------------------------------------

_STOP_PHRASES = ("stop talking", "jarvis stop", "shut up", "stop", "quiet", "enough")
# An utterance made ONLY of these words is a pure stop command, not a
# request like "stop the music" (a real command that must keep working).
_STOP_ONLY_WORDS = {"stop", "quiet", "shut", "up", "enough", "talking",
                    "jarvis", "please", "now"}


def _is_stop_command(transcript, spoken_text=""):
    """
    True when a mic window holds a genuine stop command. Echo guard:
    Jarvis hears his own voice through the speakers, so a window that
    mostly repeats the text currently being spoken is NOT a command -
    unless the window is basically only the stop word itself.
    """
    if not transcript:
        return False
    words = re.findall(r"[a-z']+", transcript.lower())
    if not words:
        return False
    joined = " " + " ".join(words) + " "
    if not any((" " + p + " ") in joined for p in _STOP_PHRASES):
        return False
    if len(words) <= 3:
        return True  # basically just the stop word - that's him, not echo
    # A window DOMINATED by stop tokens is him shouting over Jarvis,
    # even when the spoken sentence happens to contain the word "stop".
    stop_tokens = {"stop", "quiet", "shut", "up", "enough", "talking", "jarvis"}
    if sum(1 for w in words if w in stop_tokens) / len(words) >= 0.5:
        return True
    spoken_words = set(re.findall(r"[a-z']+", (spoken_text or "").lower()))
    if not spoken_words:
        return True
    overlap = sum(1 for w in words if w in spoken_words) / len(words)
    return overlap < 0.6


def _is_pure_stop(text):
    """True when a fresh utterance is ONLY a stop word ('stop', 'shut up')."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words:
        return False
    return all(w in _STOP_ONLY_WORDS for w in words) and any(
        w in ("stop", "quiet", "enough") or words == ["shut", "up"] for w in words)


def _record_stop_window(seconds=1.1):
    """
    Record one short raw mic window for the stop listener (no VAD - a
    fixed ~1.1s slice). Raises on any problem (no mic, device busy,
    sounddevice missing) so the listener can disable itself silently.
    """
    import numpy as np
    import sounddevice as sd

    frames = int(SAMPLE_RATE * seconds)
    data = sd.rec(frames, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16")
    sd.wait()
    audio = np.asarray(data, dtype="int16").reshape(-1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    buf.seek(0)
    return buf


def _transcribe_stop_window(wav_bytes):
    """Fastest available transcription, for stop-word spotting only."""
    if _groq_key():
        try:
            return _transcribe_groq(wav_bytes)
        except Exception:
            pass
    try:
        return _transcribe_google(wav_bytes)
    except Exception:
        return None


def _stop_listener_loop():
    """
    While Jarvis speaks, watch the mic in ~1.1s windows; on a genuine
    stop word, kill the speech and go back to listening. If the mic
    can't be opened alongside playback, disable for the session,
    silently - barge-in is a bonus, never a crash.
    """
    global _stop_listener_running, _stop_listener_disabled
    try:
        while _speech_active.is_set() and not _stop_event.is_set():
            try:
                wav = _record_stop_window(1.1)
            except Exception:
                _stop_listener_disabled = True
                return
            text = _transcribe_stop_window(wav)
            if text and _is_stop_command(text, _current_spoken_text):
                stop_speaking()
                print("\n[Heard you - speech stopped.]")
                _hud_emit("state", "listening")
                return
    finally:
        _stop_listener_running = False


def _maybe_start_stop_listener():
    global _stop_listener_running
    if not _stop_listen_enabled or _stop_listener_disabled or _stop_listener_running:
        return
    _stop_listener_running = True
    try:
        threading.Thread(target=_stop_listener_loop, name="jarvis-stop-ear",
                         daemon=True).start()
    except Exception:
        _stop_listener_running = False


# --------------------------------------------------------------------------
# v4.1 human voice: neural text-to-speech (Groq Orpheus -> OpenAI -> say)
# --------------------------------------------------------------------------

def _split_long(text, limit):
    """Split one over-long sentence into <= limit pieces (commas, then spaces)."""
    pieces = []
    rest = text.strip()
    while len(rest) > limit:
        cut = rest.rfind(", ", 0, limit)
        if cut < limit // 3:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut + 1].strip() if rest[cut:cut + 1] == "," else rest[:cut].strip())
        rest = rest[cut:].lstrip(", ").strip()
    if rest:
        pieces.append(rest)
    return [p for p in pieces if p]


def chunk_text(text, limit=180):
    """
    Split spoken text into chunks of at most `limit` characters, keeping
    whole sentences together where possible (Orpheus accepts ~200 chars
    per request, so replies are synthesised sentence-group by
    sentence-group and played back-to-back - it sounds like one person
    talking, not fragments).
    """
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    chunks = []
    cur = ""
    for s in sentences:
        if len(s) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_split_long(s, limit))
        elif not cur:
            cur = s
        elif len(cur) + 1 + len(s) <= limit:
            cur = cur + " " + s
        else:
            chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks


def _openai_key():
    """The OpenAI key Jarvis may have (brain key store), or None."""
    try:
        import jarvis_brain
        return jarvis_brain.openai_key()
    except Exception:
        return None


def _tts_client(provider):
    """OpenAI-SDK client pointed at the provider's speech endpoint."""
    from openai import OpenAI  # lazy, like everywhere else

    cfg = jarvis_config.load_config()
    if provider == "groq":
        return OpenAI(api_key=_groq_key(), base_url=cfg.get("groq_base_url"),
                      timeout=30.0)
    return OpenAI(api_key=_openai_key(), base_url=cfg.get("openai_base_url"),
                  timeout=30.0)


def _synth_chunk(client, model, voice_name, text):
    """One speech synthesis call; returns WAV bytes. Raises on failure."""
    resp = client.audio.speech.create(
        model=model, voice=voice_name, input=text, response_format="wav")
    if isinstance(resp, (bytes, bytearray)):
        return bytes(resp)
    data = getattr(resp, "content", None)
    if data is None and hasattr(resp, "read"):
        data = resp.read()
    if data is None and hasattr(resp, "iter_bytes"):
        data = b"".join(resp.iter_bytes())
    if not data:
        raise RuntimeError("speech API returned no audio")
    return bytes(data)


def _play_audio_file(path):
    """
    Play a WAV file (macOS afplay). Raises anywhere else / on error.
    v5: runs as a tracked Popen so stop_speaking() can kill playback
    mid-chunk; being stopped is NOT an error.
    """
    if sys.platform != "darwin":
        raise RuntimeError("afplay is only available on macOS")
    proc = subprocess.Popen(["afplay", path],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _register_player(proc)
    try:
        rc = proc.wait()
    finally:
        _clear_player(proc)
    if rc != 0 and not _stop_event.is_set():
        raise RuntimeError(f"afplay exited with status {rc}")


def _tts_attempts():
    """
    Ordered neural-TTS attempts from config: [(provider, model, voice,
    chunk_limit)]. Empty when tts_engine forces `say` or no key exists -
    in which case nothing was attempted and nothing is latched.
    """
    cfg = jarvis_config.load_config()
    engine = str(cfg.get("tts_engine", "auto")).lower()
    attempts = []
    if engine in ("auto", "groq") and _groq_key():
        attempts.append(("groq",
                         str(cfg.get("groq_tts_model", "canopylabs/orpheus-v1-english")),
                         str(cfg.get("tts_voice", "daniel")), 180))
    if engine in ("auto", "openai") and _openai_key():
        attempts.append(("openai",
                         str(cfg.get("openai_tts_model", "gpt-4o-mini-tts")),
                         str(cfg.get("openai_tts_voice", "nova")), 400))
    return attempts


def _speak_neural(text):
    """
    Try to speak `text` with a neural (human) voice. Returns True when
    the whole reply was spoken neurally. Any failure falls down the
    chain; when every configured engine fails, the neural voice latches
    OFF for the session (one printed line) and the caller uses `say`.
    """
    global _tts_neural_broken
    if _tts_neural_broken:
        return False
    attempts = _tts_attempts()
    if not attempts:
        return False
    last_error = None
    for provider, model, voice_name, limit in attempts:
        tmp_path = None
        try:
            client = _tts_client(provider)
            for chunk in chunk_text(text, limit=limit):
                if _stop_event.is_set():
                    return True  # v5: he said stop - abort the queue quietly
                data = _synth_chunk(client, model, voice_name, chunk)
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                    tmp_path = tf.name
                    tf.write(data)
                try:
                    _play_audio_file(tmp_path)
                finally:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                    tmp_path = None
                if _stop_event.is_set():
                    return True  # stopped mid-reply: handled, not a failure
            return True
        except Exception as e:
            last_error = e
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            continue  # fall down the chain to the next engine
    _tts_neural_broken = True
    print(f"[Human voice not available right now ({last_error}) - using the "
          f"Mac voice for the rest of this session.]")
    return False


def _speak_legacy(text):
    """
    The v4 voice path: macOS `say` on a Mac, else optional pyttsx3.
    v5: both run as tracked players so stop_speaking() can cut them off.
    """
    if sys.platform == "darwin":
        cfg = jarvis_config.load_config()
        voice = str(cfg.get("voice", "Samantha"))
        rate = str(cfg.get("speech_rate", 185))
        try:
            proc = subprocess.Popen(["say", "-v", voice, "-r", rate, text],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            _register_player(proc)
            try:
                proc.wait()
            finally:
                _clear_player(proc)
            return
        except Exception:
            pass
    try:
        import pyttsx3  # optional dependency (requirements-optional.txt)
        engine = pyttsx3.init()
        _register_player(engine)
        try:
            engine.say(text)
            engine.runAndWait()
        finally:
            _clear_player(engine)
    except Exception:
        pass


def tts_status():
    """Human line describing the speaking voice that will be used."""
    cfg = jarvis_config.load_config()
    engine = str(cfg.get("tts_engine", "auto")).lower()
    say_desc = f"macOS say ({cfg.get('voice', 'Samantha')} @ {cfg.get('speech_rate', 185)} wpm)"
    if engine == "say":
        return say_desc
    if engine in ("auto", "groq") and _groq_key():
        return (f"neural (groq {cfg.get('groq_tts_model', 'canopylabs/orpheus-v1-english')}, "
                f"voice {cfg.get('tts_voice', 'daniel')}) with macOS say fallback")
    if engine in ("auto", "openai") and _openai_key():
        return (f"neural (openai {cfg.get('openai_tts_model', 'gpt-4o-mini-tts')}, "
                f"voice {cfg.get('openai_tts_voice', 'nova')}) with macOS say fallback")
    return say_desc + " - no neural voice key configured"


def _speak_audio(text):
    """
    The audio half of a reply: neural human voice first (v4.1), macOS
    `say` as fallback. v5: tracked + stoppable - the stop flag is reset
    for each new utterance, the stop listener watches while audio plays,
    and concurrent speeches serialise on a lock.
    """
    global _current_spoken_text
    if not text:
        return
    with _speech_lock:
        _stop_event.clear()
        _current_spoken_text = text
        _speech_active.set()
        _maybe_start_stop_listener()
        try:
            if _speak_neural(text):
                return
            if _stop_event.is_set():
                return  # stopped during synthesis - stay quiet
            _speak_legacy(text)
        finally:
            _speech_active.clear()
            _current_spoken_text = ""
            _clear_player()


def speak(text, no_speak=False):
    """
    Print the reply, then speak it in full. Kept for direct callers
    (greetings, --once, --voice-test); the conversation loops use
    present_reply(), which applies the v5 speak policy.
    """
    if not text:
        return
    _hud_emit("state", "speaking")
    _hud_emit("reply", text)
    print(f"\n[JARVIS]: {text}\n")
    if no_speak:
        return
    _speak_audio(text)


# ---------------------------------------------------------------------------
# v5 speak policy: silent actions, short answers
# ---------------------------------------------------------------------------
# Owner's rule: "If I say open, it should open normally and NOT speak.
# Speak only when there's something to tell me." Pure acknowledgements
# are printed + shown on the HUD, never spoken. Information is spoken,
# trimmed: chat to ~55 words, info routes (search/read/market/tasks)
# to ~110 words. The FULL text is always printed and on the HUD.

_SILENT_LEADINS = (
    "opening", "opened", "launching", "volume set", "volume increased",
    "volume decreased", "system audio muted", "system audio unmuted",
    "playing", "music paused", "paused", "next song", "previous song",
    "copied", "moved", "file created", "folder created",
    "screenshot saved", "screenshot taken", "timer set", "dark mode",
    "locking your screen", "locked", "typed", "pressed", "note saved",
    "reminder created", "reminder set", "event added", "switched",
    "scrolled", "page up", "page down", "brightness", "wi-fi turned",
    "wifi turned", "closing", "closed", "tab closed", "window closed",
    "window minimized", "window zoomed", "selected all", "saved",
    "undone", "pasted", "searching spotify", "searching google for",
    "starting a facetime", "starting a call", "showing the desktop",
    "mission control", "playing music",
)

# Problems and anything needing his answer must ALWAYS reach his ears.
_SPEAK_MARKERS = (
    "couldn't", "could not", "can't", "cannot", "needs your confirmation",
    "are you sure", "say yes", "did not", "didn't", "failed",
    "unavailable", "isn't available", "not available", "offline",
    "needs a one-time setup", "error", "sorry",
)

_LONG_INFO_SOURCES = ("action", "task", "search", "read", "web", "market")


def _asks_him(text_low):
    """True when the reply is a question he is meant to answer."""
    if "?" not in text_low:
        return False
    return any(k in text_low for k in (
        "want me to", "should i", "do you want", "say yes", "shall i",
        "would you like"))


def classify_reply(text, source=None):
    """
    "silent" for pure action acknowledgements; "speak" for information,
    questions, chat, and anything that went wrong. `source` comes from
    jarvis_brain.last_reply_source() ("action"/"chat"/"fallback") or
    the loops ("task").
    """
    t = (text or "").strip()
    if not t:
        return "silent"
    if source == "chat":
        return "speak"  # conversation is the point - but trimmed below
    low = t.lower()
    if any(m in low for m in _SPEAK_MARKERS):
        return "speak"
    if _asks_him(low):
        return "speak"
    if len(t) <= 220 and low.startswith(_SILENT_LEADINS):
        return "silent"
    return "speak"


def trim_for_speech(text, source=None):
    """
    The spoken trim: keep whole sentences up to the word limit for the
    reply's source (chat ~55 words; info routes ~110). A single huge
    first sentence is hard-cut. The full text is still printed / on HUD.
    """
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if not t:
        return t
    limit = 110 if source in _LONG_INFO_SOURCES else 55
    words = t.split()
    if len(words) <= limit:
        return t
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
    out, count = [], 0
    for s in sentences:
        w = len(s.split())
        if out and count + w > limit:
            break
        if not out and w > limit:
            return " ".join(words[:limit]) + "…"
        out.append(s)
        count += w
    return " ".join(out) if out else " ".join(words[:limit]) + "…"


def present_reply(text, no_speak=False, source=None):
    """
    The v5 way loops deliver a reply: ALWAYS print the full text (and
    feed the HUD), then speak only what deserves a voice - information,
    trimmed; pure acknowledgements stay silent unless config
    speak_actions=True restores the old speak-everything behaviour.
    Returns "silent" or "speak".
    """
    if not text:
        return "silent"
    kind = classify_reply(text, source)
    try:
        if kind == "silent" and jarvis_config.load_config().get("speak_actions", False):
            kind = "speak"
    except Exception:
        pass
    _hud_emit("reply", text)
    print(f"\n[JARVIS]: {text}\n")
    if no_speak or kind == "silent":
        return kind
    _hud_emit("state", "speaking")
    _speak_audio(trim_for_speech(text, source))
    return kind


def mic_available():
    """True if sounddevice can see an input device. Never raises."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        for d in devices:
            if d.get("max_input_channels", 0) > 0:
                return True
        # Some setups only expose a default device.
        return sd.default.device is not None
    except Exception:
        return False


def mic_status():
    if mic_available():
        return "available (sounddevice)"
    return "not available - text mode (install sounddevice / check mic permission)"


def _record_wav_bytes(seconds=None, max_seconds=10):
    """
    Record from the default mic with sounddevice and return WAV bytes
    (16 kHz, mono, 16-bit).

    v3.1: silence detection (simple energy VAD). v3.0 always recorded a
    fixed 6 seconds, so Jarvis kept "listening" long after the user had
    finished speaking - the main reason replies felt slow. Now recording
    stops ~1 second after speech ends, waits up to ~6 seconds for speech
    to start, and never records longer than max_seconds.
    Raises on any failure so the caller can fall back to text mode.
    """
    import numpy as np
    import sounddevice as sd

    chunk_seconds = 0.25
    chunk_frames = int(SAMPLE_RATE * chunk_seconds)
    silence_chunks_to_stop = 4          # 4 x 0.25s = 1.0s of silence ends it
    wait_chunks_no_speech = 24          # ~6s waiting for speech to start
    max_chunks = int(max_seconds / chunk_seconds)

    recorded = []
    speech_started = False
    silent_after_speech = 0
    ambient_levels = []
    threshold = 600.0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16") as stream:
        for i in range(max_chunks):
            data, _overflowed = stream.read(chunk_frames)
            arr = np.asarray(data, dtype=np.float64).reshape(-1)
            recorded.append(arr.astype("int16"))
            rms = float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0
            if i < 2:
                ambient_levels.append(rms)
                if len(ambient_levels) == 2:
                    threshold = max(500.0, float(np.mean(ambient_levels)) * 2.5)
                continue
            if rms > threshold:
                speech_started = True
                silent_after_speech = 0
            elif speech_started:
                silent_after_speech += 1
                if silent_after_speech >= silence_chunks_to_stop:
                    break
            elif i >= wait_chunks_no_speech:
                break

    import numpy as np
    audio = np.concatenate(recorded) if recorded else np.zeros(0, dtype="int16")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(np.asarray(audio, dtype="int16").tobytes())
    buf.seek(0)
    return buf


def _transcribe_google(wav_bytes):
    """Online transcription via SpeechRecognition's free Google recognizer."""
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = 0.8
    wav_bytes.seek(0)
    with sr.AudioFile(wav_bytes) as source:
        audio = recognizer.record(source)
    cfg = jarvis_config.load_config()
    language = str(cfg.get("stt_language", "en-IN"))
    try:
        return recognizer.recognize_google(audio, language=language).strip()
    except sr.UnknownValueError:
        return None
    except Exception:
        # If the configured language is ever rejected, retry with the
        # recognizer default rather than losing the utterance.
        try:
            return recognizer.recognize_google(audio).strip()
        except sr.UnknownValueError:
            return None


def _whisper_available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _groq_key():
    """The Groq key Jarvis already has (brain key store), or None."""
    try:
        import jarvis_brain
        return jarvis_brain.groq_key()
    except Exception:
        return None


def _transcribe_groq(wav_bytes):
    """
    v4 flagship ears: Groq's Whisper API (OpenAI-compatible
    /audio/transcriptions, model from config groq_stt_model). v4.1: the
    default model is whisper-large-v3 (full model - accuracy over the
    turbo variant) and a vocabulary prompt (config stt_vocab_prompt)
    biases Whisper towards the words Umesh actually says - his name,
    "bioinformatics", "Indianapolis" - the words v4's ears garbled live
    ("bioinformatics" -> "Bighton"). Uses the SAME key as the brain.
    Raises on any failure so listen_once can fall through.
    """
    key = _groq_key()
    if not key:
        raise RuntimeError("no Groq key configured")
    from openai import OpenAI  # lazy: local mode must work without the SDK

    cfg = jarvis_config.load_config()
    client = OpenAI(api_key=key, base_url=cfg.get("groq_base_url"))
    data = wav_bytes.getvalue() if hasattr(wav_bytes, "getvalue") else None
    if data is None:
        wav_bytes.seek(0)
        data = wav_bytes.read()
    audio_file = io.BytesIO(data)
    audio_file.name = "jarvis.wav"  # the API uses the suffix for the format
    language = str(cfg.get("stt_language", "en-IN")).split("-")[0].lower() or "en"
    kwargs = {
        "model": str(cfg.get("groq_stt_model", "whisper-large-v3")),
        "file": audio_file,
        "language": language,
    }
    prompt = str(cfg.get("stt_vocab_prompt", "") or "").strip()
    if prompt:
        kwargs["prompt"] = prompt
    try:
        resp = client.audio.transcriptions.create(**kwargs)
    except TypeError:
        # An older SDK build that doesn't accept `prompt` - retry plainly
        # rather than losing the utterance.
        kwargs.pop("prompt", None)
        audio_file.seek(0)
        resp = client.audio.transcriptions.create(**kwargs)
    text = getattr(resp, "text", None)
    if text is None and isinstance(resp, dict):
        text = resp.get("text")
    return (text or "").strip() or None


def _transcribe_whisper(wav_path):
    """
    Fully offline transcription with faster-whisper. The model (config
    whisper_model, default base.en) is downloaded ONCE on first use -
    that first download needs internet; afterwards it works offline.
    Raises on failure so the caller can fall back to Google.
    """
    global _whisper_model_cache
    # The Xet download backend in newer huggingface-hub builds is what
    # produced "open() got an unexpected keyword argument 'metadata_errors'"
    # on Umesh's Mac - disable it so the classic download path is used.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from faster_whisper import WhisperModel

    if _whisper_model_cache is None:
        cfg = jarvis_config.load_config()
        model_name = str(cfg.get("whisper_model", "base.en"))
        _whisper_model_cache = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = _whisper_model_cache.transcribe(wav_path, language="en")
    text = " ".join(seg.text for seg in segments).strip()
    return text or None


def stt_status():
    """Human line describing the speech-to-text engine that will be used."""
    cfg = jarvis_config.load_config()
    engine = str(cfg.get("stt_engine", "auto")).lower()
    model = str(cfg.get("groq_stt_model", "whisper-large-v3"))
    if engine == "groq":
        return f"groq {model} (fast, online)" if _groq_key() else "groq requested but no Groq key - google fallback"
    if engine == "whisper":
        return "whisper (offline)" if _whisper_available() else "whisper requested but not installed - google fallback"
    if engine == "google":
        return "google (online)"
    # auto: Groq Whisper first, then local Whisper, then Google.
    if _groq_key():
        return f"auto -> groq {model} (fast ears), whisper/google fallback"
    return "auto -> whisper (offline)" if _whisper_available() else "auto -> google (online)"


def _try_whisper(wav_bytes):
    """Local faster-whisper attempt; latches off for the session on failure."""
    global _whisper_broken, _whisper_broken_reason
    if _whisper_broken or not _whisper_available():
        return None
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_path = tf.name
            wav_bytes.seek(0)
            tf.write(wav_bytes.read())
        return _transcribe_whisper(tmp_path)
    except Exception as e:
        # v3.1 latch: fail ONCE, then never retry the slow broken
        # load again this session - the next engine takes over silently.
        _whisper_broken = True
        _whisper_broken_reason = str(e)
        print(f"[Offline voice had a problem ({e}). Using the online "
              f"recognizers for the rest of this session.]")
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def listen_once(seconds=6):
    """
    Record once and transcribe, trying engines in order.

    stt_engine config:
    - "auto" (default): Groq Whisper (fast, needs the brain's Groq key)
      -> local faster-whisper (offline) -> Google free recognizer.
    - "groq" / "whisper" / "google": that engine first, the remaining
      ones as fallbacks in the same call.
    A Groq STT error falls through immediately and latches Groq ears off
    for 60 seconds; a local-Whisper error latches it off for the session
    (v3.1). Returns the transcript, or None if nothing was understood.
    """
    global _groq_stt_broken_until
    wav_bytes = _record_wav_bytes()
    cfg = jarvis_config.load_config()
    engine = str(cfg.get("stt_engine", "auto")).lower()
    if engine == "google":
        order = ["google"]
    elif engine == "whisper":
        order = ["whisper", "groq", "google"]
    elif engine == "groq":
        order = ["groq", "whisper", "google"]
    else:
        order = ["groq", "whisper", "google"]

    for eng in order:
        if eng == "groq":
            if not _groq_key() or time.time() < _groq_stt_broken_until:
                continue
            try:
                result = _transcribe_groq(wav_bytes)
                if result:
                    return result
            except Exception as e:
                _groq_stt_broken_until = time.time() + 60
                print(f"[Fast ears (Groq) had a problem ({e}) - trying the next "
                      f"recognizer; Groq ears rest for a minute.]")
        elif eng == "whisper":
            result = _try_whisper(wav_bytes)
            if result:
                return result
        else:  # google - the last resort
            return _transcribe_google(wav_bytes)
    return None


def startup_greeting():
    """
    v5: the greeting is ONE short line - "Morning, Umesh. I'm
    listening." Battery warnings and the briefing offer are the
    butler's job now, delivered as their own alerts, not a monologue.
    """
    import datetime as _dt
    cfg = jarvis_config.load_config()
    owner = cfg.get("owner_name", "Umesh")
    hour = _dt.datetime.now().hour
    hello = "Morning" if hour < 12 else "Afternoon" if hour < 18 else "Evening"
    return f"{hello}, {owner}. I'm listening."


def _strip_wake_word(text):
    cfg = jarvis_config.load_config()
    wake = str(cfg.get("wake_word", "jarvis")).lower()
    q = text.lower().strip()
    if wake and wake in q:
        q = q.replace(wake, "").strip(" ,.!?")
    return q


def _start_butler(no_speak=False):
    """Start the v5 proactive butler for this loop (guarded, idempotent)."""
    try:
        jarvis_butler.set_speaker(lambda msg: present_reply(msg, no_speak=no_speak, source="task"))
        jarvis_butler.start()
    except Exception:
        pass


def _deliver_butler():
    try:
        jarvis_butler.deliver_pending()
    except Exception:
        pass


def _butler_response(q_clean, no_speak=False):
    """
    If a butler suggestion is pending, let it interpret this utterance.
    Returns True when the utterance was consumed (acted on / dismissed);
    the caller then skips normal processing for it.
    """
    try:
        handled = jarvis_butler.handle_response(q_clean)
    except Exception:
        return False
    if handled is None:
        return False
    outcome, payload = handled
    if outcome == "acted" and payload:
        present_reply(payload, no_speak=no_speak, source="action")
    # "dismissed" is silent by design - he said no, and that's okay.
    return True


def text_loop(no_speak=False):
    print("Text mode - type a command, or 'quit' to exit.")
    _register_announcer(no_speak=no_speak)
    _start_butler(no_speak=no_speak)
    _maybe_open_hud()
    _hud_emit("state", "idle")
    while True:
        _deliver_butler()
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nShutting down Jarvis. Goodbye!")
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "goodbye"):
            speak("Goodbye!", no_speak=no_speak)
            break
        _hud_emit("heard", user_input)
        if _butler_response(user_input.lower().strip(" .,!?"), no_speak=no_speak):
            _hud_emit("state", "idle")
            continue
        _hud_emit("state", "thinking")
        import jarvis_brain
        reply = jarvis_brain.think(user_input, confirm=_confirm_prompt)
        present_reply(reply, no_speak=no_speak, source=jarvis_brain.last_reply_source())
        _hud_emit("state", "idle")


def _maybe_open_hud():
    """Auto-open the live HUD once, when it is running and configured."""
    try:
        cfg = jarvis_config.load_config()
        if cfg.get("hud_auto_open", True) and jarvis_hud.is_running():
            jarvis_hud.open_hud()
    except Exception:
        pass


def _confirm_prompt(prompt):
    """Spoken/typed yes-confirmation for destructive actions."""
    print(prompt)
    try:
        answer = input("Type 'yes' to confirm: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return False
    return answer in ("yes", "y")


def listen_loop(no_speak=False):
    """Default voice loop; falls back to text mode when there is no mic."""
    if not mic_available():
        print("Microphone not available - falling back to text mode.")
        print("On your Mac: System Settings > Privacy & Security > Microphone > enable Terminal,")
        print("and make sure install.sh installed sounddevice into the Jarvis venv.")
        text_loop(no_speak=no_speak)
        return

    global _stop_listen_enabled
    cfg = jarvis_config.load_config()
    owner = cfg.get("owner_name", "Umesh")
    _register_announcer(no_speak=no_speak)
    _start_butler(no_speak=no_speak)
    if not no_speak:
        _stop_listen_enabled = True  # v5 barge-in: he can say "stop" mid-speech
    _maybe_open_hud()
    print("\n" + "=" * 54)
    print("  JARVIS AI ASSISTANT v5 - AUTONOMOUS BUTLER - IS LISTENING")
    print(f"  Welcome, {owner}. Say commands like:")
    print("    - 'Jarvis, open VS Code' / 'open ChatGPT' / 'open LinkedIn'")
    print("    - 'Play lofi on YouTube'")
    print("    - 'Stock market update' / 'Analyze Nvidia'")
    print("    - 'Battery status' / 'System info' / 'Take a screenshot'")
    print("    - 'Set volume to 50' / 'Toggle dark mode' / 'Lock screen'")
    print("    - 'Note buy milk' / 'Set a timer for 5 minutes'")
    print("    - 'Read my emails' (opens Gmail - Jarvis cannot read it directly)")
    print("    - 'Read my resume' / 'Read report.pdf' / 'Read more' (reads files aloud)")
    print("    - 'Open file resume.pdf' / 'Send a message to Mom saying hi'")
    print("    - 'Remind me to call Mom tomorrow at 6 pm' / 'Morning briefing'")
    print("    - 'Type hello world' / 'Play music' / 'Scroll down' / 'Click Save'")
    print("    - 'Search for electric cars and tell me' / 'Read this website'")
    print("    - 'Show me the job openings in Indianapolis in bioinformatics'")
    print("    - 'Create a python file called hello that prints the date'")
    print("    - 'Run it' / 'Fix it' / 'What are you doing' (background tasks)")
    print("    - 'Show yourself' (opens the live HUD) / 'Open browser'")
    print("  Actions are done silently now - Jarvis speaks only to tell you")
    print("  something. Say 'stop' while he talks to cut him off.")
    print("  Press Ctrl+C to stop Jarvis entirely.")
    print("=" * 54 + "\n")
    # v5: one short spoken line, not a monologue.
    if not no_speak:
        speak(startup_greeting(), no_speak=no_speak)
    else:
        print(startup_greeting())

    import jarvis_brain

    while True:
        try:
            # v5 butler: queued alerts/suggestions land between listen
            # cycles - never over his voice, never mid-sentence.
            _deliver_butler()
            _hud_emit("state", "listening")
            print("Listening... ", end="", flush=True)
            query = listen_once()
            if not query:
                print("(didn't catch that)")
                continue
            print(f'\nYou said: "{query}"')
            _hud_emit("heard", query)
            q_clean = _strip_wake_word(query)
            if not q_clean:
                speak("Yes, I'm listening.", no_speak=no_speak)
                continue
            if q_clean in ("quit", "exit", "goodbye", "stop listening"):
                speak("Goodbye!", no_speak=no_speak)
                break
            if _butler_response(q_clean, no_speak=no_speak):
                continue
            if _is_pure_stop(q_clean):
                # v5: "stop" as a whole utterance = stay silent, no reply.
                stop_speaking()
                print("(stopped - staying quiet; I'm still listening.)")
                continue
            _hud_emit("state", "thinking")
            response = jarvis_brain.think(q_clean, confirm=_confirm_prompt)
            present_reply(response, no_speak=no_speak,
                          source=jarvis_brain.last_reply_source())
        except KeyboardInterrupt:
            print("\nShutting down Jarvis. Goodbye!")
            break
        except Exception as e:
            _hud_emit("error", str(e))
            print(f"\n[Notice]: {e}")
            time.sleep(0.5)


# Backwards-compatible name from the original file.
def listen():
    listen_loop()


if __name__ == "__main__":
    listen_loop()
