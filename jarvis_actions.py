"""
jarvis_actions.py v3 - "Full Voice Control" local automation.

Keeps everything v2 did (open apps/sites, volume, market, honest mail
brief, battery/system/Wi-Fi, screenshot, notes, timers, clipboard,
destructive actions gated on a "yes" confirmation, no sudo anywhere) and
adds v3:

- Open ANY file/folder by name (Spotlight mdfind + a home-folder scan).
- Contacts book (~/.openjarvis/contacts.json) via jarvis_contacts.
- Messaging with confirm-before-send: iMessage (Messages/AppleScript),
  WhatsApp (wa.me deep link, Enter pressed only after a "yes"),
  email (prefilled Gmail compose - never silently auto-sent), FaceTime calls.
- Dictation / keyboard control via System Events keystrokes
  (needs macOS Accessibility permission for Terminal - see README).
- Media control (Spotify if present, else Apple Music).
- REAL Reminders and Calendar events via AppleScript (simple date
  parsing: today / tomorrow, times like "6 pm" / "18:30").
- File management: create folder/file, move, trash - moves and
  trashing require the confirm callback.
- Brightness keys, Wi-Fi on/off, Do Not Disturb via a user Shortcut.
- Morning briefing builder.

v5 adds: read local files aloud (jarvis_read), "switch to <app>",
and best-effort "click <name>" via System Events UI search.

EVERYTHING mac-only is guarded: on a system without mdfind / osascript /
pmset the actions return a clean honest sentence, never a traceback.
All system calls go through _open / _osa / _run / _run_input so tests can
stub them and nothing real ever happens on a test machine.
"""

import datetime
import glob
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

import jarvis_contacts
import jarvis_market
import jarvis_tasks


def _emit_action(text):
    """Feed the live HUD's activity stream (v4). Never raises."""
    try:
        import jarvis_hud
        jarvis_hud.emit("action", str(text))
    except Exception:
        pass

SITES = {
    "youtube": "https://www.youtube.com",
    "whatsapp": "https://web.whatsapp.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "maps": "https://maps.google.com",
    "calendar": "https://calendar.google.com",
    "drive": "https://drive.google.com",
    "chatgpt": "https://chatgpt.com",
    "linkedin": "https://www.linkedin.com",
    "indeed": "https://www.indeed.com",
}

ALIASES = {
    "vs code": "Visual Studio Code",
    "code": "Visual Studio Code",
    "chrome": "Google Chrome",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "settings": "System Settings",
    "system preferences": "System Settings",
    "terminal": "Terminal",
    "spotify": "Spotify",
    "finder": "Finder",
    "safari": "Safari",
}

FOLDERS = {
    "downloads": "~/Downloads",
    "download": "~/Downloads",
    "documents": "~/Documents",
    "document": "~/Documents",
    "desktop": "~/Desktop",
    "pictures": "~/Pictures",
    "picture": "~/Pictures",
    "photos": "~/Pictures",
    "home": "~",
    "movies": "~/Movies",
    "music": "~/Music",
}

# Sparse, deterministic follow-up suggestions (config flag: suggestions).
# Deliberately NOT applied after every action - only these few.
SUGGESTION_MAP = {
    "screenshot": "I saved it to your Desktop - say 'open desktop' to see it.",
    "note": "Say 'open my notes' style commands are not in yet - your notes live in ~/.openjarvis/notes.txt.",
    "timer": "While you wait, say 'stock market report' if you want a quick market check.",
    "reminder": "Say 'morning briefing' any time to hear your reminders and your day.",
    "folder_created": "Say 'open downloads' or 'open documents' to jump into a folder.",
}

# Remove the note suggestion's self-referential wording; keep it useful.
SUGGESTION_MAP["note"] = "Your notes are saved in ~/.openjarvis/notes.txt."


def get_suggestion(action_key):
    """Return the canned suggestion line for an action key, or None."""
    try:
        import jarvis_config
        if not jarvis_config.load_config().get("suggestions", True):
            return None
    except Exception:
        pass
    return SUGGESTION_MAP.get(action_key)


def _with_suggestion(result, action_key):
    s = get_suggestion(action_key)
    if s:
        return result + " " + s
    return result


# --------------------------------------------------------------------------
# Low-level helpers. Tests monkeypatch _open / _osa / _run / _run_input /
# _pause so nothing ever opens, sends, types, or sleeps in tests.
# --------------------------------------------------------------------------

def _open(args):
    """macOS `open` - returns True on success."""
    try:
        return subprocess.run(["open"] + list(args), capture_output=True).returncode == 0
    except Exception:
        return False


def _osa(script):
    """Run an AppleScript snippet. Returns True on success."""
    try:
        return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0
    except Exception:
        return False


def _run(cmd):
    """Run a command, return (returncode, stdout_text). Never raises."""
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=15)
        return p.returncode, (p.stdout or "").strip()
    except Exception as e:
        return 1, str(e)


def _run_input(cmd, input_text):
    """Run a command feeding input_text on stdin (e.g. pbcopy)."""
    try:
        p = subprocess.run(list(cmd), input=input_text, capture_output=True, text=True, timeout=15)
        return p.returncode, (p.stdout or "").strip()
    except Exception as e:
        return 1, str(e)


def _pause(seconds):
    time.sleep(seconds)


def _osa_run(script):
    """osascript via _run so callers get output (used by Reminders reads)."""
    return _run(["osascript", "-e", script])


