#!/usr/bin/env python3
"""
jarvis.py - Jarvis v6 (Eyes & Hands, M1) main entry point.

Usage:
    python jarvis.py                      Voice loop (default; text fallback if no mic)
    python jarvis.py --text               Text console loop
    python jarvis.py --once "open github" Run one command, print + speak, exit
    python jarvis.py --once "what time is it" --no-speak
    python jarvis.py --voice-test        Speak one sentence (human-voice check)

The v4 live HUD (jarvis_hud) is started for every mode on a daemon
thread: even in --once mode the process still exits normally.
"""

import argparse
import sys

import jarvis_brain
import jarvis_config
import jarvis_hud
import jarvis_notify
import jarvis_voice


def _confirm_prompt(prompt):
    print(prompt)
    try:
        answer = input("Type 'yes' to confirm: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return False
    return answer in ("yes", "y")


def _banner():
    cfg = jarvis_config.load_config()
    provider = jarvis_brain.active_provider()
    deep = jarvis_brain.deep_providers_available()
    # v7 brain split, stated honestly: with an OpenAI key, deep/agent
    # work runs on OpenAI while Groq stays the fast free chat + ears.
    if deep and deep[0] == "openai" and jarvis_brain.groq_key():
        brain_line = (f"Groq fast chat + OpenAI deep/agent "
                      f"({cfg.get('openai_model', 'gpt-4.1')})")
    elif provider == "groq":
        brain_line = "Groq (cloud) - models: " + ", ".join(jarvis_brain._models_for("groq"))
    elif provider == "openai":
        brain_line = "OpenAI (cloud) - model: " + ", ".join(jarvis_brain._models_for("openai"))
    else:
        brain_line = "Local-only (no Groq/OpenAI key found - local actions still work)"
    print("=" * 54)
    print("  JARVIS v6 - Eyes & Hands (Mac M1)")
    print(f"  Owner : {cfg.get('owner_name', 'Umesh')}")
    print(f"  Brain : {brain_line}")
    print(f"  Mic   : {jarvis_voice.mic_status()}")
    print(f"  Ears  : speech-to-text = {jarvis_voice.stt_status()}")
    print(f"  Voice : {jarvis_voice.tts_status()}")
    print(f"  Eyes  : {jarvis_notify.notify_status()} (say 'what did I miss')")
    print(f"  HUD   : live screen at {jarvis_hud.hud_url()} (say 'show yourself')")
    print("=" * 54)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jarvis v6 - Eyes & Hands AI assistant for Mac M1")
    parser.add_argument("--text", action="store_true", help="Text console loop instead of voice")
    parser.add_argument("--once", metavar="COMMAND", help="Run a single command and exit")
    parser.add_argument("--no-speak", action="store_true", help="Print replies, do not speak them")
    parser.add_argument("--voice-test", action="store_true",
                        help="Speak one test sentence (checks the human voice) and exit")
    args = parser.parse_args(argv)

    _banner()

    # v4 live HUD: localhost-only, daemon thread, guarded. If the port is
    # taken Jarvis simply runs without it.
    try:
        server = jarvis_hud.start_server()
        if server is not None:
            print(f"  HUD live at {jarvis_hud.hud_url()}")
            jarvis_hud.emit("state", "idle")
    except Exception:
        pass

    if args.voice_test:
        jarvis_voice.speak(
            "Hello Umesh, this is my new human voice. If I sound like a "
            "person talking to you, the neural voice is working.")
        return 0

    if args.once:
        reply = jarvis_brain.think(args.once, confirm=_confirm_prompt)
        # speak() prints too; with --no-speak we still want the reply printed once.
        if args.no_speak:
            print(reply)
        else:
            jarvis_voice.speak(reply)
        return 0

    if args.text:
        jarvis_voice.text_loop(no_speak=args.no_speak)
        return 0

    jarvis_voice.listen_loop(no_speak=args.no_speak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
