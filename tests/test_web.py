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

    print(f"\n{len(PASSED)}/{len(PASSED)} web tests passed.")


if __name__ == "__main__":
    main()
