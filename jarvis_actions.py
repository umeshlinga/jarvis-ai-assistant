"""
jarvis_actions.py v2 - Local System Automation, WhatsApp, Mail & Stock Commands
Handles instant offline actions without hitting external LLM APIs.

v2 changes (Advanced / M1 build):
- The old fake mail brief is GONE. Jarvis never invents emails: it either
  reads ~/.openjarvis/mail_cache.txt (only if you created it) or says
  plainly that it cannot read Gmail directly and opens Gmail for you.
- New macOS actions: quit app, open folder, find file (mdfind), screenshot,
  battery, system info, Wi-Fi, set-volume-to-N, unmute, dark mode, lock
  screen, clipboard copy/read, notes, timers.
- Destructive actions (empty trash, restart, shutdown, logout) require an
  explicit "yes" confirmation through the `confirm` callback.
- Nothing here uses sudo. Ever.
"""

import datetime
import glob
import os
import re
import subprocess
import threading
import urllib.parse

import jarvis_market

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


# --------------------------------------------------------------------------
# Low-level helpers. Tests monkeypatch _open / _osa / _run / _run_input so
# that no test ever opens an app, changes a setting, or touches the system.
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


def _say_local(text):
    """Speak with macOS `say` directly (used by the timer thread)."""
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
    Destructive-action gate. Returns True only if the confirm callback
    exists and answers truthy. With no callback, nothing destructive runs.
    """
    if confirm is None:
        return False
    try:
        return bool(confirm(f"Are you sure you want me to {what}? Say yes to confirm."))
    except Exception:
        return False


# --------------------------------------------------------------------------
# Individual actions
# --------------------------------------------------------------------------

def send_whatsapp_message(contact_name, message="Hello from Jarvis"):
    """Opens WhatsApp Web with the message pre-filled for the contact."""
    encoded_msg = urllib.parse.quote(message)
    _open([f"https://web.whatsapp.com/send?text={encoded_msg}"])
    return f"Opening WhatsApp to message {contact_name}."


def get_mail_brief():
    """
    HONEST mail brief. Jarvis cannot read your Gmail account directly.
    If you have saved a briefing yourself at ~/.openjarvis/mail_cache.txt,
    that text is read back. Otherwise Jarvis says so plainly and opens Gmail.
    """
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
        import psutil  # optional at runtime; installed by requirements.txt
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


def find_file(name):
    rc, out = _run(["mdfind", "-name", name])
    if rc == 0 and out:
        hits = [line for line in out.splitlines() if line.strip()][:3]
        if hits:
            spoken = "; ".join(hits)
            return f"I found {name}. Top matches: {spoken}."
    return f"I couldn't find any file matching {name}."


def take_screenshot():
    desktop = os.path.expanduser("~/Desktop")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(desktop, f"Jarvis_Screenshot_{stamp}.png")
    rc, _ = _run(["screencapture", path])
    if rc == 0:
        return f"Screenshot saved to your Desktop as Jarvis_Screenshot_{stamp}.png."
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
    return f"Note saved: {text}"


def set_timer(minutes, label="timer"):
    minutes = float(minutes)
    seconds = minutes * 60.0

    def _fire():
        msg = f"Your {label} is done - {minutes:g} minutes are up."
        _say_local(msg)

    t = threading.Timer(seconds, _fire)
    t.daemon = True
    t.start()
    unit = "minute" if minutes == 1 else "minutes"
    return f"Timer set for {minutes:g} {unit}. I will speak when it is done."


# --------------------------------------------------------------------------
# Main router
# --------------------------------------------------------------------------

def try_action(q, confirm=None):
    """
    Executes instant deterministic actions. Returns a response string if
    handled, or None to pass the query to the AI brain.

    `confirm` is an optional callable(prompt) -> bool used to authorise
    destructive actions (empty trash, restart, shutdown, logout).
    """
    q_clean = q.lower().strip(" .,!?")
    if not q_clean:
        return None

    # 1. Stock Market Queries & Buy/Sell Analysis
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

    # 2. WhatsApp Auto-Message
    m = re.search(r"(?:send|reply|message)\s+(?:on\s+whatsapp\s+to|whatsapp\s+to|to)\s+(.+?)(?:\s+saying\s+(.+))?$", q_clean)
    if "whatsapp" in q_clean and m:
        contact = m.group(1).strip()
        msg = m.group(2).strip() if m.group(2) else "I received your message, will get back shortly."
        return send_whatsapp_message(contact, msg)

    if q_clean in ("open whatsapp", "check whatsapp", "whatsapp"):
        _open([SITES["whatsapp"]])
        return "Opening WhatsApp."

    # 3. Mail Briefing (honest - never invents emails)
    if any(k in q_clean for k in ("mail brief", "check mail", "read emails", "read my emails",
                                  "inbox summary", "unread emails", "check my email", "check email")):
        return get_mail_brief()

    # 4. Timers (before the time check - "timer" contains "time")
    m = re.search(r"(?:set\s+(?:a\s+)?timer|timer)\s*(?:for)?\s*(\d+(?:\.\d+)?)\s*(minute|min|hour|second|sec)", q_clean)
    if m or q_clean.startswith("set a timer") or q_clean.startswith("set timer"):
        if m:
            value = float(m.group(1))
            unit = m.group(2)
            minutes = value * 60 if unit.startswith("hour") else value / 60 if unit.startswith("sec") else value
            return set_timer(minutes)
        return "For how long? Try: set a timer for 5 minutes."

    # 5. Notes (text kept in the user's original capitalisation)
    m = re.match(r"(?:note|remember|take a note)[:\s]+(.+)", q.strip(), re.IGNORECASE)
    if m and not q_clean.startswith("notebook"):
        return save_note(m.group(1).strip())

    # 6. Clipboard (copied text kept in the user's original capitalisation)
    m = re.match(r"copy\s+(.+?)(?:\s+to\s+(?:my\s+)?clipboard)?$", q.strip(), re.IGNORECASE)
    if m:
        text = m.group(1).strip()
        # "copy <text>" and "copy <text> to clipboard" both land here.
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

    # 8. Battery / System / Wi-Fi
    if "battery" in q_clean:
        return battery_status()
    if any(k in q_clean for k in ("system info", "system information", "about my mac",
                                  "about this mac", "how much ram", "disk space", "storage left")):
        return system_info()
    if "wi-fi" in q_clean or "wifi" in q_clean:
        return wifi_status()

    # 9. Volume: set to N, up, down, mute, unmute
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

    # 15. Open a folder
    m = re.match(r"(?:please\s+)?open\s+(?:the\s+)?(?:my\s+)?(.+?)\s*(?:folder)?$", q_clean)
    if m and m.group(1).strip() in FOLDERS:
        folder = m.group(1).strip()
        _open([os.path.expanduser(FOLDERS[folder])])
        return f"Opening your {folder} folder."

    # 16. YouTube Music / Search
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

    # 19. Date and Time
    if "time" in q_clean:
        return "It is currently " + datetime.datetime.now().strftime("%I:%M %p")
    if "date" in q_clean or "today" in q_clean:
        return "Today is " + datetime.datetime.now().strftime("%A, %B %d, %Y")

    return None
