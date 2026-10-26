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
import subprocess
import sys
import time
import wave

import jarvis_config

SAMPLE_RATE = 16000
CHANNELS = 1


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


def _record_wav_bytes(seconds=6):
    """
    Record `seconds` of audio from the default mic with sounddevice and
    return WAV bytes (16 kHz, mono, 16-bit). Raises on any failure so the
    caller can fall back to text mode.
    """
    import numpy as np
    import sounddevice as sd

    frames = int(seconds * SAMPLE_RATE)
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(np.asarray(audio, dtype="int16").tobytes())
    buf.seek(0)
    return buf


def listen_once(seconds=6):
    """
    Record once and transcribe with SpeechRecognition's Google recognizer
    (the same free recognizer the original used, fed via AudioFile so no
    PyAudio is involved). Returns the transcript, or None if nothing was
    understood / no mic is available.
    """
    import speech_recognition as sr

    wav_bytes = _record_wav_bytes(seconds=seconds)
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = 0.8
    with sr.AudioFile(wav_bytes) as source:
        audio = recognizer.record(source)
    try:
        return recognizer.recognize_google(audio).strip()
    except sr.UnknownValueError:
        return None


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
    print("  JARVIS AI ASSISTANT v2 IS ACTIVE AND LISTENING")
    print(f"  Welcome, {owner}. Say commands like:")
    print("    - 'Jarvis, open VS Code' / 'open ChatGPT' / 'open LinkedIn'")
    print("    - 'Play lofi on YouTube'")
    print("    - 'Stock market update' / 'Analyze Nvidia'")
    print("    - 'Battery status' / 'System info' / 'Take a screenshot'")
    print("    - 'Set volume to 50' / 'Toggle dark mode' / 'Lock screen'")
    print("    - 'Note buy milk' / 'Set a timer for 5 minutes'")
    print("    - 'Read my emails' (opens Gmail - Jarvis cannot read it directly)")
    print("  Press Ctrl+C to stop.")
    print("=" * 54 + "\n")

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
