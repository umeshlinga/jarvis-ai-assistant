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
    "groq_models": [
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
        "qwen/qwen3.8-27b",
    ],
    "groq_base_url": "https://api.groq.com/openai/v1",
    # OpenAI (paid, optional): key in ~/.openjarvis/openai.key or OPENAI_API_KEY
    "openai_model": "gpt-4.1-mini",
    "openai_base_url": "https://api.openai.com/v1",
    "max_history_turns": 20,
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