def _asq(text):
    """Escape a string for a double-quoted AppleScript literal."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _say_local(text):
    try:
        import sys
        if sys.platform == "darwin":
            subprocess.run(["say", text], capture_output=True)
    except Exception:
        pass
    print(f"\n[JARVIS]: {text}\n")


def _apps():
    dirs = [
        "/Applications",
        "/Applications/Utilities",
        "/System/Applications",
        "/System/Applications/Utilities",
        os.path.expanduser("~/Applications"),
    ]
    apps = {}
    for d in dirs:
        for path in glob.glob(d + "/*.app"):
            n = os.path.basename(path)[:-4]
            apps[n.lower()] = n
    return apps


def find_app(name):
    name = ALIASES.get(name.lower().strip(), name).lower().strip()
    apps = _apps()
    if name in apps:
        return apps[name]
    for k, v in apps.items():
        if name in k:
            return v
    return None


def _ask_confirmation(what, confirm):
    """
    Send / delete / move gate. True only if the confirm callback exists
    and answers truthy. No callback = nothing risky runs.
    """
    if confirm is None:
        return False
    try:
        return bool(confirm(f"Are you sure you want me to {what}? Say yes to confirm."))
    except Exception:
        return False


# --------------------------------------------------------------------------
# File/folder search by name (Spotlight first, home scan as fallback)
# --------------------------------------------------------------------------

def _pref_key(path):
    p = path.lower()
    for i, folder in enumerate(("/desktop/", "/documents/", "/downloads/")):
        if folder in p:
            return i
    return 3


def search_files(name, want_folder=False):
    """
    Find files (or folders) named like `name`. Uses mdfind, guarded so a
    machine without Spotlight (e.g. Linux) yields the home-scan fallback
    or an empty list - never an exception.
    """
    results = []
    rc, out = _run(["mdfind", "-name", name])
    if rc == 0 and out:
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            if want_folder and not os.path.isdir(line):
                # On a stub/Linux box isdir may be False for Mac paths;
                # keep the hit unless it plainly looks like a file.
                if "." in os.path.basename(line):
                    continue
            if line not in results:
                results.append(line)
    if not results:
        # Fallback: shallow scan of the main home folders.
        roots = [os.path.expanduser(f) for f in ("~/Desktop", "~/Documents", "~/Downloads", "~")]
        needle = name.lower()
        for root in roots:
            if not os.path.isdir(root):
                continue
            for pattern_root in (root, os.path.join(root, "*")):
                for path in glob.glob(os.path.join(pattern_root, "*")):
                    base = os.path.basename(path).lower()
                    if needle in base and (want_folder == os.path.isdir(path) or not want_folder):
                        if path not in results:
                            results.append(path)
    results.sort(key=_pref_key)
    return results


def open_named(name, want_folder=False):
    hits = search_files(name, want_folder=want_folder)
    kind = "folder" if want_folder else "file"
    if not hits:
        return (f"I couldn't find a {kind} called {name} - the search found nothing "
                f"(Spotlight may be unavailable here).")
    top = hits[0]
    _open([top])
    return f"Opening {os.path.basename(top)}."


def find_file(name):
    rc, out = _run(["mdfind", "-name", name])
    if rc == 0 and out:
        hits = [line for line in out.splitlines() if line.strip()][:3]
        if hits:
            return f"I found {name}. Top matches: {'; '.join(hits)}."
    hits = search_files(name)
    if hits:
        return f"I found {name}. Top matches: {'; '.join(hits[:3])}."
    return f"I couldn't find any file matching {name}."


# --------------------------------------------------------------------------
# Contacts, messaging, calls
# --------------------------------------------------------------------------

def _contact_or_raw(target):
    """
    Resolve a spoken target. Returns (display_name, identifier_dict) where
    the dict may carry phone/email. Raw emails / raw numbers spoken aloud
    become pseudo-contacts. Unknown names return (name, None).
    """
    t = (target or "").strip()
    if "@" in t:
        return t, {"phone": "", "email": t, "channel": ""}
    if sum(ch.isdigit() for ch in t) >= 7 and all(ch.isdigit() or ch in " +-()" for ch in t):
        return t, {"phone": t, "email": "", "channel": ""}
    found = jarvis_contacts.resolve(t)
    if found:
        return found
    return t, None


def send_imessage(target, text, confirm=None):
    name, entry = _contact_or_raw(target)
    if not entry or not (entry.get("phone") or entry.get("email")):
        return (f"I don't have a phone number or email for {name} in your contacts. "
                f"Tell me the number or email and I can add it to ~/.openjarvis/contacts.json - "
                f"I did not send anything.")
    buddy = (entry.get("phone") or entry.get("email")).strip()
    if not _ask_confirmation(f"send an iMessage to {name} saying \"{text}\"", confirm):
        return (f"Sending an iMessage needs your confirmation. I did not send anything to {name} - "
                f"ask again and say yes to confirm.")
    script = (
        'tell application "Messages" to set targetService to 1st service whose service type = iMessage\n'
        f'tell application "Messages" to send "{_asq(text)}" to buddy "{_asq(buddy)}" of targetService'
    )
    # Single -e argument: keep it one string like the other _osa calls.
    script = (
        'tell application "Messages" '
        f'to send "{_asq(text)}" to buddy "{_asq(buddy)}" '
        'of (1st service whose service type = iMessage)'
    )
    if _osa(script):
        return f"iMessage sent to {name}: {text}"
    return f"I couldn't send the iMessage to {name} - Messages may not be available here."


def send_whatsapp_v3(target, text, confirm=None):
    name, entry = _contact_or_raw(target)
    phone = jarvis_contacts.digits(entry.get("phone")) if entry else ""
    if not phone:
        # v2 behaviour: open WhatsApp Web; say honestly why not a deep link.
        _open(["https://web.whatsapp.com/send?text=" + urllib.parse.quote(text)])
        return (f"{name}'s number isn't in your contacts, so I can't open a direct WhatsApp chat. "
                f"Opening WhatsApp Web with your message filled in - you can pick the chat and press send.")
    _open([f"https://wa.me/{phone}?text={urllib.parse.quote(text)}"])
    if not _ask_confirmation(f"send the WhatsApp message to {name}", confirm):
        return (f"I opened the WhatsApp chat with {name} and filled in your message, but I did not send it - "
                f"sending needs your yes. Ask again and confirm, or press send yourself.")
    # Give WhatsApp time to load, then press Return. This needs macOS
    # Accessibility permission for Terminal (see README).
    _pause(6)
    _osa('tell application "System Events" to key code 36')
    return f"WhatsApp message sent to {name}: {text}"


def compose_email(target, text, subject=""):
    name, entry = _contact_or_raw(target)
    to_addr = ""
    if entry:
        to_addr = entry.get("email") or (target if "@" in target else "")
    if not to_addr and "@" in target:
        to_addr = target
    params = {"view": "cm", "fs": "1", "to": to_addr, "su": subject, "body": text}
    url = "https://mail.google.com/mail/?" + urllib.parse.urlencode(params)
    _open([url])
    who = to_addr or name
    return (f"Opening Gmail with your email to {who} prefilled"
            + (f" (subject: {subject})" if subject else "")
            + " - I don't send email silently, so press Send in Gmail when you're happy with it.")


def email_with_attachment(target, file_name, confirm=None):
    """
    v4: "email <contact> and attach <file>". Opens Apple Mail with a new
    VISIBLE message addressed to the contact and the file attached -
    Jarvis never sends it; Umesh checks it and presses Send himself.
    Needs the usual confirmation first. If Mail/AppleScript is
    unavailable, falls back honestly: Gmail compose + Finder reveal.
    """
    name, entry = _contact_or_raw(target)
    to_addr = ""
    if entry:
        to_addr = (entry.get("email") or "").strip()
    if not to_addr and "@" in target:
        to_addr = target.strip()
    if not to_addr:
        return (f"I don't have an email address for {name} in your contacts, so I can't "
                f"address the email. Add it to ~/.openjarvis/contacts.json and ask again - "
                f"I didn't create anything.")
    hits = search_files(file_name)
    if not hits:
        return f"I couldn't find a file matching {file_name} to attach."
    path = hits[0]
    base = os.path.basename(path)
    if not _ask_confirmation(f"open Mail with {base} attached, addressed to {name}", confirm):
        return (f"Attaching a file to an email needs your confirmation. I did not create "
                f"the email - ask again and say yes to confirm.")
    script = (
        'tell application "Mail"\n'
        'set newMsg to make new outgoing message with properties {subject:"", content:"", visible:true}\n'
        'tell newMsg\n'
        f'make new to recipient at end of to recipients with properties {{address:"{_asq(to_addr)}"}}\n'
        f'make new attachment with properties {{file name:"{_asq(path)}"}} at after last paragraph\n'
        'end tell\n'
        'end tell'
    )
    if _osa(script):
        return (f"I've opened Mail with {base} attached, addressed to {name} - "
                f"check it and press Send when you're happy with it.")
    # Honest fallback: Gmail compose + show the file in Finder.
    params = {"view": "cm", "fs": "1", "to": to_addr}
    _open(["https://mail.google.com/mail/?" + urllib.parse.urlencode(params)])
    _open(["-R", path])
    return (f"Apple Mail didn't respond here, so I opened a Gmail compose to {name} "
            f"and showed you {base} in Finder - attach it manually and press Send.")


def start_call(target, video=False):
    name, entry = _contact_or_raw(target)
    ident = ""
    if entry:
        ident = (entry.get("phone") or entry.get("email") or "").strip()
    if not ident:
        return (f"I don't have a phone number or email for {name} in your contacts, so I can't start the call. "
                f"Add them to ~/.openjarvis/contacts.json and ask again.")
    scheme = "facetimeaudio://" if video else "facetime://"
    _open([scheme + ident])
    kind = "FaceTime video call" if video else "FaceTime call"
    return f"Starting a {kind} with {name}."


# --------------------------------------------------------------------------
# Reminders & Calendar (real, via AppleScript; simple parsing by design)
# --------------------------------------------------------------------------

def _parse_time_of_day(text):
    """'6 pm' / '6:30 pm' / '18:30' -> (hour, minute) or None."""
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if m:
        h = int(m.group(1)) % 12
        if m.group(3) == "pm":
            h += 12
        return h, int(m.group(2) or 0)
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _parse_day_offset(text):
    if "tomorrow" in text:
        return 1
    return 0  # today (default)


def _strip_when_words(text):
    t = re.sub(r"\b(today|tomorrow)\b", " ", text)
    t = re.sub(r"\bat\s+\d{1,2}(?::\d{2})?\s*(am|pm)?\b", " ", t)
    t = re.sub(r"\b\d{1,2}:\d{2}\b", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .,!?")


def _due_datetime(text):
    tod = _parse_time_of_day(text)
    if tod is None:
        return None
    day = datetime.date.today() + datetime.timedelta(days=_parse_day_offset(text))
    return datetime.datetime(day.year, day.month, day.day, tod[0], tod[1])


def _as_date_str(dt):
    return dt.strftime("%A, %B %d, %Y %I:%M:%S %p")


def create_reminder(task, due=None):
    if due is not None:
        script = (
            'tell application "Reminders" to make new reminder in default list '
            f'with properties {{name:"{_asq(task)}", due date:date "{_asq(_as_date_str(due))}"}}'
        )
    else:
        script = (
            'tell application "Reminders" to make new reminder in default list '
            f'with properties {{name:"{_asq(task)}"}}'
        )
    rc, _ = _osa_run(script)
    if rc != 0:
        return None
    if due is not None:
        return f"Reminder created: {task} - due {due.strftime('%A at %I:%M %p')}."
    return f"Reminder created: {task}."


def todays_reminders():
    """Titles of reminders due today, or None when unreadable."""
    script = (
        'set out to ""\n'
        'tell application "Reminders"\n'
        'set rl to reminders in default list whose completed is false\n'
        'repeat with r in rl\n'
        'set out to out & (name of r) & linefeed\n'
        'end repeat\n'
        'end tell\n'
        'return out'
    )
    rc, out = _osa_run(script)
    if rc != 0:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def add_calendar_event(title, start_dt):
    end_dt = start_dt + datetime.timedelta(hours=1)
    script = (
        'tell application "Calendar"\n'
        'set theCal to first calendar whose writable is true\n'
        f'make new event in theCal with properties {{summary:"{_asq(title)}", '
        f'start date:date "{_asq(_as_date_str(start_dt))}", '
        f'end date:date "{_asq(_as_date_str(end_dt))}"}}\n'
        'end tell'
    )
    rc, _ = _osa_run(script)
    if rc != 0:
        return None
    return (f"Event added to your calendar: {title}, "
            f"{start_dt.strftime('%A at %I:%M %p')}, for one hour.")


def morning_briefing():
    """Spoken day briefing; every source is guarded, failures are skipped."""
    try:
        import jarvis_config
        owner = jarvis_config.load_config().get("owner_name", "Umesh")
    except Exception:
        owner = "Umesh"
    now = datetime.datetime.now()
    hour = now.hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    parts = [f"{greet}, {owner}."]
    parts.append("Today is " + now.strftime("%A, %B %d, %Y") + ", and it is currently "
                 + now.strftime("%I:%M %p") + ".")
    try:
        bat = battery_status()
        if "unavailable" not in bat.lower() and "couldn't" not in bat.lower():
            parts.append(bat)
    except Exception:
        pass
    try:
        titles = todays_reminders()
        if titles is not None:
            if titles:
                shown = ", ".join(titles[:5])
                parts.append(f"You have {len(titles)} reminder{'s' if len(titles) != 1 else ''}: {shown}.")
            else:
                parts.append("You have no reminders on your list.")
    except Exception:
        pass
    try:
        brief = jarvis_market.market_briefing()
        if brief:
            parts.append("Markets: " + brief)
    except Exception:
        pass
    parts.append("That's your briefing - ask me anything, or say a command whenever you're ready.")
    return " ".join(parts)


# --------------------------------------------------------------------------
# v2 individual actions (kept)
# --------------------------------------------------------------------------

def send_whatsapp_message(contact_name, message="Hello from Jarvis"):
    encoded_msg = urllib.parse.quote(message)
    _open([f"https://web.whatsapp.com/send?text={encoded_msg}"])
    return f"Opening WhatsApp to message {contact_name}."


def get_mail_brief():
    cache_file = os.path.expanduser("~/.openjarvis/mail_cache.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", errors="ignore") as f:
                content = f.read().strip()
            if content:
                return "Here is your saved mail briefing: " + content[:600]
        except Exception:
            pass
    _open([SITES["gmail"]])
    return (
        "I can't read your Gmail directly, so I have no real email summary to give you. "
        "Opening Gmail so you can see your inbox."
    )


def battery_status():
    rc, out = _run(["pmset", "-g", "batt"])
    if rc != 0 or not out:
        return "Battery status is unavailable on this system."
    m = re.search(r"(\d+)%", out)
    state = ""
    low = out.lower()
    if "discharging" in low:
        state = ", currently discharging"
    elif "charging" in low:
        state = ", currently charging"
    elif "charged" in low:
        state = ", fully charged"
    if m:
        return f"Your battery is at {m.group(1)} percent{state}."
    return "Battery status: " + out.splitlines()[-1]


def system_info():
    parts = []
    rc, chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if rc == 0 and chip:
        parts.append(f"Chip: {chip}")
    rc, ver = _run(["sw_vers", "-productVersion"])
    if rc == 0 and ver:
        parts.append(f"macOS version {ver}")
    try:
        import psutil
        vm = psutil.virtual_memory()
        parts.append(f"RAM: {round(vm.total / (1024 ** 3))} GB total, {vm.percent}% used")
        du = psutil.disk_usage("/")
        parts.append(f"Disk: {round(du.total / (1024 ** 3))} GB total, {du.percent}% used")
    except Exception:
        rc, out = _run(["df", "-h", "/"])
        if rc == 0 and out:
            lines = out.splitlines()
            if len(lines) > 1:
                parts.append("Disk: " + lines[1])
    if not parts:
        return "I couldn't read the system information on this machine."
    return "System information - " + "; ".join(parts) + "."


def wifi_status():
    rc, out = _run(["networksetup", "-getairportnetwork", "en0"])
    if rc == 0 and out:
        m = re.search(r"Current Wi-Fi Network:\s*(.+)", out)
        if m:
            return f"You are connected to Wi-Fi network {m.group(1).strip()}."
        if "not associated" in out.lower():
            return "Wi-Fi is not connected to any network right now."
    return "I couldn't determine the Wi-Fi status on this system."


def take_screenshot():
    desktop = os.path.expanduser("~/Desktop")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(desktop, f"Jarvis_Screenshot_{stamp}.png")
    rc, _ = _run(["screencapture", path])
    if rc == 0:
        return _with_suggestion(
            f"Screenshot saved to your Desktop as Jarvis_Screenshot_{stamp}.png.", "screenshot")
    return "I couldn't take a screenshot on this system."


def save_note(text):
    notes_dir = os.path.expanduser("~/.openjarvis")
    try:
        os.makedirs(notes_dir, exist_ok=True)
    except Exception:
        pass
    notes_file = os.path.join(notes_dir, "notes.txt")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(notes_file, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {text}\n")
    return _with_suggestion(f"Note saved: {text}", "note")


def set_timer(minutes, label="timer"):
    minutes = float(minutes)
    seconds = minutes * 60.0

    def _job():
        time.sleep(seconds)
        return f"Your {label} is done - {minutes:g} minutes are up."

    try:
        # v4: timers are background tasks now - the announcer speaks the
        # result when the timer fires, even if Umesh moved on to talking
        # about something else.
        jarvis_tasks.start(f"Timer ({minutes:g} min)", _job)
    except Exception:
        def _fire():
            _say_local(f"Your {label} is done - {minutes:g} minutes are up.")

        t = threading.Timer(seconds, _fire)
        t.daemon = True
        t.start()
    unit = "minute" if minutes == 1 else "minutes"
    return _with_suggestion(f"Timer set for {minutes:g} {unit}. I will speak when it is done.", "timer")


def _folder_for(spoken):
    for key in ("downloads", "download", "documents", "document", "desktop", "pictures", "home"):
        if key in spoken:
            return os.path.expanduser(FOLDERS[key]), key
    return os.path.expanduser("~/Desktop"), "desktop"


def _key(code):
    _osa(f'tell application "System Events" to key code {code}')
    return True


def click_named(name):
    """
    v5: best-effort "click <name>" - System Events searches the front
    app's front window for a UI element whose name or title matches and
    clicks the first hit. Needs macOS Accessibility permission for
    Terminal; any failure is an honest sentence, never a fake success.
    """
    script = (
        f'set targetName to "{_asq(name)}"\n'
        'tell application "System Events"\n'
        'tell (first application process whose frontmost is true)\n'
        'repeat with el in entire contents of front window\n'
        'try\n'
        'if (name of el) is targetName then\n'
        'click el\n'
        'return "clicked"\n'
        'end if\n'
        'end try\n'
        'try\n'
        'if (title of el) is targetName then\n'
        'click el\n'
        'return "clicked"\n'
        'end if\n'
        'end try\n'
        'end repeat\n'
        'end tell\n'
        'end tell\n'
        'return "notfound"'
    )
    rc, out = _osa_run(script)
    if rc == 0 and (out or "").strip() == "clicked":
        return f"Clicked {name}."
    return (f"I couldn't click {name} - it isn't visible on screen, or Terminal "
            f"needs Accessibility permission (System Settings > Privacy & "
            f"Security > Accessibility). Nothing was clicked.")


def _media_app():
    return "Spotify" if find_app("spotify") else "Music"


# --------------------------------------------------------------------------
# Main router
# --------------------------------------------------------------------------

def try_action(q, confirm=None):
    """
    Public router: runs _try_action_impl and, when an action handled the
    query, feeds the v4 HUD activity stream. Returns the response string
    or None (pass to the AI brain).
    """
    result = _try_action_impl(q, confirm=confirm)
    if result is not None:
        _emit_action(q)
    return result


def _try_action_impl(q, confirm=None):
    """
    Executes instant deterministic actions. Returns a response string if
    handled, or None to pass the query to the AI brain.

    `confirm` is an optional callable(prompt) -> bool authorising sends,
    file moves/deletes, and destructive actions.
    """
    q_orig = q.strip()
    q_clean = q.lower().strip(" .,!?")
    if not q_clean:
        return None

    # v3: screen recording is not supported - say so, don't fake it.
    if any(k in q_clean for k in ("screen recording", "record my screen", "record a video",
                                  "take a video", "take video")):
        return "Screen recording isn't supported in Jarvis yet - I can take a screenshot if you'd like."

    # v3: read what's selected / on screen (via the clipboard, honestly).
    # v4 exception: "read this website" belongs to the web reader below.
    if "website" not in q_clean and "webpage" not in q_clean and any(
            k in q_clean for k in ("read this", "read my screen", "what's on my screen",
                                   "what is on my screen", "read the screen", "read selection",
                                   "read my selection")):
        _osa('tell application "System Events" to keystroke "c" using command down')
        _pause(0.3)
        rc, out = _run(["pbpaste"])
        if rc == 0 and out:
            return "Here's what I read from your screen selection: " + out
        return ("I couldn't read anything from your screen - Jarvis reads the clipboard, "
                "so select or copy some text first and ask again.")

    # v3: morning briefing
    if q_clean in ("morning briefing", "brief me", "briefing", "what's my day",
                   "what is my day", "my day", "daily briefing"):
        return morning_briefing()

    # --- v4 routes ---------------------------------------------------------
    # Background task status (jarvis_tasks)
    if q_clean in ("what are you doing", "task status", "what's running",
                   "what is running", "what tasks are running", "update me",
                   "any updates", "what's happening", "what is happening"):
        return jarvis_tasks.status_text()

    # v6: "what did I miss" - recap of the important notifications the
    # watcher caught (jarvis_notify keeps the ring buffer).
    if q_clean in ("what did i miss", "what have i missed", "did i miss anything",
                   "anything i missed", "any notifications",
                   "any new notifications", "any important notifications",
                   "what notifications", "notifications"):
        import jarvis_notify
        return jarvis_notify.what_did_i_miss()

    # Live HUD: "show yourself / open the hud"
    if q_clean in ("show yourself", "show me yourself", "open your screen",
                   "open the hud", "open hud", "show the hud", "show me the hud",
                   "open jarvis screen", "open your hud"):
        try:
            import jarvis_hud
            url = jarvis_hud.hud_url()
            if jarvis_hud.is_running():
                jarvis_hud.open_hud()
                return (f"Opening my live screen, {url} - you can watch what I'm "
                        f"hearing, saying, and doing there, live.")
            return (f"My live screen lives at {url} - it starts automatically when "
                    f"Jarvis is running, so open that address in your browser to watch me work.")
        except Exception:
            return "I couldn't open my live screen just now."

    # Open his default browser
    if q_clean in ("open browser", "open the browser", "open my browser",
                   "launch browser", "open a browser"):
        _open(["https://www.google.com"])
        return "Opening your browser."

    # Voice coding (jarvis_code)
    import jarvis_code
    m = re.search(r"write\s+code\s+for\s+(.+?)\s+in\s+(?:a\s+)?file\s+(?:called\s+|named\s+)?([A-Za-z0-9_]+)$",
                  q_orig, re.IGNORECASE)
    if m:
        return jarvis_code.create_code(m.group(2), m.group(1))
    m = re.search(r"(?:create|write|make)\s+(?:a\s+)?(?:python\s+)?(?:file|script|program|code)\s+"
                  r"(?:called\s+|named\s+)?([A-Za-z0-9_]+)\s+(?:that|which|to)\s+(.+)$",
                  q_orig, re.IGNORECASE)
    if m:
        return jarvis_code.create_code(m.group(1), m.group(2))
    if q_clean in ("run it", "run the file", "run the code", "run my code",
                   "run that", "run this", "run the program", "run the script"):
        return jarvis_code.run_last_code(confirm=confirm)
    if q_clean in ("open it", "open the code", "open my code", "open the code file"):
        return jarvis_code.open_last_code()
    if q_clean in ("fix it", "fix the error", "fix the code", "fix that error", "fix that"):
        return jarvis_code.fix_last_code()

    # v5: read a local file aloud (jarvis_read) - "read my resume",
    # "read report.pdf", "what's in notes.txt", "open and read X",
    # "read more" to continue. Guards keep the web reader's phrasings
    # ("read about ...", "read bbc dot com", "read this website ...")
    # and the clipboard/mail routes flowing to their own blocks below.
    import jarvis_read
    if q_clean in ("read more", "continue reading", "read on", "keep reading"):
        return jarvis_read.read_more()
    m = re.match(r"(?:what's|what is) in (?:the )?(?:file )?(.+)$", q_clean)
    if m and "clipboard" not in q_clean:
        return jarvis_read.read_file(m.group(1).strip())
    m = re.match(r"open and read (?:the )?(?:file )?(.+)$", q_clean)
    if m:
        return jarvis_read.read_file(m.group(1).strip(), open_too=True)
    m = re.match(r"read (?:my |the file |file )(.+)$", q_clean)
    if m and not any(bad in m.group(1) for bad in
                     ("clipboard", "email", "screen", "selection")):
        return jarvis_read.read_file(m.group(1).strip())
    m = re.match(r"read (.+\.(?:txt|md|py|csv|json|log|docx|pdf))$", q_clean)
    if m:
        return jarvis_read.read_file(m.group(1).strip())
    m = re.match(r"read ([a-z0-9_\- ]+)$", q_clean)
    if m and " dot " not in q_clean and not q_clean.startswith(("read about", "read this")):
        _read_name = m.group(1).strip()
        if _read_name and search_files(_read_name):
            return jarvis_read.read_file(_read_name)
        # No such file: fall through - the web routes / brain may mean it.

    # Browser brain (jarvis_web)
    import jarvis_web
    # v4.1 DO-THE-WORK: a job search always ACTS - opens LinkedIn Jobs
    # with the live results and reads findings aloud - instead of the
    # brain replying with "check LinkedIn and Indeed" suggestions.
    if jarvis_web.is_job_search_query(q_clean):
        return jarvis_web.job_search(q_clean)
    if q_clean in ("open the first result", "open first result", "open the top result"):
        return jarvis_web.open_first_result()
    if q_clean.startswith("read about "):
        return jarvis_web.search_and_tell(q_clean[len("read about "):].strip())
    m = re.search(r"^(?:search|look\s*up|google)(?:\s+for)?\s+(.+?)\s+(?:and\s+)?"
                  r"(?:tell\s+me|read\s+it\s+to\s+me|read\s+to\s+me).*$", q_clean)
    if m:
        return jarvis_web.search_and_tell(m.group(1).strip())
    m = re.search(r"read\s+(?:this\s+)?(?:website|webpage|web\s+page|site)(?:\s+(?:called\s+)?(.+))?$",
                  q_clean)
    if m:
        target = (m.group(1) or "").strip()
        if not target:
            return ("Which website should I read? Say 'read bbc dot com', or "
                    "'read this website' with an address after it.")
        return jarvis_web.read_website(target)
    m = re.search(r"^read\s+((?:https?://)?[\w-]+(?:\s+dot\s+|\.)[\w./-]*)$", q_clean)
    if m:
        return jarvis_web.read_website(m.group(1).strip())
    # v4.1 widened external-info route: everyday phrasings ("show me
    # ...", "find out ...", "what are the ...", "any news about ...",
    # a bare "search ..." / "look up ...") DO the search instead of
    # chatting about it. "show" also opens the results page visually.
    _topic = jarvis_web.general_search_topic(q_clean)
    if _topic:
        return jarvis_web.search_and_tell(_topic, open_results=("show" in q_clean))

    # Email with an attachment (Apple Mail, visible, never auto-sent)
    m = re.search(r"email\s+(.+?)\s+and\s+attach\s+(.+)$", q_orig, re.IGNORECASE)
    if m:
        return email_with_attachment(m.group(1).strip(), m.group(2).strip(), confirm=confirm)
    m = re.search(r"attach\s+(.+?)\s+to\s+an?\s+email\s+to\s+(.+)$", q_orig, re.IGNORECASE)
    if m:
        return email_with_attachment(m.group(2).strip(), m.group(1).strip(), confirm=confirm)

    # Copy a file to a folder (no confirmation needed for a copy)
    m = re.search(r"copy\s+(.+?)\s+to\s+(downloads|documents|desktop|pictures)$", q_clean)
    if m:
        name, where = m.group(1).strip(), m.group(2)
        hits = search_files(name)
        if not hits:
            return f"I couldn't find a file matching {name} to copy."
        dest_dir = os.path.expanduser(FOLDERS[where])
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(hits[0], dest_dir)
            return f"Copied {os.path.basename(hits[0])} to your {where}."
        except Exception:
            return f"I couldn't copy {os.path.basename(hits[0])}."

    # v3: open a website spoken aloud ("example dot com" -> example.com)
    m = re.match(r"(?:open|go to|visit)\s+(?:the\s+)?website\s+(.+)$", q_clean)
    if m:
        domain = m.group(1).replace(" dot ", ".").replace(" ", "").strip(".")
        if "." not in domain:
            domain += ".com"
        url = "https://" + domain
        _open([url])
        return f"Opening {domain}."

    # v3: WhatsApp to a contact (deep link + confirm-to-send)
    m = re.search(r"(?:send\s+)?whatsapp\s+(?:to\s+)?(.+?)\s+saying\s+(.+)$", q_orig, re.IGNORECASE)
    if m:
        return send_whatsapp_v3(m.group(1).strip(), m.group(2).strip(), confirm=confirm)
    m = re.search(r"(?:send|message)\s+(.+?)\s+on\s+whatsapp\s+saying\s+(.+)$", q_orig, re.IGNORECASE)
    if m:
        return send_whatsapp_v3(m.group(1).strip(), m.group(2).strip(), confirm=confirm)

    # v3: email (prefilled Gmail compose - never auto-sent)
    m = re.search(r"(?:send\s+)?email\s+(?:to\s+)?(.+?)\s+subject\s+(.+?)\s+saying\s+(.+)$",
                  q_orig, re.IGNORECASE)
    if m:
        return compose_email(m.group(1).strip(), m.group(3).strip(), subject=m.group(2).strip())
    m = re.search(r"(?:send\s+)?email\s+(?:to\s+)?(.+?)\s+saying\s+(.+)$", q_orig, re.IGNORECASE)
    if m:
        return compose_email(m.group(1).strip(), m.group(2).strip())
    m = re.search(r"email\s+(.+)$", q_orig, re.IGNORECASE)
    if m and "@" in m.group(1) and " " not in m.group(1).strip():
        return compose_email(m.group(1).strip(), "")

    # v3: iMessage (default "send a message to X saying Y")
    m = re.search(r"(?:send\s+(?:a\s+)?(?:message|text|imessage)\s+to|message|text)\s+(.+?)\s+saying\s+(.+)$",
                  q_orig, re.IGNORECASE)
    if m and "whatsapp" not in q_clean and "email" not in q_clean and "mail" not in q_clean:
        return send_imessage(m.group(1).strip(), m.group(2).strip(), confirm=confirm)
    m = re.search(r"imessage\s+(.+?)\s+(.+)$", q_orig, re.IGNORECASE)
    if m:
        return send_imessage(m.group(1).strip(), m.group(2).strip(), confirm=confirm)

    # v3: calls (FaceTime - a Mac has no cellular calling)
    m = re.search(r"(?:video\s+call|facetime|face\s*time)\s+(.+)$", q_orig, re.IGNORECASE)
    if m and "open" not in q_clean:
        return start_call(m.group(1).strip(), video=True)
    m = re.search(r"call\s+(.+)$", q_orig, re.IGNORECASE)
    if m and not any(k in q_clean for k in ("call mom tomorrow", "reminder")) \
            and not q_clean.startswith("remind"):
        target = m.group(1).strip()
        if " " not in target or "@" in target or any(ch.isdigit() for ch in target) \
                or jarvis_contacts.resolve(target):
            return start_call(target, video=False)
        # Multi-word unknown name: still try contacts, else honest answer.
        return start_call(target, video=False)

    # v3: dictation / keyboard control (System Events; Accessibility perm)
    m = re.match(r"type\s+(.+)$", q_orig, re.IGNORECASE)
    if m and not q_clean.startswith("type of"):
        text = m.group(1).strip()
        _osa(f'tell application "System Events" to keystroke "{_asq(text)}"')
        return f"Typed: {text}"
    simple_keys = {
        "press enter": (36, "Pressed Enter."), "hit enter": (36, "Pressed Enter."),
        "press tab": (48, "Pressed Tab."), "press escape": (53, "Pressed Escape."),
        "press esc": (53, "Pressed Escape."), "press space": (49, "Pressed Space."),
        "press delete": (51, "Pressed Delete."), "press backspace": (51, "Pressed Delete."),
        "show desktop": (103, "Showing the desktop."),
        "mission control": (160, "Opening Mission Control."),
        "scroll up": (116, "Scrolled up."), "scroll down": (121, "Scrolled down."),
        "page up": (116, "Page up."), "page down": (121, "Page down."),
    }
    if q_clean in simple_keys:
        code, reply = simple_keys[q_clean]
        _key(code)
        return reply
    if q_clean in ("switch app", "switch apps", "switch window"):
        _osa('tell application "System Events" to key code 48 using command down')
        return "Switched app."
    # v5: "switch to <app>" activates that app by name.
    m = re.match(r"switch to\s+(.+)$", q_clean)
    if m:
        _app_name = m.group(1).strip()
        _app = find_app(_app_name) or ALIASES.get(_app_name, _app_name.title())
        if _osa(f'tell application "{_app}" to activate'):
            return f"Switched to {_app}."
        return (f"I couldn't switch to {_app} - it may not be installed, "
                f"or macOS refused the switch.")
    # v5: "click <name>" - best-effort click on a named UI element.
    m = re.match(r"click\s+(?:on\s+)?(.+)$", q_clean)
    if m:
        return click_named(m.group(1).strip())
    cmd_keys = {
        "copy that": ("c", "Copied."), "copy this": ("c", "Copied."),
        "paste": ("v", "Pasted."), "paste that": ("v", "Pasted."),
        "select all": ("a", "Selected all."),
        "save this": ("s", "Saved."), "save": ("s", "Saved."),
        "undo": ("z", "Undone."), "undo that": ("z", "Undone."),
        "new tab": ("t", "New tab opened."),
        "close tab": ("w", "Tab closed."),
        "minimize window": ("m", "Window minimized."),
        "minimise window": ("m", "Window minimized."),
    }
    if q_clean in cmd_keys:
        letter, reply = cmd_keys[q_clean]
        _osa(f'tell application "System Events" to keystroke "{letter}" using command down')
        return reply
    if q_clean in ("close window",):
        _osa('tell application "System Events" to keystroke "w" using {command down, shift down}')
        return "Window closed."
    if q_clean in ("zoom window", "maximize", "maximize window", "maximise window"):
        _osa('tell application "System Events" to tell (first process whose frontmost is true) '
             'to set zoomed of front window to true')
        return "Window zoomed."
    m = re.match(r"find\s+(\S.{0,40})$", q_clean)
    if m and "." not in m.group(1) and "file" not in q_clean:
        text = m.group(1).strip()
        _osa('tell application "System Events" to keystroke "f" using command down')
        _osa(f'tell application "System Events" to keystroke "{_asq(text)}"')
        return f"Finding {text}."

    # v3: open a file / folder by name
    m = re.search(r"open\s+(?:the\s+)?file\s+(.+)$", q_clean)
    if m:
        return open_named(m.group(1).strip(), want_folder=False)
    m = re.search(r"open\s+(?:the\s+)?folder\s+(.+)$", q_clean)
    if m and m.group(1).strip() not in FOLDERS:
        return open_named(m.group(1).strip(), want_folder=True)
    m = re.search(r"open\s+(?:the\s+)?(.+?)\s+(?:file|document|pdf|spreadsheet|presentation)$", q_clean)
    if m and m.group(1).strip() not in SITES and m.group(1).strip() not in FOLDERS:
        return open_named(m.group(1).strip(), want_folder=False)

    # v3: media control (Spotify if present, else Apple Music)
    if q_clean in ("play music", "play the music", "play some music", "resume music"):
        app = _media_app()
        _osa(f'tell application "{app}" to play')
        return f"Playing music in {app}."
    if q_clean in ("pause music", "pause the music", "stop music", "pause song"):
        app = _media_app()
        _osa(f'tell application "{app}" to pause')
        return f"Music paused in {app}."
    if q_clean in ("next song", "next track", "skip song", "skip this song"):
        app = _media_app()
        _osa(f'tell application "{app}" to next track')
        return "Next song."
    if q_clean in ("previous song", "previous track", "last song", "go back a song"):
        app = _media_app()
        _osa(f'tell application "{app}" to previous track')
        return "Previous song."
    m = re.search(r"play\s+(.+?)\s+on\s+spotify$", q_clean)
    if m:
        _open(["spotify:search:" + urllib.parse.quote(m.group(1).strip())])
        return f"Searching Spotify for {m.group(1).strip()}."

    # v3: reminders & calendar events (real, via AppleScript)
    m = re.search(r"remind\s+me\s+to\s+(.+)$", q_orig, re.IGNORECASE)
    if m:
        body = m.group(1).strip()
        due = _due_datetime(q_clean)
        task = _strip_when_words(body.lower()) if due else body
        task = (task or body).strip()
        made = create_reminder(task, due=due)
        if made is None:
            return ("I couldn't create that reminder - Apple Reminders isn't available here "
                    "(macOS with the Reminders app is needed). Nothing was created.")
        return _with_suggestion(made, "reminder")
    m = re.search(r"add\s+(?:an?\s+)?event\s+(.+)$", q_orig, re.IGNORECASE)
    if m:
        body = m.group(1).strip()
        start = _due_datetime(q_clean) or (
            datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
            + datetime.timedelta(days=_parse_day_offset(q_clean)))
        title = _strip_when_words(body.lower()) or body
        made = add_calendar_event(title.strip(), start)
        if made is None:
            return ("I couldn't add that event - Apple Calendar isn't available here "
                    "(macOS with the Calendar app is needed). Nothing was added.")
        return made

    # v3: file management (moves & trashing require confirmation)
    m = re.search(r"create\s+(?:a\s+)?folder\s+(?:called\s+|named\s+)?(.+?)(?:\s+on\s+(?:the\s+)?(desktop|documents|downloads)|\s+in\s+(?:the\s+)?(documents|desktop|downloads))?$",
                  q_clean)
    if m:
        name = m.group(1).strip()
        where = (m.group(2) or m.group(3) or "desktop")
        base = os.path.expanduser(FOLDERS[where])
        try:
            os.makedirs(os.path.join(base, name), exist_ok=True)
            return _with_suggestion(f"Folder created: {name}, in your {where}.", "folder_created")
        except Exception:
            return f"I couldn't create the folder {name} in your {where}."
    m = re.search(r"create\s+(?:a\s+)?file\s+(?:called\s+|named\s+)?(.+?)(?:\s+on\s+(?:the\s+)?(desktop|documents|downloads)|\s+in\s+(?:the\s+)?(documents|desktop|downloads))?$",
                  q_clean)
    if m:
        name = m.group(1).strip()
        where = (m.group(2) or m.group(3) or "desktop")
        base = os.path.expanduser(FOLDERS[where])
        try:
            os.makedirs(base, exist_ok=True)
            path = os.path.join(base, name)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")
            return f"File created: {name}, in your {where}."
        except Exception:
            return f"I couldn't create the file {name} in your {where}."
    m = re.search(r"move\s+(.+?)\s+to\s+(downloads|documents|desktop|pictures)$", q_clean)
    if m:
        name, where = m.group(1).strip(), m.group(2)
        hits = search_files(name)
        if not hits:
            return f"I couldn't find a file matching {name} to move."
        if not _ask_confirmation(f"move {os.path.basename(hits[0])} to your {where}", confirm):
            return (f"Moving a file needs your confirmation. I did not move {os.path.basename(hits[0])} - "
                    f"ask again and say yes to confirm.")
        dest_dir = os.path.expanduser(FOLDERS[where])
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(hits[0], dest_dir)
            return f"Moved {os.path.basename(hits[0])} to your {where}."
        except Exception:
            return f"I couldn't move {os.path.basename(hits[0])}."
    m = re.search(r"(?:delete|trash|remove)\s+(?:the\s+)?(.+)$", q_clean)
    if m and m.group(1).strip() not in ("trash",) and "empty" not in q_clean:
        name = m.group(1).strip()
        hits = search_files(name)
        if not hits:
            return f"I couldn't find a file matching {name} to delete."
        if not _ask_confirmation(f"move {os.path.basename(hits[0])} to the Trash", confirm):
            return (f"Deleting a file needs your confirmation. I did not delete {os.path.basename(hits[0])} - "
                    f"ask again and say yes to confirm.")
        trash_dir = os.path.expanduser("~/.Trash")
        try:
            os.makedirs(trash_dir, exist_ok=True)
            base = os.path.basename(hits[0])
            dest = os.path.join(trash_dir, base)
            if os.path.exists(dest):
                dest = os.path.join(trash_dir, datetime.datetime.now().strftime("%H%M%S_") + base)
            shutil.move(hits[0], dest)
            return f"Moved {base} to the Trash. You can still restore it from the Trash."
        except Exception:
            return f"I couldn't delete {os.path.basename(hits[0])}."

    # v3: brightness / Wi-Fi power / Do Not Disturb
    if q_clean in ("brightness up", "increase brightness", "brighter", "screen brighter"):
        _key(144)
        return "Brightness increased."
    if q_clean in ("brightness down", "decrease brightness", "dimmer", "dim screen", "screen dimmer"):
        _key(145)
        return "Brightness decreased."
    if q_clean in ("wifi on", "wi-fi on", "turn wifi on", "turn wi-fi on", "enable wifi"):
        _run(["networksetup", "-setairportpower", "en0", "on"])
        return "Wi-Fi turned on."
    if q_clean in ("wifi off", "wi-fi off", "turn wifi off", "turn wi-fi off", "disable wifi"):
        _run(["networksetup", "-setairportpower", "en0", "off"])
        return "Wi-Fi turned off."
    if "do not disturb" in q_clean or "dnd" == q_clean:
        rc, out = _run(["shortcuts", "list"])
        if rc == 0 and "toggle focus" in out.lower():
            _run(["shortcuts", "run", "Toggle Focus"])
            return "Do Not Disturb toggled via your Toggle Focus shortcut."
        return ("Do Not Disturb needs a one-time setup: create a Shortcut named 'Toggle Focus' "
                "that toggles Focus (see the README) - then I can run it for you. I didn't change anything.")

    # --- v2 blocks (unchanged behaviour) -----------------------------------
    # 1. Stock Market
    if any(k in q_clean for k in ("stock", "market", "nifty", "sensex", "crypto", "bitcoin", "buy or sell")):
        for sym, name in jarvis_market.DEFAULT_TICKERS.items():
            if name.lower() in q_clean or sym.lower().replace(".ns", "").replace("^", "") in q_clean:
                data = jarvis_market.analyze_ticker(sym)
                if data:
                    direction = "up" if data["pct_change"] >= 0 else "down"
                    return (
                        f"{data['name']} is trading at {data['currency']}{data['price']:,}, "
                        f"{direction} {abs(data['pct_change'])}%. "
                        f"Technical indicator: {data['signal']}. {data['reason']}"
                    )
        return "Here is the market overview: " + jarvis_market.market_briefing()

    # 2. WhatsApp (v2 fallback - contact path handled above)
    m = re.search(r"(?:send|reply|message)\s+(?:on\s+whatsapp\s+to|whatsapp\s+to|to)\s+(.+?)(?:\s+saying\s+(.+))?$", q_clean)
    if "whatsapp" in q_clean and m:
        contact = m.group(1).strip()
        msg = m.group(2).strip() if m.group(2) else "I received your message, will get back shortly."
        return send_whatsapp_message(contact, msg)
    if q_clean in ("open whatsapp", "check whatsapp", "whatsapp"):
        _open([SITES["whatsapp"]])
        return "Opening WhatsApp."

    # 3. Mail briefing (honest)
    if any(k in q_clean for k in ("mail brief", "check mail", "read emails", "read my emails",
                                  "inbox summary", "unread emails", "check my email", "check email")):
        return get_mail_brief()

    # 4. Timers
    m = re.search(r"(?:set\s+(?:a\s+)?timer|timer)\s*(?:for)?\s*(\d+(?:\.\d+)?)\s*(minute|min|hour|second|sec)", q_clean)
    if m or q_clean.startswith("set a timer") or q_clean.startswith("set timer"):
        if m:
            value = float(m.group(1))
            unit = m.group(2)
            minutes = value * 60 if unit.startswith("hour") else value / 60 if unit.startswith("sec") else value
            return set_timer(minutes)
        return "For how long? Try: set a timer for 5 minutes."

    # 5. Notes (original capitalisation preserved)
    m = re.match(r"(?:note|remember|take a note)[:\s]+(.+)", q_orig, re.IGNORECASE)
    if m and not q_clean.startswith("notebook"):
        return save_note(m.group(1).strip())

    # 6. Clipboard
    m = re.match(r"copy\s+(.+?)(?:\s+to\s+(?:my\s+)?clipboard)?$", q_orig, re.IGNORECASE)
    if m and q_clean not in ("copy that", "copy this"):
        text = m.group(1).strip()
        rc, _ = _run_input(["pbcopy"], text)
        if rc == 0:
            return f"Copied to your clipboard: {text}"
        return "I couldn't copy that - the clipboard is unavailable on this system."
    if any(k in q_clean for k in ("read my clipboard", "read clipboard", "what is in my clipboard",
                                  "what's in my clipboard", "clipboard contents")):
        rc, out = _run(["pbpaste"])
        if rc == 0 and out:
            return f"Your clipboard says: {out}"
        return "Your clipboard is empty, or the clipboard is unavailable on this system."

    # 7. Screenshot
    if "screenshot" in q_clean or "screen shot" in q_clean:
        return take_screenshot()

    # 8. Battery / System / Wi-Fi status
    if "battery" in q_clean:
        return battery_status()
    if any(k in q_clean for k in ("system info", "system information", "about my mac",
                                  "about this mac", "how much ram", "disk space", "storage left")):
        return system_info()
    if "wi-fi" in q_clean or "wifi" in q_clean:
        return wifi_status()

    # 9. Volume
    m = re.search(r"(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*(?:percent|%)?", q_clean)
    if m and ("volume" in q_clean):
        n = max(0, min(100, int(m.group(1))))
        _osa(f"set volume output volume {n}")
        _osa("set volume without output muted")
        return f"Volume set to {n} percent."
    if "volume up" in q_clean or "louder" in q_clean or q_clean == "increase volume":
        _osa("set volume output volume ((output volume of (get volume settings)) + 15)")
        return "Volume increased."
    if "volume down" in q_clean or "quieter" in q_clean or q_clean == "decrease volume":
        _osa("set volume output volume ((output volume of (get volume settings)) - 15)")
        return "Volume decreased."
    if q_clean in ("unmute", "unmute volume", "unmute the volume", "turn sound on"):
        _osa("set volume without output muted")
        return "System audio unmuted."
    if q_clean in ("mute", "mute volume", "mute the volume"):
        _osa("set volume with output muted")
        return "System audio muted."

    # 10. Dark mode
    if "dark mode" in q_clean:
        _osa('tell application "System Events" to tell appearance preferences to set dark mode to not dark mode')
        return "Dark mode toggled."

    # 11. Lock screen
    if q_clean in ("lock screen", "lock my screen", "lock the screen", "lock my mac", "lock computer"):
        _run(["pmset", "displaysleepnow"])
        return "Locking your screen."

    # 12. Destructive actions - confirmation required
    if "empty trash" in q_clean or "empty the trash" in q_clean:
        if not _ask_confirmation("empty the Trash", confirm):
            return "Emptying the Trash needs your confirmation. Ask again and say yes to confirm - I did not empty it."
        _osa('tell application "Finder" to empty trash')
        return "Trash emptied."
    if re.search(r"\b(shut ?down)\b", q_clean):
        if not _ask_confirmation("shut down this Mac", confirm):
            return "Shutting down needs your confirmation. Ask again and say yes to confirm - I did not shut down."
        _osa('tell application "System Events" to shut down')
        return "Shutting down."
    if re.search(r"\b(restart|reboot)\b", q_clean) and any(
            k in q_clean for k in ("mac", "computer", "system", "restart", "reboot")) \
            and not re.search(r"restart\s+(.+?)\s*(app|application)?$", q_clean.replace("restart mac", "").replace("restart computer", "")):
        if not _ask_confirmation("restart this Mac", confirm):
            return "Restarting needs your confirmation. Ask again and say yes to confirm - I did not restart."
        _osa('tell application "System Events" to restart')
        return "Restarting."
    if "log out" in q_clean or "logout" in q_clean or "log me out" in q_clean:
        if not _ask_confirmation("log you out", confirm):
            return "Logging out needs your confirmation. Ask again and say yes to confirm - I did not log you out."
        _osa('tell application "System Events" to log out')
        return "Logging out."

    # 13. Find a file (Spotlight)
    m = re.search(r"(?:find|locate|search for)(?:\s+the)?(?:\s+file)?\s+(.+)", q_clean)
    if m and ("file" in q_clean or "." in m.group(1)):
        return find_file(m.group(1).strip())

    # 14. Quit / close an app
    m = re.match(r"(?:please\s+)?(?:quit|close|exit)\s+(?:the\s+)?(.+?)(?:\s+app)?$", q_clean)
    if m:
        name = m.group(1).strip()
        if name not in SITES:
            app = find_app(name) or ALIASES.get(name, name.title())
            _osa(f'tell application "{app}" to quit')
            return f"Closing {app}."

    # 15. Open a known folder
    m = re.match(r"(?:please\s+)?open\s+(?:the\s+)?(?:my\s+)?(.+?)\s*(?:folder)?$", q_clean)
    if m and m.group(1).strip() in FOLDERS:
        folder = m.group(1).strip()
        _open([os.path.expanduser(FOLDERS[folder])])
        return f"Opening your {folder} folder."

    # 16. YouTube
    m = re.search(r"(?:play|search|find|open)\s+(.+?)\s+(?:on|in)\s+youtube", q_clean)
    if m:
        query = m.group(1).strip()
        _open(["https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)])
        return f"Playing {query} on YouTube."

    # 17. Google Search
    m = re.match(r"(?:search|google)(?: for)?\s+(.+)", q_clean)
    if m:
        query = m.group(1).strip()
        _open(["https://www.google.com/search?q=" + urllib.parse.quote(query)])
        return f"Searching Google for {query}."

    # 18. App / Site Launch
    m = re.match(r"(?:please\s+)?(?:open|launch|start)\s+(.+)", q_clean)
    if m:
        name = m.group(1).strip().replace(" folder", "").strip()
        if name in SITES:
            _open([SITES[name]])
            return f"Opening {name}."
        if name in FOLDERS:
            _open([os.path.expanduser(FOLDERS[name])])
            return f"Opening your {name} folder."
        app = find_app(name)
        if app and _open(["-a", app]):
            return f"Opening {app}."
        return f"I couldn't find an application named {name}."

    # 19. Date and Time (only when actually asked - a chat sentence that
    # merely contains "today"/"time" must reach the brain, not this branch)
    if re.search(r"\bwhat(?:'s| is)?\b[^.]*\btime\b", q_clean) or q_clean in (
            "time", "tell me the time", "the time"):
        return "It is currently " + datetime.datetime.now().strftime("%I:%M %p")
    if re.search(r"\bwhat(?:'s| is)?\b[^.]*\b(date|day|today)\b", q_clean) or q_clean in (
            "date", "today", "tell me the date", "what day is it"):
        return "Today is " + datetime.datetime.now().strftime("%A, %B %d, %Y")

    return None
