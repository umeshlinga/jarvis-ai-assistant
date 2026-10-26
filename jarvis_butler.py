"""
jarvis_butler.py - Jarvis v5 proactive butler.

Owner's ask: "In the backend it should run automatically; if something
is important it should remind me - suggest 'Umesh, there is an opening
like this, can you open it or not?' If I say no, okay."

A daemon thread checks every `proactive_interval_min` minutes (default
30) while a voice/text loop runs:
- Apple Reminders due within the window, or overdue
- Calendar events starting within the window
- battery at 20% or less and not charging
- a morning briefing offer (once per day, after 7 AM)
- one morning job suggestion (config proactive_jobs): Jarvis counts
  fresh web results for his field + city and asks "Want me to open
  them?" - yes runs the real job search, no dismisses silently.

Alerts are QUEUED and delivered by the loop between listen cycles -
never over his speech. Each alert fires once per item per day
(in-memory seen-set). Actionable alerts are questions: they set a
pending suggestion that his NEXT utterance answers (yes/open it runs
the stored action; no/leave it dismisses; anything else lets the
suggestion expire and is handled normally).

Every provider is individually guarded to silence: a Mac without
Calendar permission simply produces no calendar alerts. Providers are
module-level functions so tests can inject fakes.
"""

import datetime
import queue
import threading

import jarvis_config

_alert_queue = queue.Queue()
_seen = set()                 # "key|YYYY-MM-DD" - once per item per day
_pending = None               # {"action": callable} awaiting his yes/no
_pending_lock = threading.Lock()
_speaker = None               # callable(str) registered by the loops
_started = False
_thread = None


def set_speaker(fn):
    """Register how alerts are delivered (voice loop: spoken; text: printed)."""
    global _speaker
    _speaker = fn


def _say(text):
    fn = _speaker
    if fn is not None:
        try:
            fn(text)
            return
        except Exception:
            pass
    print(f"\n[JARVIS]: {text}\n")


# --------------------------------------------------------------------------
# Providers - real data, each guarded to silence. Tests monkeypatch these.
# --------------------------------------------------------------------------

def reminders_due(window_min=30):
    """[(title, seconds_until_due)] for incomplete Reminders due within
    the window (negative seconds = overdue). [] on any failure."""
    try:
        import jarvis_actions
        window_secs = int(window_min) * 60
        script = (
            "set nowDate to current date\n"
            "set out to \"\"\n"
            "tell application \"Reminders\"\n"
            "set rl to reminders in default list whose completed is false\n"
            "repeat with r in rl\n"
            "try\n"
            "set dd to due date of r\n"
            "if dd is not missing value then\n"
            "set secs to (dd - nowDate)\n"
            f"if secs is less than or equal to {window_secs} then "
            "set out to out & (name of r) & \"|\" & secs & linefeed\n"
            "end if\n"
            "end try\n"
            "end repeat\n"
            "end tell\n"
            "return out"
        )
        rc, out = jarvis_actions._osa_run(script)
        if rc != 0 or not out:
            return []
        return _parse_timed(out)
    except Exception:
        return []


def events_starting(window_min=30):
    """[(title, seconds_until_start)] for Calendar events starting
    within the window. [] on any failure."""
    try:
        import jarvis_actions
        window_secs = int(window_min) * 60
        script = (
            "set nowDate to current date\n"
            "set out to \"\"\n"
            "tell application \"Calendar\"\n"
            "repeat with cal in calendars\n"
            "try\n"
            f"set evs to (every event of cal whose start date is greater "
            f"than or equal to (nowDate - 60) and start date is less than "
            f"or equal to (nowDate + {window_secs}))\n"
            "repeat with e in evs\n"
            "set out to out & (summary of e) & \"|\" & "
            "((start date of e) - nowDate) & linefeed\n"
            "end repeat\n"
            "end try\n"
            "end repeat\n"
            "end tell\n"
            "return out"
        )
        rc, out = jarvis_actions._osa_run(script)
        if rc != 0 or not out:
            return []
        return _parse_timed(out)
    except Exception:
        return []


def _parse_timed(out):
    items = []
    for line in (out or "").splitlines():
        if "|" not in line:
            continue
        name, secs = line.rsplit("|", 1)
        try:
            items.append((name.strip(), float(secs)))
        except Exception:
            continue
    return items


def battery_state():
    """{"percent": float, "plugged": bool} or None (guarded)."""
    try:
        import psutil
        bat = psutil.sensors_battery()
        if bat is None:
            return None
        return {"percent": float(bat.percent), "plugged": bool(bat.power_plugged)}
    except Exception:
        return None


def _job_findings_count():
    """How many fresh web results exist for his field + city today."""
    try:
        import jarvis_web
        results = jarvis_web._fetch_results("bioinformatics jobs Indianapolis", limit=5)
        return len(results)
    except Exception:
        return 0


