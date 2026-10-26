#!/usr/bin/env python3
"""
jarvis.py - Jarvis v3 (Full Voice Control, M1) main entry point.

Usage:
    python jarvis.py                      Voice loop (default; text fallback if no mic)
    python jarvis.py --text               Text console loop
    python jarvis.py --once "open github" Run one command, print + speak, exit
    python jarvis.py --once "what time is it" --no-speak
"""

import argparse
import sys

import jarvis_brain
import jarvis_config
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
    if provider == "groq":
        brain_line = "Groq (cloud) - models: " + ", ".join(jarvis_brain._models_for("groq"))
    elif provider == "openai":
        brain_line = "OpenAI (cloud) - model: " + ", ".join(jarvis_brain._models_for("openai"))
    else:
        brain_line = "Local-only (no Groq/OpenAI key found - local actions still work)"
    print("=" * 54)
    print("  JARVIS v3 - Full Voice Control (Mac M1)")
    print(f"  Owner : {cfg.get('owner_name', 'Umesh')}")
    print(f"  Brain : {brain_line}")
    print(f"  Mic   : {jarvis_voice.mic_status()}")
    print(f"  Ears  : speech-to-text = {jarvis_voice.stt_status()}")
    print(f"  Voice : {cfg.get('voice', 'Samantha')} @ {cfg.get('speech_rate', 185)} wpm (macOS say)")
    print("=" * 54)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jarvis v3 - Full Voice Control AI assistant for Mac M1")
    parser.add_argument("--text", action="store_true", help="Text console loop instead of voice")
    parser.add_argument("--once", metavar="COMMAND", help="Run a single command and exit")
    parser.add_argument("--no-speak", action="store_true", help="Print replies, do not speak them")
    args = parser.parse_args(argv)

    _banner()

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
