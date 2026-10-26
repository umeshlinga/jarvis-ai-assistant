"""
test_butler.py - Jarvis v5 proactive butler, plain-python:
    python3 tests/test_butler.py

All data providers (Reminders / Calendar / battery / job count) are
injected fakes; the clock is injected too. No osascript, no network,
no real waiting.
"""

import datetime
import os
import sys
import tempfile

_TMP_HOME = tempfile.mkdtemp(prefix="jarvis-test-home-")
os.environ["HOME"] = _TMP_HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jarvis_butler  # noqa: E402
import jarvis_config  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    PASSED.append(name)
    print(f"PASS: {name}")


NOW = datetime.datetime(2026, 10, 26, 9, 0)  # a Monday, 9 AM


def _fake_providers(jobs=0):
    jarvis_butler.reminders_due = lambda window_min=30: [("Call Kiran", 1500.0)]
    jarvis_butler.events_starting = lambda window_min=30: [("Team standup", 600.0)]
    jarvis_butler.battery_state = lambda: {"percent": 15.0, "plugged": False}
    jarvis_butler._job_findings_count = lambda: jobs


def main():
    cfg = jarvis_config.load_config(force_reload=True)
    cfg["proactive_enabled"] = True
    cfg["proactive_jobs"] = True
    _fake_providers(jobs=0)

    # ---- collect: reminder + event + battery + morning offer, once ----
    jarvis_butler._reset_for_tests()
    alerts = jarvis_butler.collect_alerts(now=NOW)
    texts = [a["text"] for a in alerts]
    check("reminder alert generated",
          any("Call Kiran" in t and "due in 25 minutes" in t for t in texts),
          repr(texts))
    check("event alert generated with start time",
          any("Team standup" in t and "9:10 AM" in t and "in 10 minutes" in t
              for t in texts), repr(texts))
    check("low-battery alert generated",
          any("15 percent" in t and "not charging" in t for t in texts),
          repr(texts))
    check("morning briefing offer generated as a suggestion",
          any(a["kind"] == "suggestion" and "morning briefing" in a["text"]
              and a.get("action") for a in alerts), repr(texts))
    again = jarvis_butler.collect_alerts(now=NOW)
    check("alerts fire once per item per day, not twice", again == [],
          repr(again))

    # ---- disabled config: silence ----
    cfg["proactive_enabled"] = False
    try:
        check("proactive_enabled=False means no alerts",
              jarvis_butler.collect_alerts(now=NOW) == [])
    finally:
        cfg["proactive_enabled"] = True

    # ---- delivery + suggestion YES path runs the stored action ----
    jarvis_butler._reset_for_tests()
    _fake_providers(jobs=3)
    jarvis_butler._open_job_results = lambda: "JOB RESULTS OPENED"
    alerts = jarvis_butler.collect_alerts(now=NOW)
    job_alert = [a for a in alerts if "job results" in a["text"]]
    check("jobs suggestion appears when there are results", len(job_alert) == 1,
          repr([a["text"] for a in alerts]))
    spoken = []
    jarvis_butler.set_speaker(lambda msg: spoken.append(msg))
    jarvis_butler._alert_queue.put(job_alert[0])
    n = jarvis_butler.deliver_pending()
    check("deliver_pending speaks the queued suggestion",
          n == 1 and spoken and "Want me to open them" in spoken[0], repr(spoken))
    check("suggestion is pending after delivery", jarvis_butler.has_pending())
    out = jarvis_butler.handle_response("yes")
    check("yes runs the stored action",
          out == ("acted", "JOB RESULTS OPENED"), repr(out))
    check("pending cleared after the answer", not jarvis_butler.has_pending())

    # ---- suggestion NO path dismisses silently, action never runs ----
    jarvis_butler._reset_for_tests()
    _fake_providers(jobs=0)
    called = []
    _real_briefing_fn = jarvis_butler._morning_briefing
    jarvis_butler._morning_briefing = lambda: called.append(1) or "BRIEFING"
    alerts = jarvis_butler.collect_alerts(now=NOW)
    offer = [a for a in alerts if "morning briefing" in a["text"]][0]
    jarvis_butler._alert_queue.put(offer)
    jarvis_butler.deliver_pending()
    out = jarvis_butler.handle_response("no")
    check("no dismisses the suggestion", out == ("dismissed", None), repr(out))
    check("dismissed action never ran", called == [])
    jarvis_butler._morning_briefing = _real_briefing_fn

    # ---- a non-answer expires the suggestion and falls through ----
    jarvis_butler._reset_for_tests()
    alerts = jarvis_butler.collect_alerts(now=NOW)
    offer = [a for a in alerts if "morning briefing" in a["text"]][0]
    jarvis_butler._alert_queue.put(offer)
    jarvis_butler.deliver_pending()
    out = jarvis_butler.handle_response("what time is it")
    check("unrelated utterance expires the suggestion (handled normally)",
          out is None and not jarvis_butler.has_pending())
    check("no pending suggestion -> handle_response is None",
          jarvis_butler.handle_response("yes") is None)

    # ---- overdue + due-now phrasing ----
    jarvis_butler._reset_for_tests()
    jarvis_butler.reminders_due = lambda window_min=30: [
        ("Submit report", -1800.0), ("Standup notes", 30.0)]
    jarvis_butler.events_starting = lambda window_min=30: []
    jarvis_butler.battery_state = lambda: None
    texts = [a["text"] for a in jarvis_butler.collect_alerts(now=NOW)]
    check("overdue reminder phrasing",
          any("was due 30 minutes ago" in t for t in texts), repr(texts))
    check("due-now reminder phrasing",
          any("is due now" in t for t in texts), repr(texts))

    # ---- v7: composed morning briefing (news + stocks) ----
    import jarvis_market
    import jarvis_web
    real_market = jarvis_market.market_briefing
    real_news = jarvis_web.news_headlines
    try:
        _fake_providers(jobs=0)
        jarvis_market.market_briefing = lambda: "Nifty is up 0.4%. NVDA is up 2.1%."
        jarvis_web.news_headlines = lambda topic=None, limit=3: [
            "City wins big grant - The city announced a grant.",
            "Markets steady - Stocks were flat.",
            "Local lab expands - A lab is hiring.",
        ]
        text = jarvis_butler.morning_briefing_text(now=NOW)
        check("briefing: greets with the date",
              text.startswith("Good morning, Umesh. It's Monday, October 26"),
              text)
        check("briefing: includes reminders and calendar",
              "Call Kiran" in text and "Team standup" in text, text)
        check("briefing: includes the market snapshot",
              "Markets:" in text and "NVDA" in text, text)
        check("briefing: includes top news headlines",
              "In the news:" in text and "City wins big grant" in text, text)
        check("briefing: stays speakable (~150 words max)",
              len(text.split()) <= 155, f"{len(text.split())} words")

        def _boom(*a, **k):
            raise RuntimeError("source down")

        jarvis_web.news_headlines = _boom
        text = jarvis_butler.morning_briefing_text(now=NOW)
        check("briefing: news failure -> briefing still produced, no news section",
              text and "In the news" not in text and "Markets:" in text, text)
        jarvis_market.market_briefing = _boom
        jarvis_web.news_headlines = real_news
        jarvis_web.news_headlines = lambda topic=None, limit=3: ["One headline - x."]
        jarvis_butler.reminders_due = lambda window_min=30: []
        jarvis_butler.events_starting = lambda window_min=30: []
        text = jarvis_butler.morning_briefing_text(now=NOW)
        check("briefing: market failure -> omitted, other sections survive",
              "Markets:" not in text and "In the news:" in text, text)
        jarvis_web.news_headlines = _boom
        check("briefing: every source down -> empty (caller falls back)",
              jarvis_butler.morning_briefing_text(now=NOW) == "")
        import jarvis_actions
        real_actions_brief = jarvis_actions.morning_briefing
        jarvis_actions.morning_briefing = lambda: "FALLBACK BRIEFING"
        try:
            check("briefing: action falls back to the v3 briefing",
                  jarvis_butler._morning_briefing() == "FALLBACK BRIEFING")
        finally:
            jarvis_actions.morning_briefing = real_actions_brief
    finally:
        jarvis_market.market_briefing = real_market
        jarvis_web.news_headlines = real_news
        _fake_providers(jobs=0)

    print(f"\n{len(PASSED)}/{len(PASSED)} butler tests passed.")


if __name__ == "__main__":
    main()
