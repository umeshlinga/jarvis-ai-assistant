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

v4.1 "DO THE WORK" routes (the owner's verdict on v4 was: "it's just
telling, it's not doing the work - if I tell something it should search
it and work"):
- Job searches ("show me the job openings in Indianapolis in
  bioinformatics", "find jobs in Indianapolis", ...) ACT: Jarvis opens
  LinkedIn Jobs with the live listings in the browser AND reads the top
  web findings aloud. No more "check LinkedIn and Indeed" suggestions.
- Everyday external-info phrasings ("show me ...", "find out ...",
  "what are the ...", "any news about ...", a bare "search ...") route
  here too via general_search_topic(); when he said "show", the results
  page is opened in the browser as visible proof of work.

Everything is guarded: any fetch/parse problem returns an honest spoken
sentence, never a traceback. All HTTP goes through http_get() and all
browser opens through open_url(), so tests can stub both completely.
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


def open_url(url):
    """Single choke point for opening the browser (tests stub this)."""
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception:
        return False


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


def _ddg_search_url(topic):
    import urllib.parse
    return "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(topic)


def _fetch_results(topic, limit=3):
    """
    Fetch + parse DuckDuckGo results for a topic. Returns a list of
    {title, url, snippet} (possibly empty on any failure) and remembers
    non-empty results for "open the first result".
    """
    global _last_results
    try:
        html_text = http_get(_ddg_search_url(topic))
        results = parse_ddg_results(html_text, limit=limit)
    except Exception:
        results = []
    if results:
        _last_results = results
    return results


def _results_spoken(results):
    bits = []
    for r in results:
        line = r["title"]
        if r["snippet"]:
            line += " - " + r["snippet"]
        bits.append(line)
    return " ".join(bits)


def search_and_tell(topic, open_results=False):
    """
    Search DuckDuckGo and speak a short combined answer. With
    open_results=True (he said "show me"), the results page is ALSO
    opened in his browser - visible proof that Jarvis did the work.
    """
    topic = (topic or "").strip()
    if not topic:
        return "What would you like me to search for?"
    opened = open_url(_ddg_search_url(topic)) if open_results else False
    results = _fetch_results(topic)
    if not results:
        if opened:
            return (f"I opened the search results for {topic} in your browser, "
                    f"but I couldn't read them aloud just now - the search page "
                    f"didn't answer. The results are on your screen.")
        return (f"I tried to search for {topic}, but I couldn't reach the search "
                f"results just now - the internet may be down, or the search page "
                f"didn't answer. Nothing was opened.")
    bits = [f"Here's what I found about {topic}.", _results_spoken(results)]
    if opened:
        bits.append("I've opened the results page in your browser too, so you can see them.")
    bits.append("Say 'open the first result' if you want me to open the top page.")
    return " ".join(bits)


def open_first_result():
    if not _last_results:
        return "I don't have any search results yet - ask me to search for something first."
    top = _last_results[0]
    if not open_url(top["url"]):
        return f"I couldn't open the browser, but the top result is {top['title']} at {top['url']}."
    return f"Opening the top result: {top['title']}."


# --------------------------------------------------------------------------
# v4.1: job search - DO the work (open live results + read findings)
# --------------------------------------------------------------------------

_JOB_NOUN_RE = re.compile(r"\b(jobs|job|openings|opening|vacancies|vacancy|positions|position)\b")
_JOB_REQUEST_MARKERS = (
    "show", "find", "search", "look", "what", "which", "are there",
    "list", "get", "tell me", "can you", "could you", "give me",
    "i want", "i need", "any",
)
# Places Jarvis recognises by name; anything else in an "in <Place>" tail
# is accepted as the location too.
_KNOWN_LOCATIONS = {"indianapolis": "Indianapolis"}
# His home base when a job query names no place.
_DEFAULT_JOB_LOCATION = "Indianapolis"
# His field when a job query names no role (config personal domain).
_DEFAULT_JOB_ROLE = "bioinformatics"
# Words Whisper is known to produce for his domain terms (heard live:
# "bioinformatics" -> "Bighton"). Mapped back before searching.
_GARBLED_ROLE_WORDS = {"bighton": "bioinformatics"}
# Role phrases recognised in an "in <...>" tail (so "jobs in
# bioinformatics" reads the tail as the role, not as a place).
_ROLE_HINTS = {
    "bioinformatics", "data science", "data scientist", "data analyst",
    "software", "software engineer", "software engineering", "engineering",
    "developer", "scientist", "analyst", "nursing", "nurse", "biotech",
    "bioinformatics jobs",
}
_JOB_STOPWORDS = {
    "show", "me", "find", "search", "look", "looking", "for", "the", "a",
    "an", "what", "are", "is", "there", "any", "current", "currently",
    "latest", "new", "available", "list", "get", "tell", "can", "you",
    "could", "please", "having", "have", "all", "some", "give", "in",
    "near", "around", "of", "openings", "opening",
}


def is_job_search_query(q):
    """
    True for job-hunting requests: a job noun (jobs / openings /
    vacancies / positions / hiring) plus request phrasing - or role
    words in front of the noun ("bioinformatics jobs in Indianapolis"
    starts with the topic, which is itself the request).
    """
    low = " " + re.sub(r"\s+", " ", (q or "").lower()).strip(" .,!?") + " "
    if not (_JOB_NOUN_RE.search(low) or " hiring " in low):
        return False
    if any(m in low for m in _JOB_REQUEST_MARKERS):
        return True
    m = _JOB_NOUN_RE.search(low)
    if m:
        before = [w for w in re.findall(r"[a-z]+", low[:m.start()])
                  if w not in _JOB_STOPWORDS]
        return bool(before)
    return " hiring " in low


def _clean_role_words(text):
    words = []
    for w in re.findall(r"[a-z][a-z0-9.+#-]*", (text or "").lower()):
        w = _GARBLED_ROLE_WORDS.get(w, w)
        if w not in _JOB_STOPWORDS:
            words.append(w)
    return " ".join(words)


def parse_job_query(query):
    """
    Split a spoken job query into (role keywords, location).
    Handles the shapes he actually uses:
      "show me the job openings in Indianapolis in bioinformatics"
      "what are the job openings in Indianapolis"   (role -> his default)
      "bioinformatics jobs in Indianapolis"         (role before noun)
      "find data science jobs in Chicago"           (unknown place ok)
    """
    text = re.sub(r"[?.!,]", " ", (query or "").lower())
    text = re.sub(r"\s+", " ", text).strip()
    m = _JOB_NOUN_RE.search(text)
    if m:
        before, after = text[:m.start()], text[m.end():]
    else:  # "... hiring in Indianapolis" style
        before, after = "", text.replace("hiring", " ")
    role_before = _clean_role_words(before)

    location = None
    role_chunks = []
    segments = [s.strip() for s in after.split(" in ") if s.strip()]
    for i, seg in enumerate(segments):
        seg_role = seg.startswith("for ")
        core = seg[4:].strip() if seg_role else seg
        cleaned = _clean_role_words(core)
        if not seg_role and not cleaned and core not in _KNOWN_LOCATIONS:
            continue  # pure filler ("having", "the") - neither place nor role
        if seg_role:
            if cleaned:
                role_chunks.append(cleaned)
        elif location is None and core in _KNOWN_LOCATIONS:
            location = _KNOWN_LOCATIONS[core]
        elif location is None and cleaned in _ROLE_HINTS:
            role_chunks.append(cleaned)  # "in bioinformatics" - a role tail
        elif location is None and i == 0:
            location = core.title()  # "in Chicago in ..." - place comes first
        elif cleaned:
            role_chunks.append(cleaned)
    role = " ".join(x for x in [role_before] + role_chunks if x).strip()
    return (role or _DEFAULT_JOB_ROLE), (location or _DEFAULT_JOB_LOCATION)


def job_search(query):
    """
    ACT on a job request: open LinkedIn Jobs with the live results in
    his browser, and read the top web findings aloud. If the web read
    fails, the browser page is still open and Jarvis says so honestly.
    """
    import urllib.parse
    role, location = parse_job_query(query)
    linkedin_url = ("https://www.linkedin.com/jobs/search/?keywords="
                    + urllib.parse.quote(role)
                    + "&location=" + urllib.parse.quote(location))
    opened = open_url(linkedin_url)
    prefix = (f"I searched for {role} jobs in {location} and opened "
              f"LinkedIn Jobs with the live results.")
    if not opened:
        prefix = (f"I searched for {role} jobs in {location}. I couldn't "
                  f"open LinkedIn Jobs in the browser just now.")
    results = _fetch_results(f"{role} jobs {location}")
    if results:
        return prefix + " Here's what I found: " + _results_spoken(results)
    if opened:
        return (prefix + " I couldn't read the web results aloud just now - "
                "the search page didn't answer - but the LinkedIn Jobs page "
                "in your browser has the live listings.")
    return (prefix + " I couldn't read the web results aloud just now "
            "either - the search page didn't answer. Try again in a moment.")


# --------------------------------------------------------------------------
# v4.1: everyday external-info phrasings -> web search
# --------------------------------------------------------------------------

_MARKET_WORDS = ("stock", "market", "bitcoin", "crypto", "nifty", "sensex")
_LEADIN_ANYWHERE_RES = (
    r"\bshow me (?:the )?",
    r"\bfind me (?:the )?",
    r"\bfind out (?:about )?",
    r"\blook up ",
    r"\bsearch (?:for )?",
)
_LEADIN_START_RES = (
    r"^(?:can|could) you (?:please )?",
    r"^please ",
    r"^what are the ",
    r"^what is the latest ",
    r"^what's the latest ",
    r"^tell me about ",
    r"^any news about ",
    r"^news about ",
    r"^google ",
)


def clean_search_topic(query):
    """Strip request lead-ins, leaving just the topic to search for."""
    t = re.sub(r"\s+", " ", (query or "").lower()).strip(" .!?")
    for pat in _LEADIN_ANYWHERE_RES:
        t = re.sub(pat, "", t, count=1)
    changed = True
    while changed:
        changed = False
        for pat in _LEADIN_START_RES:
            new = re.sub(pat, "", t, count=1)
            if new != t:
                t, changed = new, True
    return re.sub(r"\s+", " ", t).strip(" .!?")


def general_search_topic(q):
    """
    If q is an everyday external-info request ("show me ...", "find
    out ...", "what are the ...", "any news about ...", a bare
    "search ...", "look up ..."), return the cleaned topic; else None.
    Guards keep local routes safe: file finds keep their route (a
    "file" mention or a file extension), and market questions keep the
    market module's real data.
    """
    low = re.sub(r"\s+", " ", (q or "").lower()).strip(" .,!?")
    if not low:
        return None
    if "file" in low or re.search(r"\.\w{2,4}\b", low):
        return None
    if any(w in low for w in _MARKET_WORDS):
        return None
    triggered = (
        "show me" in low
        or "find me" in low
        or "find out" in low
        or low.startswith("what are the ")
        or "what is the latest" in low
        or "what's the latest" in low
        or "any news about" in low
        or low.startswith("search ")
        or low.startswith("look up ")
    )
    if not triggered:
        return None
    topic = clean_search_topic(low)
    if len(topic) < 2:
        return None
    return topic


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
