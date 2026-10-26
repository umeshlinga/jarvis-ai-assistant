"""
test_read.py - Jarvis v5 read-anything (jarvis_read), plain-python:
    python3 tests/test_read.py

Search/open/brain layers are stubbed; extraction runs for real on temp
files (including a real minimal .docx built with zipfile). No network.
"""

import os
import sys
import tempfile
import zipfile

_TMP_HOME = tempfile.mkdtemp(prefix="jarvis-test-home-")
os.environ["HOME"] = _TMP_HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jarvis_actions  # noqa: E402
import jarvis_brain  # noqa: E402
import jarvis_read  # noqa: E402
import jarvis_web  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    PASSED.append(name)
    print(f"PASS: {name}")


def _sentences(word, n_sent, words_per=12):
    return " ".join(
        " ".join([word] + [f"w{i}"] * (words_per - 2) + [f"end{i}."])
        for i in range(n_sent))


def main():
    work = tempfile.mkdtemp(prefix="jarvis-read-files-")

    resume_path = os.path.join(work, "resume.txt")
    with open(resume_path, "w") as f:
        f.write("Umesh Linga is a bioinformatics engineer based in Indianapolis "
                "with five years of pipeline experience. He builds variant "
                "calling workflows and sequencing data platforms for research "
                "teams. His tools include Python, Nextflow and cloud systems.")

    notes_path = os.path.join(work, "notes.txt")
    with open(notes_path, "w") as f:
        f.write(_sentences("alpha", 40))  # 480 words: several spoken pages

    docx_path = os.path.join(work, "cv.docx")
    xml = ('<?xml version="1.0"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>'
           '<w:p><w:r><w:t>Umesh Linga bioinformatics engineer Indianapolis '
           'experience building genomics pipelines.</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Second paragraph about variant calling and '
           'sequencing data analysis work.</w:t></w:r></w:p>'
           '</w:body></w:document>')
    with zipfile.ZipFile(docx_path, "w") as z:
        z.writestr("word/document.xml", xml)

    pdf_path = os.path.join(work, "report.pdf")
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF-1.4 fake body")

    png_path = os.path.join(work, "photo.png")
    with open(png_path, "wb") as f:
        f.write(b"\x89PNG fake")

    FILES = {"resume": resume_path, "notes": notes_path, "cv": docx_path,
             "report": pdf_path, "photo": png_path}

    def _fake_search(name, want_folder=False):
        if name in FILES:
            return [FILES[name]]
        for path in FILES.values():
            if os.path.basename(path) == name:
                return [path]
        return []

    jarvis_actions.search_files = _fake_search
    opened = []
    jarvis_actions._open = lambda args: opened.append(list(args)) or True
    jarvis_brain.complete = lambda *a, **k: "Summary one here. Summary two follows."

    # ---- txt: summary path announces name + size, offers read more ----
    jarvis_read._reset_for_tests()
    r = jarvis_read.read_file("resume")
    check("txt read announces the file", "Your resume.txt" in r, r)
    check("txt read gives the brain summary", "Summary one here" in r, r)
    check("txt read offers 'read more'", "read more" in r.lower(), r)

    # ---- read more paging across turns, offsets advance, ends honestly ----
    jarvis_read.read_file("notes")
    chunks = []
    for _ in range(10):
        c = jarvis_read.read_more()
        chunks.append(c)
        if "end of notes.txt" in c:
            break
    check("paging produced several pages", len(chunks) >= 3, f"{len(chunks)} pages")
    check("first page is a full spoken page (~110 words)",
          100 <= len(chunks[0].split()) <= 155, f"{len(chunks[0].split())} words")
    check("pages advance (page 2 differs from page 1)", chunks[1] != chunks[0])
    check("paging reaches the end of the file", "end of notes.txt" in chunks[-1])
    r = jarvis_read.read_more()
    check("read more past the end says so", "end of notes.txt" in r, r)
    jarvis_read._reset_for_tests()
    r = jarvis_read.read_more()
    check("read more with nothing loaded is honest", "nothing waiting" in r, r)

    # ---- docx: real extraction from word/document.xml ----
    r = jarvis_read.read_file("cv")
    check("docx read summarised", "Summary one here" in r, r)
    page = jarvis_read.read_more()
    check("docx text really extracted", "bioinformatics engineer" in page, page[:80])

    # ---- offline: no brain -> first sentences of the file itself ----
    jarvis_brain.complete = lambda *a, **k: None
    r = jarvis_read.read_file("resume")
    check("offline read falls back to the file's own first lines",
          "first lines" in r and "Umesh Linga is a bioinformatics" in r, r)
    jarvis_brain.complete = lambda *a, **k: "Summary one here. Summary two follows."

    # ---- missing file: honest ----
    r = jarvis_read.read_file("doesnotexist")
    check("missing file is honest", "couldn't find a file" in r, r)

    # ---- pdf without pdftotext: honest + opened, no fake content ----
    real_which = jarvis_read.shutil.which
    jarvis_read.shutil.which = lambda name: None
    opened.clear()
    try:
        r = jarvis_read.read_file("report")
    finally:
        jarvis_read.shutil.which = real_which
    check("pdf without poppler is honest", "poppler" in r and "pdftotext" in r, r)
    check("pdf without poppler still opens the file",
          any(pdf_path in a for a in opened), repr(opened))
    check("pdf path never invents content", "Summary" not in r, r)

    # ---- unsupported format: opened, honest ----
    opened.clear()
    r = jarvis_read.read_file("photo")
    check("unsupported format is honest",
          "can't read that kind of file aloud" in r, r)
    check("unsupported format still opens the file",
          any(png_path in a for a in opened), repr(opened))

    # ---- routing through try_action ----
    jarvis_web.read_website = lambda target: f"WEB:{target}"
    jarvis_web.search_and_tell = lambda topic, open_results=False: f"SEARCH:{topic}"
    jarvis_read._reset_for_tests()
    r = jarvis_actions.try_action("read my resume")
    check("route: 'read my resume' reads the file",
          r is not None and "here's what it says" in r, repr(r))
    r = jarvis_actions.try_action("read more")
    check("route: 'read more' continues the file",
          r is not None and "Umesh" in r, repr(r)[:80])
    jarvis_read.shutil.which = lambda name: None
    try:
        r = jarvis_actions.try_action("read report.pdf")
    finally:
        jarvis_read.shutil.which = real_which
    check("route: 'read report.pdf' hits the pdf path",
          r is not None and "poppler" in r, repr(r))
    r = jarvis_actions.try_action("read bbc dot com")
    check("route: 'read bbc dot com' still goes to the web reader",
          r == "WEB:bbc dot com", repr(r))
    r = jarvis_actions.try_action("read my clipboard")
    check("route: 'read my clipboard' still goes to the clipboard",
          r is not None and "clipboard" in r, repr(r))

    print(f"\n{len(PASSED)}/{len(PASSED)} read tests passed.")


if __name__ == "__main__":
    main()
