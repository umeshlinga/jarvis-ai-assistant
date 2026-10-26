#!/usr/bin/env python3
"""
test_notify.py - Jarvis v6 "Eyes & Hands" watcher tests.

    python3 tests/test_notify.py

Everything is stubbed or fixtured: a REAL temp sqlite database with
the modern Notification Center schema + plistlib-encoded payloads, a
scripted Accessibility snapshot, a stubbed brain judge, and stubbed
delivery runners. No osascript runs, no network, nothing is sent.
"""

import os
import plistlib
import sqlite3
import sys
import tempfile

_TMP_HOME = tempfile.mkdtemp(prefix="jarvis-test-home-")
os.environ["HOME"] = _TMP_HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json as _json  # noqa: E402

os.makedirs(os.path.join(_TMP_HOME, ".openjarvis"), exist_ok=True)
with open(os.path.join(_TMP_HOME, ".openjarvis", "contacts.json"), "w") as _f:
    _json.dump({"Mom": {"phone": "+1 555 010 2030",
                        "email": "mom@example.com"}}, _f)

import jarvis_actions  # noqa: E402
import jarvis_brain  # noqa: E402
import jarvis_butler  # noqa: E402
import jarvis_notify  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    PASSED.append(name)
    print(f"PASS: {name}")


_REAL_BRAIN_COMPLETE = jarvis_brain.complete
_REAL_AX_RAW = jarvis_notify._ax_raw
_REAL_NCDB_PATH = jarvis_notify.ncdb_path
_REAL_MESSAGES_SEND = jarvis_notify._messages_send_reply
_REAL_TYPE_REPLY = jarvis_notify._type_reply
_REAL_EXECUTE_SEND = jarvis_notify._execute_send
_REAL_CLICK_POPUP = jarvis_notify._click_popup_button
_REAL_OSA_RUN = jarvis_actions._osa_run
_REAL_OSA = jarvis_actions._osa

SPOKEN = []


def _drain():
    jarvis_butler.deliver_pending()


def _reset_all():
    jarvis_notify._reset_for_tests()
    jarvis_butler._reset_for_tests()
    SPOKEN.clear()
    jarvis_butler.set_speaker(lambda msg: SPOKEN.append(msg))
    jarvis_brain.complete = lambda *a, **k: None  # brain away -> defaults
    jarvis_notify._ax_raw = lambda: ""            # no windows anywhere
    jarvis_notify._messages_send_reply = _REAL_MESSAGES_SEND
    jarvis_notify._type_reply = _REAL_TYPE_REPLY
    jarvis_notify._execute_send = _REAL_EXECUTE_SEND
    jarvis_notify._click_popup_button = _REAL_CLICK_POPUP
    jarvis_actions._osa_run = _REAL_OSA_RUN
    jarvis_actions._osa = _REAL_OSA


# ---------------------------------------------------------------------------
# Fixture: a modern Notification Center database
# ---------------------------------------------------------------------------

