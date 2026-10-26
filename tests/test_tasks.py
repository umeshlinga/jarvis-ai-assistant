#!/usr/bin/env python3
"""
test_tasks.py - Jarvis v4 background-task manager tests: statuses,
announcer messages (start / finish / fail) and the spoken status
command, all with fast in-process functions.

Run:  python3 tests/test_tasks.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HOME"] = tempfile.mkdtemp(prefix="jarvis_tasks_test_")

import jarvis_actions  # noqa: E402
import jarvis_tasks  # noqa: E402

PASSED = []


def check(name, cond):
    assert cond, f"FAILED: {name}"
    PASSED.append(name)
    print(f"PASS: {name}")


def main():
    jarvis_tasks._reset_for_tests()
    announced = []
    jarvis_tasks.set_announcer(announced.append)

    # Empty registry -> clean spoken line (also via the actions router).
    r = jarvis_tasks.status_text()
    check("empty status line", "not running any background tasks" in r)
    r2 = jarvis_actions.try_action("what are you doing")
    check("status command routed", r2 is not None and "background tasks" in r2)

    def quick():
        return "Quick job finished with result 42."

    def bad():
        raise ValueError("kaput")

    jarvis_tasks.start("Quick job", quick)
    jarvis_tasks.start("Bad job", bad)
    check("wait_all completes", jarvis_tasks.wait_all(timeout=5))

    tasks = {t["name"]: t for t in jarvis_tasks.list_tasks()}
    check("quick job done with result",
          tasks["Quick job"]["status"] == "done" and "42" in tasks["Quick job"]["result"])
    check("bad job failed with error",
          tasks["Bad job"]["status"] == "failed" and "kaput" in tasks["Bad job"]["error"])
    joined = " | ".join(announced)
    check("announcer heard the starts", "Started: Quick job" in joined and "Started: Bad job" in joined)
    check("announcer heard the finish", "Quick job finished with result 42." in joined)
    check("announcer heard the failure", "Bad job failed: kaput" in joined)

    st = jarvis_tasks.status_text()
    check("status text reports the last task outcome",
          "last" in st.lower() and ("Quick job" in st or "Bad job" in st))

    print(f"\n{len(PASSED)}/{len(PASSED)} task tests passed.")


if __name__ == "__main__":
    main()
