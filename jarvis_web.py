"""
jarvis_web.py - Jarvis v4 browser brain: search and read the web aloud.

- "search for <topic> and tell me" / "read about <topic>": fetch
  DuckDuckGo's HTML results page, take the top 3 titles + snippets, and
  speak a short combined answer. Nothing is opened unless he then says
  "open the first result" (the last result list is remembered here).
- "read this website" / "read <url>": fetch the page, strip it to text
  with the stdlib HTML parser, and summarise it for speaking with the
  brain's deep tier when a cloud brain exists; offline, speak the first
  few sentences of the page itself.

Everything is guarded: any fetch/parse problem returns an honest spoken
sentence, never a traceback. All HTTP goes through http_get() so tests
can stub the network completely.
"""

import re
from html.parser import HTMLParser

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15"}

_last_results = []   # [{title, url, snippet}] from the most recent search


def http_get(url, timeout=10):
    """Single choke point for web fetches (tests stub this)."""
    import requests  # lazy: local mode must work without the package
    resp = requests.get(url, headers=UA, timeout=timeout)
    resp.raise_for_status()
    return resp.text


# --------------------------------------------------------------------------
# DuckDuckGo HTML results parsing (regex over their stable lite markup)
# --------------------------------------------------------------------------

_RESULT_RE = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html_text(fragment):
    text = _TAG_RE.sub(" ", fragment or "")
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#x27;", "'").replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", text).strip()


def _ddg_real_url(href):
    """DuckDuckGo wraps result links in /l/?uddg=<encoded real url>."""
    try:
        import urllib.parse
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return qs["uddg"][0]
        if href.startswith("//"):
            return "https:" + href
        return href
    except Exception:
        return href


def parse_ddg_results(html_text, limit=3):
    results = []
    snippets = _SNIPPET_RE.findall(html_text or "")
    for i, (href, title_html) in enumerate(_RESULT_RE.findall(html_text or "")):
        title = _clean_html_text(title_html)
        if not title:
            continue
        snippet = _clean_html_text(snippets[i]) if i < len(snippets) else ""
        results.append({"title": title, "url": _ddg_real_url(href), "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def search_and_tell(topic):
    """Search DuckDuckGo and speak a short combined answer."""
    global _last_results
    topic = (topic or "").strip()
    if not topic:
        return "What would you like me to search for?"
    import urllib.parse
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(topic)
    try:
        html_text = http_get(url)
        results = parse_ddg_results(html_text, limit=3)
    except Exception:
        results = []
    if not results:
        return (f"I tried to search for {topic}, but I couldn't reach the search "
                f"results just now - the internet may be down, or the search page "
                f"didn't answer. Nothing was opened.")
    _last_results = results
    bits = [f"Here's what I found about {topic}."]
    for r in results:
        line = r["title"]
        if r["snippet"]:
            line += " - " + r["snippet"]
        bits.append(line)
    bits.append("Say 'open the first result' if you want me to open the top page.")
    return " ".join(bits)


def open_first_result():
    if not _last_results:
        return "I don't have any search results yet - ask me to search for something first."
    top = _last_results[0]
    try:
        import webbrowser
        webbrowser.open(top["url"])
    except Exception:
        return f"I couldn't open the browser, but the top result is {top['title']} at {top['url']}."
    return f"Opening the top result: {top['title']}."


# --------------------------------------------------------------------------
# Read a page aloud
# --------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def extract_text(html_text, limit=6000):
    parser = _TextExtractor()
    try:
        parser.feed(html_text or "")
    except Exception:
        pass
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:limit]


def _summarize(text):
    """Deep-tier spoken summary, or None when no cloud brain is available."""
    try:
        import jarvis_brain
        return jarvis_brain.complete(
            text,
            system=("Summarise the following web page text for SPEAKING aloud: "
                    "four to five natural sentences, the key points only, no "
                    "markdown, no lists, written the way a person would say it."),
            deep=True,
        )
    except Exception:
        return None


def _first_sentences(text, count=3):
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip())
    picked = [s.strip() for s in sentences if len(s.strip()) > 25]
    return " ".join(picked[:count])


def _normalise_target(target):
    t = (target or "").strip()
    low = t.lower()
    if low.startswith(("http://", "https://")):
        return t
    spoken = low.replace(" dot ", ".").replace(" ", "")
    if "." in spoken:
        return "https://" + spoken
    return "https://" + spoken + ".com"


def read_website(target):
    """Fetch a page and read a spoken summary of it."""
    url = _normalise_target(target)
    try:
        html_text = http_get(url)
        text = extract_text(html_text)
    except Exception:
        return (f"I couldn't read {url} just now - the page didn't load, or the "
                f"site refused the request. I didn't open anything.")
    if not text or len(text) < 80:
        return (f"I loaded {url}, but there was almost no readable text on it - "
                f"it may be a page that needs JavaScript or a login.")
    summary = _summarize(text)
    if summary:
        return summary
    raw = _first_sentences(text, count=3)
    if raw:
        return ("My thinking brain isn't available right now, so here are the "
                "page's own opening lines: " + raw)
    return f"I read {url}, but I couldn't pull a readable summary from it."
