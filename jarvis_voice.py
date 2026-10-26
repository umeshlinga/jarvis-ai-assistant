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

Speaking uses macOS `say` (voice + rate from config); on non-Mac systems
it prints only (pyttsx3 is an optional extra, never a requirement).
"""

import io
import os
import subprocess
import sys
import tempfile
import time
import wave

import jarvis_config

SAMPLE_RATE = 16000
CHANNELS = 1

_whisper_model_cache = None
# v3.1 speed fix: if the Whisper load fails ONCE (e.g. a library version
# clash on the machine), remember it for the whole session and go straight
# to the Google recognizer. v3.0 retried the slow failing load after every
# single sentence, which made every reply feel slow.
_whisper_broken = False
_whisper_broken_reason = ""


def speak(text, no_speak=False):
    """Print the reply, and speak it with macOS `say` when available."""
    if not text:
        return
    print(f"\n[JARVIS]: {text}\n")
    if no_speak:
        return
    if sys.platform == "darwin":
        cfg = jarvis_config.load_config()
        voice = str(cfg.get("voice", "Samantha"))
        rate = str(cfg.get("speech_rate", 185))
        try:
            subprocess.run(["say", "-v", voice, "-r", rate, text])
            return
        except Exception:
            pass
    # Non-Mac fallback: optional pyttsx3 if the user installed it, else print-only.
    try:
        import pyttsx3  # optional dependency (requirements-optional.txt)
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
    except Exception:
        pass


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
    if engine == "whisper":
        return "whisper (offline)" if _whisper_available() else "whisper requested but not installed - google fallback"
    if engine == "google":
        return "google (online)"
    # auto
    return "auto -> whisper (offline)" if _whisper_available() else "auto -> google (online)"


def listen_once(seconds=6):
    """
    Record once and transcribe. Engine from config stt_engine:
    "whisper" = fully offline (faster-whisper), "google" = online free
    recognizer, "auto" = Whisper when installed, else Google. A Whisper
    failure falls back to Google with the reason printed. Returns the
    transcript, or None if nothing was understood / no mic.
    """
    global _whisper_broken, _whisper_broken_reason
    wav_bytes = _record_wav_bytes()
    cfg = jarvis_config.load_config()
    engine = str(cfg.get("stt_engine", "auto")).lower()
    use_whisper = (engine == "whisper" or (engine == "auto" and _whisper_available())) \
        and not _whisper_broken
    if use_whisper:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tmp_path = tf.name
                wav_bytes.seek(0)
                tf.write(wav_bytes.read())
            return _transcribe_whisper(tmp_path)
        except Exception as e:
            # v3.1 latch: fail ONCE, then never retry the slow broken
            # load again this session - Google takes over silently.
            _whisper_broken = True
            _whisper_broken_reason = str(e)
            print(f"[Offline voice had a problem ({e}). Using the fast online "
                  f"recognizer for the rest of this session.]")
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
    return _transcribe_google(wav_bytes)


def startup_greeting():
    """
    Warm spoken greeting for voice mode: time-of-day hello by name,
    battery when on battery / low (psutil, guarded), and one suggestion.
    Returns the greeting text - the caller speaks it in full.
    """
    import datetime as _dt
    cfg = jarvis_config.load_config()
    owner = cfg.get("owner_name", "Umesh")
    hour = _dt.datetime.now().hour
    hello = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    parts = [f"{hello}, {owner}. I'm Jarvis, and I'm listening."]
    try:
        import psutil
        bat = psutil.sensors_battery()
        if bat is not None and (not bat.power_plugged or bat.percent < 40):
            parts.append(f"Your battery is at {round(bat.percent)} percent"
                         + ("" if bat.power_plugged else " and you're on battery power") + ".")
    except Exception:
        pass
    parts.append("Say 'morning briefing' for your day, or just talk to me - ask me anything, or tell me what to open.")
    return " ".join(parts)


def _strip_wake_word(text):
    cfg = jarvis_config.load_config()
    wake = str(cfg.get("wake_word", "jarvis")).lower()
    q = text.lower().strip()
    if wake and wake in q:
        q = q.replace(wake, "").strip(" ,.!?")
    return q


def text_loop(no_speak=False):
    print("Text mode - type a command, or 'quit' to exit.")
    while True:
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
        import jarvis_brain
        reply = jarvis_brain.think(user_input, confirm=_confirm_prompt)
        speak(reply, no_speak=no_speak)


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

    cfg = jarvis_config.load_config()
    owner = cfg.get("owner_name", "Umesh")
    print("\n" + "=" * 54)
    print("  JARVIS AI ASSISTANT v3 - FULL VOICE CONTROL - IS LISTENING")
    print(f"  Welcome, {owner}. Say commands like:")
    print("    - 'Jarvis, open VS Code' / 'open ChatGPT' / 'open LinkedIn'")
    print("    - 'Play lofi on YouTube'")
    print("    - 'Stock market update' / 'Analyze Nvidia'")
    print("    - 'Battery status' / 'System info' / 'Take a screenshot'")
    print("    - 'Set volume to 50' / 'Toggle dark mode' / 'Lock screen'")
    print("    - 'Note buy milk' / 'Set a timer for 5 minutes'")
    print("    - 'Read my emails' (opens Gmail - Jarvis cannot read it directly)")
    print("    - 'Open file resume.pdf' / 'Send a message to Mom saying hi'")
    print("    - 'Remind me to call Mom tomorrow at 6 pm' / 'Morning briefing'")
    print("    - 'Type hello world' / 'Play music' / 'What do you think about...?'")
    print("  Press Ctrl+C to stop.")
    print("=" * 54 + "\n")
    # Speak EVERYTHING: the startup greeting is spoken in full, like
    # every reply in the loop below.
    if not no_speak:
        speak(startup_greeting(), no_speak=no_speak)
    else:
        print(startup_greeting())

    import jarvis_brain

    while True:
        try:
            print("Listening... ", end="", flush=True)
            query = listen_once()
            if not query:
                print("(didn't catch that)")
                continue
            print(f'\nYou said: "{query}"')
            q_clean = _strip_wake_word(query)
            if not q_clean:
                speak("Yes, I'm listening.", no_speak=no_speak)
                continue
            if q_clean in ("quit", "exit", "goodbye", "stop listening"):
                speak("Goodbye!", no_speak=no_speak)
                break
            response = jarvis_brain.think(q_clean, confirm=_confirm_prompt)
            speak(response, no_speak=no_speak)
        except KeyboardInterrupt:
            print("\nShutting down Jarvis. Goodbye!")
            break
        except Exception as e:
            print(f"\n[Notice]: {e}")
            time.sleep(0.5)


# Backwards-compatible name from the original file.
def listen():
    listen_loop()


if __name__ == "__main__":
    listen_loop()
