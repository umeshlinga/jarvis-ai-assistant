"""
jarvis_read.py - Jarvis v5 "read anything on the laptop".

Owner's ask: "You should read everything in my laptop - whatever I ask,
open it and tell me." So "read my resume" / "read report.pdf" / "what's
in notes.txt" resolves the file (Spotlight search via jarvis_actions),
extracts its text honestly by format, and speaks a short summary -
"say 'read more'" then speaks the actual text, one page-sized chunk
per ask, remembering where it stopped.

Formats:
- .txt / .md / .py / .csv / .json / .log: read directly (UTF-8).
- .docx: stdlib zipfile + XML text extraction (word/document.xml).
- .pdf: the `pdftotext` tool (poppler) when installed; without it
  Jarvis says so honestly, opens the file, and never fakes content.
- anything else: the file is opened on screen; Jarvis says plainly he
  can't read that format aloud.

No invented content, ever: an extraction failure is an honest sentence.
"""

import os
import re
import shutil
import subprocess
import zipfile
from xml.sax.saxutils import unescape as _xml_unescape

import jarvis_actions

TEXT_EXTS = {".txt", ".md", ".py", ".csv", ".json", ".log"}
_MAX_TEXT_CHARS = 400_000
_PAGE_WORDS = 110  # one spoken "page" per 'read more'

# Reading state for 'read more': {path, base, words, index}
_current = None


def _reset_for_tests():
    global _current
    _current = None


def _human_size(n):
    try:
        n = float(n)
    except Exception:
        return None
    for unit in ("bytes", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024.0
    return None


def _docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = xml.replace("</w:p>", "\n")
    text = re.sub(r"<[^>]+>", "", xml)
    return _xml_unescape(text)


def _pdf_text(path):
    """(text, error, pages). error is None, 'needs_poppler', or 'failed'."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "needs_poppler", None
    try:
        p = subprocess.run([exe, "-layout", path, "-"],
                           capture_output=True, text=True, timeout=30)
        if p.returncode != 0 or not (p.stdout or "").strip():
            return None, "failed", None
        pages = (p.stdout.count("\f") or 0) + 1
        return p.stdout, None, pages
    except Exception:
        return None, "failed", None


def _extract_text(path):
    """(text, error, pages) for a file path. error: None / 'needs_poppler'
    / 'unsupported' / 'failed'."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in TEXT_EXTS:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(_MAX_TEXT_CHARS), None, None
        if ext == ".docx":
            return _docx_text(path)[:_MAX_TEXT_CHARS], None, None
        if ext == ".pdf":
            return _pdf_text(path)
        return None, "unsupported", None
    except Exception:
        return None, "failed", None


def _first_sentences(text, count=3):
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip())
    picked = [s.strip() for s in sentences if len(s.strip()) > 25]
    return " ".join(picked[:count])


def _summarize(text):
    """Deep-tier spoken summary, or None when no cloud brain exists."""
    try:
        import jarvis_brain
        return jarvis_brain.complete(
            text[:8000],
            system=("Summarise the following document for SPEAKING aloud: "
                    "three to four natural sentences, the key points only, "
                    "no markdown, no lists, no preamble - say what it "
                    "actually says, the way a person would tell it."),
            deep=True,
        )
    except Exception:
        return None


def _set_current(path, text):
    global _current
    _current = {
        "path": path,
        "base": os.path.basename(path),
        "words": re.sub(r"\s+", " ", text or "").split(),
        "index": 0,
    }


def read_file(name, open_too=False):
    """
    Find a file by name and read it aloud (summary first). open_too also
    opens the file on screen ("open and read ..."). Returns the spoken
    reply string - honest on every failure path.
    """
    name = (name or "").strip().strip(" .,!?")
    if not name:
        return "Which file should I read? Say 'read my resume', or 'read report.pdf'."
    hits = jarvis_actions.search_files(name)
    if not hits:
        return (f"I couldn't find a file called {name} on this Mac - "
                f"so there's nothing to read.")
    path = hits[0]
    base = os.path.basename(path)
    try:
        size = _human_size(os.path.getsize(path))
    except Exception:
        size = None

    text, err, pages = _extract_text(path)
    if err == "needs_poppler":
        jarvis_actions._open([path])
        return (f"I found {base} and opened it for you - but reading a PDF "
                f"aloud needs the free poppler tool (the pdftotext command), "
                f"and it isn't installed yet. Leo can add it with: brew "
                f"install poppler. Until then I can open PDFs, just not "
                f"read them out.")
    if err == "unsupported":
        jarvis_actions._open([path])
        return (f"I opened {base} for you - but I can't read that kind of "
                f"file aloud.")
    if err or not text or not text.strip():
        return (f"I found {base}, but I couldn't pull any readable text "
                f"out of it.")

    _set_current(path, text)
    if open_too:
        jarvis_actions._open([path])

    header_bits = [f"Your {base}"]
    if size:
        header_bits.append(size)
    if pages:
        header_bits.append(f"{pages} page{'s' if pages != 1 else ''} of text")
    header = ", ".join(header_bits)

    summary = _summarize(text)
    if summary:
        return (f"{header} - here's what it says: {summary} "
                f"Say 'read more' to hear the actual text.")
    raw = _first_sentences(text, count=3)
    if raw:
        return (f"{header} - here's what it says. My thinking brain is "
                f"offline, so here are its first lines: {raw} "
                f"Say 'read more' to keep hearing the actual text.")
    return (f"{header} - I have its text ready. Say 'read more' to hear it.")


def read_more():
    """
    Speak the next page (~110 words) of the file being read, ending at a
    sentence boundary where possible. Remembers the offset across turns.
    """
    cur = _current
    if not cur or not cur["words"]:
        return "There's nothing waiting to be read - ask me to read a file first."
    words = cur["words"]
    idx = cur["index"]
    if idx >= len(words):
        return f"That's the end of {cur['base']} - ask me to read another file whenever you like."
    end = min(len(words), idx + _PAGE_WORDS)
    # Extend to the end of the current sentence (within 40 extra words).
    j = end
    while j < len(words) and j - end < 40 and not words[j - 1].endswith((".", "!", "?")):
        j += 1
    if j > end:
        end = j
    chunk = " ".join(words[idx:end])
    cur["index"] = end
    if end >= len(words):
        return chunk + f" That's the end of {cur['base']}."
    return chunk