def _open_job_results():
    """The suggestion's action: the real v4.1 job search (opens + tells)."""
    import jarvis_web
    return jarvis_web.job_search(
        "show me the job openings in Indianapolis in bioinformatics")


def _trim_words(text, limit=150):
    """Trim a briefing to ~`limit` words at a sentence boundary."""
    words = (text or "").split()
    if len(words) <= limit:
        return text
    cut = " ".join(words[:limit])
    for end in (". ", "! ", "? "):
        idx = cut.rfind(end)
        if idx > len(cut) // 2:
            return cut[:idx + 1]
    return cut.rstrip() + "…"


def morning_briefing_text(now=None):
    """
    v7: the composed morning briefing - greeting + date/time, today's
    reminders and calendar (the butler's own providers), a market
    snapshot (jarvis_market, guarded), and the top 3 news headlines
    (jarvis_web.news_headlines, guarded). Every section is omitted
    gracefully when its source fails; there is deliberately NO weather
    section - Jarvis has no weather source and never invents one.
    Kept speakable: ~150 words maximum.
    """
    cfg = jarvis_config.load_config()
    try:
        owner = str(cfg.get("owner_name", "Umesh"))
    except Exception:
        owner = "Umesh"
    now = now or datetime.datetime.now()
    when = now.strftime("%A, %B %d").replace(" 0", " ")
    clock = now.strftime("%I:%M %p").lstrip("0")
    parts = [f"Good morning, {owner}. It's {when}, {clock}."]
    sections = 0

    try:
        rems = reminders_due(12 * 60)
        if rems:
            sections += 1
            names = ", ".join(t for t, _ in rems[:3])
            parts.append(f"You have {len(rems)} reminder"
                         f"{'s' if len(rems) != 1 else ''} due today: {names}.")
    except Exception:
        pass
    try:
        evs = events_starting(12 * 60)
        if evs:
            sections += 1
            bits = []
            for title, secs in evs[:3]:
                at = (now + datetime.timedelta(seconds=secs)).strftime("%I:%M %p").lstrip("0")
                bits.append(f"{title} at {at}")
            parts.append("On your calendar: " + ", ".join(bits) + ".")
    except Exception:
        pass
    try:
        import jarvis_market
        snap = jarvis_market.market_briefing()
        if snap and str(snap).strip():
            sections += 1
            parts.append("Markets: " + str(snap).strip())
    except Exception:
        pass
    try:
        import jarvis_web
        heads = jarvis_web.news_headlines(limit=3)
        if heads:
            sections += 1
            parts.append("In the news: " + " ".join(
                f"{i + 1}. {h}" for i, h in enumerate(heads)) + ".")
    except Exception:
        pass
    if not sections:
        return ""
    return _trim_words(" ".join(parts), 150)


def _morning_briefing():
    """The suggestion's action: the composed v7 briefing, falling back
    to the v3 actions briefing when every live source is unavailable."""
    text = morning_briefing_text()
    if text:
        return text
    import jarvis_actions
    return jarvis_actions.morning_briefing()


# --------------------------------------------------------------------------
# Alert collection (once per item per day)
# --------------------------------------------------------------------------

def _already(key, day):
    return f"{key}|{day}" in _seen


def _mark(key, day):
    _seen.add(f"{key}|{day}")


