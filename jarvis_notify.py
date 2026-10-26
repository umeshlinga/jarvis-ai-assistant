"""
jarvis_notify.py - Jarvis v6 "Eyes & Hands": notification + pop-up watcher.

Owner's ask: "If I get any pop-up messages, if it is important Jarvis
should THINK and tell me - and it should reply also: if I say reply, it
should type and reply."

A daemon thread polls two sources (config notify_enabled, every
notify_interval_sec seconds, default 5):

A. The macOS Notification Center database
   (~/Library/Group Containers/group.com.apple.usernoted/db2/db).
   The schema varies between macOS versions, so tables/columns are
   discovered at runtime and the record payload (a binary plist) is
   parsed defensively. Reading it needs Full Disk Access for Terminal;
   without it this source reports "needs Full Disk Access" ONCE (see
   notify_status()) and the watcher carries on with pop-ups only.

B. An Accessibility (System Events) dialog watcher: one osascript query
   per poll lists every window of every process; windows that were not
   in the baseline (alert-like or not) become pop-up candidate events.

Every event is deduped (same app+title+body fires once per day, the
butler's discipline), judged for importance (rules first, then the
fast-tier brain for the rest), and:
- silent events go to the HUD only (kind "note");
- important events are announced through the BUTLER's alert queue
  (spoken between listen cycles, never over his voice) and set a
  pending state his next utterance can answer: "reply" starts a reply
  flow for messages/mail, a button name answers a pop-up.

Everything is guarded: no database, no osascript, no permission - the
watcher degrades silently and never error-loops.
"""

import collections
import datetime
import hashlib
import os
import plistlib
import re
import sqlite3
import subprocess
import threading

import jarvis_butler
import jarvis_config
import jarvis_hud

# ---------------------------------------------------------------------------
# State (in-memory only; a restart re-baselines silently)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_seen = {}                                   # event hash -> ISO day fired
_recent = collections.deque(maxlen=20)       # important events (ring buffer)
_pending_reply = None        # {"app", "sender", "body"} awaiting "reply"
_awaiting_dictation = False  # he said "reply"; next utterance is the text
_pending_send = None         # {"app"} - a typed reply awaiting "send it"
_pending_popup = None        # {"app", "buttons"} awaiting a button name
_started = False
_thread = None

_watermark = None            # max Notification Center rec_id seen
_ncdb_primed = False
_ncdb_status = None          # "ok" | "needs Full Disk Access" | "unavailable"
_ax_baseline = set()
_ax_primed = False


def _owner():
    try:
        return str(jarvis_config.load_config().get("owner_name", "Umesh"))
    except Exception:
        return "Umesh"


def _hud_note(text):
    try:
        jarvis_hud.emit("note", text)
    except Exception:
        pass


