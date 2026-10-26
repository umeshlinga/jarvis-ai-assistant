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
import jarvis_market  # noqa: E402

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
        return 0, "/Users/test/Documents/report.pdf\n/Users/test/Downloads/report-old.pdf"
    if tool == "networksetup":
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


ALL_TESTS = [
    test_time, test_date, test_battery, test_set_volume_50,
    test_note_taking_temp_home, test_clipboard_copy, test_clipboard_read,
    test_open_youtube_site, test_open_new_sites, test_whatsapp_parse,
    test_mail_brief_honesty_no_cache, test_timer,
    test_destructive_requires_confirmation, test_unknown_query_returns_none,
    test_market_import_only,
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