def collect_alerts(now=None):
    """
    Build the alerts that are due right now. Pure-ish: providers are
    module functions (injectable), `now` is injectable, and every item
    is marked seen as it is collected so it never fires twice in a day.
    """
    cfg = jarvis_config.load_config()
    if not cfg.get("proactive_enabled", True):
        return []
    now = now or datetime.datetime.now()
    day = now.date().isoformat()
    window = int(cfg.get("proactive_interval_min", 30))
    try:
        owner = str(cfg.get("owner_name", "Umesh"))
    except Exception:
        owner = "Umesh"
    alerts = []

    # Morning briefing offer - once per day, after 7 AM.
    if now.hour >= 7 and not _already("morning-offer", day):
        _mark("morning-offer", day)
        alerts.append({
            "kind": "suggestion",
            "text": (f"{owner}, want your morning briefing? It has your "
                     f"reminders and your day. Say yes, or just tell me "
                     f"what you need."),
            "action": _morning_briefing,
        })

    # Reminders due soon / overdue.
    try:
        for title, secs in reminders_due(window):
            if _already(f"reminder:{title}", day):
                continue
            _mark(f"reminder:{title}", day)
            if secs < -90:
                text = (f"{owner}, reminder: {title} was due "
                        f"{int(-secs // 60)} minutes ago.")
            elif secs < 90:
                text = f"{owner}, reminder: {title} is due now."
            else:
                text = (f"{owner}, reminder: {title} is due in "
                        f"{round(secs / 60)} minutes.")
            alerts.append({"kind": "alert", "text": text})
    except Exception:
        pass

    # Calendar events starting soon.
    try:
        for title, secs in events_starting(window):
            if _already(f"event:{title}", day):
                continue
            _mark(f"event:{title}", day)
            if secs < 90:
                text = f"{owner}, '{title}' is starting now."
            else:
                when = (now + datetime.timedelta(seconds=secs)).strftime("%I:%M %p").lstrip("0")
                text = (f"{owner}, your '{title}' starts at {when} - "
                        f"in {round(secs / 60)} minutes.")
            alerts.append({"kind": "alert", "text": text})
    except Exception:
        pass

    # Low battery, not charging.
    try:
        bat = battery_state()
        if bat and bat.get("percent", 100) <= 20 and not bat.get("plugged", True) \
                and not _already("battery-low", day):
            _mark("battery-low", day)
            alerts.append({
                "kind": "alert",
                "text": (f"{owner}, your battery is at "
                         f"{round(bat['percent'])} percent and not "
                         f"charging - plug in soon."),
            })
    except Exception:
        pass

    # One morning job suggestion per day (only when there ARE results).
    try:
        if cfg.get("proactive_jobs", True) and now.hour >= 7 \
                and not _already("jobs-suggestion", day):
            count = _job_findings_count()
            if count:
                _mark("jobs-suggestion", day)
                alerts.append({
                    "kind": "suggestion",
                    "text": (f"{owner}, I found {count} bioinformatics job "
                             f"results in Indianapolis this morning. Want "
                             f"me to open them? Say yes to open, or no to "
                             f"leave it."),
                    "action": _open_job_results,
                })
    except Exception:
        pass

    return alerts


# --------------------------------------------------------------------------
# Background thread + delivery + the yes/no exchange
# --------------------------------------------------------------------------

def _loop():
    while True:
        try:
            for alert in collect_alerts():
                _alert_queue.put(alert)
        except Exception:
            pass
        try:
            interval = int(jarvis_config.load_config().get(
                "proactive_interval_min", 30)) * 60
        except Exception:
            interval = 1800
        threading.Event().wait(max(60, interval))


def start():
    """Start the butler thread (idempotent; no-op when disabled)."""
    global _started, _thread
    if _started:
        return
    try:
        if not jarvis_config.load_config().get("proactive_enabled", True):
            return
    except Exception:
        pass
    _started = True
    _thread = threading.Thread(target=_loop, name="jarvis-butler", daemon=True)
    _thread.start()


def queue_alert(text, kind="alert"):
    """
    v6: push a plain (non-suggestion) alert onto the delivery queue.
    jarvis_notify uses this so notification announcements ride the exact
    same between-listen-cycles delivery path as butler alerts - no
    second queue, no second speaker.
    """
    if text:
        _alert_queue.put({"kind": kind, "text": text})


def deliver_pending():
    """
    Speak/print every queued alert. A suggestion sets the pending state
    BEFORE it is spoken, so his immediate yes/no is understood. Returns
    how many alerts were delivered. Called by the loops between cycles.
    """
    global _pending
    delivered = 0
    while True:
        try:
            alert = _alert_queue.get_nowait()
        except queue.Empty:
            break
        if alert.get("action") is not None:
            with _pending_lock:
                _pending = {"action": alert["action"]}
        _say(alert["text"])
        delivered += 1
    return delivered


_YES_WORDS = {
    "yes", "yeah", "yep", "ok", "okay", "sure", "open it", "open",
    "open them", "go ahead", "yes please", "please do", "do it",
}
_NO_WORDS = {
    "no", "nope", "leave it", "not now", "later", "dismiss",
    "no thanks", "no thank you", "don't", "dont",
}


def handle_response(q):
    """
    If a suggestion is pending, interpret this utterance as its answer.
    Returns ("acted", result_text) for yes, ("dismissed", None) for no,
    or None when there is no pending suggestion - or when the utterance
    is something else entirely (the suggestion expires after this one
    exchange and the utterance is then handled normally by the caller).
    """
    global _pending
    with _pending_lock:
        pending, _pending = _pending, None
    if not pending:
        return None
    low = (q or "").strip().lower().strip(" .,!?")
    if low in _YES_WORDS:
        try:
            result = pending["action"]()
        except Exception as e:
            result = f"I tried, but something went wrong: {e}"
        return ("acted", result)
    if low in _NO_WORDS:
        return ("dismissed", None)
    return None


def has_pending():
    with _pending_lock:
        return _pending is not None


def _reset_for_tests():
    global _pending, _started
    _seen.clear()
    with _pending_lock:
        _pending = None
    while True:
        try:
            _alert_queue.get_nowait()
        except queue.Empty:
            break
    _started = False
