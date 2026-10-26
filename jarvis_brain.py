"""
jarvis_brain.py v2 - Hybrid Cloud + Offline AI Brain with Rate Limit Failover.

v2 changes (Advanced / M1 build):
- Two providers, auto-selected in config order (default: Groq first, then
  OpenAI). Groq key: GROQ_API_KEY env or ~/.openjarvis/free.key.
  OpenAI key: OPENAI_API_KEY env or ~/.openjarvis/openai.key
  (base URL + model configurable, defaults api.openai.com/v1 / gpt-4.1-mini).
- Conversation history persists to ~/.openjarvis/history.json and the
  last 20 turns are kept / restored across runs.
- Local actions are still tried BEFORE any LLM call (original order kept),
  and the original rate-limit cooldown + local fallback logic is preserved.
- The OpenAI SDK is imported lazily, so local actions work even before
  `pip install` has run.
"""

import json
import os
import re
import time

import jarvis_actions
import jarvis_config

_client = None
_client_provider = None
_history = None  # loaded lazily from disk
_cooldown_until = 0.0

SYSTEM_PROMPT_TEMPLATE = """You are Jarvis, a fast, highly capable AI executive assistant running on {owner}'s Mac.
Respond concisely in 1-2 sentences. Be direct, helpful, and natural. Do not use filler or formal corporate jargon.
"""


def _history_file():
    return os.path.expanduser("~/.openjarvis/history.json")


def _load_history():
    global _history
    if _history is not None:
        return _history
    _history = []
    try:
        path = _history_file()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                _history = [m for m in data if isinstance(m, dict) and "role" in m and "content" in m]
    except Exception:
        _history = []
    return _history


def _save_history():
    try:
        cfg = jarvis_config.load_config()
        max_turns = int(cfg.get("max_history_turns", 20))
        hist = _load_history()[-(max_turns * 2):]
        d = os.path.expanduser("~/.openjarvis")
        os.makedirs(d, exist_ok=True)
        with open(_history_file(), "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=2)
    except Exception:
        pass


def _read_key(env_name, *filenames):
    if os.environ.get(env_name):
        return os.environ[env_name].strip()
    for name in filenames:
        path = os.path.expanduser(os.path.join("~/.openjarvis", name))
        if os.path.exists(path):
            try:
                value = open(path).read().strip()
                if value:
                    return value
            except Exception:
                pass
    return None


def groq_key():
    return _read_key("GROQ_API_KEY", "free.key", "groq.key")


def openai_key():
    return _read_key("OPENAI_API_KEY", "openai.key")


def providers_available():
    """Ordered list of providers that currently have a key."""
    cfg = jarvis_config.load_config()
    order = cfg.get("provider_order") or ["groq", "openai"]
    available = []
    for name in order:
        name = str(name).lower().strip()
        if name == "groq" and groq_key():
            available.append("groq")
        elif name == "openai" and openai_key():
            available.append("openai")
    return available


def active_provider():
    """The provider Jarvis will use right now, or None for local-only mode."""
    available = providers_available()
    return available[0] if available else None


def ready():
    """True if a cloud brain is configured and not currently rate-limited."""
    if not providers_available():
        return False
    return time.time() >= _cooldown_until


def _is_rate_limited(e):
    status = getattr(e, "status_code", None)
    if status == 429:
        return True
    s = str(e).lower()
    return any(w in s for w in ("429", "rate limit", "too many requests", "rate_limit", "exceeded"))


def _retry_after_seconds(e, default=60):
    s = str(e).lower()
    m = re.search(r"try again in\s*(\d+)m\s*([0-9]+(?:\.[0-9]+)?)s", s)
    if m:
        return int(float(m.group(1)) * 60 + float(m.group(2))) + 2
    m = re.search(r"try again in\s*([0-9]+(?:\.[0-9]+)?)s", s)
    if m:
        return int(float(m.group(1))) + 2
    return default


def _set_cooldown(secs):
    global _cooldown_until
    _cooldown_until = max(_cooldown_until, time.time() + float(secs))


def _get_client(provider):
    global _client, _client_provider
    if _client is not None and _client_provider == provider:
        return _client
    from openai import OpenAI  # lazy: local mode must work without the SDK

    cfg = jarvis_config.load_config()
    if provider == "groq":
        _client = OpenAI(api_key=groq_key(), base_url=cfg.get("groq_base_url"))
    else:
        _client = OpenAI(api_key=openai_key(), base_url=cfg.get("openai_base_url"))
    _client_provider = provider
    return _client


def _models_for(provider):
    cfg = jarvis_config.load_config()
    if provider == "groq":
        models = cfg.get("groq_models") or []
        return [str(m) for m in models] or ["openai/gpt-oss-20b"]
    return [str(cfg.get("openai_model") or "gpt-4.1-mini")]


def _local_fallback(q, confirm=None):
    """Called when the cloud API is unavailable or cooling down."""
    action_res = jarvis_actions.try_action(q, confirm=confirm)
    if action_res:
        return action_res
    return (
        "I'm in local mode right now - no cloud key is active or the API is cooling down. "
        "I can still open your apps and sites, control volume, take notes, set timers, "
        "check battery and system info, and analyze stock charts for you."
    )


def think(q, confirm=None):
    """
    Main entry point. Local actions first, then the cloud AI (Groq, else
    OpenAI), with automatic cooldown + local fallback on rate limits.
    """
    # 1. Immediate local action check (original order preserved)
    local_done = jarvis_actions.try_action(q, confirm=confirm)
    if local_done:
        return local_done

    # 2. Cloud providers in configured order
    if time.time() < _cooldown_until:
        return _local_fallback(q, confirm=confirm)

    cfg = jarvis_config.load_config()
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(owner=cfg.get("owner_name", "Umesh"))
    history = _load_history()
    msgs = [{"role": "system", "content": system_prompt}] + history[-12:] + [
        {"role": "user", "content": q}
    ]

    for provider in providers_available():
        client = None
        try:
            client = _get_client(provider)
        except Exception as e:
            print(f"[Provider {provider} unavailable: {e}]")
            continue
        for model in _models_for(provider):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=msgs,
                    max_tokens=350,
                    temperature=0.6,
                )
                answer = resp.choices[0].message.content.strip()
                history.extend([
                    {"role": "user", "content": q},
                    {"role": "assistant", "content": answer},
                ])
                max_turns = int(cfg.get("max_history_turns", 20))
                del history[: max(0, len(history) - max_turns * 2)]
                _save_history()
                return answer
            except Exception as e:
                if _is_rate_limited(e):
                    wait_sec = _retry_after_seconds(e)
                    _set_cooldown(wait_sec)
                    print(f"[Rate-limit triggered: cooldown for {wait_sec}s]")
                    return _local_fallback(q, confirm=confirm)
                print(f"[Model {model} on {provider} failed: {e}]")

    return _local_fallback(q, confirm=confirm)


def clear_history():
    global _history
    _history = []
    try:
        path = _history_file()
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


if __name__ == "__main__":
    print("Testing Jarvis Brain...")
    print("Active provider:", active_provider() or "local-only")
    print(think("what time is it"))
    print(think("how are you"))
