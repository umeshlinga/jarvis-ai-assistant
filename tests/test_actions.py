#!/usr/bin/env python3
"""
tests/test_actions.py - Jarvis v2 action tests.

Runnable two ways:
    python3 tests/test_actions.py     (plain, no pytest needed)
    pytest tests/test_actions.py

Every system helper in jarvis_actions (_open, _osa, _run, _run_input) is
monkeypatched/stubbed, so NOTHING actually opens, quits, speaks, or
changes on the machine running the tests, and no network is used.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolated HOME so notes / mail-cache tests never touch the real one.
_TMP_HOME = tempfile.mkdtemp(prefix="jarvis_test_home_")
os.environ["HOME"] = _TMP_HOME

import jarvis_actions  # noqa: E402
import jarvis_brain  # noqa: E402
import jarvis_config  # noqa: E402
import jarvis_market  # noqa: E402

jarvis_actions._pause = lambda seconds: None  # never really sleep in tests

# Contacts book in the temporary HOME.
import json as _json  # noqa: E402
os.makedirs(os.path.join(_TMP_HOME, ".openjarvis"), exist_ok=True)
with open(os.path.join(_TMP_HOME, ".openjarvis", "contacts.json"), "w") as _f:
    _json.dump({
        "Mom": {"phone": "+1 555 010 2030", "email": "mom@example.com"},
        "Aunt May": {"phone": "", "email": "may@example.com"},
    }, _f)

FLAGS = {"reminders_fail": False}

# ---------------------------------------------------------------- stubs

CALLS = {"open": [], "osa": [], "run": [], "run_input": []}
CLIPBOARD = {"text": ""}


def fake_open(args):
    CALLS["open"].append(list(args))
    return True


def fake_osa(script):
    CALLS["osa"].append(script)
    return True


def fake_run(cmd):
    cmd = list(cmd)
    CALLS["run"].append(cmd)
    tool = cmd[0] if cmd else ""
    if tool == "pmset" and "batt" in cmd:
        return 0, "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1234567) 87%; discharging; 4:12 remaining present: true"
    if tool == "pmset":
        return 0, ""
    if tool == "pbpaste":
        return 0, CLIPBOARD["text"]
    if tool == "mdfind":
        name = cmd[-1] if len(cmd) > 1 else ""
        if "report" in name:
            return 0, "/Users/test/Documents/report.pdf\n/Users/test/Downloads/report-old.pdf"
        return 0, ""
    if tool == "osascript":
        script = cmd[-1] if cmd else ""
        if "Reminders" in script and "make new reminder" not in script:
            if FLAGS["reminders_fail"]:
                return 1, ""
            return 0, "Buy milk\nCall the dentist"
        return 0, ""
    if tool == "shortcuts":
        return 1, ""
    if tool == "networksetup":
        if "-setairportpower" in cmd:
            return 0, ""
        return 0, "Current Wi-Fi Network: HomeWifi"
    if tool == "sysctl":
        return 0, "Apple M1"
    if tool == "sw_vers":
        return 0, "14.5"
    if tool == "screencapture":
        return 0, ""
    return 0, ""


def fake_run_input(cmd, input_text):
    CALLS["run_input"].append((list(cmd), input_text))
    if cmd and cmd[0] == "pbcopy":
        CLIPBOARD["text"] = input_text
        return 0, ""
    return 0, ""


jarvis_actions._open = fake_open
jarvis_actions._osa = fake_osa
jarvis_actions._run = fake_run
jarvis_actions._run_input = fake_run_input

# ---------------------------------------------------------------- helpers


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    print(f"PASS: {name}")


# ---------------------------------------------------------------- tests


def test_time():
    r = jarvis_actions.try_action("what time is it")
    check("time", r is not None and "It is currently" in r, repr(r))


def test_date():
    r = jarvis_actions.try_action("what is the date")
    check("date", r is not None and "Today is" in r, repr(r))


def test_battery():
    r = jarvis_actions.try_action("battery status")
    check("battery", r is not None and "87" in r and "battery" in r.lower(), repr(r))


def test_set_volume_50():
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("set volume to 50 percent")
    check("set volume 50", r == "Volume set to 50 percent.", repr(r))
    check("set volume 50 (osascript called)",
          any("output volume 50" in s for s in CALLS["osa"]), repr(CALLS["osa"]))


def test_note_taking_temp_home():
    r = jarvis_actions.try_action("note buy milk tomorrow")
    notes = os.path.join(_TMP_HOME, ".openjarvis", "notes.txt")
    ok = os.path.exists(notes) and "buy milk tomorrow" in open(notes).read()
    check("note taking", r is not None and "Note saved" in r and ok, repr(r))


def test_clipboard_copy():
    CALLS["run_input"].clear()
    r = jarvis_actions.try_action("copy hello jarvis")
    check("clipboard copy", r is not None and "Copied" in r and CLIPBOARD["text"] == "hello jarvis", repr(r))


def test_clipboard_read():
    CLIPBOARD["text"] = "some copied words"
    r = jarvis_actions.try_action("read my clipboard")
    check("clipboard read", r is not None and "some copied words" in r, repr(r))


def test_open_youtube_site():
    CALLS["open"].clear()
    r = jarvis_actions.try_action("open youtube")
    check("open youtube", r == "Opening youtube." and
          any("youtube.com" in " ".join(args) for args in CALLS["open"]), repr(r))


def test_open_new_sites():
    for phrase, needle in (("open chatgpt", "chatgpt"), ("open linkedin", "linkedin"),
                           ("open indeed", "indeed")):
        CALLS["open"].clear()
        r = jarvis_actions.try_action(phrase)
        check(f"open site: {phrase}", r is not None and "Opening" in r and
              any(needle in " ".join(args) for args in CALLS["open"]), repr(r))


def test_whatsapp_parse():
    CALLS["open"].clear()
    r = jarvis_actions.try_action("send whatsapp to pallavi saying hello from jarvis")
    check("whatsapp parse", r is not None and "WhatsApp" in r and "pallavi" in r.lower(), repr(r))


def test_mail_brief_honesty_no_cache():
    cache = os.path.join(_TMP_HOME, ".openjarvis", "mail_cache.txt")
    if os.path.exists(cache):
        os.remove(cache)
    r = jarvis_actions.try_action("read my emails")
    check("mail brief honesty (says it cannot read Gmail)",
          r is not None and "can't read your Gmail" in r, repr(r))
    check("mail brief honesty (no invented emails)",
          r is not None and "unread priority emails" not in r and "team project sync" not in r
          and "server alert" not in r, repr(r))


def test_timer():
    r = jarvis_actions.try_action("set a timer for 5 minutes")
    check("timer", r is not None and "Timer set for 5 minutes" in r, repr(r))


def test_destructive_requires_confirmation():
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("empty trash")  # no confirm callback
    check("empty trash blocked without confirmation",
          r is not None and "confirmation" in r.lower() and not CALLS["osa"], repr(r))
    r2 = jarvis_actions.try_action("empty trash", confirm=lambda prompt: True)
    check("empty trash runs with yes confirmation",
          r2 == "Trash emptied." and bool(CALLS["osa"]), repr(r2))


def test_unknown_query_returns_none():
    r = jarvis_actions.try_action("explain quantum computing please")
    check("unknown query -> None", r is None, repr(r))


def test_market_import_only():
    check("market import (no network)",
          "AAPL" in jarvis_market.DEFAULT_TICKERS
          and "MSFT" in jarvis_market.DEFAULT_TICKERS
          and "TSLA" in jarvis_market.DEFAULT_TICKERS
          and "^GSPC" in jarvis_market.DEFAULT_TICKERS,
          repr(sorted(jarvis_market.DEFAULT_TICKERS)))


# ---------------------------------------------------------------- v3 tests


def test_open_file_by_name():
    CALLS["open"].clear()
    r = jarvis_actions.try_action("open file report")
    check("open file by name", r is not None and "report.pdf" in r and
          any("/Users/test/Documents/report.pdf" in " ".join(a) for a in CALLS["open"]), repr(r))


def test_open_file_missing_is_honest():
    r = jarvis_actions.try_action("open file no_such_file_xyz")
    check("open missing file honest", r is not None and "couldn't find" in r.lower(), repr(r))


def test_contacts_resolve_hit_and_miss():
    import jarvis_contacts
    hit = jarvis_contacts.resolve("mom")
    check("contacts resolve hit", hit is not None and hit[0] == "Mom"
          and hit[1]["phone"] == "+1 555 010 2030", repr(hit))
    partial = jarvis_contacts.resolve("aunt")
    check("contacts resolve partial", partial is not None and partial[0] == "Aunt May", repr(partial))
    miss = jarvis_contacts.resolve("zanzibar nobody")
    check("contacts resolve miss", miss is None, repr(miss))


def test_imessage_blocked_without_confirm():
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("send a message to mom saying hello there")
    check("imessage blocked without confirm",
          r is not None and "confirmation" in r.lower() and not CALLS["osa"], repr(r))


def test_imessage_sends_with_yes():
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("send a message to mom saying hello there",
                                  confirm=lambda prompt: True)
    joined = " ".join(CALLS["osa"])
    check("imessage sent with yes",
          r is not None and "sent to Mom" in r and "+1 555 010 2030" in joined
          and "hello there" in joined and "Messages" in joined, repr(r) + " | " + joined)


def test_imessage_unknown_contact_never_sends():
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("send a message to zanzibar saying hi",
                                  confirm=lambda prompt: True)
    check("imessage unknown contact", r is not None and "don't have" in r and not CALLS["osa"], repr(r))


def test_whatsapp_no_phone_fallback():
    CALLS["open"].clear()
    r = jarvis_actions.try_action("send whatsapp to aunt may saying hello")
    check("whatsapp no-phone fallback",
          r is not None and "isn't in your contacts" in r and
          any("web.whatsapp.com" in " ".join(a) for a in CALLS["open"]), repr(r))


def test_whatsapp_with_phone_and_confirm():
    CALLS["open"].clear()
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("send whatsapp to mom saying hello on whatsapp",
                                  confirm=lambda prompt: True)
    check("whatsapp wa.me + enter after yes",
          r is not None and "sent to Mom" in r and
          any("wa.me/15550102030" in " ".join(a) for a in CALLS["open"]) and
          any("36" in s for s in CALLS["osa"]), repr(r))


def test_email_compose_prefilled_not_sent():
    CALLS["open"].clear()
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("send email to mom@example.com subject Lunch plans saying bring the salad")
    import urllib.parse as _up
    urls = [_up.unquote_plus(" ".join(a)) for a in CALLS["open"]]
    joined = " ".join(urls)
    check("email compose prefilled",
          r is not None and "press Send" in r and "view=cm" in joined and
          "mom@example.com" in joined and "Lunch plans" in joined and
          "bring the salad" in joined and not CALLS["osa"], repr(r) + " | " + joined)


def test_call_facetime():
    CALLS["open"].clear()
    r = jarvis_actions.try_action("call mom")
    check("call facetime url", r is not None and "FaceTime call" in r and
          any("facetime://" in " ".join(a) and "555 010 2030" in " ".join(a)
              for a in CALLS["open"]), repr(r))


def test_video_call_facetimeaudio():
    CALLS["open"].clear()
    r = jarvis_actions.try_action("video call mom")
    check("video call facetimeaudio", r is not None and "video" in r.lower() and
          any("facetimeaudio://" in " ".join(a) for a in CALLS["open"]), repr(r))


def test_type_text_keystroke():
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("type hello world")
    check("type text", r is not None and "Typed" in r and
          any('keystroke "hello world"' in s for s in CALLS["osa"]), repr(CALLS["osa"]))


def test_keyboard_commands():
    for phrase, needle in (("press enter", "key code 36"), ("copy that", 'keystroke "c"'),
                           ("paste", 'keystroke "v"'), ("save this", 'keystroke "s"'),
                           ("select all", 'keystroke "a"'), ("undo", 'keystroke "z"'),
                           ("switch app", "key code 48")):
        CALLS["osa"].clear()
        r = jarvis_actions.try_action(phrase)
        check(f"keyboard: {phrase}", r is not None and
              any(needle in s for s in CALLS["osa"]), repr(CALLS["osa"]))


def test_reminder_parse_tomorrow_6pm():
    CALLS["run"].clear()
    r = jarvis_actions.try_action("remind me to call mom tomorrow at 6 pm")
    scripts = " ".join(" ".join(c) for c in CALLS["run"] if c and c[0] == "osascript")
    tomorrow = jarvis_actions.datetime.date.today() + jarvis_actions.datetime.timedelta(days=1)
    check("reminder created tomorrow 6pm",
          r is not None and "Reminder created" in r and "call mom" in scripts
          and tomorrow.strftime("%A") in scripts and "06:00:00 PM" in scripts,
          repr(r) + " | " + scripts)


def test_calendar_event():
    CALLS["run"].clear()
    r = jarvis_actions.try_action("add event dentist tomorrow at 10 am")
    scripts = " ".join(" ".join(c) for c in CALLS["run"] if c and c[0] == "osascript")
    check("calendar event", r is not None and "Event added" in r
          and "Calendar" in scripts and "dentist" in scripts, repr(r))


def test_create_folder_temp_home():
    r = jarvis_actions.try_action("create folder jarvis_test_folder on desktop")
    path = os.path.join(_TMP_HOME, "Desktop", "jarvis_test_folder")
    check("create folder", r is not None and "Folder created" in r and os.path.isdir(path), repr(r))


def test_create_file_temp_home():
    r = jarvis_actions.try_action("create file jarvis_test_note.txt on desktop")
    path = os.path.join(_TMP_HOME, "Desktop", "jarvis_test_note.txt")
    check("create file", r is not None and "File created" in r and os.path.isfile(path), repr(r))


def test_trash_blocked_without_confirm():
    victim = os.path.join(_TMP_HOME, "Desktop", "trashme.txt")
    with open(victim, "w") as f:
        f.write("x")
    r = jarvis_actions.try_action("trash trashme")
    check("trash blocked without confirm",
          r is not None and "confirmation" in r.lower() and os.path.exists(victim), repr(r))
    r2 = jarvis_actions.try_action("trash trashme", confirm=lambda prompt: True)
    trashed = os.path.join(_TMP_HOME, ".Trash", "trashme.txt")
    check("trash moves to ~/.Trash with yes",
          r2 is not None and "Trash" in r2 and not os.path.exists(victim)
          and os.path.exists(trashed), repr(r2))


def test_brightness():
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("brightness up")
    check("brightness up", r is not None and "Brightness increased" in r and
          any("144" in s for s in CALLS["osa"]), repr(CALLS["osa"]))
    r2 = jarvis_actions.try_action("brightness down")
    check("brightness down", r2 is not None and "Brightness decreased" in r2 and
          any("145" in s for s in CALLS["osa"]), repr(CALLS["osa"]))


def test_wifi_off():
    CALLS["run"].clear()
    r = jarvis_actions.try_action("wifi off")
    flat = [" ".join(c) for c in CALLS["run"]]
    check("wifi off", r is not None and "Wi-Fi turned off" in r and
          any("-setairportpower en0 off" in c for c in flat), repr(CALLS["run"]))


def test_open_website_dot_com():
    CALLS["open"].clear()
    r = jarvis_actions.try_action("open website example dot com")
    check("open website normalization", r is not None and "example.com" in r and
          any("https://example.com" in " ".join(a) for a in CALLS["open"]), repr(r))


def test_media_controls():
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("play music")
    check("play music", r is not None and "Playing music" in r and bool(CALLS["osa"]), repr(r))
    r2 = jarvis_actions.try_action("next song")
    check("next song", r2 is not None and "Next song" in r2, repr(r2))


def test_dnd_needs_shortcut_honest():
    r = jarvis_actions.try_action("turn on do not disturb")
    check("dnd honest without shortcut", r is not None and "Toggle Focus" in r, repr(r))


def test_screen_recording_not_supported():
    r = jarvis_actions.try_action("take a video of my screen")
    check("screen recording not supported", r is not None and "isn't supported" in r, repr(r))


# ------------------------------------------------------- conversational tests


def test_advisory_classification():
    for phrase in ("what do you think about moving to a new city",
                   "is it correct that the meeting is on friday",
                   "is this right", "should i take the new job",
                   "what is your opinion on this plan", "advise me on saving money"):
        check(f"advisory: {phrase[:32]}", jarvis_brain.is_advisory(phrase), phrase)
    for phrase in ("remind me to call mom tomorrow", "open youtube",
                   "set a timer for 5 minutes", "note remember the milk"):
        check(f"not advisory: {phrase[:32]}", not jarvis_brain.is_advisory(phrase), phrase)


def test_advisory_offline_honest():
    # No keys exist in the temp HOME, so an opinion question must get the
    # honest offline answer - not a local action, not a fabrication.
    r = jarvis_brain.think("what do you think about the time in paris")
    check("advisory offline honest", r is not None and "offline" in r.lower()
          and "can't think" in r, repr(r))


def test_suggestion_map():
    check("suggestion for screenshot", bool(jarvis_actions.get_suggestion("screenshot")))
    check("suggestion for note", bool(jarvis_actions.get_suggestion("note")))
    check("suggestion for timer", bool(jarvis_actions.get_suggestion("timer")))
    check("no suggestion for unknown", jarvis_actions.get_suggestion("nope") is None)


def test_morning_briefing_assembles():
    old_market = jarvis_market.market_briefing
    jarvis_market.market_briefing = lambda: "Nifty is up 1 percent."
    try:
        FLAGS["reminders_fail"] = False
        r = jarvis_actions.morning_briefing()
        import datetime as _dtmod
        ok = (any(g in r for g in ("Good morning", "Good afternoon", "Good evening"))
              and _dtmod.date.today().strftime("%A") in r and "87" in r
              and "Buy milk" in r and "Nifty" in r)
        check("briefing assembles greeting+date+battery+reminders+market", ok, repr(r))
        FLAGS["reminders_fail"] = True
        jarvis_market.market_briefing = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
        r2 = jarvis_actions.morning_briefing()
        check("briefing survives reminders+market failure",
              r2 is not None and "briefing" in r2.lower(), repr(r2))
    finally:
        FLAGS["reminders_fail"] = False
        jarvis_market.market_briefing = old_market


def test_v4_task_status_route():
    r = jarvis_actions.try_action("what are you doing")
    assert r is not None and ("background tasks" in r or "working on" in r or "last" in r.lower()), r


def test_v4_hud_route():
    r = jarvis_actions.try_action("show yourself")
    assert r is not None and "127.0.0.1" in r, r


def test_v4_open_browser():
    CALLS["open"].clear()
    r = jarvis_actions.try_action("open browser")
    assert r == "Opening your browser.", r
    assert any("google.com" in " ".join(map(str, args)) for args in CALLS["open"]), CALLS["open"]


def test_v4_copy_file():
    src = os.path.join(os.path.expanduser("~"), "Desktop", "copyme.txt")
    with open(src, "w") as f:
        f.write("copy me please")
    r = jarvis_actions.try_action("copy copyme to downloads")
    assert r == "Copied copyme.txt to your downloads.", r
    dest = os.path.join(os.path.expanduser("~"), "Downloads", "copyme.txt")
    assert os.path.exists(dest) and open(dest).read() == "copy me please"


def test_v4_email_attach():
    src = os.path.join(os.path.expanduser("~"), "Desktop", "attachme.pdf")
    with open(src, "w") as f:
        f.write("fake pdf")
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("email mom and attach attachme.pdf",
                                  confirm=lambda p: True)
    assert "opened Mail" in r and "attachme.pdf" in r, r
    scripts = " ".join(CALLS["osa"])
    assert "mom@example.com" in scripts, scripts
    assert "attachme.pdf" in scripts and "attachment" in scripts, scripts
    assert "send" not in scripts.lower(), scripts  # visible only, NEVER auto-sent
    # Without confirmation nothing is created.
    CALLS["osa"].clear()
    r2 = jarvis_actions.try_action("email mom and attach attachme.pdf")
    assert "confirmation" in r2.lower() and not CALLS["osa"], r2


def test_v4_web_routes_stubbed():
    import jarvis_web
    orig_search, orig_read = jarvis_web.search_and_tell, jarvis_web.read_website
    jarvis_web.search_and_tell = lambda topic: f"SEARCHED:{topic}"
    jarvis_web.read_website = lambda target: f"READ:{target}"
    try:
        r = jarvis_actions.try_action("search for electric cars and tell me")
        assert r == "SEARCHED:electric cars", r
        r = jarvis_actions.try_action("read about black holes")
        assert r == "SEARCHED:black holes", r
        r = jarvis_actions.try_action("read this website example dot com")
        assert r == "READ:example dot com", r
        r = jarvis_actions.try_action("read bbc dot com")
        assert r == "READ:bbc dot com", r
    finally:
        jarvis_web.search_and_tell, jarvis_web.read_website = orig_search, orig_read


def test_v41_job_route_stubbed():
    # v4.1 headline fix: spoken job searches must DO the work (route to
    # jarvis_web.job_search), never fall through to the LLM for a
    # "check LinkedIn and Indeed" suggestion reply.
    import jarvis_web
    orig_job = jarvis_web.job_search
    seen = {}

    def fake_job(q):
        seen["q"] = q
        return "JOBSEARCH DONE"

    jarvis_web.job_search = fake_job
    try:
        for phrase in ("show me the job openings in indianapolis in bioinformatics",
                       "what are the job openings in indianapolis",
                       "find jobs in indianapolis",
                       "bioinformatics jobs in indianapolis"):
            seen.clear()
            r = jarvis_actions.try_action(phrase)
            assert r == "JOBSEARCH DONE", (phrase, r)
            assert seen.get("q") == phrase, (phrase, seen)
    finally:
        jarvis_web.job_search = orig_job


def test_v41_general_search_route_stubbed():
    # v4.1 widened route: everyday external-info phrasings reach the web
    # search with a cleaned topic; "show" also opens the results page.
    import jarvis_web
    orig_search = jarvis_web.search_and_tell

    def fake_search(topic, open_results=False):
        return f"SEARCHED:{topic}:{open_results}"

    jarvis_web.search_and_tell = fake_search
    try:
        r = jarvis_actions.try_action("show me electric cars")
        assert r == "SEARCHED:electric cars:True", r
        r = jarvis_actions.try_action("find out about black holes")
        assert r == "SEARCHED:black holes:False", r
        r = jarvis_actions.try_action("what are the latest iphone prices")
        assert r == "SEARCHED:latest iphone prices:False", r
        r = jarvis_actions.try_action("search electric cars")
        assert r == "SEARCHED:electric cars:False", r
        r = jarvis_actions.try_action("look up the moon landing")
        assert r.startswith("SEARCHED:") and "moon landing" in r, r
    finally:
        jarvis_web.search_and_tell = orig_search


def test_v4_code_route_stubbed():
    import jarvis_code
    orig = jarvis_code.create_code
    jarvis_code.create_code = lambda name, task: f"MADE {name}: {task}"
    try:
        r = jarvis_actions.try_action("create a python file called demo that prints hi")
        assert r == "MADE demo: prints hi", r
    finally:
        jarvis_code.create_code = orig


def test_v5_gui_verbs():
    # Scroll up/down already ride the System Events key codes 116/121.
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("scroll up")
    check("v5 scroll up acks", r == "Scrolled up.", repr(r))
    check("v5 scroll up built key code 116 script",
          any("key code 116" in s for s in CALLS["osa"]), repr(CALLS["osa"]))
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("scroll down")
    check("v5 scroll down acks", r == "Scrolled down.", repr(r))
    check("v5 scroll down built key code 121 script",
          any("key code 121" in s for s in CALLS["osa"]), repr(CALLS["osa"]))
    # Press keys ride the same table.
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("press enter")
    check("v5 press enter acks", r == "Pressed Enter."
          and any("key code 36" in s for s in CALLS["osa"]), repr(r))
    # Switch to <app> activates it by name.
    CALLS["osa"].clear()
    r = jarvis_actions.try_action("switch to chrome")
    check("v5 switch to app activates it", r == "Switched to Google Chrome."
          and any("activate" in s and "Google Chrome" in s for s in CALLS["osa"]),
          repr(r))
    # Click <name>: script built; "clicked" output -> success ack.
    orig_osa_run = jarvis_actions._osa_run
    seen = {}

    def fake_osa_run_ok(script):
        seen["script"] = script
        return 0, "clicked"

    jarvis_actions._osa_run = fake_osa_run_ok
    try:
        r = jarvis_actions.try_action("click Save")
        check("v5 click success acks", r == "Clicked save.", repr(r))
        check("v5 click script searches UI elements by name/title",
              "System Events" in seen["script"] and "entire contents" in seen["script"]
              and "save" in seen["script"], seen.get("script", "")[:80])
    finally:
        jarvis_actions._osa_run = orig_osa_run
    # Click failure (element not found / no Accessibility) is honest.
    jarvis_actions._osa_run = lambda script: (1, "")
    try:
        r = jarvis_actions.try_action("click Nonexistent Button")
        check("v5 click failure is honest (Accessibility / not visible)",
              r is not None and "couldn't click" in r
              and "Accessibility" in r, repr(r))
    finally:
        jarvis_actions._osa_run = orig_osa_run


def test_v5_config_defaults():
    cfg = jarvis_config.load_config()
    check("v5 config defaults (speak policy + butler)",
          cfg.get("speak_actions") is False
          and cfg.get("proactive_enabled") is True
          and cfg.get("proactive_interval_min") == 30
          and cfg.get("proactive_jobs") is True,
          repr({k: cfg.get(k) for k in ("speak_actions", "proactive_enabled",
                                        "proactive_interval_min", "proactive_jobs")}))


def test_stt_config_defaults():
    cfg = jarvis_config.load_config()
    check("stt config defaults", cfg.get("stt_engine") == "auto"
          and cfg.get("whisper_model") == "base.en"
          and cfg.get("suggestions") is True
          and cfg.get("max_history_turns") == 30, repr({k: cfg.get(k) for k in
              ("stt_engine", "whisper_model", "suggestions", "max_history_turns")}))


ALL_TESTS = [
    test_time, test_date, test_battery, test_set_volume_50,
    test_note_taking_temp_home, test_clipboard_copy, test_clipboard_read,
    test_open_youtube_site, test_open_new_sites, test_whatsapp_parse,
    test_mail_brief_honesty_no_cache, test_timer,
    test_destructive_requires_confirmation, test_unknown_query_returns_none,
    test_market_import_only,
    test_open_file_by_name, test_open_file_missing_is_honest,
    test_contacts_resolve_hit_and_miss, test_imessage_blocked_without_confirm,
    test_imessage_sends_with_yes, test_imessage_unknown_contact_never_sends,
    test_whatsapp_no_phone_fallback, test_whatsapp_with_phone_and_confirm,
    test_email_compose_prefilled_not_sent, test_call_facetime,
    test_video_call_facetimeaudio, test_type_text_keystroke,
    test_keyboard_commands, test_reminder_parse_tomorrow_6pm,
    test_calendar_event, test_create_folder_temp_home,
    test_create_file_temp_home, test_trash_blocked_without_confirm,
    test_brightness, test_wifi_off, test_open_website_dot_com,
    test_media_controls, test_dnd_needs_shortcut_honest,
    test_screen_recording_not_supported, test_advisory_classification,
    test_advisory_offline_honest, test_suggestion_map,
    test_morning_briefing_assembles, test_stt_config_defaults,
    test_v4_task_status_route, test_v4_hud_route, test_v4_open_browser,
    test_v4_copy_file, test_v4_email_attach, test_v4_web_routes_stubbed,
    test_v4_code_route_stubbed,
    test_v41_job_route_stubbed, test_v41_general_search_route_stubbed,
    test_v5_gui_verbs, test_v5_config_defaults,
]


def main():
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
        except AssertionError as e:
            failures += 1
            print(str(e))
        except Exception as e:  # unexpected error = failure, reported honestly
            failures += 1
            print(f"ERROR in {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} tests passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
