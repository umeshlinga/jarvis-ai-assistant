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
What you know about him: {personal_context} Use this when it is relevant - talk about HIS real situation, his work and his search, instead of giving generic advice that could be for anyone.
Your answers are SPOKEN aloud, so write the way people talk: natural sentences, no markdown, no bullet points, no headings, no code blocks. Sound like a person talking, never like a document or a plain text file being read out.
Brevity rule for voice: default answers are two to four natural sentences - enough to be genuinely helpful, short enough to enjoy listening to. Expand and explain in real depth ONLY when he asks you to - "explain in detail", "tell me more", and similar.
When he asks what you think, whether something is correct or wrong, or whether he should do something, give him a clear, honest view: say what looks right, what looks wrong, and why, and what you would suggest instead. Never fake certainty - if you do not know a fact, say so plainly, and do not invent numbers, names, or events.
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


def _models_for(provider, tier="deep"):
    """
    v4 two-speed brain. Groq has two tiers:
    - "fast": the small instant model first (config fast_model), with the
      deep models as fallbacks if it errors.
    - "deep": the existing groq_models list (reasoning models) - used for
      advisory / deep questions and by helper modules (summaries, code).
    OpenAI is a single-model provider and ignores the tier.
    """
    cfg = jarvis_config.load_config()
    if provider == "groq":
        deep = [str(m) for m in (cfg.get("groq_models") or [])] or ["openai/gpt-oss-20b"]
        if tier == "fast":
            fast = str(cfg.get("fast_model") or "llama-3.1-8b-instant")
            return [fast] + [m for m in deep if m != fast]
        return deep
    return [str(cfg.get("openai_model") or "gpt-4.1-mini")]


def _system_prompt():
    cfg = jarvis_config.load_config()
    return SYSTEM_PROMPT_TEMPLATE.format(
        owner=cfg.get("owner_name", "Umesh"),
        personal_context=cfg.get("personal_context", ""),
    )


def _complete_msgs(msgs, tier="fast", max_tokens=450):
    """
    Run one cloud completion in the given tier. Returns the answer text,
    or None when no provider is available, a rate limit set the cooldown,
    or every model failed. Callers decide their own fallback.
    """
    if time.time() < _cooldown_until:
        return None
    for provider in providers_available():
        try:
            client = _get_client(provider)
        except Exception as e:
            print(f"[Provider {provider} unavailable: {e}]")
            continue
        for model in _models_for(provider, tier=tier):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=msgs,
                    max_tokens=max_tokens,
                    temperature=0.6,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                if _is_rate_limited(e):
                    wait_sec = _retry_after_seconds(e)
                    _set_cooldown(wait_sec)
                    print(f"[Rate-limit triggered: cooldown for {wait_sec}s]")
                    return None
                print(f"[Model {model} on {provider} failed: {e}]")
    return None


def complete(user_text, system=None, deep=False, max_tokens=None):
    """
    One-shot helper for other modules (web summaries, code generation).
    Returns the model's text, or None when the cloud brain is unavailable.
    These calls do not touch the conversation history.
    """
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": user_text})
    if max_tokens is None:
        max_tokens = 900 if deep else 450
    return _complete_msgs(msgs, tier="deep" if deep else "fast", max_tokens=max_tokens)


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

    # 3. Cloud providers in configured order. Two speeds (v4): everyday
    #    chat runs on the fast tier; advisory/deep questions on the deep
    #    tier (reasoning models).
    if time.time() < _cooldown_until:
        return _local_fallback(q, confirm=confirm)

    cfg = jarvis_config.load_config()
    advisory = is_advisory(q)
    history = _load_history()
    msgs = [{"role": "system", "content": _system_prompt()}] + history[-12:] + [
        {"role": "user", "content": q}
    ]
    answer = _complete_msgs(
        msgs,
        tier="deep" if advisory else "fast",
        max_tokens=700 if advisory else 450,
    )
    if answer:
        history.extend([
            {"role": "user", "content": q},
            {"role": "assistant", "content": answer},
        ])
        max_turns = int(cfg.get("max_history_turns", 30))
        del history[: max(0, len(history) - max_turns * 2)]
        _save_history()
        return answer

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
