#!/usr/bin/env python3
"""
test_web.py - Jarvis v4 browser-brain tests. The HTTP layer and the
summariser are stubbed - NO network happens in these tests.

Run:  python3 tests/test_web.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HOME"] = tempfile.mkdtemp(prefix="jarvis_web_test_")

import jarvis_web  # noqa: E402

PASSED = []


def check(name, cond):
    assert cond, f"FAILED: {name}"
    PASSED.append(name)
    print(f"PASS: {name}")


DDG_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone">First Title</a>
  <a class="result__snippet">First snippet text here.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Ftwo">Second Title</a>
  <a class="result__snippet">Second snippet text here.</a>
</div>
<div class="result">
  <a class="result__a" href="https://direct.example.net/three">Third Title</a>
  <a class="result__snippet">Third snippet text here.</a>
</div>
</body></html>
"""

ARTICLE_HTML = """
<html><head><title>Story</title><script>var evil = 1;</script></head>
<body><h1>Big News</h1>
<p>Scientists announced a new result today. The study covered five years of data.
It was published in a major journal. More details will follow next week.</p>
</body></html>
"""


def main():
    jarvis_web.http_get = lambda url, timeout=10: DDG_HTML

    results = jarvis_web.parse_ddg_results(jarvis_web.http_get("x"))
    check("DDG parse: 3 results", len(results) == 3)
    check("DDG parse: redirect unwrapped", results[0]["url"] == "https://example.com/one")
    check("DDG parse: direct link kept", results[2]["url"] == "https://direct.example.net/three")

    spoken = jarvis_web.search_and_tell("electric cars")
    check("search speaks top results", "First Title" in spoken and "Second Title" in spoken)
    check("search stores results for 'open the first result'",
          jarvis_web._last_results and jarvis_web._last_results[0]["url"] == "https://example.com/one")
    check("search tells him he can open the first result", "open the first result" in spoken)

    opened = []
    import webbrowser
    orig_open = webbrowser.open
    webbrowser.open = lambda url, *a, **k: opened.append(url) or True
    try:
        r = jarvis_web.open_first_result()
    finally:
        webbrowser.open = orig_open
    check("open first result opens the stored URL",
          opened == ["https://example.com/one"] and "Opening the top result" in r)

    def boom(url, timeout=10):
        raise RuntimeError("no route to host")
    jarvis_web.http_get = boom
    r = jarvis_web.search_and_tell("anything")
    check("search failure is honest", "couldn't reach" in r.lower())
    r = jarvis_web.read_website("example dot com")
    check("read failure is honest", "couldn't read" in r.lower())

    # Read-a-website: summary path when the brain is available.
    jarvis_web.http_get = lambda url, timeout=10: ARTICLE_HTML
    seen = {}

    def fake_summarize(text):
        seen["text"] = text
        return "SPOKEN SUMMARY"

    jarvis_web._summarize = fake_summarize
    r = jarvis_web.read_website("example dot com")
    check("read uses the brain summary when available", r == "SPOKEN SUMMARY")
    check("extracted text passed to summariser (script stripped)",
          "Scientists announced" in seen["text"] and "evil" not in seen["text"])

    # Read-a-website: raw fallback when no brain.
    jarvis_web._summarize = lambda text: None
    r = jarvis_web.read_website("example dot com")
    check("read falls back to raw sentences honestly",
          "thinking brain isn't available" in r and "Scientists announced a new result today" in r)

    # ---- v4.1: job search parsing (pure - no stubs needed) ----
    check("job parse: his exact live phrasing",
          jarvis_web.parse_job_query(
              "show me the job openings in indianapolis in bioinformatics")
          == ("bioinformatics", "Indianapolis"))
    check("job parse: garbled domain word mapped back",
          jarvis_web.parse_job_query(
              "show me the job openings having in indianapolis in bighton")
          == ("bioinformatics", "Indianapolis"))
    check("job parse: no role -> his default field",
          jarvis_web.parse_job_query("what are the job openings in indianapolis")
          == ("bioinformatics", "Indianapolis"))
    check("job parse: role before the noun",
          jarvis_web.parse_job_query("bioinformatics jobs in indianapolis")
          == ("bioinformatics", "Indianapolis"))
    check("job parse: unknown place accepted, role kept",
          jarvis_web.parse_job_query("find data science jobs in chicago")
          == ("data science", "Chicago"))
    check("job query detector: positives",
          jarvis_web.is_job_search_query("show me the job openings in indianapolis")
          and jarvis_web.is_job_search_query("find jobs in indianapolis")
          and jarvis_web.is_job_search_query("bioinformatics jobs in indianapolis"))
    check("job query detector: negatives",
          not jarvis_web.is_job_search_query("explain quantum computing please")
          and not jarvis_web.is_job_search_query("what time is it"))

    # ---- v4.1: job search DOES the work (DDG + browser stubbed) ----
    jarvis_web.http_get = lambda url, timeout=10: DDG_HTML
    opened_urls = []
    orig_open_url = jarvis_web.open_url
    jarvis_web.open_url = lambda url: opened_urls.append(url) or True
    try:
        r = jarvis_web.job_search(
            "show me the job openings in indianapolis in bioinformatics")
        check("job search opens LinkedIn Jobs with keywords + location",
              opened_urls and "linkedin.com/jobs/search" in opened_urls[0]
              and "bioinformatics" in opened_urls[0]
              and "Indianapolis" in opened_urls[0])
        check("job search speaks the did-the-work prefix",
              r.startswith("I searched for bioinformatics jobs in Indianapolis "
                           "and opened LinkedIn Jobs with the live results. "
                           "Here's what I found:"))
        check("job search reads the top findings aloud", "First Title" in r)

        def boom2(url, timeout=10):
            raise RuntimeError("no route to host")
        jarvis_web.http_get = boom2
        opened_urls.clear()
        r = jarvis_web.job_search("find jobs in indianapolis")
        check("job search: DDG failure still opens browser + honest speech",
              opened_urls and r.startswith("I searched for bioinformatics jobs "
                                             "in Indianapolis")
              and "couldn't read the web results aloud" in r)
    finally:
        jarvis_web.open_url = orig_open_url
        jarvis_web.http_get = lambda url, timeout=10: DDG_HTML

    # ---- v4.1: general topic cleaning + guards ----
    check("topic: show me", jarvis_web.general_search_topic("show me electric cars")
          == "electric cars")
    check("topic: find out", jarvis_web.general_search_topic("find out about black holes")
          == "black holes")
    check("topic: what are the", jarvis_web.general_search_topic(
        "what are the latest iphone prices") == "latest iphone prices")
    check("topic: bare search", jarvis_web.general_search_topic("search electric cars")
          == "electric cars")
    check("topic: look up", "moon landing" in (
        jarvis_web.general_search_topic("look up the moon landing") or ""))
    check("topic guard: file finds keep their route",
          jarvis_web.general_search_topic("search for the file report.pdf") is None)
    check("topic guard: market questions keep the market module",
          jarvis_web.general_search_topic("show me the stock price") is None)
    check("topic: plain chat is not a search",
          jarvis_web.general_search_topic("explain quantum computing please") is None)

    # ---- v4.1: search_and_tell can open the results page ----
    jarvis_web.open_url = lambda url: opened_urls.append(url) or True
    opened_urls.clear()
    try:
        r = jarvis_web.search_and_tell("electric cars", open_results=True)
        check("search open_results opens the DDG page",
              opened_urls and "duckduckgo.com" in opened_urls[0])
        check("search open_results mentions the opened page",
              "opened the results page in your browser" in r)
        jarvis_web.http_get = boom2
        r = jarvis_web.search_and_tell("electric cars", open_results=True)
        check("search open_results: fetch failure honest, page still opened",
              "opened the search results" in r and "on your screen" in r)
    finally:
        jarvis_web.open_url = orig_open_url
        jarvis_web.http_get = lambda url, timeout=10: DDG_HTML

    print(f"\n{len(PASSED)}/{len(PASSED)} web tests passed.")


if __name__ == "__main__":
    main()
