#!/usr/bin/env python3
"""
test_code.py - Jarvis v4 voice-coding tests. The LLM is stubbed (no
network), the editor opener is stubbed (nothing actually opens), and
runs execute tiny real scripts inside a TEMP code_dir sandbox.

Run:  python3 tests/test_code.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMPHOME = tempfile.mkdtemp(prefix="jarvis_code_test_")
os.environ["HOME"] = _TMPHOME

import jarvis_code  # noqa: E402
import jarvis_config  # noqa: E402
import jarvis_tasks  # noqa: E402

PASSED = []


def check(name, cond):
    assert cond, f"FAILED: {name}"
    PASSED.append(name)
    print(f"PASS: {name}")


def main():
    code_dir = os.path.join(_TMPHOME, "projects")
    jarvis_config.load_config()["code_dir"] = code_dir
    jarvis_tasks._reset_for_tests()
    jarvis_tasks.set_announcer(lambda msg: None)

    # --- create ---
    jarvis_code._llm_code = lambda prompt, system=None: "```python\nprint('hello from code')\n```"
    r = jarvis_code.create_code("hello", "prints a greeting")
    path = os.path.join(code_dir, "hello.py")
    check("create reply names the file", "Created hello.py" in r)
    check("create wrote the file with the code",
          os.path.exists(path) and "print('hello from code')" in open(path).read())
    check("state remembers last file", jarvis_code.last_file() == path)

    # brain down -> honest, no file
    jarvis_code._llm_code = lambda prompt, system=None: None
    r = jarvis_code.create_code("nope", "does nothing")
    check("create with brain down is honest", "couldn't write" in r.lower())
    check("no file written when brain is down", not os.path.exists(os.path.join(code_dir, "nope.py")))

    # --- open (stubbed) ---
    opened = []
    jarvis_code._open = lambda args: opened.append(args) or True
    r = jarvis_code.open_last_code()
    check("open it opens the last file", "Opening hello.py" in r and opened and opened[0][-1] == path)

    # --- run synchronously (real subprocess on a tiny script) ---
    r = jarvis_code.run_file_sync(path)
    check("run success speaks output", "ran successfully" in r and "hello from code" in r)

    # --- run through the background-task runner (needs confirmation) ---
    r = jarvis_code.run_last_code(confirm=None)
    check("run without a way to confirm is refused honestly", "can't ask" in r.lower())
    r = jarvis_code.run_last_code(confirm=lambda prompt: True)
    check("confirmed run starts as a background task", "Starting hello.py" in r)
    check("background run finishes", jarvis_tasks.wait_all(timeout=10))

    # --- break it, run it, fix it ---
    bad = "raise ValueError('boom goes the script')\n"
    with open(path, "w") as f:
        f.write(bad)
    r = jarvis_code.run_file_sync(path)
    check("broken run reports the error's last line", "boom goes the script" in r and "fix it" in r.lower())

    def fix_llm(prompt, system=None):
        if "corrected script" in prompt:
            return "```python\nprint('fixed and fine')\n```"
        return "It was raising an error on purpose; now it just prints."

    jarvis_code._llm_code = fix_llm
    r = jarvis_code.fix_last_code()
    check("fix reply explains the change", "I fixed hello.py" in r and "raising an error" in r)
    check("fix rewrote the file", "fixed and fine" in open(path).read())
    check("fix kept a .bak of the broken version",
          os.path.exists(path + ".bak") and "boom" in open(path + ".bak").read())
    r = jarvis_code.run_file_sync(path)
    check("fixed file runs clean", "ran successfully" in r and "fixed and fine" in r)

    print(f"\n{len(PASSED)}/{len(PASSED)} code tests passed.")


if __name__ == "__main__":
    main()
