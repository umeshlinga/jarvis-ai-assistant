#!/usr/bin/env python3
"""
test_hud.py - Jarvis v4 live HUD server tests (localhost only).

Starts the real stdlib server on an ephemeral port, fetches the page,
emits an event and reads it back over Server-Sent Events, then shuts
down cleanly.

Run:  python3 tests/test_hud.py
"""
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HOME"] = tempfile.mkdtemp(prefix="jarvis_hud_test_")

import jarvis_hud  # noqa: E402

PASSED = []


def check(name, cond):
    assert cond, f"FAILED: {name}"
    PASSED.append(name)
    print(f"PASS: {name}")


def main():
    server = jarvis_hud.start_server(0)  # ephemeral port
    check("server started on ephemeral port", server is not None)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as resp:
            body = resp.read().decode("utf-8", "replace")
        check("HUD page served and names JARVIS", "JARVIS" in body)
        check("HUD page has the live event wiring", "/events" in body)

        jarvis_hud.emit("heard", "open youtube please")
        found = False
        with urllib.request.urlopen(base + "/events", timeout=5) as resp:
            for _ in range(60):
                line = resp.readline().decode("utf-8", "replace")
                if not line:
                    break
                if "open youtube please" in line:
                    found = True
                    break
        check("emitted event arrives over SSE (backlog replay)", found)
        check("is_running reports True", jarvis_hud.is_running())
    finally:
        jarvis_hud.stop_server()
    check("server stopped cleanly", not jarvis_hud.is_running())

    print(f"\n{len(PASSED)}/{len(PASSED)} HUD tests passed.")


if __name__ == "__main__":
    main()