def _make_ncdb(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE app (app_id INTEGER PRIMARY KEY, identifier TEXT)")
    conn.execute("CREATE TABLE record (rec_id INTEGER PRIMARY KEY, "
                 "app_id INTEGER, uuid BLOB, data BLOB, delivered_date REAL)")
    conn.execute("INSERT INTO app VALUES (1, 'com.apple.MobileSMS')")
    conn.execute("INSERT INTO app VALUES (2, 'com.apple.news')")
    blob = plistlib.dumps({"req": {"titl": "Mom",
                                   "body": "Old already-seen message"}},
                          fmt=plistlib.FMT_BINARY)
    conn.execute("INSERT INTO record VALUES (1, 1, x'01', ?, 700000000.0)",
                 (blob,))
    conn.commit()
    conn.close()


def _ncdb_add(path, rec_id, app_id, title, body):
    conn = sqlite3.connect(path)
    blob = plistlib.dumps({"req": {"titl": title, "body": body}},
                          fmt=plistlib.FMT_BINARY)
    conn.execute("INSERT INTO record VALUES (?, ?, x'02', ?, 700000100.0)",
                 (rec_id, app_id, blob))
    conn.commit()
    conn.close()


def test_ncdb_polling():
    _reset_all()
    db = os.path.join(_TMP_HOME, "nc_fixture.db")
    _make_ncdb(db)
    jarvis_notify.ncdb_path = lambda: db

    n = jarvis_notify.poll_once()
    check("first poll is a silent baseline (no flood of old items)",
          n == 0 and SPOKEN == [], f"n={n} spoken={SPOKEN}")
    check("NCDB status is ok after a good read",
          "notifications + pop-ups" in jarvis_notify.notify_status())

    _ncdb_add(db, 2, 1, "Mom", "Interview moved to 3pm - bring your resume")
    _ncdb_add(db, 3, 2, "Apple News", "Top stories and a big sale promotion")
    n = jarvis_notify.poll_once()
    _drain()
    check("second poll surfaces the new records", n == 2, f"n={n}")
    check("important message announced exactly once",
          len(SPOKEN) == 1 and "message from Mom" in SPOKEN[0]
          and "Interview moved to 3pm" in SPOKEN[0], repr(SPOKEN))
    check("news promo stayed silent (HUD note only)",
          all("Top stories" not in s for s in SPOKEN))
    check("pending reply set for the message",
          jarvis_notify._pending_reply is not None
          and jarvis_notify._pending_reply["sender"] == "Mom")
    n = jarvis_notify.poll_once()
    _drain()
    check("third poll: nothing new, nothing repeated", n == 0 and len(SPOKEN) == 1)
    jarvis_notify.ncdb_path = _REAL_NCDB_PATH


def test_ncdb_missing_and_permissions():
    _reset_all()
    jarvis_notify.ncdb_path = lambda: os.path.join(_TMP_HOME, "nope.db")
    check("missing DB -> poll is a quiet no-op", jarvis_notify.poll_once() == 0)
    check("missing DB -> status says so honestly",
          "no Notification Center database found" in jarvis_notify.notify_status(),
          jarvis_notify.notify_status())
    # A directory where the DB should be: sqlite cannot open it, which is
    # exactly what macOS Full-Disk-Access denial looks like to sqlite.
    jarvis_notify._reset_for_tests()
    jarvis_notify.ncdb_path = lambda: _TMP_HOME
    check("unopenable DB -> status asks for Full Disk Access",
          "Full Disk Access" in jarvis_notify.notify_status(),
          jarvis_notify.notify_status())
    jarvis_notify.ncdb_path = _REAL_NCDB_PATH


# ---------------------------------------------------------------------------
# Importance judge: rules table + brain parse
# ---------------------------------------------------------------------------

def _ev(**kw):
    base = {"kind": "notification", "app": "SomeApp", "sender": "",
            "title": "", "subtitle": "", "body": "", "texts": []}
    base.update(kw)
    return base


def test_judge_rules():
    _reset_all()
    cases = [
        ("OTP in a message is important",
         _ev(kind="message", app="Messages", sender="+1 555 999 0000",
             title="+1 555 999 0000", body="Your verification code is 482916"),
         "important"),
        ("contact sender is important",
         _ev(kind="message", app="Messages", sender="Mom", title="Mom",
             body="ok see you soon"), "important"),
        ("job keyword from a stranger is important",
         _ev(kind="message", app="WhatsApp", sender="Recruiter Raj",
             title="Recruiter Raj",
             body="Your interview is confirmed for Tuesday"), "important"),
        ("plain stranger message, brain away -> important (a person waits)",
         _ev(kind="message", app="Messages", sender="Aunt May",
             title="Aunt May", body="call when free"), "important"),
        ("money keyword mail is important",
         _ev(kind="mail", app="Mail", sender="Chase Bank", title="Chase Bank",
             body="Your payment failed and the card was charged twice"),
         "important"),
        ("stranger newsletter mail, brain away -> silent",
         _ev(kind="mail", app="Mail", sender="Newsletters",
             title="Newsletters", body="Top stories and deals this week"),
         "silent"),
        ("News app is silent",
         _ev(app="News", title="News", body="Markets rally on jobs data"),
         "silent"),
        ("Software Update app is silent",
         _ev(app="Software Update", title="Software Update",
             body="macOS update available"), "silent"),
        ("Jarvis's own notification is silent",
         _ev(app="Jarvis", title="Jarvis", body="Timer done"), "silent"),
        ("empty notification is silent",
         _ev(app="Finder", title="", body=""), "silent"),
        ("Calendar app is important",
         _ev(kind="alert", app="Calendar", title="Team standup",
             body="Starts in 10 minutes"), "important"),
        ("pop-up asking for a password is important",
         _ev(kind="popup", app="SecurityAgent", title="",
             texts=["Enter your password to continue"], body="",
             buttons=["Cancel", "OK"]), "important"),
        ("plain new window, brain away -> silent",
         _ev(kind="popup", app="Finder", title="Documents", texts=[],
             body="", buttons=[]), "silent"),
    ]
    for name, event, want in cases:
        got = jarvis_notify.judge(event)
        check("judge: " + name, got == want, f"got {got}")
    check("judge table has >= 12 cases", len(cases) >= 12)

    check("OTP extraction: 'code is N'",
          jarvis_notify.extract_otp("Your verification code is 482916") == "482916")
    check("OTP extraction: 'N is your code'",
          jarvis_notify.extract_otp("482916 is your one-time code") == "482916")
    check("OTP extraction: bare number is NOT a code",
          jarvis_notify.extract_otp("Meeting at 482916 street") is None)
    check("OTP extraction: Google G- style",
          jarvis_notify.extract_otp("G-123456 is your Google code") == "123456")

    # Brain parse: IMPORTANT / IGNORE / garbage->default.
    generic = _ev(app="BankApp", title="Hello", body="Something happened")
    jarvis_brain.complete = lambda *a, **k: "IMPORTANT"
    check("judge: brain IMPORTANT wins", jarvis_notify.judge(generic) == "important")
    jarvis_brain.complete = lambda *a, **k: "IGNORE"
    check("judge: brain IGNORE wins", jarvis_notify.judge(generic) == "silent")
    jarvis_brain.complete = lambda *a, **k: "banana"
    check("judge: brain garbage -> default (silent)",
          jarvis_notify.judge(generic) == "silent")
    msg = _ev(kind="message", app="Messages", sender="Aunt May",
              title="Aunt May", body="call when free")
    check("judge: brain garbage on a person's message -> default important",
          jarvis_notify.judge(msg) == "important")
    jarvis_brain.complete = _REAL_BRAIN_COMPLETE


# ---------------------------------------------------------------------------
# Voice reply flow, end to end with stubbed runners
# ---------------------------------------------------------------------------

def test_reply_flow():
    _reset_all()
    msg_calls = []
    jarvis_notify._messages_send_reply = lambda s, t: msg_calls.append((s, t)) or True
    jarvis_notify.handle_event(_ev(
        kind="message", app="Messages", sender="Mom", title="Mom",
        body="How did the interview go?"))
    _drain()
    check("flow: announcement offered a reply",
          SPOKEN and "Say 'reply'" in SPOKEN[0], repr(SPOKEN))
    out = jarvis_notify.handle_response("reply")
    check("flow: 'reply' asks what to say",
          out == ("acted", "What should I say to Mom?"), repr(out))
    out = jarvis_notify.handle_response(
        "the interview went well thanks",
        raw="The interview went well, thanks")
    check("flow: dictation sent via Messages script (case preserved)",
          msg_calls == [("Mom", "The interview went well, thanks")],
          repr(msg_calls))
    check("flow: success is reported as sent",
          out == ("acted", "Sent your reply to Mom."), repr(out))

    # Ladder rung (b): Messages script fails -> type it, then "send it".
    _reset_all()
    jarvis_notify._messages_send_reply = lambda s, t: False
    typed = []
    jarvis_notify._type_reply = lambda p, t: typed.append((p, t)) or True
    sent = []
    jarvis_notify._execute_send = lambda app: sent.append(app) or True
    jarvis_notify.handle_event(_ev(
        kind="message", app="Messages", sender="Mom", title="Mom",
        body="Are you coming for dinner?"))
    _drain()
    jarvis_notify.handle_response("reply")
    out = jarvis_notify.handle_response("yes at seven", raw="Yes, at seven")
    check("flow: fallback types instead of claiming a send",
          typed and typed[0][1] == "Yes, at seven", repr(typed))
    check("flow: typed reply tells him about 'send it'",
          out == ("acted", "Typed. Say 'send it' to send, or check the screen."),
          repr(out))
    out = jarvis_notify.handle_response("send it")
    check("flow: 'send it' presses the send key, silently",
          out == ("silent", None) and sent == ["Messages"], repr(sent))

    # Type rung fails too -> one honest line, nothing claimed.
    _reset_all()
    jarvis_notify._messages_send_reply = lambda s, t: False
    jarvis_notify._type_reply = lambda p, t: False
    jarvis_notify.handle_event(_ev(
        kind="message", app="Messages", sender="Mom", title="Mom",
        body="ping"))
    jarvis_notify.handle_response("reply")
    out = jarvis_notify.handle_response("hello", raw="Hello")
    check("flow: total failure is one honest line",
          out is not None and out[0] == "acted"
          and "couldn't type" in out[1], repr(out))

    # Ignore dismisses silently; a fresh command expires dictation.
    _reset_all()
    jarvis_notify.handle_event(_ev(
        kind="message", app="Messages", sender="Mom", title="Mom", body="ping"))
    out = jarvis_notify.handle_response("ignore")
    check("flow: ignore dismisses silently",
          out == ("silent", None) and not jarvis_notify.has_pending())
    jarvis_notify.handle_event(_ev(
        kind="message", app="Messages", sender="Mom", title="Mom",
        body="ping again"))
    jarvis_notify.handle_response("reply")
    out = jarvis_notify.handle_response("open google")
    check("flow: a new command expires dictation and routes normally",
          out is None and not jarvis_notify._awaiting_dictation)


def test_real_runners_scripts():
    _reset_all()
    scripts = []
    jarvis_actions._osa_run = lambda s: scripts.append(s) or (0, "sent")
    ok = jarvis_notify._messages_send_reply("Mom", "Hi there")
    check("runner: Messages script finds the chat by sender and sends",
          ok and any("Mom" in s and "Hi there" in s and "chats" in s
                     for s in scripts), repr(scripts))
    jarvis_actions._osa_run = lambda s: (0, "notfound")
    check("runner: chat not found -> False (falls to typing)",
          jarvis_notify._messages_send_reply("Mom", "Hi") is False)

    osa_calls = []
    jarvis_actions._osa = lambda s: osa_calls.append(s) or True
    check("runner: Mail send is Cmd+Shift+D",
          jarvis_notify._execute_send("Mail")
          and any('keystroke "d"' in s and "command down" in s
                  and "shift down" in s for s in osa_calls), repr(osa_calls))
    osa_calls.clear()
    check("runner: Messages send is Return (key code 36)",
          jarvis_notify._execute_send("Messages")
          and any("key code 36" in s for s in osa_calls), repr(osa_calls))


# ---------------------------------------------------------------------------
# Pop-up answers + "what did I miss"
# ---------------------------------------------------------------------------

_FS = "\x1f"
_RS = "\x1e"


def test_popup_flow():
    _reset_all()
    base = ("Finder" + _FS + "Documents" + _FS + "" + _FS)
    jarvis_notify._ax_raw = lambda: base
    jarvis_notify.poll_once()  # prime baseline
    dialog = ("SecurityAgent" + _FS + "" + _FS +
              "Software Update wants to make changes. Enter your password."
              + _RS + _FS + "Cancel" + _RS + "OK" + _RS)
    jarvis_notify._ax_raw = lambda: base + "\n" + dialog
    jarvis_notify.poll_once()
    _drain()
    check("popup: new dialog window became an event and was announced",
          SPOKEN and "pop-up from SecurityAgent" in SPOKEN[0], repr(SPOKEN))
    check("popup: pending popup holds the buttons",
          jarvis_notify._pending_popup is not None
          and jarvis_notify._pending_popup["buttons"] == ["Cancel", "OK"])
    clicks = []
    jarvis_notify._click_popup_button = lambda a, b: clicks.append((a, b)) or True
    out = jarvis_notify.handle_response("ok")
    check("popup: saying the button name clicks it, silently",
          out == ("silent", None) and clicks == [("SecurityAgent", "OK")],
          repr(clicks))
    # Dismiss path clicks a dismiss-like button when one exists.
    jarvis_notify.handle_event(_ev(
        kind="popup", app="Installer", title="",
        texts=["This package will install software. "
               "Enter your password to continue."], body="",
        buttons=["Cancel", "Install"]))
    out = jarvis_notify.handle_response("ignore")
    check("popup: 'ignore' clicks the dismiss button",
          out == ("silent", None) and clicks[-1] == ("Installer", "Cancel"),
          repr(clicks))


def test_what_did_i_miss():
    _reset_all()
    check("missed: empty buffer -> clean line",
          jarvis_notify.what_did_i_miss() == "Nothing important.")
    jarvis_notify.handle_event(_ev(
        kind="message", app="Messages", sender="Mom", title="Mom",
        body="First thing"))
    jarvis_notify.handle_event(_ev(
        kind="mail", app="Mail", sender="Chase Bank", title="Chase Bank",
        subtitle="Payment failed", body="Your payment failed"))
    recap = jarvis_notify.what_did_i_miss()
    check("missed: recap keeps arrival order",
          recap.index("Mom") < recap.index("Chase Bank")
          and "2 important things" in recap, recap)
    dup = jarvis_notify.handle_event(_ev(
        kind="message", app="Messages", sender="Mom", title="Mom",
        body="First thing"))
    check("missed: same event twice in a day is a duplicate",
          dup == "duplicate")
    jarvis_notify._ax_raw = _REAL_AX_RAW


def main():
    test_ncdb_polling()
    test_ncdb_missing_and_permissions()
    test_judge_rules()
    test_reply_flow()
    test_real_runners_scripts()
    test_popup_flow()
    test_what_did_i_miss()
    print(f"\n{len(PASSED)}/{len(PASSED)} notify tests passed.")


if __name__ == "__main__":
    main()
