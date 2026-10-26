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


def _morning_briefing():
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
