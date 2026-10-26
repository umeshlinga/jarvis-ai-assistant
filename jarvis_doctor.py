"""
jarvis_doctor.py - Jarvis v7 health check (`./jarvis --doctor`).

Owner-facing diagnostics: Leo can ask for one screenshot of this
output and know the state of the whole install. Every check prints one
plain line starting PASS / FAIL / NOTE, each check is individually
guarded, and the doctor itself can never traceback - a broken install
is exactly when it is needed most.

Checks: python version; microphone; Groq key (present + shape, masked);
the OpenAI slot (absent / valid / WRONG-KEY - the v6 run had his Groq
key sitting in it, silently 401-ing every deep call); brain routes
after validation; speech-to-text status; the voice probe (synthesises
one tiny silent sample per neural rung); Notification Center database
readability (Full Disk Access); Accessibility (best-effort); HUD port;
internet reachability.
"""

import os
import socket
import subprocess
import sys


def _mask(key):
    k = str(key or "")
    if len(k) <= 8:
        return "set"
    return k[:4] + "…" + k[-4:]


def _check(label, fn):
    """Run one check; an unexpected error becomes a NOTE, never a crash."""
    try:
        fn()
    except Exception as e:
        print(f"NOTE: {label}: couldn't check ({e})")


def run_doctor():
    print("=" * 54)
    print("  JARVIS DOCTOR - health check")
    print("=" * 54)

    import jarvis_brain
    import jarvis_config
    import jarvis_notify
    import jarvis_voice

    def c_python():
        v = sys.version_info
        level = "PASS" if v >= (3, 10) else "FAIL"
        print(f"{level}: Python {v.major}.{v.minor}.{v.micro}")

    def c_mic():
        if jarvis_voice.mic_available():
            print("PASS: Microphone: an input device is available")
        else:
            print("FAIL: Microphone: no input device found (or "
                  "sounddevice missing) - check Microphone permission")

    def c_groq_key():
        state = jarvis_brain.key_slot_state("groq")
        if state == "ok":
            print(f"PASS: Groq key: present ({_mask(jarvis_brain.groq_key())})")
        elif state == "absent":
            print("NOTE: Groq key: not set - local mode only until one is added")
        elif state == "wrong-key":
            print("NOTE: Groq key: the Groq slot holds an OpenAI key - "
                  "ignored it")
        else:
            print("NOTE: Groq key: the Groq slot holds a key I don't "
                  "recognise - ignored it")

    def c_openai_slot():
        state = jarvis_brain.key_slot_state("openai")
        if state == "ok":
            print(f"PASS: OpenAI key: present ({_mask(jarvis_brain.openai_key())}) "
                  f"- deep/agent brain upgrade active")
        elif state == "absent":
            print("NOTE: OpenAI key: not set - Groq-only, as you chose")
        elif state == "wrong-key":
            # The exact v6 failure, stated the way the banner states it.
            for note in jarvis_brain.key_notes():
                if "OpenAI slot" in note:
                    print(f"NOTE: {note[6:] if note.startswith('Note: ') else note}")
                    break
            else:
                print("NOTE: OpenAI key: the OpenAI slot holds a Groq "
                      "key - ignored it; Jarvis is Groq-only, as you chose")
        else:
            print("NOTE: OpenAI key: the OpenAI slot holds a key I don't "
                  "recognise - ignored it")

    def c_brain():
        fast = jarvis_brain.providers_available()
        deep = jarvis_brain.deep_providers_available()
        if fast or deep:
            print(f"PASS: Brain routes: fast chat via "
                  f"{'/'.join(fast) or 'none'}; deep/agent via "
                  f"{'/'.join(deep) or 'none'}")
        else:
            print("NOTE: Brain routes: no usable key - local actions only")

    def c_ears():
        print(f"PASS: Ears (speech-to-text): {jarvis_voice.stt_status()}")

    def c_voice():
        for level, text in jarvis_voice.voice_probe():
            print(f"{level}: Voice - {text}")

    def c_ncdb():
        path = jarvis_notify.ncdb_path()
        if not os.path.exists(path):
            print("NOTE: Notification Center: database not found here")
            return
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            try:
                conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
            finally:
                conn.close()
            print("PASS: Notification Center: database readable - "
                  "Jarvis can watch your notifications")
        except Exception:
            print("NOTE: Notification Center: database exists but is not "
                  "readable - needs Full Disk Access for the app running "
                  "Jarvis (Terminal, or Jarvis.app if you launch the app)")

    def c_accessibility():
        if sys.platform != "darwin":
            print("NOTE: Accessibility: unknown (can only be checked on a Mac)")
            return
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to return name of '
                 'first application process'],
                capture_output=True, timeout=3)
            if r.returncode == 0:
                print("PASS: Accessibility: System Events answered - "
                      "typing / clicking should work")
            else:
                print("NOTE: Accessibility: unknown - System Events did "
                      "not answer (grant it under Privacy & Security > "
                      "Accessibility)")
        except Exception:
            print("NOTE: Accessibility: unknown - the check itself failed")

    def c_hud_port():
        try:
            import jarvis_hud
            if jarvis_hud.is_running():
                print("PASS: HUD port: in use by this Jarvis (fine)")
                return
        except Exception:
            pass
        try:
            port = int(jarvis_config.load_config().get("hud_port", 8765))
        except Exception:
            port = 8765
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            print(f"PASS: HUD port {port}: free")
        except Exception:
            print(f"NOTE: HUD port {port}: busy - another Jarvis (or app) "
                  f"holds it; the HUD will be skipped, voice still works")
        finally:
            try:
                s.close()
            except Exception:
                pass

    def c_internet():
        try:
            import http.client
            conn = http.client.HTTPSConnection("api.groq.com", timeout=4)
            try:
                conn.request("HEAD", "/")
                resp = conn.getresponse()
                resp.read()
                print(f"PASS: Internet: api.groq.com reachable "
                      f"(HTTP {resp.status})")
            finally:
                conn.close()
        except Exception as e:
            print(f"NOTE: Internet: api.groq.com not reachable "
                  f"({jarvis_voice._scrub_error(e, limit=60)}) - cloud "
                  f"brain/ears pause until it is back")

    for label, fn in (
        ("Python", c_python),
        ("Microphone", c_mic),
        ("Groq key", c_groq_key),
        ("OpenAI slot", c_openai_slot),
        ("Brain routes", c_brain),
        ("Ears", c_ears),
        ("Voice", c_voice),
        ("Notification Center", c_ncdb),
        ("Accessibility", c_accessibility),
        ("HUD port", c_hud_port),
        ("Internet", c_internet),
    ):
        _check(label, fn)

    print("=" * 54)
    return 0


if __name__ == "__main__":
    sys.exit(run_doctor())
