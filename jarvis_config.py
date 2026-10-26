"""
jarvis_config.py - Configuration for Jarvis v2 (Advanced, M1).

Config lives at ~/.openjarvis/config.json. If the file is missing or
broken, safe defaults are used - Jarvis never crashes over config.
Copy config.example.json to ~/.openjarvis/config.json to customise.
"""

import json
import os

CONFIG_DIR = os.path.expanduser("~/.openjarvis")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "owner_name": "Umesh",
    "wake_word": "jarvis",
    "voice": "Samantha",
    "speech_rate": 185,
    # Provider order: the first provider with a key available wins.
    "provider_order": ["groq", "openai"],
    # Groq (free tier): https://console.groq.com/keys
    # v7: the deep tier is headed by the bigger gpt-oss-120b; if the
    # free account doesn't offer it, the v4.1 dead-model latch skips it
    # automatically and the proven 20b answers.
    "groq_models": [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
        "qwen/qwen3.8-27b",
    ],
    "groq_base_url": "https://api.groq.com/openai/v1",
    # OpenAI (paid, optional): key in ~/.openjarvis/openai.key or OPENAI_API_KEY
    # v7: gpt-4.1 is the deep/agent brain when a key exists, with
    # gpt-4.1-mini as the automatic fallback model on error.
    "openai_model": "gpt-4.1",
    "openai_base_url": "https://api.openai.com/v1",
    # Which provider does DEEP work (advisory answers, summaries, the
    # agent loop): "auto" = OpenAI first when a key exists, else Groq;
    # "groq" / "openai" force a side. Fast chat + ears stay on Groq.
    "deep_provider": "auto",
    "max_history_turns": 30,
    # Speech-to-text: "auto" (Whisper if installed, else Google) |
    # "google" (online, free recognizer) | "whisper" (fully offline).
    "stt_engine": "auto",
    "whisper_model": "base.en",
    # Recognition language for the Google recognizer. en-IN matches
    # Indian-English pronunciation/spellings better for Umesh than en-US.
    "stt_language": "en-IN",
    # Occasional short follow-up suggestions after a few actions.
    "suggestions": True,
    # ---- v5 "Autonomous Butler" ----
    # Speak policy: False (default) = pure action acknowledgements
    # ("Opening Chrome.", "Volume set to 50 percent.") are PRINTED and
    # shown on the HUD but NOT spoken - Jarvis speaks only when he has
    # something to tell you. True restores the pre-v5 behaviour of
    # speaking every reply in full.
    "speak_actions": False,
    # Proactive butler: background checks (Reminders due soon, Calendar
    # events starting soon, low battery, a morning briefing offer, one
    # morning job suggestion) delivered between listen cycles.
    "proactive_enabled": True,
    "proactive_interval_min": 30,
    "proactive_jobs": True,
    # ---- v4 "Supercomputer" ----
    # Groq Whisper speech-to-text (uses the same Groq key as the brain).
    # v4.1: whisper-large-v3 (full model) - accuracy over the turbo
    # variant; it is still fast on Groq and hears Indian-English domain
    # words (bioinformatics, Indianapolis...) far better.
    "groq_stt_model": "whisper-large-v3",
    # Vocabulary hint sent to Whisper: biases transcription towards the
    # words Umesh actually says (names, his field, his apps). Edit it in
    # ~/.openjarvis/config.json to teach the ears new words.
    "stt_vocab_prompt": (
        "Jarvis. Umesh Linga, bioinformatics, Indianapolis, LinkedIn, "
        "resume, GitHub, VS Code, WhatsApp, iMessage, screenshot, reminder."
    ),
    # Fast everyday chat model (Groq). Advisory/deep questions use the
    # deep tier (groq_models above) instead. v4.1: gpt-oss-20b - Groq no
    # longer offers llama-3.1-8b-instant on this account (live 404s), and
    # gpt-oss-20b is already proven on his key as the deep tier's first
    # model. A saved "llama-3.1-8b-instant" value is auto-migrated by
    # jarvis_brain (treated as unset).
    "fast_model": "openai/gpt-oss-20b",
    # ---- v4.1 human voice (neural text-to-speech) ----
    # tts_engine: "auto" (Groq neural -> OpenAI neural -> macOS say) |
    # "groq" | "openai" | "say" (force the built-in Mac voice).
    # Neural voices need internet; offline Jarvis falls back to `say`.
    "tts_engine": "auto",
    # Groq neural voice (Orpheus, via the same Groq key as the brain).
    "groq_tts_model": "canopylabs/orpheus-v1-english",
    # Orpheus voices: daniel, austin, troy (male); hannah, autumn, diana (female).
    "tts_voice": "daniel",
    # OpenAI neural fallback (only when an OpenAI key is configured).
    "openai_tts_model": "gpt-4o-mini-tts",
    "openai_tts_voice": "nova",
    # Personal context for the brain's system prompt. Local only, in
    # ~/.openjarvis/config.json - edit it there to change what Jarvis
    # knows about you.
    "personal_context": (
        "Umesh Linga, bioinformatician in Indianapolis, 5+ years experience "
        "(Cipla, Indiana University, Karyon Bio), currently job searching in "
        "bioinformatics/data/AI roles, on F-1 STEM OPT."
    ),
    # Live visual HUD (local web page showing what Jarvis is doing).
    "hud_port": 8765,
    "hud_auto_open": True,
    # Sandbox folder for code Jarvis writes/runs by voice.
    "code_dir": "~/jarvis-projects",
    # ---- v6 "Eyes & Hands" ----
    # Notification + pop-up watcher: reads the macOS Notification
    # Center database (needs Full Disk Access for Terminal) and watches
    # for dialog windows, judges what is important, announces it
    # between listen cycles, and can type replies / click pop-up
    # buttons on your word. notify_interval_sec is the poll cadence.
    "notify_enabled": True,
    "notify_interval_sec": 5,
    # "Muse mode": the tool-using agent loop for multi-step goals
    # ("do this task ...", "... and then ..."). agent_max_steps caps
    # how many tool steps one goal may take before Jarvis stops and
    # summarises what got done.
    "agent_enabled": True,
    "agent_max_steps": 8,
}

_cache = None


def config_path():
    return os.path.expanduser("~/.openjarvis/config.json")


def load_config(force_reload=False):
    """Return the merged config dict (defaults + user file)."""
    global _cache
    if _cache is not None and not force_reload:
        return _cache
    cfg = dict(DEFAULTS)
    try:
        path = config_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            if isinstance(user_cfg, dict):
                cfg.update(user_cfg)
    except Exception:
        # A broken config file must never stop Jarvis.
        pass
    _cache = cfg
    return cfg


def get(key, default=None):
    cfg = load_config()
    if key in cfg:
        return cfg[key]
    if default is not None:
        return default
    return DEFAULTS.get(key)


def openjarvis_dir():
    d = os.path.expanduser("~/.openjarvis")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


if __name__ == "__main__":
    print(json.dumps(load_config(), indent=2))