def _shorten(text, limit=140):
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(t) <= limit:
        return t
    cut = t.rfind(" ", 0, limit)
    return (t[:cut] if cut > limit // 2 else t[:limit]).rstrip() + "…"


# ---------------------------------------------------------------------------
# Source A: Notification Center database
# ---------------------------------------------------------------------------

_APP_NAMES = {
    "com.apple.mobilesms": "Messages",
    "com.apple.mobilemail": "Mail",
    "com.apple.mail": "Mail",
    "com.microsoft.outlook": "Outlook",
    "net.whatsapp.whatsapp": "WhatsApp",
    "net.whatsapp.whatsapp.sm": "WhatsApp",
    "org.telegram.desktop": "Telegram",
    "org.telegram.messenger": "Telegram",
    "org.whispersystems.signal-desktop": "Signal",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.apple.ical": "Calendar",
    "com.apple.reminders": "Reminders",
    "com.apple.calendar": "Calendar",
    "com.apple.facetime": "FaceTime",
    "com.apple.news": "News",
    "com.apple.tips": "Tips",
    "com.apple.appstore": "App Store",
}


def ncdb_path():
    """Module-level so tests can point the watcher at a fixture DB."""
    return os.path.expanduser(
        "~/Library/Group Containers/group.com.apple.usernoted/db2/db")


def _friendly_app(identifier):
    ident = str(identifier or "").strip()
    if not ident:
        return "An app"
    if ident.lower() in _APP_NAMES:
        return _APP_NAMES[ident.lower()]
    # A bundle id we don't know: last component, tidied ("com.foo.Bar" -> "Bar").
    last = ident.rsplit(".", 1)[-1]
    return last[:1].upper() + last[1:] if last else ident


def _probe_ncdb():
    """Cached one-shot status of the Notification Center source."""
    global _ncdb_status
    if _ncdb_status is not None:
        return _ncdb_status
    path = ncdb_path()
    if not os.path.exists(path):
        _ncdb_status = "unavailable"
        return _ncdb_status
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        conn.close()
        _ncdb_status = "ok"
    except Exception:
        _ncdb_status = "needs Full Disk Access"
    return _ncdb_status


def notify_status():
    """One honest line about what the eyes can currently see."""
    try:
        if not jarvis_config.load_config().get("notify_enabled", True):
            return "off (notify_enabled is false)"
    except Exception:
        pass
    status = _ncdb_status or _probe_ncdb()
    if status == "ok":
        return "notifications + pop-ups watched"
    if status == "needs Full Disk Access":
        return ("pop-ups watched; Notification Center needs Full Disk "
                "Access for Terminal (see README)")
    return "pop-ups watched; no Notification Center database found"


def _first_str(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _parse_ncdb_record(identifier, data):
    """One record's binary plist -> an event dict, or None."""
    try:
        d = plistlib.loads(bytes(data))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    req = d.get("req")
    if not isinstance(req, dict):
        req = d
    title = _first_str(req, ("titl", "title")) or _first_str(d, ("titl", "title"))
    subtitle = _first_str(req, ("subt", "subtitle"))
    body = _first_str(req, ("body", "text", "message"))
    app = _friendly_app(identifier)
    low = app.lower()
    if low in ("messages", "whatsapp", "telegram", "signal", "slack", "messenger"):
        kind, sender = "message", title
    elif low in ("mail", "outlook"):
        kind, sender = "mail", title
    elif low in ("calendar", "reminders"):
        kind, sender = "alert", app
    else:
        kind, sender = "notification", title
    return {
        "kind": kind, "app": app, "identifier": str(identifier or ""),
        "sender": sender, "title": title, "subtitle": subtitle, "body": body,
        "texts": [t for t in (title, subtitle, body) if t],
    }


def _ncdb_events():
    """New Notification Center events since the watermark. [] on any problem."""
    global _watermark, _ncdb_primed, _ncdb_status
    path = ncdb_path()
    if not os.path.exists(path):
        _ncdb_status = "unavailable"
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except Exception:
        _ncdb_status = "needs Full Disk Access"
        return []
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "record" not in tables:
            _ncdb_status = "unavailable"
            return []
        cols = {r[1] for r in conn.execute("PRAGMA table_info(record)")}
        if "data" not in cols:
            _ncdb_status = "unavailable"
            return []
        idcol = "rec_id" if "rec_id" in cols else "rowid"
        appmap = {}
        if "app" in tables:
            try:
                acols = {r[1] for r in conn.execute("PRAGMA table_info(app)")}
                idc = "identifier" if "identifier" in acols else None
                if idc:
                    for aid, ident in conn.execute(
                            f"SELECT app_id, {idc} FROM app"):
                        appmap[aid] = ident
            except Exception:
                appmap = {}
        if not _ncdb_primed:
            row = conn.execute(f"SELECT MAX({idcol}) FROM record").fetchone()
            _watermark = (row[0] if row and row[0] is not None else 0)
            _ncdb_primed = True
            _ncdb_status = "ok"
            return []  # first poll: baseline only - no flood of old news
        sel = idcol + (", app_id" if "app_id" in cols else "") + ", data"
        rows = conn.execute(
            f"SELECT {sel} FROM record WHERE {idcol} > ? ORDER BY {idcol}",
            (_watermark,)).fetchall()
        events = []
        for row in rows:
            rid = row[0]
            if isinstance(rid, (int, float)):
                _watermark = max(_watermark or 0, int(rid))
            app_id = row[1] if "app_id" in cols else None
            data = row[-1]
            ev = _parse_ncdb_record(appmap.get(app_id, ""), data)
            if ev:
                events.append(ev)
        _ncdb_status = "ok"
        return events
    except sqlite3.Error:
        if _ncdb_status != "ok":
            _ncdb_status = "needs Full Disk Access"
        return []
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Source B: Accessibility pop-up / dialog watcher
# ---------------------------------------------------------------------------

_FS = "\x1f"   # field separator (matches ASCII character 31 in the script)
_RS = "\x1e"   # item separator (ASCII character 30)

_AX_SCRIPT = (
    'set FS to ASCII character 31\n'
    'set RS to ASCII character 30\n'
    'set out to ""\n'
    'tell application "System Events"\n'
    'repeat with p in application processes\n'
    'try\n'
    'repeat with w in windows of p\n'
    'try\n'
    'set statics to ""\n'
    'repeat with t in static texts of w\n'
    'try\n'
    'set statics to statics & (value of t) & RS\n'
    'end try\n'
    'end repeat\n'
    'set btns to ""\n'
    'repeat with b in buttons of w\n'
    'try\n'
    'set btns to btns & (name of b) & RS\n'
    'end try\n'
    'end repeat\n'
    'set out to out & (name of p) & FS & (name of w) & FS & statics & FS & btns & linefeed\n'
    'end try\n'
    'end repeat\n'
    'end try\n'
    'end repeat\n'
    'end tell\n'
    'return out'
)


def _ax_raw():
    """Raw AX snapshot text, or None (no osascript / timeout / error)."""
    try:
        p = subprocess.run(["osascript", "-e", _AX_SCRIPT],
                           capture_output=True, text=True, timeout=3)
        if p.returncode != 0:
            return None
        return p.stdout or ""
    except Exception:
        return None


def _parse_ax(raw):
    """Snapshot text -> [{app, title, texts, buttons}]. Defensive."""
    wins = []
    # NOTE: split on "\n" only - str.splitlines() would also break on
    # the \x1e item separator itself.
    for line in (raw or "").split("\n"):
        if _FS not in line:
            continue
        parts = line.split(_FS)
        if len(parts) < 4:
            parts += [""] * (4 - len(parts))
        app, title, statics, buttons = (parts[0].strip(), parts[1].strip(),
                                        parts[2], parts[3])
        texts = [t.strip() for t in statics.split(_RS) if t.strip()]
        btns = [b.strip() for b in buttons.split(_RS) if b.strip()]
        wins.append({"app": app or "An app", "title": title,
                     "texts": texts, "buttons": btns})
    return wins


def _win_key(w):
    return (w["app"], w["title"], " ".join(w["texts"]))


_ALERT_WORDS = (
    "alert", "warning", "attention", "error", "failed", "failure",
    "wants to", "would like to", "allow", "don't allow", "password",
    "passcode", "permission", "requires", "sign in", "log in",
    "cannot", "could not", "unable", "update", "restart",
)


def _is_alert_like(app, title, texts):
    blob = " ".join([title or ""] + list(texts or [])).lower()
    return any(w in blob for w in _ALERT_WORDS)


def _ax_events():
    """New windows since the baseline, as pop-up candidate events."""
    global _ax_baseline, _ax_primed
    raw = _ax_raw()
    if raw is None:
        return []
    wins = _parse_ax(raw)
    keys = {_win_key(w) for w in wins}
    if not _ax_primed:
        _ax_baseline = keys
        _ax_primed = True
        return []  # first poll: baseline only
    events = []
    for w in wins:
        if _win_key(w) not in _ax_baseline:
            events.append({
                "kind": "popup", "app": w["app"], "title": w["title"],
                "texts": w["texts"], "buttons": w["buttons"],
                "sender": w["app"], "subtitle": "",
                "body": " ".join(w["texts"]),
            })
    _ax_baseline = keys
    return events


# ---------------------------------------------------------------------------
# Importance judge: rules first, then the fast-tier brain
# ---------------------------------------------------------------------------

_MESSAGE_APPS = {"messages", "whatsapp", "telegram", "signal", "slack", "messenger"}
_MAIL_APPS = {"mail", "outlook"}
_ALERT_APPS = {"calendar", "reminders", "facetime"}
_NOISE_APPS = {"news", "tips", "app store", "software update", "garageband", "keynote"}
_JOB_WORDS = ("job", "interview", "offer letter", "hiring", "recruiter",
              "application", "position", "resume", "opportunity", "shortlisted")
_MONEY_WORDS = ("bank", "payment", "paid", "invoice", "loan", "credit card",
                "transaction", "charged", "deposit", "salary", "refund",
                "bill due", "balance", "debited", "credited", "upi")
_NOISE_WORDS = ("software update", "app update available", "promotion",
                "promotional", "sale ends", "% off", "newsletter",
                "top stories", "breaking news", "daily tips", "new features")


def extract_otp(text):
    """A verification code in `text`, or None. Keyword-anchored on purpose:
    a bare number is not read aloud as a code."""
    t = str(text or "")
    if not t:
        return None
    m = re.search(r"G-(\d{6})\b", t)
    if m:
        return m.group(1)
    m = re.search(r"(?:verification code|security code|one[\s-]?time "
                  r"(?:code|password|pin)|otp|passcode|code|pin)"
                  r"[^0-9]{0,30}(\d{4,8})\b", t, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{4,8})\b[^0-9]{0,30}(?:is your|verification|"
                  r"otp|passcode|code|pin)\b", t, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _has_keywords(text):
    low = str(text or "").lower()
    return any(w in low for w in _JOB_WORDS + _MONEY_WORDS)


def _sender_in_contacts(sender):
    if not sender:
        return False
    try:
        import jarvis_contacts
        return jarvis_contacts.resolve(str(sender)) is not None
    except Exception:
        return False


def _brain_judge(event, default):
    """
    Ask the fast-tier brain, strictly: "IMPORTANT" or "IGNORE".
    Any failure / garbage answer falls back to `default`.
    """
    try:
        import jarvis_brain
        owner_ctx = ""
        try:
            owner_ctx = str(jarvis_config.load_config().get(
                "personal_context", "") or "")
        except Exception:
            pass
        prompt = (
            f"App: {event.get('app', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Text: {event.get('body', '')}\n\n"
            f"The owner: {owner_ctx}\n"
            "Is this notification IMPORTANT for him (needs his attention "
            "now: a real person waiting, money, work, a deadline, a "
            "problem) or can it be IGNORED (promotions, news noise, app "
            "marketing, routine FYI)? Answer with exactly one word: "
            "IMPORTANT or IGNORE."
        )
        ans = jarvis_brain.complete(
            prompt,
            system="You classify notifications. Answer exactly IMPORTANT or IGNORE.",
            deep=False, max_tokens=10)
        if ans:
            up = str(ans).strip().upper()
            if up.startswith("IMPORTANT"):
                return "important"
            if up.startswith("IGNORE"):
                return "silent"
    except Exception:
        pass
    return default


def judge(event):
    """Return "important" or "silent" for one event."""
    app = str(event.get("app") or "")
    low_app = app.lower()
    title = str(event.get("title") or "")
    body = str(event.get("body") or "")
    text = f"{title} {body}".strip()
    kind = event.get("kind") or "notification"

    if "jarvis" in low_app:
        return "silent"
    if extract_otp(text):
        return "important"
    if low_app in _NOISE_APPS:
        return "silent"
    if not text and kind != "popup":
        return "silent"

    if kind == "popup":
        blob = " ".join([title] + list(event.get("texts") or [])).lower()
        if not blob.strip():
            return "silent"
        if any(w in blob for w in ("password", "passcode", "failed",
                                   "error", "permission", "cannot",
                                   "could not", "unable")):
            return "important"
        if _is_alert_like(app, title, event.get("texts") or []):
            return _brain_judge(event, "silent")
        # A new window with meaningful text: let the brain decide,
        # defaulting to quiet - new windows are usually just him working.
        return _brain_judge(event, "silent")

    if low_app in _ALERT_APPS:
        return "important"

    low_text = text.lower()
    if low_app not in _MESSAGE_APPS and low_app not in _MAIL_APPS:
        if any(w in low_text for w in _NOISE_WORDS):
            return "silent"

    if kind == "message" or low_app in _MESSAGE_APPS:
        if _sender_in_contacts(event.get("sender")):
            return "important"
        if _has_keywords(text):
            return "important"
        # A person wrote to him: err on telling him if the brain is away.
        return _brain_judge(event, "important" if event.get("sender") else "silent")

    if kind == "mail" or low_app in _MAIL_APPS:
        if _sender_in_contacts(event.get("sender")) or _has_keywords(text):
            return "important"
        return _brain_judge(event, "silent")

    return _brain_judge(event, "silent")


# ---------------------------------------------------------------------------
# Event handling: dedupe, announce, pending state, ring buffer
# ---------------------------------------------------------------------------

def _event_hash(event):
    raw = "|".join([str(event.get("app", "")), str(event.get("title", "")),
                    str(event.get("body", ""))])
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()


def handle_event(event):
    """Dedupe (once/day) + judge + announce one event. Returns the verdict."""
    global _pending_reply, _pending_popup
    day = datetime.date.today().isoformat()
    h = _event_hash(event)
    with _lock:
        if _seen.get(h) == day:
            return "duplicate"
        _seen[h] = day

    verdict = judge(event)
    app = str(event.get("app") or "An app")
    if verdict != "important":
        _hud_note(f"[quiet] {app}: "
                  f"{_shorten(str(event.get('title') or '') + ' ' + str(event.get('body') or ''), 120)}")
        return "silent"

    owner = _owner()
    kind = event.get("kind") or "notification"
    title = str(event.get("title") or "")
    body = str(event.get("body") or "")
    text = f"{title} {body}".strip()
    code = extract_otp(text)
    spoken = None

    if code:
        spoken = f"{owner}, your {app} code is {code}."
    elif kind == "message":
        sender = str(event.get("sender") or title or app)
        spoken = (f"{owner}, message from {sender}: {_shorten(body, 140)} "
                  f"Say 'reply' and I'll type your reply.")
        with _lock:
            _pending_reply = {"app": app, "sender": sender, "body": body}
    elif kind == "mail":
        sender = str(event.get("sender") or title or app)
        subject = str(event.get("subtitle") or "") or _shorten(body, 80) or title
        spoken = f"{owner}, email from {sender}: {subject}."
        if app.lower() == "mail":
            spoken += " Say 'reply' and I'll type your reply."
            with _lock:
                _pending_reply = {"app": app, "sender": sender, "body": body}
    elif kind == "popup":
        said = _shorten(" ".join([title] + list(event.get("texts") or [])), 160)
        spoken = f"{owner}, a pop-up from {app}: {said}."
        buttons = [b for b in (event.get("buttons") or []) if b]
        if buttons:
            spoken += (f" It asks: {', '.join(buttons)}. "
                       f"Say a button name and I'll click it.")
            with _lock:
                _pending_popup = {"app": app, "buttons": buttons}
    else:
        spoken = f"{owner}, {app}: {_shorten(text, 150)}."

    with _lock:
        _recent.append({
            "app": app, "kind": kind, "title": title, "body": body,
            "spoken": spoken, "ts": datetime.datetime.now().isoformat(),
        })
    jarvis_butler.queue_alert(spoken)
    return "important"


def what_did_i_miss():
    """Spoken recap of the last 3 important events (the safety net)."""
    with _lock:
        items = list(_recent)[-3:]
    if not items:
        return "Nothing important."
    if len(items) == 1:
        return "Here's what you missed: " + items[0]["spoken"]
    bits = " ".join(it["spoken"] for it in items)
    return f"Here's what you missed - {len(items)} important things: {bits}"


# ---------------------------------------------------------------------------
# Reply delivery: Messages AppleScript first, then the type-it ladder
# ---------------------------------------------------------------------------

def _messages_send_reply(sender, text):
    """AppleScript: find the Messages chat for `sender` and send. Bool."""
    try:
        import jarvis_actions as actions
        script = (
            'tell application "Messages"\n'
            'set targetChat to missing value\n'
            'repeat with c in chats\n'
            'try\n'
            'repeat with p in participants of c\n'
            'try\n'
            f'if (name of p) is "{actions._asq(sender)}" or '
            f'(id of p) contains "{actions._asq(sender)}" then '
            'set targetChat to c\n'
            'end try\n'
            'end repeat\n'
            'end try\n'
            'end repeat\n'
            'if targetChat is not missing value then\n'
            f'send "{actions._asq(text)}" to targetChat\n'
            'return "sent"\n'
            'end if\n'
            'end tell\n'
            'return "notfound"'
        )
        rc, out = actions._osa_run(script)
        return rc == 0 and (out or "").strip() == "sent"
    except Exception:
        return False


def _open_chat_row(app, sender):
    """Best-effort: click the conversation row matching `sender` in `app`."""
    try:
        import jarvis_actions as actions
        script = (
            'tell application "System Events"\n'
            f'tell process "{actions._asq(app)}"\n'
            'try\n'
            'repeat with r in rows of table 1 of scroll area 1 of window 1\n'
            'try\n'
            'repeat with t in static texts of r\n'
            f'if (value of t) contains "{actions._asq(sender)}" then\n'
            'click r\n'
            'return "clicked"\n'
            'end if\n'
            'end repeat\n'
            'end try\n'
            'end repeat\n'
            'end try\n'
            'end tell\n'
            'end tell\n'
            'return "notfound"'
        )
        rc, out = actions._osa_run(script)
        return rc == 0 and (out or "").strip() == "clicked"
    except Exception:
        return False


def _type_reply(pending, text):
    """
    The type-it ladder: activate the app, best-effort open the chat,
    then TYPE the reply into the focused field. Never sends.
    """
    try:
        import jarvis_actions as actions
        app = pending.get("app") or ""
        sender = pending.get("sender") or ""
        if not actions._osa(f'tell application "{actions._asq(app)}" to activate'):
            return False
        actions._pause(0.4)
        if app == "Messages":
            _open_chat_row("Messages", sender)
        elif app == "WhatsApp":
            opened = False
            try:
                import jarvis_contacts
                found = jarvis_contacts.resolve(sender)
                if found and found[1].get("phone"):
                    opened = actions._open(
                        ["https://wa.me/" + jarvis_contacts.digits(found[1]["phone"])])
                    actions._pause(1.5)
            except Exception:
                opened = False
            if not opened:
                _open_chat_row("WhatsApp", sender)
        elif app == "Mail":
            _open_chat_row("Mail", sender)
        return bool(actions._osa(
            f'tell application "System Events" to keystroke '
            f'"{actions._asq(text)}"'))
    except Exception:
        return False


def _execute_send(app):
    """The send keystroke for a typed reply. Bool (honest)."""
    try:
        import jarvis_actions as actions
        actions._osa(f'tell application "{actions._asq(app)}" to activate')
        if (app or "").lower() == "mail":
            return bool(actions._osa(
                'tell application "System Events" to keystroke "d" '
                'using {command down, shift down}'))
        return bool(actions._osa(
            'tell application "System Events" to key code 36'))
    except Exception:
        return False


def _click_popup_button(app, button):
    """Activate the pop-up's app, then reuse the v5 click-by-name path."""
    try:
        import jarvis_actions as actions
        actions._osa(f'tell application "{actions._asq(app)}" to activate')
        result = actions.click_named(button)
        return bool(result) and result.startswith("Clicked")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Utterance handling (called by the loops BEFORE the butler; notify wins)
# ---------------------------------------------------------------------------

_SEND_WORDS = {"send it", "send", "send now", "yes send", "send it now",
               "send the reply", "send reply", "go ahead and send"}
_DISMISS_WORDS = {"ignore", "leave it", "no", "dismiss", "never mind",
                  "nevermind", "forget it", "not now", "cancel", "no reply"}
_REPLY_WORDS = {"reply", "reply to him", "reply to her", "reply to them",
                "yes reply", "reply please", "reply back", "respond",
                "yes, reply"}
_PURE_STOPS = {"stop", "quiet", "enough", "shut up", "stop talking",
               "jarvis stop"}
_COMMAND_STARTS = (
    "open", "search", "read", "create", "write", "play", "set", "remind",
    "what", "show", "find", "call", "type", "click", "switch", "send a",
    "send an", "send message", "message", "email", "morning", "goodbye",
    "quit", "how", "why", "when", "who", "tell me", "look", "check",
    "start", "launch", "take", "note", "volume", "lock", "stop", "run",
    "fix", "update me",
)
_DISMISS_BUTTONS = ("close", "don't allow", "dont allow", "not now",
                    "cancel", "later", "dismiss", "no thanks", "no")


def _starts_command(low):
    return any(low == c or low.startswith(c + " ") for c in _COMMAND_STARTS)


def _clear_reply():
    global _pending_reply, _awaiting_dictation
    _pending_reply = None
    _awaiting_dictation = False


def _deliver_reply(pending, text):
    """Run the delivery ladder for a dictated reply."""
    global _pending_send
    app = pending.get("app") or ""
    sender = pending.get("sender") or ""
    if app == "Messages" and _messages_send_reply(sender, text):
        return ("acted", f"Sent your reply to {sender}.")
    if _type_reply(pending, text):
        with _lock:
            _pending_send = {"app": app}
        return ("acted", "Typed. Say 'send it' to send, or check the screen.")
    return ("acted", "I couldn't type that — the chat isn't open on screen.")


def handle_response(q, raw=None):
    """
    Interpret an utterance against the notify pending states. Mirrors
    jarvis_butler.handle_response: returns ("acted", text) when there is
    something to say back, ("silent", None) when consumed quietly, or
    None when the utterance is not ours (state may have expired - the
    caller then routes it normally). `raw` preserves case for dictation.
    """
    global _pending_reply, _awaiting_dictation, _pending_send, _pending_popup
    low = (q or "").strip().lower().strip(" .,!?")
    if not low:
        return None
    dictated = (raw or q or "").strip()

    with _lock:
        pend_send = _pending_send
        pend_reply = _pending_reply
        pend_popup = _pending_popup
        awaiting = _awaiting_dictation

    # 1) A typed reply is waiting for "send it".
    if pend_send:
        if low in _SEND_WORDS:
            with _lock:
                _pending_send = None
            if _execute_send(pend_send.get("app") or ""):
                return ("silent", None)
            return ("acted", "I couldn't send it — the app may not be in "
                             "front. The typed text is still there; check "
                             "the screen.")
        if low in _DISMISS_WORDS or low in _PURE_STOPS:
            with _lock:
                _pending_send = None
            return ("silent", None)
        with _lock:
            _pending_send = None  # anything else: it expires, falls through
        pend_send = None

    # 2) He said "reply" and now dictates the text.
    if awaiting and pend_reply:
        if low in _PURE_STOPS:
            with _lock:
                _clear_reply()
            return None  # let the loops' own stop handling have it
        if low in _DISMISS_WORDS:
            with _lock:
                _clear_reply()
            return ("silent", None)
        if _starts_command(low):
            with _lock:
                _clear_reply()
            return None  # a new command, not dictation
        sender = pend_reply.get("sender") or ""
        with _lock:
            _clear_reply()
        return _deliver_reply(pend_reply, dictated)

    # 3) A pop-up is waiting for a button name.
    if pend_popup:
        buttons = pend_popup.get("buttons") or []
        for b in buttons:
            bl = str(b).strip().lower()
            if bl and (bl in low or (len(low) >= 2 and low in bl)):
                with _lock:
                    _pending_popup = None
                if _click_popup_button(pend_popup.get("app") or "", b):
                    return ("silent", None)
                return ("acted", f"I couldn't click {b} — it may have "
                                 f"closed already, or Accessibility is off.")
        if low in _DISMISS_WORDS or low in ("close it", "close", "dismiss it"):
            with _lock:
                _pending_popup = None
            for b in buttons:
                if str(b).strip().lower() in _DISMISS_BUTTONS:
                    _click_popup_button(pend_popup.get("app") or "", b)
                    break
            return ("silent", None)

    # 4) A message/mail alert is waiting for "reply".
    if pend_reply:
        if low in _REPLY_WORDS:
            with _lock:
                _awaiting_dictation = True
            sender = pend_reply.get("sender") or "them"
            return ("acted", f"What should I say to {sender}?")
        if low in _DISMISS_WORDS:
            with _lock:
                _clear_reply()
            return ("silent", None)

    return None


def has_pending():
    with _lock:
        return bool(_pending_reply or _pending_popup or _pending_send
                    or _awaiting_dictation)


# ---------------------------------------------------------------------------
# Watcher thread
# ---------------------------------------------------------------------------

def poll_once():
    """One poll of both sources; every event handled. Returns event count."""
    events = []
    try:
        events.extend(_ncdb_events())
    except Exception:
        pass
    try:
        events.extend(_ax_events())
    except Exception:
        pass
    for ev in events:
        try:
            handle_event(ev)
        except Exception:
            pass
    return len(events)


def _loop():
    while True:
        try:
            poll_once()
        except Exception:
            pass
        try:
            interval = int(jarvis_config.load_config().get(
                "notify_interval_sec", 5))
        except Exception:
            interval = 5
        threading.Event().wait(max(2, interval))


def start():
    """Start the watcher thread (idempotent; no-op when disabled)."""
    global _started, _thread
    if _started:
        return
    try:
        if not jarvis_config.load_config().get("notify_enabled", True):
            return
    except Exception:
        pass
    _started = True
    _thread = threading.Thread(target=_loop, name="jarvis-notify", daemon=True)
    _thread.start()


def _reset_for_tests():
    global _pending_reply, _awaiting_dictation, _pending_send, _pending_popup
    global _started, _watermark, _ncdb_primed, _ncdb_status, _ax_baseline, _ax_primed
    with _lock:
        _seen.clear()
        _recent.clear()
        _pending_reply = None
        _awaiting_dictation = False
        _pending_send = None
        _pending_popup = None
    _started = False
    _watermark = None
    _ncdb_primed = False
    _ncdb_status = None
    _ax_baseline = set()
    _ax_primed = False
