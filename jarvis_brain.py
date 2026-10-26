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

SYSTEM_PROMPT_TEMPLATE = """You are Jarvis, {owner}'s friendly companion and thinking partner, living on his Mac and talking with him face to face, in voice.
Be warm, direct, and conversational - like a smart friend sitting next to him, not a corporate help desk. Address him by name ({owner}) naturally, sometimes - not in every sentence.
Your answers are SPOKEN aloud, so write the way people talk: natural sentences, no markdown, no bullet points, no headings, no code blocks. Explain things properly - talk through what something is, why it matters, and what it means for him - instead of one-line data drops.
When he asks what you think, whether something is correct or wrong, or whether he should do something, give him a clear, honest view: say what looks right, what looks wrong, and why, and what you would suggest instead. Never fake certainty - if you do not know a fact, say so plainly, and do not invent numbers, names, or events.
Keep most answers under about 150 words so they are pleasant to listen to, and go longer only when he asks you to explain in depth.
"""

# Opinion / advice phrasings. These go to the LLM even when a local
# keyword might superficially match - he is asking Jarvis to THINK.
ADVISORY_PATTERNS = (
    "what do you think about",
    "what do you think of",
    "what do you think",
    "do you think",
    "is it correct",
    "is this correct",
    "is that correct",
    "is it right",
    "is this right",
    "is that right",
    "is it wrong",
    "is this wrong",
    "is that wrong",
    "should i",
    "your opinion",
    "what's your opinion",
    "what is your opinion",
    "advise me",
    "give me advice",
    "your advice",
)

# Commands that must never be mistaken for advice questions.
_COMMAND_STARTS = ("remind me", "remember", "create", "send", "open", "set",
                   "add event", "note", "call", "email", "move", "delete",
                   "trash", "play", "type", "press", "copy", "find")


def is_advisory(q):
    """True for opinion/advice questions that should reach the LLM."""
    low = " " + (q or "").lower().strip(" .,!?") + " "
    head = low.strip()
    if head.startswith(_COMMAND_STARTS):
        # "remind me I should call..." etc. are commands, not questions.
        if not any(p in low for p in ("what do you think", "your opinion", "advise me")):
            return False
    return any(p in low for p in ADVISORY_PATTERNS)


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
    try:
        owner = jarvis_config.load_config().get("owner_name", "Umesh")
    except Exception:
        owner = "Umesh"
    return (
        f"I'm offline right now, {owner} - either there's no internet or my cloud brain is "
        "cooling down, so I can't really chat or look things up at the moment. But I can still "
        "do a lot locally: open your apps and any file by name, send iMessages, set reminders "
        "and calendar events, control music and volume, type and press keys for you, take "
        "notes and screenshots, and check your battery. Just tell me what you need."
    )


def _advisory_offline(q):
    try:
        owner = jarvis_config.load_config().get("owner_name", "Umesh")
    except Exception:
        owner = "Umesh"
    return (
        f"Honestly, {owner}, I can't think that one through properly while I'm offline - "
        "my opinions and explanations come from my online brain, and right now I can only "
        "follow commands and local facts. Ask me again when we're back online, or give me "
        "something to do on the Mac in the meantime."
    )


def think(q, confirm=None):
    """
    Main entry point. Local actions first, then the cloud AI (Groq, else
    OpenAI), with automatic cooldown + local fallback on rate limits.
    """
    # 1. Opinion/advice questions go to the LLM first - he is asking
    #    Jarvis to think, not to run a command. Offline, say so honestly.
    if is_advisory(q):
        if not providers_available() or time.time() < _cooldown_until:
            return _advisory_offline(q)
        # fall through to the cloud call below (skip local actions)
    else:
        # 2. Immediate local action check (original order preserved)
        local_done = jarvis_actions.try_action(q, confirm=confirm)
        if local_done:
            return local_done

    # 3. Cloud providers in configured order
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
                    max_tokens=700,
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
